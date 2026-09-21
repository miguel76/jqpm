"""End-to-end tests for transitive dependency resolution: a package's own
jqpackage.json is walked recursively and everything gets flattened into the
project's top-level jq_modules/."""

import json
import subprocess

MANIFEST = "jqpackage.json"
LOCKFILE = "jqpackage-lock.json"


def _git(args, cwd):
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    )


def read_json(project, name):
    return json.loads((project / name).read_text())


def _with_manifest(deps):
    return {"name": "x", "version": "0.1.0", "dependencies": deps}


def _dep(fixture_repo, spec):
    """A dependency entry pointing at a local file:// fixture repo -- the
    explicit {url, version} manifest form, since the shorthand 'owner/repo'
    always resolves to a real GitHub URL."""
    return {"url": fixture_repo.url, "version": spec}


def test_transitive_dependency_is_fetched_and_flattened(jqpm, make_repo):
    base = make_repo("acme", "base", jq_body='def deepgreet: "deep";\n', tags=["v1.0.0"])
    mid = make_repo(
        "acme", "mid",
        jq_body='import "acme/base" as B;\ndef greet: B::deepgreet;\n',
        extra_files={MANIFEST: json.dumps(_with_manifest({"acme/base": _dep(base, "^1.0.0")}))},
        tags=["v1.0.0"],
    )
    jqpm("init")
    jqpm("add", f"{mid}@^1.0.0")

    base_entry = jqpm.project / "jq_modules" / "acme" / "base" / "base.jq"
    mid_entry = jqpm.project / "jq_modules" / "acme" / "mid" / "mid.jq"
    assert base_entry.exists()
    assert mid_entry.exists()

    lock = read_json(jqpm.project, LOCKFILE)
    assert lock["acme/mid"]["direct"] is True
    assert lock["acme/base"]["direct"] is False
    assert lock["acme/base"]["requested_by"] == ["acme/mid"]

    result = jqpm(
        "run", "--", "-n", "-r",
        'import "acme/mid" as M; M::greet',
    )
    assert result.stdout.strip() == "deep"


def test_direct_dependency_wins_over_transitive_request(jqpm, make_repo):
    base = make_repo("acme", "base", jq_body='def v: "old";\n', tags=["v1.0.0"])
    base.tag("v2.0.0", jq_body='def v: "new";\n')
    mid = make_repo(
        "acme", "mid",
        jq_body='import "acme/base" as B;\n',
        extra_files={MANIFEST: json.dumps(_with_manifest({"acme/base": _dep(base, "^1.0.0")}))},
        tags=["v1.0.0"],
    )
    jqpm("init")
    jqpm("add", f"{mid}@^1.0.0", "--no-install")
    jqpm("add", f"{base}@^2.0.0", "--no-install")
    jqpm("install")

    lock = read_json(jqpm.project, LOCKFILE)
    assert lock["acme/base"]["direct"] is True
    assert lock["acme/base"]["resolved"] == "v2.0.0"
    entry = jqpm.project / "jq_modules" / "acme" / "base" / "base.jq"
    assert "new" in entry.read_text()


def test_diamond_dependency_picks_higher_transitive_version(jqpm, make_repo):
    base = make_repo("acme", "base", jq_body='def v: "1";\n', tags=["v1.0.0"])
    base.tag("v1.5.0", jq_body='def v: "1.5";\n')

    left = make_repo(
        "acme", "left",
        jq_body='import "acme/base" as B;\n',
        extra_files={MANIFEST: json.dumps(_with_manifest({"acme/base": _dep(base, "^1.0.0")}))},
        tags=["v1.0.0"],
    )
    right = make_repo(
        "acme", "right",
        jq_body='import "acme/base" as B;\n',
        extra_files={MANIFEST: json.dumps(_with_manifest({"acme/base": _dep(base, "~1.5.0")}))},
        tags=["v1.0.0"],
    )

    jqpm("init")
    jqpm("add", f"{left}@^1.0.0", "--no-install")
    jqpm("add", f"{right}@^1.0.0", "--no-install")
    jqpm("install")

    lock = read_json(jqpm.project, LOCKFILE)
    assert lock["acme/base"]["resolved"] == "v1.5.0"
    assert sorted(lock["acme/base"]["requested_by"]) == ["acme/left", "acme/right"]
    entry = jqpm.project / "jq_modules" / "acme" / "base" / "base.jq"
    assert "1.5" in entry.read_text()


