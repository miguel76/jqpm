"""End-to-end tests: drive the real `jqpm` CLI as a subprocess against local
git fixture repos (via file:// URLs). No GitHub account or network needed.
"""

import json

MANIFEST = "jqpackage.json"
LOCKFILE = "jqpackage-lock.json"


def read_json(project, name):
    return json.loads((project / name).read_text())


def test_init_creates_manifest(jqpm):
    jqpm("init", "--name", "myproj")
    manifest = read_json(jqpm.project, MANIFEST)
    assert manifest["name"] == "myproj"
    assert manifest["dependencies"] == {}


def test_init_refuses_to_overwrite_without_force(jqpm):
    jqpm("init")
    result = jqpm("install", check=False)  # sanity: no deps yet is fine
    assert result.returncode == 0
    result = jqpm("init", check=False)
    assert result.returncode != 0
    assert "already exists" in result.stderr


def test_add_and_install_fetches_package(jqpm, make_repo):
    url = make_repo("acme", "strhelp", jq_body='def shout: . + "!";\n',
                     tags=["v1.0.0", "v1.1.0"])
    jqpm("init")
    jqpm("add", f"{url}@^1.0.0", "--no-install")
    jqpm("install")

    entry = jqpm.project / "jq_modules" / "acme" / "strhelp" / "strhelp.jq"
    assert entry.exists()
    assert "shout" in entry.read_text()

    # .git should be stripped from the installed tree
    assert not (entry.parent / ".git").exists()

    lock = read_json(jqpm.project, LOCKFILE)
    key = [k for k in lock if k.startswith("acme/")][0]
    assert lock[key]["resolved"] == "v1.1.0"
    assert lock[key]["spec"] == "^1.0.0"
    assert len(lock[key]["commit"]) == 40


def test_add_with_install_flag_installs_immediately(jqpm, make_repo):
    url = make_repo("acme", "strhelp", tags=["v1.0.0"])
    jqpm("init")
    jqpm("add", f"{url}@1.0.0")  # --install is the default
    entry = jqpm.project / "jq_modules" / "acme" / "strhelp" / "strhelp.jq"
    assert entry.exists()


def test_install_is_reproducible_via_lockfile(jqpm, make_repo):
    repo = make_repo("acme", "strhelp", tags=["v1.0.0"])
    jqpm("init")
    jqpm("add", f"{repo}@^1.0.0")
    lock_before = read_json(jqpm.project, LOCKFILE)

    # a new matching tag appears upstream after the lockfile was written
    repo.tag("v1.1.0")
    result = jqpm("install")
    lock_after = read_json(jqpm.project, LOCKFILE)
    assert lock_before == lock_after, "plain install must stay pinned to the lockfile"
    assert "locked @" in result.stdout


def test_install_update_reresolves_to_new_tag(jqpm, make_repo):
    repo = make_repo("acme", "strhelp", tags=["v1.0.0"])
    jqpm("init")
    jqpm("add", f"{repo}@^1.0.0")
    lock_before = read_json(jqpm.project, LOCKFILE)
    assert lock_before["acme/strhelp"]["resolved"] == "v1.0.0"

    repo.tag("v1.1.0", jq_body='def shout: . + "!!";\n')

    result = jqpm("install", check=False)
    lock_still_locked = read_json(jqpm.project, LOCKFILE)
    assert lock_still_locked["acme/strhelp"]["resolved"] == "v1.0.0", (
        "plain install must stay pinned to the lockfile"
    )

    jqpm("install", "--update")
    lock_after = read_json(jqpm.project, LOCKFILE)
    assert lock_after["acme/strhelp"]["resolved"] == "v1.1.0"
    entry = jqpm.project / "jq_modules" / "acme" / "strhelp" / "strhelp.jq"
    assert "!!" in entry.read_text()


