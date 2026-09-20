"""resolve_spec() / list_remote_tags() against real local git repos.

No GitHub, no network: `make_repo` builds an actual git repo on disk and we
point resolve_spec at it via a file:// URL, exercising the real
`git ls-remote` codepath.
"""

import jqpm


def test_latest_picks_highest_semver_tag(make_repo):
    url = make_repo("acme", "strhelp", tags=["v1.0.0", "v1.2.0", "v1.1.0"])
    assert jqpm.resolve_spec(url.url, "*") == "v1.2.0"


def test_latest_falls_back_to_head_with_no_tags(make_repo):
    url = make_repo("acme", "notags")
    assert jqpm.resolve_spec(url.url, "*") == "HEAD"


def test_exact_match(make_repo):
    url = make_repo("acme", "strhelp", tags=["v1.0.0", "v1.1.0"])
    assert jqpm.resolve_spec(url.url, "1.0.0") == "v1.0.0"


def test_exact_no_match_dies(make_repo):
    url = make_repo("acme", "strhelp", tags=["v1.0.0"])
    try:
        jqpm.resolve_spec(url.url, "9.9.9")
        assert False, "expected SystemExit"
    except SystemExit:
        pass


def test_caret_respects_major(make_repo):
    url = make_repo("acme", "strhelp", tags=["v1.0.0", "v1.5.0", "v2.0.0"])
    assert jqpm.resolve_spec(url.url, "^1.0.0") == "v1.5.0"


def test_caret_no_match_dies(make_repo):
    url = make_repo("acme", "strhelp", tags=["v1.0.0", "v1.5.0"])
    try:
        jqpm.resolve_spec(url.url, "^1.6.0")
        assert False, "expected SystemExit"
    except SystemExit:
        pass


def test_tilde_respects_minor(make_repo):
    url = make_repo("acme", "strhelp", tags=["v1.2.0", "v1.2.5", "v1.3.0"])
    assert jqpm.resolve_spec(url.url, "~1.2.0") == "v1.2.5"


def test_ref_bypasses_tag_resolution_entirely(make_repo):
    # a ref spec shouldn't even need to reach the network/remote
    assert jqpm.resolve_spec("not-a-real-url", "#some-branch") == "some-branch"


def test_unprefixed_tags_are_matched_too(make_repo):
    url = make_repo("acme", "strhelp", tags=["1.0.0", "1.1.0"])
    assert jqpm.resolve_spec(url.url, "^1.0.0") == "1.1.0"

