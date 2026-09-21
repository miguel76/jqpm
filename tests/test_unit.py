"""Unit tests for pure logic in jqpm.py — no git, no network."""

import pytest

import jqpm


class TestSemverTuple:
    def test_parses_plain(self):
        assert jqpm.semver_tuple("1.2.3") == (1, 2, 3)

    def test_parses_v_prefix(self):
        assert jqpm.semver_tuple("v1.2.3") == (1, 2, 3)

    def test_ignores_suffix(self):
        assert jqpm.semver_tuple("1.2.3-rc1") == (1, 2, 3)

    def test_rejects_non_semver(self):
        assert jqpm.semver_tuple("not-a-version") is None
        assert jqpm.semver_tuple("main") is None


class TestParseDepKey:
    def test_shorthand_defaults_to_github(self):
        owner, repo, url = jqpm.parse_dep_key("someuser/jq-strings")
        assert (owner, repo, url) == (
            "someuser", "jq-strings", "https://github.com/someuser/jq-strings.git",
        )

    def test_https_url(self):
        owner, repo, url = jqpm.parse_dep_key("https://example.com/owner/repo.git")
        assert (owner, repo, url) == (
            "owner", "repo", "https://example.com/owner/repo.git",
        )

    def test_https_url_without_dot_git(self):
        owner, repo, url = jqpm.parse_dep_key("https://example.com/owner/repo")
        assert (owner, repo) == ("owner", "repo")

    def test_ssh_url(self):
        owner, repo, url = jqpm.parse_dep_key("git@example.com:owner/repo.git")
        assert (owner, repo, url) == (
            "owner", "repo", "git@example.com:owner/repo.git",
        )

    def test_missing_slash_dies(self):
        with pytest.raises(SystemExit):
            jqpm.parse_dep_key("just-a-name")

    def test_url_without_enough_segments_dies(self):
        with pytest.raises(SystemExit):
            jqpm.parse_dep_key("https://onlyhost.example.com")


class TestParseSpec:
    @pytest.mark.parametrize("spec", ["", "*", "latest", None])
    def test_latest(self, spec):
        assert jqpm.parse_spec(spec) == ("latest", None)

    def test_ref(self):
        assert jqpm.parse_spec("#main") == ("ref", "main")

    def test_caret(self):
        assert jqpm.parse_spec("^1.2.3") == ("caret", (1, 2, 3))

    def test_tilde(self):
        assert jqpm.parse_spec("~1.2.3") == ("tilde", (1, 2, 3))

    def test_exact(self):
        assert jqpm.parse_spec("1.2.3") == ("exact", "1.2.3")

    def test_bare_word_treated_as_ref(self):
        assert jqpm.parse_spec("some-branch") == ("ref", "some-branch")

    def test_bad_caret_dies(self):
        with pytest.raises(SystemExit):
            jqpm.parse_spec("^not-a-version")

    def test_bad_tilde_dies(self):
        with pytest.raises(SystemExit):
            jqpm.parse_spec("~nope")


class TestRewriteLocalImports:
    def test_rewrites_direct_and_transitive_relative_imports(self, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "strhelp.jq").write_text(
            'import "./helper" as H;\n'
            'import "./sub/deep" as D;\n'
        )
        (tmp_path / "helper.jq").write_text(
            'import "./sub/deep" as D;\n'  # indirect: sibling reaches into a subdir
        )
        (tmp_path / "sub" / "deep.jq").write_text('def deepgreet: "deep";\n')

        jqpm.rewrite_local_imports(tmp_path, "acme/strhelp")

        assert (tmp_path / "strhelp.jq").read_text() == (
            'import "acme/strhelp/helper" as H;\n'
            'import "acme/strhelp/sub/deep" as D;\n'
        )
        assert (tmp_path / "helper.jq").read_text() == (
            'import "acme/strhelp/sub/deep" as D;\n'
        )

    def test_leaves_package_mediated_imports_alone(self, tmp_path):
        (tmp_path / "strhelp.jq").write_text('import "someother/pkg" as O;\n')
        jqpm.rewrite_local_imports(tmp_path, "acme/strhelp")
        assert (tmp_path / "strhelp.jq").read_text() == 'import "someother/pkg" as O;\n'

    def test_rewrites_include_too(self, tmp_path):
        (tmp_path / "strhelp.jq").write_text('include "./helper";\n')
        (tmp_path / "helper.jq").write_text('def greet: "hi";\n')
        jqpm.rewrite_local_imports(tmp_path, "acme/strhelp")
        assert (tmp_path / "strhelp.jq").read_text() == 'include "acme/strhelp/helper";\n'

    def test_parent_relative_import_from_subdir_stays_in_package(self, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "helper.jq").write_text('def greet: "hi";\n')
        (tmp_path / "sub" / "deep.jq").write_text('import "../helper" as H;\n')
        jqpm.rewrite_local_imports(tmp_path, "acme/strhelp")
        assert (tmp_path / "sub" / "deep.jq").read_text() == (
            'import "acme/strhelp/helper" as H;\n'
        )

    def test_import_escaping_package_root_dies(self, tmp_path):
        (tmp_path / "strhelp.jq").write_text('import "../outside" as O;\n')
        with pytest.raises(SystemExit):
            jqpm.rewrite_local_imports(tmp_path, "acme/strhelp")


class TestSplitPkgAndSpec:
    def test_bare_shorthand(self):
        assert jqpm.split_pkg_and_spec("owner/repo") == ("owner/repo", "*")

    def test_shorthand_with_spec(self):
        assert jqpm.split_pkg_and_spec("owner/repo@^1.0.0") == ("owner/repo", "^1.0.0")

    def test_https_url_with_spec(self):
        assert jqpm.split_pkg_and_spec("https://host/o/r.git@^1.0.0") == (
            "https://host/o/r.git", "^1.0.0",
        )

    def test_https_url_without_spec(self):
        assert jqpm.split_pkg_and_spec("https://host/o/r.git") == (
            "https://host/o/r.git", "*",
        )

    def test_ssh_url_without_spec_is_not_mistaken_for_version(self):
        # the '@' here is part of `git@host:...`, not a version separator
        assert jqpm.split_pkg_and_spec("git@host:owner/repo.git") == (
            "git@host:owner/repo.git", "*",
        )

    def test_ssh_url_with_spec(self):
        assert jqpm.split_pkg_and_spec("git@host:owner/repo.git@1.0.0") == (
            "git@host:owner/repo.git", "1.0.0",
        )
