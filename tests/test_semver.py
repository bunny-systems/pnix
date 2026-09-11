import pytest

from pnix import semver

TAGS = ["v1.2.3", "v1.9.0", "v1.10.0", "v2.0.0", "v1.3.0-rc1",
        "v0.2.9", "v0.3.0", "nixos-24.05", "latest", "v1"]


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("^1.2", "v1.10.0"),     # not v1.9.0: components, not strings
        ("~1.9", "v1.9.0"),      # tilde does not cross a minor
        ("^0.2", "v0.2.9"),      # caret on 0.x does not cross a minor either
        ("*", "v2.0.0"),
        (">=1.10", "v2.0.0"),
        ("<2.0.0", "v1.10.0"),
        ("v1.2.3", "v1.2.3"),    # bare version is exact
        ("^9", None),
    ],
)
def test_newest(spec, expected):
    assert semver.newest(TAGS, spec) == expected


def test_a_range_never_selects_a_prerelease():
    """`^1.2` must not jump onto a release candidate."""
    assert semver.newest(["v1.2.0", "v1.3.0-rc1"], "^1.2") == "v1.2.0"
    assert semver.newest(["v1.3.0-rc1"], "^1.2") is None


def test_a_range_that_names_a_prerelease_can_match_one():
    assert semver.newest(["v1.3.0-rc1"], ">=1.3.0-rc0") == "v1.3.0-rc1"


def test_a_prerelease_sorts_below_its_release():
    assert semver.parse("1.2.3-rc1").key < semver.parse("1.2.3").key


def test_numeric_prerelease_parts_compare_numerically():
    assert semver.parse("1.0.0-rc.2").key < semver.parse("1.0.0-rc.10").key


def test_unparseable_tags_are_ignored_not_fatal():
    """A tag list is a mixed bag; a range query must read past what it cannot
    parse rather than fail on it."""
    assert semver.parse("nixos-24.05") is None
    assert semver.parse("debian/1.2-3") is None
    assert semver.newest(["nixos-24.05", "v1.0.0"], "*") == "v1.0.0"


def test_partial_versions_parse():
    assert semver.parse("v1").key[:3] == (1, 0, 0)
    assert semver.parse("1.2").key[:3] == (1, 2, 0)
