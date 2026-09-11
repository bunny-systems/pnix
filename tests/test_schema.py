import pytest

from pnix import schema


def test_accepts_a_valid_github_pin():
    schema.validate(
        {"foo": {"type": "github", "owner": "o", "repo": "r", "ref": "main"}},
        {"foo": "/decl.nix"},
    )


def test_type_defaults_to_github():
    schema.validate({"foo": {"owner": "o", "repo": "r"}}, {"foo": "/decl.nix"})


def test_rejects_unknown_field_naming_the_file():
    with pytest.raises(schema.SchemaError) as e:
        schema.validate({"foo": {"type": "github", "onwer": "o", "repo": "r"}},
                        {"foo": "/decl.nix"})
    msg = str(e.value)
    assert "onwer" in msg and "/decl.nix" in msg


def test_rejects_wrong_type_naming_the_file():
    with pytest.raises(schema.SchemaError) as e:
        schema.validate({"foo": {"type": "github", "owner": 3, "repo": "r"}},
                        {"foo": "/decl.nix"})
    assert "owner" in str(e.value) and "/decl.nix" in str(e.value)


def test_github_requires_owner_and_repo():
    with pytest.raises(schema.SchemaError) as e:
        schema.validate({"foo": {"type": "github", "owner": "o"}},
                        {"foo": "/decl.nix"})
    assert "repo" in str(e.value)


def test_git_requires_url():
    with pytest.raises(schema.SchemaError) as e:
        schema.validate({"foo": {"type": "git"}}, {"foo": "/decl.nix"})
    assert "url" in str(e.value)


def test_reports_every_problem_not_just_the_first():
    with pytest.raises(schema.SchemaError) as e:
        schema.validate(
            {"a": {"type": "github", "bogus": 1},
             "b": {"type": "git"}},
            {"a": "/a.nix", "b": "/b.nix"},
        )
    msg = str(e.value)
    assert "/a.nix" in msg and "/b.nix" in msg


def test_bool_is_not_accepted_where_a_string_is_wanted():
    """isinstance(True, int) is True in Python; str fields must not slip."""
    with pytest.raises(schema.SchemaError):
        schema.validate({"foo": {"type": "github", "owner": "o", "repo": "r",
                                 "ref": True}},
                        {"foo": "/decl.nix"})


def test_a_valid_patch_list_is_accepted():
    schema.validate(
        {"foo": {"type": "github", "owner": "o", "repo": "r",
                 "patches": [{"pr": 181}, {"commit": "abc"},
                             {"url": "https://e/x.diff"}, "./local.patch"]}},
        {"foo": "/decl.nix"},
    )


def test_every_unimplemented_field_is_a_known_field():
    """If a field is dropped from FIELDS the rejection must go with it."""
    assert set(schema.NOT_IMPLEMENTED) <= set(schema.FIELDS)


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({}, "needs exactly one"),
        ({"pr": 1, "url": "u"}, "needs exactly one"),
        ({"pr": "181"}, "'pr' must be a number"),
        ({"url": 3}, "'url' must be a string"),
        (7, "must be an attrset or a path"),
    ],
)
def test_a_malformed_patch_is_rejected_naming_its_index(entry, expected):
    with pytest.raises(schema.SchemaError) as e:
        schema.validate(
            {"foo": {"type": "github", "owner": "o", "repo": "r",
                     "patches": [entry]}},
            {"foo": "/decl.nix"},
        )
    msg = str(e.value)
    assert expected in msg and "patch [0]" in msg and "/decl.nix" in msg


def test_importable_without_patches_is_pointless_and_says_so():
    with pytest.raises(schema.SchemaError) as e:
        schema.validate(
            {"foo": {"type": "github", "owner": "o", "repo": "r",
                     "patches": [], "importable": True}},
            {"foo": "/decl.nix"},
        )
    assert "no patches" in str(e.value)


@pytest.mark.parametrize(
    ("spec", "missing"),
    [
        ({"type": "gitlab", "owner": "o"}, "repo"),
        ({"type": "sourcehut", "repo": "r"}, "owner"),
        ({"type": "tarball"}, "url"),
        ({"type": "file"}, "url"),
        ({"type": "channel"}, "channel"),
        ({"type": "path"}, "path"),
    ],
)
def test_each_new_source_type_states_what_it_needs(spec, missing):
    with pytest.raises(schema.SchemaError) as e:
        schema.validate({"foo": spec}, {"foo": "/decl.nix"})
    assert missing in str(e.value)


def test_a_pr_patch_on_a_forge_without_a_pr_api_is_refused():
    """GitLab's merge-request endpoints were never probed. Refusing beats
    guessing a shape, which is how the design got Forgejo wrong."""
    with pytest.raises(schema.SchemaError) as e:
        schema.validate(
            {"foo": {"type": "gitlab", "owner": "o", "repo": "r",
                     "patches": [{"pr": 5}]}},
            {"foo": "/decl.nix"},
        )
    assert "no pull-request API" in str(e.value)


def test_a_commit_patch_is_fine_on_those_types():
    schema.validate(
        {"foo": {"type": "gitlab", "owner": "o", "repo": "r",
                 "patches": [{"url": "https://e/x.diff"}]}},
        {"foo": "/decl.nix"},
    )


def test_a_git_pin_may_track_a_pr_when_the_patch_names_its_forge():
    """Refused without a forge, accepted with one -- that is how a pin with no
    owner/repo, or one mirrored from elsewhere, tracks an upstream PR."""
    decl = {"foo": {"type": "git", "url": "https://forgejo.example/o/r.git",
                    "patches": [{"pr": 42, "owner": "up", "repo": "r",
                                 "forge": "github"}]}}
    schema.validate(decl, {"foo": "/decl.nix"})

    decl["foo"]["patches"] = [{"pr": 42}]
    with pytest.raises(schema.SchemaError):
        schema.validate(decl, {"foo": "/decl.nix"})
