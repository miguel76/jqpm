# jqpm — a minimal package manager for jq

`jqpm` lets you declare, install, and pin `jq` module dependencies from git
repos (GitHub by default), the same way `npm`/`cargo`/`pip` manage
dependencies — but radically simpler, because **jq already has a module
system**. `jqpm` doesn't reinvent that; it just fetches git repos into the
exact folder layout jq's own `import` resolution already looks for.

This means once a package is installed, you use it with **plain, unmodified
`jq`** — no custom loader, no wrapper required at runtime:

```sh
jq -L jq_modules 'import "owner/repo" as X; X::somefunc' input.json
```

## Prior art

This is directly inspired by [jqnpm](https://github.com/jqnpm/jqnpm)
(2014–2021, now archived) — same idea of GitHub-namespaced packages
(`owner/repo`) and semver git tags. `jqpm` differs mainly in that it doesn't
wrap `jq` at all at runtime; it leans entirely on jq's native `-L` search
path and `import "owner/repo"` resolution, so the "package manager" part
of the system is only responsible for *fetching*, not *loading*.

## Install

Requires Python 3.8+, `git`, and `jq` (1.6+ recommended for reliable
`import`) on your `PATH`. It's a single file, no dependencies:

```sh
curl -O https://.../jqpm.py
chmod +x jqpm.py
# optionally: sudo ln -s $(pwd)/jqpm.py /usr/local/bin/jqpm
```

## Package convention

A package is just a git repo named `repo`, owned by `owner` on GitHub,
containing a file `repo.jq` at its root (this matches jq's own module
naming rules: `import "owner/repo"` looks for `owner/repo.jq` or
`owner/repo/repo.jq` on the search path — jqpm uses the latter).

```
my-jq-lib/
  my-jq-lib.jq     # the module's functions, this is what gets imported
  README.md
```

Nothing else is required — no manifest inside the package itself. Tag
releases with semver git tags (`v1.2.0` or `1.2.0`).

## Usage

```sh
# start a new project
jqpm init

# add a dependency (resolves the latest tag satisfying the range,
# writes it to jqpackage.json, installs it, and records the exact
# commit in jqpackage-lock.json)
jqpm add someuser/jq-strings@^1.0.0

# install everything from jqpackage.json
# (reproducible: reuses the exact commit from jqpackage-lock.json
#  if present, so installs are pinned until you explicitly update)
jqpm install

# re-resolve all versions against latest matching tags
jqpm install --update

# see what's installed and at what commit
jqpm list

# remove a dependency
jqpm remove someuser/jq-strings

# run jq with -L jq_modules already set, so imports just work
jqpm run -n 'import "someuser/jq-strings" as S; "hi" | S::upper'
```

Or skip `jqpm run` entirely and call `jq -L jq_modules ...` yourself —
that's the whole point of piggybacking on jq's native module resolution.

## Version specs

Matched against the package repo's git tags:

| spec         | meaning                                        |
|--------------|-------------------------------------------------|
| `1.2.3`      | exact tag (`v1.2.3` or `1.2.3`)                  |
| `^1.2.3`     | latest `1.x.x` >= `1.2.3`                        |
| `~1.2.3`     | latest `1.2.x` >= `1.2.3`                        |
| `*` (default)| latest tag, or default branch `HEAD` if no tags  |
| `#some-ref`  | exact git ref: branch name, tag, or commit sha   |

## Files

- `jqpackage.json` — manifest: name, version, dependencies (checked in)
- `jqpackage-lock.json` — resolved commit per dependency (checked in, for
  reproducible installs — same idea as `package-lock.json`/`Cargo.lock`)
- `jq_modules/` — installed packages, laid out as `jq_modules/owner/repo/repo.jq`
  (gitignore this, like `node_modules/`)

## Testing

```sh
pip install -r requirements-dev.txt
pytest
```

The test suite doesn't touch GitHub or the network: it builds throwaway git
repos on disk (with real tags) and points jqpm at them via `file://` URLs,
so it exercises the real `git clone`/`ls-remote`/`checkout` codepath end to
end without any external test fixtures to host or maintain.

## What's deliberately left out (v0)

This is intentionally minimal. Not included yet, roughly in order of
likely usefulness:

- **A real registry / search.** Right now "the registry" is just "GitHub,
  addressed by `owner/repo`." A `jqpm search` would need a real index
  (could start as a static JSON file in a shared repo, à la early
  Bower/npm-before-npm).
- **Integrity checking.** The lockfile records a commit sha (which is
  already tamper-evident for that repo's history) but doesn't verify
  signatures or checksums of the fetched tree.
- **Transitive dependencies.** If a package itself depends on other
  packages, there's no manifest-in-package to declare that yet, so nested
  `jq_modules` resolution isn't handled. jq's own `import` doesn't
  automatically search nested `jq_modules` either, so this would need
  either flattening (npm's later strategy) or a package-level manifest
  jqpm reads recursively.
- **`jqpm publish`.** Publishing today is just "push a git tag." A
  `publish` command could automate tagging/pushing and maybe validate
  that `repo.jq` exists and parses.
- **A real "private registry" concept**, though any git host already works
  today: `jqpm add https://gitlab.example.com/team/jq-lib.git@^1.0.0`
  installs the same way as a GitHub `owner/repo` dependency.

## License

Public domain / do whatever you want with it — it's a starting point.
