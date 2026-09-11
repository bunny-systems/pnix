import pytest

from pnix import schema


def test_accepts_a_valid_github_pin():
    schema.validate(
        {"foo": {"type": "github", "url": "https://github.com/o/r", "ref": "main"}},
        {"foo": "/decl.nix"},
    )


def test_type_is_inferred_from_a_known_host():
    """A declaration should say where the thing comes from and nothing else.
    github.com, codeberg.org, gitlab.com and git.sr.ht are known, so `type` is
    only needed for a self-hosted instance."""
    for url, want in [("https://github.com/o/r", "github"),
                      ("https://codeberg.org/o/r", "forgejo"),
                      ("https://gitlab.com/o/r", "gitlab"),
                      ("https://git.sr.ht/~o/r", "sourcehut")]:
        schema.validate({"foo": {"url": url}}, {"foo": "/decl.nix"})
        assert schema.type_of({"url": url}) == want


def test_an_unknown_host_must_state_its_type():
    """Guessing that a self-hosted domain runs Forgejo is how you get a 404 at
    lock time, and a confusing one."""
    with pytest.raises(schema.SchemaError) as e:
        schema.validate({"foo": {"url": "https://forgejo.example.com/o/r"}},
                        {"foo": "/decl.nix"})
    assert "not one pnix knows" in str(e.value)
    schema.validate({"foo": {"type": "forgejo",
                             "url": "https://forgejo.example.com/o/r"}},
                    {"foo": "/decl.nix"})


def test_rejects_unknown_field_naming_the_file():
    with pytest.raises(schema.SchemaError) as e:
        schema.validate({"foo": {"type": "github", "onwer": "o", "url": "https://github.com/o/r"}},
                        {"foo": "/decl.nix"})
    msg = str(e.value)
    assert "onwer" in msg and "/decl.nix" in msg


def test_rejects_wrong_type_naming_the_file():
    with pytest.raises(schema.SchemaError) as e:
        schema.validate({"foo": {"type": "github", "url": 3}},
                        {"foo": "/decl.nix"})
    assert "url" in str(e.value) and "/decl.nix" in str(e.value)


def test_owner_and_repo_are_no_longer_declaration_fields():
    """They said the same thing as the URL, in a second place that could
    disagree with it. They remain in the lock, derived, as provenance."""
    with pytest.raises(schema.SchemaError) as e:
        schema.validate({"foo": {"type": "github", "owner": "o", "repo": "r"}},
                        {"foo": "/decl.nix"})
    msg = str(e.value)
    assert "unknown field 'owner'" in msg and "unknown field 'repo'" in msg


def test_a_url_that_is_not_a_repository_is_refused():
    with pytest.raises(schema.SchemaError) as e:
        schema.validate({"foo": {"type": "github", "url": "https://github.com"}},
                        {"foo": "/decl.nix"})
    assert "owner/repo" in str(e.value)


def test_git_only_options_are_refused_on_an_archive():
    """A forge archive carries no submodules. Fetching one anyway would
    evaluate fine and be quietly missing them, so it is an error with the fix
    in it."""
    with pytest.raises(schema.SchemaError) as e:
        schema.validate(
            {"foo": {"url": "https://github.com/o/r", "submodules": True}},
            {"foo": "/decl.nix"})
    msg = str(e.value)
    assert "submodules" in msg and 'type = "git"' in msg
    schema.validate({"foo": {"type": "git", "url": "https://github.com/o/r",
                             "submodules": True}}, {"foo": "/decl.nix"})


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
        schema.validate({"foo": {"type": "github", "url": "https://github.com/o/r",
                                 "ref": True}},
                        {"foo": "/decl.nix"})


def test_a_valid_patch_list_is_accepted():
    schema.validate(
        {"foo": {"type": "github", "url": "https://github.com/o/r",
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
            {"foo": {"type": "github", "url": "https://github.com/o/r",
                     "patches": [entry]}},
            {"foo": "/decl.nix"},
        )
    msg = str(e.value)
    assert expected in msg and "patch [0]" in msg and "/decl.nix" in msg


def test_importable_without_patches_is_pointless_and_says_so():
    with pytest.raises(schema.SchemaError) as e:
        schema.validate(
            {"foo": {"type": "github", "url": "https://github.com/o/r",
                     "patches": [], "importable": True}},
            {"foo": "/decl.nix"},
        )
    assert "no patches" in str(e.value)


@pytest.mark.parametrize(
    ("spec", "missing"),
    [
        ({"type": "gitlab"}, "url"),
        ({"type": "sourcehut"}, "url"),
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
            {"foo": {"type": "gitlab", "url": "https://github.com/o/r",
                     "patches": [{"pr": 5}]}},
            {"foo": "/decl.nix"},
        )
    assert "no pull-request API" in str(e.value)


def test_a_commit_patch_is_fine_on_those_types():
    schema.validate(
        {"foo": {"type": "gitlab", "url": "https://github.com/o/r",
                 "patches": [{"url": "https://e/x.diff"}]}},
        {"foo": "/decl.nix"},
    )


def test_a_git_pin_may_track_a_pr_when_the_patch_names_its_forge():
    """Refused without a forge, accepted with one -- that is how a pin with no
    owner/repo, or one mirrored from elsewhere, tracks an upstream PR."""
    decl = {"foo": {"type": "git", "url": "https://forgejo.example/o/r.git",
                    "patches": [{"pr": 42,
                                 "repo": "https://github.com/up/r"}]}}
    schema.validate(decl, {"foo": "/decl.nix"})

    decl["foo"]["patches"] = [{"pr": 42}]
    with pytest.raises(schema.SchemaError):
        schema.validate(decl, {"foo": "/decl.nix"})
