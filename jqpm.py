#!/usr/bin/env python3
"""
jqpm -- a minimal package manager for jq

Design in one sentence: jqpm's only job is to fetch git repos into the
folder layout jq *already* knows how to `import` from (via `-L`), so no
custom module loader or jq wrapper is required at runtime.

Convention
----------
A "package" is just a git repo, referenced as `owner/repo` (GitHub by
default, but any git URL works). jqpm clones it into:

    jq_modules/<owner>/<repo>/<repo>.jq

which matches jq's native module resolution: `import "owner/repo" as X;`
with `-L jq_modules` on the command line will find exactly that file
(jq looks for `<search-path>/owner/repo.jq` or
`<search-path>/owner/repo/repo.jq` -- we use the latter).

So once installed, a package is used with **plain jq**, no wrapper needed:

    jq -L jq_modules 'import "owner/repo" as X; X::somefunc' input.json

`jqpm run` is just a convenience that adds `-L jq_modules` for you.

Manifest: jqpackage.json   (like package.json)
Lockfile: jqpackage-lock.json  (resolved commit per dependency)

Version specs (matched against git tags, tags may be "v1.2.3" or "1.2.3"):
    "1.2.3"     exact tag
    "^1.2.3"    latest 1.x.x >= 1.2.3
    "~1.2.3"    latest 1.2.x >= 1.2.3
    "*"         latest tag (or default branch HEAD if no tags)
    "#<ref>"    exact git ref / branch / commit sha

Multi-file packages
--------------------
A package's entry file (`<repo>.jq`) may `import`/`include` other `.jq`/
`.json` files that live in the same repo, exactly like Python or JS: a spec
starting with `./` or `../` is a *local* import, resolved relative to the
file that contains it, and is shipped and installed together with the
package. Anything else (e.g. `import "owner/repo" as X;`) is *package-manager-mediated*: resolved by jq itself via `-L jq_modules`, unchanged.

jq's own `import`/`include` resolve `./` paths relative to the process's
cwd, not to the file that contains them -- which breaks as soon as a
package with local imports is installed under `jq_modules/owner/repo/`
and used from a different project. jqpm works around this at install time
by rewriting each local spec into its fully-qualified `owner/repo/...`
form (still resolved by plain jq via `-L`, no runtime shim needed).
"""

import argparse
import json
import os
import posixpath
import re
import subprocess
import sys
from pathlib import Path

MANIFEST = "jqpackage.json"
LOCKFILE = "jqpackage-lock.json"
MODULES_DIR = "jq_modules"

# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def die(msg):
    print(f"jqpm: error: {msg}", file=sys.stderr)
    sys.exit(1)


def run(cmd, cwd=None, capture=True, check=True):
    result = subprocess.run(
        cmd, cwd=cwd,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
    )
    if check and result.returncode != 0:
        stderr = result.stderr if capture else ""
        die(f"command failed: {' '.join(cmd)}\n{stderr}")
    return result


