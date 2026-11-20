"""End-to-end tests for package commands: the `bin` field of a package's
jqpackage.json, linked into jq_modules/.bin/ and run with `jqpm exec`."""

import json
import os

MANIFEST = "jqpackage.json"

HELLO = '#!/bin/sh\necho "hello $*"\n'
SHOW_ENV = '#!/bin/sh\nprintf \'%s\\n\' "$JQ_LIBRARY_PATH" "$PATH"\n'


def _package_manifest(**fields):
    return json.dumps({"name": "x", "version": "1.0.0", **fields})


def _dep(fixture_repo, spec="^1.0.0"):
    return {"url": fixture_repo.url, "version": spec}


def _bin_dir(jqpm):
    return jqpm.project / "jq_modules" / ".bin"


def test_bin_object_is_linked_and_runnable(jqpm, make_repo):
    tool = make_repo(
        "acme", "tool",
        extra_files={
            "bin/hello": HELLO,
            MANIFEST: _package_manifest(bin={"hello": "bin/hello"}),
        },
        executable_files=["bin/hello"],
        tags=["v1.0.0"],
    )
    jqpm("init")
    result = jqpm("add", f"{tool}@^1.0.0")
    assert "linked command(s) in jq_modules/.bin/: hello" in result.stdout

    link = _bin_dir(jqpm) / "hello"
    assert link.is_symlink()
    # relative, so that the project directory can be moved
    assert os.readlink(link) == "../acme/tool/bin/hello"
    assert jqpm("exec", "hello", "--", "world").stdout == "hello world\n"


def test_bin_string_is_named_after_the_repo(jqpm, make_repo):
    tool = make_repo(
        "acme", "tool",
        extra_files={"run.sh": HELLO, MANIFEST: _package_manifest(bin="run.sh")},
        executable_files=["run.sh"],
        tags=["v1.0.0"],
    )
    jqpm("init")
    jqpm("add", f"{tool}@^1.0.0")
    assert (_bin_dir(jqpm) / "tool").is_symlink()
    assert jqpm("exec", "tool").stdout == "hello \n"


def test_bin_target_is_made_executable(jqpm, make_repo):
    tool = make_repo(
        "acme", "tool",
        extra_files={"hello": HELLO, MANIFEST: _package_manifest(bin={"hello": "hello"})},
        tags=["v1.0.0"],
    )
    jqpm("init")
    jqpm("add", f"{tool}@^1.0.0")
    target = jqpm.project / "jq_modules" / "acme" / "tool" / "hello"
    assert os.access(target, os.X_OK)
    assert jqpm("exec", "hello").stdout == "hello \n"


def test_exec_sets_path_and_library_path(jqpm, make_repo):
    tool = make_repo(
        "acme", "tool",
        extra_files={"env.sh": SHOW_ENV, MANIFEST: _package_manifest(bin={"show-env": "env.sh"})},
        executable_files=["env.sh"],
        tags=["v1.0.0"],
    )
    jqpm("init")
    jqpm("add", f"{tool}@^1.0.0")
    library_path, path = jqpm("exec", "show-env").stdout.splitlines()
    modules = (jqpm.project / "jq_modules").resolve()
    assert library_path.split(os.pathsep)[0] == str(modules)
    assert path.split(os.pathsep)[0] == str(modules / ".bin")


def test_exec_unknown_command_fails(jqpm, make_repo):
    jqpm("init")
    result = jqpm("exec", "nope", check=False)
    assert result.returncode != 0
    assert "no command 'nope'" in result.stderr