def test_remove_deletes_files_and_manifest_entries(jqpm, make_repo):
    repo = make_repo("acme", "strhelp", tags=["v1.0.0"])
    jqpm("init")
    jqpm("add", f"{repo}@^1.0.0")
    assert (jqpm.project / "jq_modules" / "acme" / "strhelp").exists()

    jqpm("remove", "acme/strhelp")

    assert not (jqpm.project / "jq_modules" / "acme" / "strhelp").exists()
    manifest = read_json(jqpm.project, MANIFEST)
    lock = read_json(jqpm.project, LOCKFILE)
    assert "acme/strhelp" not in manifest["dependencies"]
    assert "acme/strhelp" not in lock


def test_list_reports_installed_packages(jqpm, make_repo):
    repo = make_repo("acme", "strhelp", tags=["v1.0.0"])
    jqpm("init")
    jqpm("add", f"{repo}@^1.0.0")
    result = jqpm("list")
    assert "acme/strhelp" in result.stdout
    assert "v1.0.0" in result.stdout


def test_list_with_nothing_installed(jqpm):
    jqpm("init")
    result = jqpm("list")
    assert "no packages installed" in result.stdout


def test_install_with_no_dependencies(jqpm):
    jqpm("init")
    result = jqpm("install")
    assert "no dependencies" in result.stdout


def test_warns_when_entry_file_missing(jqpm, make_repo):
    repo = make_repo("acme", "broken", entry=False, tags=["v1.0.0"],
                      extra_files={"README.md": "no entry point here\n"})
    jqpm("init")
    result = jqpm("add", f"{repo}@^1.0.0")
    assert "warning" in result.stderr
    assert "broken.jq" in result.stderr


def test_run_executes_jq_with_modules_path(jqpm, make_repo):
    repo = make_repo("acme", "strhelp", jq_body='def shout: . + "!";\n',
                      tags=["v1.0.0"])
    jqpm("init")
    jqpm("add", f"{repo}@^1.0.0")
    result = jqpm(
        "run", "--", "-n", '-r',
        'import "acme/strhelp" as S; "hi" | S::shout',
    )
    assert result.stdout.strip() == "hi!"


def test_multi_file_package_with_transitive_relative_imports(jqpm, make_repo):
    """A package may be split across several .jq files, importing each other
    by relative path (directly or indirectly) -- jqpm must make those
    resolve correctly once installed under jq_modules/, even though jq's own
    './' resolution is cwd-relative and the consumer's cwd is the *project*
    root, not the package's own directory."""
    repo = make_repo(
        "acme", "strhelp",
        jq_body='import "./helper" as H;\nimport "./sub/deep" as D;\n'
                'def shout: H::greet + "-" + D::deepgreet;\n',
        extra_files={
            "helper.jq": 'import "./sub/deep" as D;\ndef greet: D::deepgreet;\n',
            "sub/deep.jq": 'def deepgreet: "deep";\n',
        },
        tags=["v1.0.0"],
    )
    jqpm("init")
    jqpm("add", f"{repo}@^1.0.0")

    entry = jqpm.project / "jq_modules" / "acme" / "strhelp" / "strhelp.jq"
    assert 'import "acme/strhelp/helper" as H;' in entry.read_text()
    assert 'import "acme/strhelp/sub/deep" as D;' in entry.read_text()

    result = jqpm(
        "run", "--", "-n", "-r",
        'import "acme/strhelp" as S; S::shout',
    )
    assert result.stdout.strip() == "deep-deep"


def test_explicit_url_dependency_form_in_manifest(jqpm, make_repo):
    """jqpackage.json may spell a dependency as {"url": ..., "version": ...}
    instead of the owner/repo shorthand; jqpm must honor that form too."""
    repo = make_repo("acme", "strhelp", tags=["v2.0.0"])
    jqpm("init")
    manifest = read_json(jqpm.project, MANIFEST)
    manifest["dependencies"]["acme/strhelp"] = {"url": repo.url, "version": "^2.0.0"}
    (jqpm.project / MANIFEST).write_text(json.dumps(manifest))

    jqpm("install")
    entry = jqpm.project / "jq_modules" / "acme" / "strhelp" / "strhelp.jq"
    assert entry.exists()
    lock = read_json(jqpm.project, LOCKFILE)
    assert lock["acme/strhelp"]["resolved"] == "v2.0.0"