def test_conflicting_refs_die_with_helpful_message(jqpm, make_repo):
    base = make_repo("acme", "base", tags=[], branch="main")
    base.tag("side", jq_body='def v: "side";\n')
    left = make_repo(
        "acme", "left",
        jq_body='import "acme/base" as B;\n',
        extra_files={MANIFEST: json.dumps(_with_manifest({"acme/base": _dep(base, "#main")}))},
        tags=["v1.0.0"],
    )
    right = make_repo(
        "acme", "right",
        jq_body='import "acme/base" as B;\n',
        extra_files={MANIFEST: json.dumps(_with_manifest({"acme/base": _dep(base, "#side")}))},
        tags=["v1.0.0"],
    )
    jqpm("init")
    jqpm("add", f"{left}@^1.0.0", "--no-install")
    jqpm("add", f"{right}@^1.0.0", "--no-install")
    result = jqpm("install", check=False)
    assert result.returncode != 0
    assert "conflicting versions requested for acme/base" in result.stderr
    assert "explicit top-level dependency" in result.stderr


def test_explicit_top_level_dependency_resolves_ref_conflict(jqpm, make_repo):
    base = make_repo("acme", "base", tags=[], branch="main")
    base.tag("side", jq_body='def v: "side";\n')
    left = make_repo(
        "acme", "left",
        jq_body='import "acme/base" as B;\n',
        extra_files={MANIFEST: json.dumps(_with_manifest({"acme/base": _dep(base, "#main")}))},
        tags=["v1.0.0"],
    )
    right = make_repo(
        "acme", "right",
        jq_body='import "acme/base" as B;\n',
        extra_files={MANIFEST: json.dumps(_with_manifest({"acme/base": _dep(base, "#side")}))},
        tags=["v1.0.0"],
    )
    jqpm("init")
    jqpm("add", f"{left}@^1.0.0", "--no-install")
    jqpm("add", f"{right}@^1.0.0", "--no-install")
    jqpm("add", f"{base}@#side", "--no-install")
    jqpm("install")

    lock = read_json(jqpm.project, LOCKFILE)
    assert lock["acme/base"]["direct"] is True
    entry = jqpm.project / "jq_modules" / "acme" / "base" / "base.jq"
    assert "side" in entry.read_text()


def test_transitive_install_is_reproducible_via_lockfile(jqpm, make_repo):
    base = make_repo("acme", "base", tags=["v1.0.0"])
    mid = make_repo(
        "acme", "mid",
        jq_body='import "acme/base" as B;\n',
        extra_files={MANIFEST: json.dumps(_with_manifest({"acme/base": _dep(base, "^1.0.0")}))},
        tags=["v1.0.0"],
    )
    jqpm("init")
    jqpm("add", f"{mid}@^1.0.0")
    lock_before = read_json(jqpm.project, LOCKFILE)

    base.tag("v1.1.0")
    jqpm("install")
    lock_after = read_json(jqpm.project, LOCKFILE)
    assert lock_before["acme/base"]["commit"] == lock_after["acme/base"]["commit"]

    jqpm("install", "--update")
    lock_updated = read_json(jqpm.project, LOCKFILE)
    assert lock_updated["acme/base"]["resolved"] == "v1.1.0"


def test_cycle_detection(jqpm, make_repo):
    b = make_repo(
        "acme", "b",
        jq_body='import "acme/a" as A;\n',
        tags=["v1.0.0"],
    )
    a = make_repo(
        "acme", "a",
        jq_body='import "acme/b" as B;\n',
        extra_files={MANIFEST: json.dumps(_with_manifest({"acme/b": _dep(b, "^1.0.0")}))},
        tags=["v1.0.0"],
    )
    # make b depend on a too, closing the cycle
    (b.dir / MANIFEST).write_text(json.dumps(_with_manifest({"acme/a": _dep(a, "^1.0.0")})))
    _git(["add", "-A"], cwd=b.dir)
    _git(["commit", "--quiet", "-m", "add cycle"], cwd=b.dir)
    _git(["tag", "-d", "v1.0.0"], cwd=b.dir)
    _git(["tag", "v1.0.0"], cwd=b.dir)

    jqpm("init")
    jqpm("add", f"{a}@^1.0.0", "--no-install")
    result = jqpm("install", check=False)
    assert result.returncode != 0
    assert "dependency cycle detected" in result.stderr