def test_direct_dependency_command_wins_over_transitive(jqpm, make_repo):
    base = make_repo(
        "acme", "base",
        extra_files={"tool": '#!/bin/sh\necho base\n', MANIFEST: _package_manifest(bin={"tool": "tool"})},
        executable_files=["tool"],
        tags=["v1.0.0"],
    )
    mid = make_repo(
        "acme", "mid",
        extra_files={MANIFEST: _package_manifest(dependencies={"acme/base": _dep(base)})},
        tags=["v1.0.0"],
    )
    top = make_repo(
        "acme", "top",
        extra_files={"tool": '#!/bin/sh\necho top\n', MANIFEST: _package_manifest(bin={"tool": "tool"})},
        executable_files=["tool"],
        tags=["v1.0.0"],
    )
    jqpm("init")
    jqpm("add", f"{mid}@^1.0.0", "--no-install")
    jqpm("add", f"{top}@^1.0.0", "--no-install")
    result = jqpm("install")
    assert 'command "tool" is provided by both acme/top and acme/base' in result.stderr
    assert jqpm("exec", "tool").stdout == "top\n"


def test_transitive_dependency_commands_are_linked(jqpm, make_repo):
    base = make_repo(
        "acme", "base",
        extra_files={"hello": HELLO, MANIFEST: _package_manifest(bin={"hello": "hello"})},
        executable_files=["hello"],
        tags=["v1.0.0"],
    )
    mid = make_repo(
        "acme", "mid",
        extra_files={MANIFEST: _package_manifest(dependencies={"acme/base": _dep(base)})},
        tags=["v1.0.0"],
    )
    jqpm("init")
    jqpm("add", f"{mid}@^1.0.0")
    assert jqpm("exec", "hello").stdout == "hello \n"


def test_invalid_bins_are_ignored_with_a_warning(jqpm, make_repo):
    tool = make_repo(
        "acme", "tool",
        extra_files={
            "ok": HELLO,
            MANIFEST: _package_manifest(bin={
                "ok": "ok",
                "outside": "../../../etc/passwd",
                "missing": "nothere",
                "../bad-name": "ok",
            }),
        },
        executable_files=["ok"],
        tags=["v1.0.0"],
    )
    jqpm("init")
    result = jqpm("add", f"{tool}@^1.0.0")
    assert 'command "outside": ../../../etc/passwd is outside the package' in result.stderr
    assert 'command "missing": nothere not found in the package' in result.stderr
    assert 'invalid name "../bad-name"' in result.stderr
    assert sorted(os.listdir(_bin_dir(jqpm))) == ["ok"]


def test_remove_unlinks_commands(jqpm, make_repo):
    tool = make_repo(
        "acme", "tool",
        extra_files={"hello": HELLO, MANIFEST: _package_manifest(bin={"hello": "hello"})},
        executable_files=["hello"],
        tags=["v1.0.0"],
    )
    jqpm("init")
    jqpm("add", f"{tool}@^1.0.0")
    assert (_bin_dir(jqpm) / "hello").is_symlink()
    jqpm("remove", "acme/tool")
    assert not (_bin_dir(jqpm) / "hello").exists()


def test_reinstall_keeps_commands(jqpm, make_repo):
    tool = make_repo(
        "acme", "tool",
        extra_files={"hello": HELLO, MANIFEST: _package_manifest(bin={"hello": "hello"})},
        executable_files=["hello"],
        tags=["v1.0.0"],
    )
    jqpm("init")
    jqpm("add", f"{tool}@^1.0.0")
    jqpm("install")
    jqpm("install", "--update")
    assert jqpm("exec", "hello").stdout == "hello \n"


def test_main_null_skips_missing_entry_warning(jqpm, make_repo):
    tool = make_repo(
        "acme", "tool",
        entry=False,
        extra_files={"hello": HELLO, MANIFEST: _package_manifest(main=None, bin={"hello": "hello"})},
        executable_files=["hello"],
        tags=["v1.0.0"],
    )
    jqpm("init")
    result = jqpm("add", f"{tool}@^1.0.0")
    assert "expected entry file" not in result.stderr


def test_missing_entry_still_warns_without_main_null(jqpm, make_repo):
    tool = make_repo(
        "acme", "tool",
        entry=False,
        extra_files={"hello": HELLO, MANIFEST: _package_manifest(bin={"hello": "hello"})},
        executable_files=["hello"],
        tags=["v1.0.0"],
    )
    jqpm("init")
    result = jqpm("add", f"{tool}@^1.0.0")
    assert "expected entry file" in result.stderr
    assert '"main": null' in result.stderr