def load_json(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    with open(p) as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def load_manifest():
    m = load_json(MANIFEST)
    if m is None:
        die(f"no {MANIFEST} found here. Run `jqpm init` first.")
    assert m is not None
    m.setdefault("dependencies", {})
    return m


# --------------------------------------------------------------------------
# spec / version parsing
# --------------------------------------------------------------------------

SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")


def semver_tuple(tag):
    match = SEMVER_RE.match(tag)
    if not match:
        return None
    return tuple(int(x) for x in match.groups())


def parse_dep_key(key):
    """
    'owner/repo'                              -> assumed GitHub
    'https://example.com/owner/repo.git'       -> explicit git URL
    'git@example.com:owner/repo.git'           -> explicit git URL (ssh)
    In all cases the local install path is jq_modules/<owner>/<repo>,
    derived from the last two path segments, so `import "owner/repo"`
    works the same regardless of where it actually came from.
    """
    if "://" in key or key.startswith("git@"):
        url = key
        path = re.sub(r"\.git$", "", url.split(":")[-1].rstrip("/"))
        parts = [p for p in path.split("/") if p]
        if len(parts) < 2:
            die(f"can't infer owner/repo from URL '{key}'")
        owner, repo = parts[-2], parts[-1]
        return owner, repo, url
    if "/" not in key:
        die(f"dependency key '{key}' must be 'owner/repo' or a full git URL")
    owner, repo = key.split("/", 1)
    return owner, repo, f"https://github.com/{owner}/{repo}.git"


def parse_spec(spec):
    """Return ('exact'|'caret'|'tilde'|'latest'|'ref', value)"""
    spec = (spec or "*").strip()
    if spec in ("", "*", "latest"):
        return ("latest", None)
    if spec.startswith("#"):
        return ("ref", spec[1:])
    if spec.startswith("^"):
        v = semver_tuple(spec[1:])
        if not v:
            die(f"bad version spec: {spec}")
        return ("caret", v)
    if spec.startswith("~"):
        v = semver_tuple(spec[1:])
        if not v:
            die(f"bad version spec: {spec}")
        return ("tilde", v)
    if semver_tuple(spec):
        return ("exact", spec)
    # anything else: treat as an explicit git ref (branch/tag/sha)
    return ("ref", spec)


def list_remote_tags(url):
    result = run(["git", "ls-remote", "--tags", url], check=False)
    if result.returncode != 0:
        return []
    tags = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        ref = parts[1]
        if ref.endswith("^{}"):  # dereferenced annotated tag, skip dupe
            continue
        if ref.startswith("refs/tags/"):
            tags.append(ref[len("refs/tags/"):])
    return tags


def resolve_spec(url, spec):
    """Return a git ref (tag/branch/sha) to check out."""
    kind, value = parse_spec(spec)

    if kind == "ref":
        return value

    tags = list_remote_tags(url)
    semver_tags = [(semver_tuple(t), t) for t in tags]
    semver_tags = [(v, t) for v, t in semver_tags if v is not None]
    semver_tags.sort()

    if kind == "exact":
        target = semver_tuple(value)
        for v, t in semver_tags:
            if v == target:
                return t
        die(f"no tag matching exact version {value} found at {url}")

    if kind == "latest":
        if semver_tags:
            return semver_tags[-1][1]
        return "HEAD"  # fall back to default branch

    if kind == "caret":
        assert isinstance(value, tuple) and len(value) == 3
        major = value[0]
        candidates = [(v, t) for v, t in semver_tags if v[0] == major and v >= value]
        if not candidates:
            die(f"no tag satisfying ^{'.'.join(map(str, value))} found at {url}")
        return sorted(candidates)[-1][1]

    if kind == "tilde":
        assert isinstance(value, tuple) and len(value) == 3
        major, minor = value[0], value[1]
        candidates = [
            (v, t) for v, t in semver_tags
            if v[0] == major and v[1] == minor and v >= value
        ]
        if not candidates:
            die(f"no tag satisfying ~{'.'.join(map(str, value))} found at {url}")
        return sorted(candidates)[-1][1]

    die(f"unhandled spec kind: {kind}")


# --------------------------------------------------------------------------
# local (relative) imports within a package
# --------------------------------------------------------------------------

# import "./x" as X;   include "./x";   -- only specs starting with ./ or ../
# are local; anything else is left alone for jq's own -L search-path lookup.
LOCAL_IMPORT_RE = re.compile(r'\b(import|include)\b(\s*)"(\.\.?/[^"]*)"')


def _resolve_local_spec(file_rel_dir, spec, prefix):
    """
    file_rel_dir: POSIX path of the importing file's directory, relative to
    the package root ("" at the root).
    spec: the quoted import/include target, e.g. "./helper" or "../x/y".
    prefix: the package's fully-qualified module path, e.g. "owner/repo".
    Returns the rewritten, package-manager-resolvable spec.
    """
    joined = posixpath.normpath(posixpath.join(file_rel_dir, spec))
    if joined == ".." or joined.startswith("../"):
        die(f"local import \"{spec}\" in {prefix}/{file_rel_dir or '.'} "
            f"escapes the package root")
    return prefix if joined == "." else f"{prefix}/{joined}"


def rewrite_local_imports(root_dir, prefix):
    """
    Rewrite every local (./  or ../) import/include spec found in the .jq
    files under root_dir into its fully-qualified `prefix/...` form, so it
    resolves the same way regardless of the consuming project's own
    directory. See the "Multi-file packages" note in the module docstring.
    """
    root_dir = Path(root_dir)
    for path in sorted(root_dir.rglob("*.jq")):
        rel_dir = path.parent.relative_to(root_dir).as_posix()
        if rel_dir == ".":
            rel_dir = ""
        text = path.read_text()

        def _sub(m, rel_dir=rel_dir):
            keyword, ws, spec = m.group(1), m.group(2), m.group(3)
            new_spec = _resolve_local_spec(rel_dir, spec, prefix)
            return f'{keyword}{ws}"{new_spec}"'

        new_text = LOCAL_IMPORT_RE.sub(_sub, text)
        if new_text != text:
            path.write_text(new_text)


# --------------------------------------------------------------------------
# install
# --------------------------------------------------------------------------

def install_one(owner, repo, url, spec, lock, locked_entry=None):
    if locked_entry and locked_entry.get("spec") == spec:
        # Reproducible install: reuse the exact commit from the lockfile
        # instead of re-resolving the spec against remote tags.
        ref = locked_entry["commit"]
        resolved = locked_entry["resolved"]
        print(f"  {owner}/{repo}  (locked @ {ref[:8]})")
    else:
        ref = resolve_spec(url, spec)
        resolved = ref
        print(f"  {owner}/{repo}  ({spec} -> {ref})")

    dest = Path(MODULES_DIR) / owner / repo

    if dest.exists():
        run(["rm", "-rf", str(dest)], capture=True)
    dest.parent.mkdir(parents=True, exist_ok=True)

    run(["git", "clone", "--quiet", url, str(dest)])
    checkout_ref = ref if ref != "HEAD" else _default_branch(dest)
    run(["git", "-C", str(dest), "checkout", "--quiet", checkout_ref])
    commit = run(["git", "-C", str(dest), "rev-parse", "HEAD"]).stdout.strip()
    run(["rm", "-rf", str(dest / ".git")], capture=True)  # keep installed tree lean
    rewrite_local_imports(dest, f"{owner}/{repo}")

    entry = dest / f"{repo}.jq"
    if not entry.exists():
        print(f"    warning: expected entry file {entry} not found. "
              f"'import \"{owner}/{repo}\"' will fail unless the package "
              f"provides {repo}.jq at its root.", file=sys.stderr)

    lock[f"{owner}/{repo}"] = {
        "source": url,
        "spec": spec,
        "resolved": resolved,
        "commit": commit,
    }


def _default_branch(dest):
    result = run(["git", "-C", str(dest), "symbolic-ref", "refs/remotes/origin/HEAD"], check=False)
    if result.returncode == 0:
        return result.stdout.strip().split("/")[-1]
    return "HEAD"


def cmd_install(args):
    manifest = load_manifest()
    deps = manifest.get("dependencies", {})
    if not deps:
        print("no dependencies listed in jqpackage.json")
        return
    existing_lock = {} if args.update else load_json(LOCKFILE, {})
    new_lock = {}
    print(f"installing {len(deps)} package(s) into {MODULES_DIR}/")
    for key, value in deps.items():
        if isinstance(value, dict):
            owner, repo, _ = parse_dep_key(key)
            url, spec = value["url"], value.get("version", "*")
        else:
            owner, repo, url = parse_dep_key(key)
            spec = value
        install_one(owner, repo, url, spec, new_lock, locked_entry=existing_lock.get(key))
    save_json(LOCKFILE, new_lock)
    print("done.")


# --------------------------------------------------------------------------
# other commands
# --------------------------------------------------------------------------

def cmd_init(args):
    if Path(MANIFEST).exists() and not args.force:
        die(f"{MANIFEST} already exists (use --force to overwrite)")
    name = args.name or Path.cwd().name
    manifest = {
        "name": name,
        "version": "0.1.0",
        "description": "",
        "main": "main.jq",
        "dependencies": {},
    }
    save_json(MANIFEST, manifest)
    print(f"created {MANIFEST}")


SSH_SCP_RE = re.compile(r"^[^@/\s]+@[^@/\s]+:")


def split_pkg_and_spec(raw):
    """
    'owner/repo'                       -> ('owner/repo', '*')
    'owner/repo@^1.0.0'                -> ('owner/repo', '^1.0.0')
    'https://host/o/r.git'             -> ('https://host/o/r.git', '*')
    'https://host/o/r.git@^1.0.0'      -> ('https://host/o/r.git', '^1.0.0')
    'git@host:o/r.git'                 -> ('git@host:o/r.git', '*')
    'git@host:o/r.git@^1.0.0'          -> ('git@host:o/r.git', '^1.0.0')
    A URL's own '://' is protected from being mistaken for the '@' split
    by only splitting on the LAST '@'. An scp-like ssh URL ('user@host:path')
    has its own leading '@' that is never a version separator, so that
    prefix is stripped first and the version split only applies after it.
    """
    match = SSH_SCP_RE.match(raw)
    if match:
        prefix, rest = raw[:match.end()], raw[match.end():]
        if "@" in rest:
            path, spec = rest.rsplit("@", 1)
            return prefix + path, spec
        return raw, "*"
    if "@" in raw:
        head, spec = raw.rsplit("@", 1)
        if head == "" or head.endswith(":"):
            return raw, "*"
        return head, spec
    return raw, "*"


def cmd_add(args):
    manifest = load_manifest()
    for raw in args.packages:
        key, spec = split_pkg_and_spec(raw)
        owner, repo, url = parse_dep_key(key)
        dep_key = f"{owner}/{repo}"
        is_default_github = url == f"https://github.com/{owner}/{repo}.git"
        if is_default_github:
            manifest["dependencies"][dep_key] = spec
        else:
            manifest["dependencies"][dep_key] = {"url": url, "version": spec}
    save_json(MANIFEST, manifest)
    print(f"added to {MANIFEST}, run `jqpm install` to fetch")
    if args.install:
        cmd_install(argparse.Namespace(update=False))


def cmd_remove(args):
    manifest = load_manifest()
    lock = load_json(LOCKFILE, {})
    for key in args.packages:
        owner, repo, _ = parse_dep_key(key)
        full = f"{owner}/{repo}"
        manifest["dependencies"].pop(full, None)
        lock.pop(full, None)
        dest = Path(MODULES_DIR) / owner / repo
        if dest.exists():
            run(["rm", "-rf", str(dest)])
        print(f"removed {full}")
    save_json(MANIFEST, manifest)
    save_json(LOCKFILE, lock)


def cmd_list(args):
    lock = load_json(LOCKFILE, {})
    if not lock:
        print("no packages installed (run `jqpm install`)")
        return
    for key, info in lock.items():
        print(f"{key}  {info['spec']} -> {info['resolved']} ({info['commit'][:8]})")


def cmd_run(args):
    modules_path = str(Path(MODULES_DIR).resolve())
    jq_args = args.jq_args
    if jq_args and jq_args[0] == "--":
        jq_args = jq_args[1:]
    cmd = ["jq", "-L", modules_path] + jq_args
    os.execvp("jq", cmd)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(prog="jqpm", description="A minimal package manager for jq")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create a jqpackage.json in the current directory")
    p.add_argument("--name")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("install", help="install all dependencies from jqpackage.json")
    p.add_argument("--update", action="store_true", help="re-resolve versions, ignoring the lockfile")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("add", help="add package(s) to jqpackage.json, e.g. `jqpm add owner/repo@^1.0.0`")
    p.add_argument("packages", nargs="+")
    p.add_argument("--no-install", dest="install", action="store_false", default=True)
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("remove", help="remove package(s)")
    p.add_argument("packages", nargs="+")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("list", help="list installed packages")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("run", help="run jq with -L jq_modules already set, e.g. `jqpm run -- 'import ...'`")
    p.add_argument("jq_args", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_run)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
