"""Patch resolution. No network: the forge is faked and diffs are local files."""


import pytest

from pnix import forges, patches
from pnix.forges.base import ForgeError, Pull


class FakeForge:
    name = "github"
    immutable_diffs = True

    def __init__(self, pull=None, base=None, fail=False):
        self._pull = pull or Pull(head="h" * 40, base="b" * 40,
                                  state="open", merged=False)
        self._base = base
        self._fail = fail
        self.calls = 0

    def pull(self, host, owner, repo, number):
        self.calls += 1
        if self._fail:
            raise ForgeError("HTTP 403")
        return self._pull

    def pull_diff_url(self, host, owner, repo, number, pull):
        return f"https://{host}/{owner}/{repo}/compare/{pull.base}...{pull.head}.diff"

    def commit_diff_url(self, host, owner, repo, rev):
        return f"https://{host}/{owner}/{repo}/commit/{rev}.diff"

    def merge_base(self, host, owner, repo, base, head):
        return self._base


@pytest.fixture
def fake(monkeypatch):
    forge = FakeForge()
    monkeypatch.setattr("pnix.forges.get", lambda name: forge)
    monkeypatch.setattr("pnix.prefetch.file", lambda url: "sha256-PATCH")
    return forge


SPEC = {"type": "github", "host": "github.com", "url": "https://github.com/o/r"}


def test_a_pr_resolves_to_an_immutable_compare_diff(fake, tmp_path):
    node = patches.resolve_one({"pr": 181}, SPEC, "p", tmp_path)
    assert node["kind"] == "pr"
    assert node["number"] == 181
    assert node["head"] == "h" * 40 and node["base"] == "b" * 40
    assert node["immutable"] is True
    assert node["url"].endswith(f"{'b' * 40}...{'h' * 40}.diff")
    assert node["hash"] == "sha256-PATCH"


def test_a_commit_resolves_to_its_own_diff(fake, tmp_path):
    node = patches.resolve_one({"commit": "abc123"}, SPEC, "p", tmp_path)
    assert node == {"kind": "commit", "forge": "github", "host": "github.com",
                    "owner": "o", "repo": "r", "rev": "abc123",
                    "immutable": True, "hash": "sha256-PATCH",
                    "url": "https://github.com/o/r/commit/abc123.diff"}


def test_a_url_is_taken_as_given(fake, tmp_path):
    node = patches.resolve_one({"url": "https://e/x.diff"}, SPEC, "p", tmp_path)
    assert node == {"kind": "url", "immutable": True,
                    "url": "https://e/x.diff", "hash": "sha256-PATCH"}


def test_a_local_patch_is_recorded_relative_to_the_lock(fake, tmp_path):
    """`resolve.nix` reads a patch path as `dirOf lockFile + path`, so that is
    what it must be relative to. It was recorded relative to the *project* root
    instead, which agreed only for as long as the lock sat there: once the lock
    moved into `.pnix/`, `patches/fix.diff` resolved to `.pnix/patches/fix.diff`
    and every local patch failed with `path ... does not exist`."""
    (tmp_path / "patches").mkdir()
    p = tmp_path / "patches" / "fix.diff"
    p.write_text("diff --git a/x b/x\n")
    node = patches.resolve_one(str(p), SPEC, "p", tmp_path)
    assert node["kind"] == "path"
    assert node["path"] == "../patches/fix.diff"
    # The file's own hash, so an edit to it is evidence rather than invisible.
    assert node["hash"].startswith("sha256-")


def test_the_recorded_patch_path_resolves_from_the_lock_directory(fake, tmp_path):
    """The two halves, checked against each other rather than one at a time."""
    from pnix import cli

    (tmp_path / "patches").mkdir()
    p = tmp_path / "patches" / "fix.diff"
    p.write_text("diff --git a/x b/x\n")
    node = patches.resolve_one(str(p), SPEC, "p", tmp_path)

    lock_dir = (tmp_path / cli.LOCK_NAME).parent
    assert (lock_dir / node["path"]).resolve() == p.resolve()


def test_a_local_patch_outside_the_project_is_refused(fake, tmp_path):
    """The lock is committed; an absolute path would not survive a clone."""
    outside = tmp_path.parent / "elsewhere.diff"
    outside.write_text("x\n")
    with pytest.raises(patches.PatchError) as e:
        patches.resolve_one(str(outside), SPEC, "p", tmp_path)
    assert "outside the project root" in str(e.value)


def test_a_missing_local_patch_is_refused(fake, tmp_path):
    with pytest.raises(patches.PatchError) as e:
        patches.resolve_one(str(tmp_path / "nope.diff"), SPEC, "p", tmp_path)
    assert "no such patch file" in str(e.value)


def test_an_unknown_host_asks_which_forge_it_runs(monkeypatch, tmp_path):
    monkeypatch.setattr("pnix.prefetch.file", lambda url: "sha256-PATCH")
    spec = {"type": "forgejo", "url": "https://git.example.invalid/o/r"}
    with pytest.raises(patches.PatchError) as e:
        patches.resolve_one({"pr": 1}, spec, "p", tmp_path)
    assert "cannot tell which forge" in str(e.value)


def test_an_explicit_forge_overrides_the_host_guess(monkeypatch, tmp_path):
    forge = FakeForge()
    monkeypatch.setattr("pnix.forges.get", lambda name: forge)
    monkeypatch.setattr("pnix.prefetch.file", lambda url: "sha256-PATCH")
    spec = dict(SPEC, host="forgejo.example.invalid", forge="forgejo")
    assert patches.resolve_one({"pr": 1}, spec, "p", tmp_path)["kind"] == "pr"


def test_a_rate_limited_forge_names_the_pin(monkeypatch, tmp_path):
    monkeypatch.setattr("pnix.forges.get", lambda name: FakeForge(fail=True))
    with pytest.raises(patches.PatchError) as e:
        patches.resolve_one({"pr": 181}, SPEC, "mypin", tmp_path)
    assert "mypin" in str(e.value) and "181" in str(e.value)


def test_a_merge_base_that_differs_is_recorded_and_warned_about(monkeypatch,
                                                                tmp_path):
    """The sharpest edge in the feature: a forge renders a PR diff from the
    merge base, not from the rev you pinned. Measured on finit #181 -- base
    64e41e06, merge base c8d6ad65, 'diverged, ahead 1, behind 8'."""
    forge = FakeForge(base="c" * 40)
    monkeypatch.setattr("pnix.forges.get", lambda name: forge)
    monkeypatch.setattr("pnix.prefetch.file", lambda url: "sha256-PATCH")
    node = patches.resolve_one({"pr": 181}, SPEC, "p", tmp_path)
    assert node["mergeBase"] == "c" * 40

    warnings = patches.applies_to({"rev": "d" * 40, "patches": [node]})
    assert len(warnings) == 1 and "may not apply" in warnings[0]

    assert patches.applies_to({"rev": "c" * 40, "patches": [node]}) == []


def test_a_merge_base_lookup_that_fails_does_not_fail_the_update(monkeypatch,
                                                                 tmp_path):
    class Flaky(FakeForge):
        def merge_base(self, *a):
            raise ForgeError("HTTP 403")

    monkeypatch.setattr("pnix.forges.get", lambda name: Flaky())
    monkeypatch.setattr("pnix.prefetch.file", lambda url: "sha256-PATCH")
    node = patches.resolve_one({"pr": 1}, SPEC, "p", tmp_path)
    assert "mergeBase" not in node


def test_advice_needs_no_network():
    node = {"patches": [{"kind": "pr", "number": 181, "merged": True,
                         "state": "closed"}]}
    assert "merged upstream" in patches.advice(node)[0]

    closed = {"patches": [{"kind": "pr", "number": 9, "merged": False,
                           "state": "closed"}]}
    assert "closed without merging" in patches.advice(closed)[0]

    assert patches.advice({"patches": [{"kind": "url"}]}) == []


def test_drift_reports_new_commits_and_a_rebase(monkeypatch):
    forge = FakeForge(pull=Pull(head="n" * 40, base="m" * 40,
                                state="open", merged=False))
    monkeypatch.setattr("pnix.forges.get", lambda name: forge)
    node = {"url": "https://github.com/o/r", "host": "github.com",
            "owner": "o", "repo": "r",
            "patches": [{"kind": "pr", "forge": "github", "number": 7,
                         "head": "h" * 40, "base": "b" * 40, "merged": False}]}
    lines = patches.drift(node, "foo")
    assert any("new commits" in line for line in lines)
    assert any("rebased onto a new base" in line for line in lines)


def test_drift_reports_a_merge_that_happened_after_locking(monkeypatch):
    forge = FakeForge(pull=Pull(head="h" * 40, base="b" * 40,
                                state="closed", merged=True))
    monkeypatch.setattr("pnix.forges.get", lambda name: forge)
    node = {"url": "https://github.com/o/r",
            "patches": [{"kind": "pr", "forge": "github", "number": 7,
                         "head": "h" * 40, "base": "b" * 40, "merged": False}]}
    assert "merged since you locked" in patches.drift(node, "foo")[0]


def test_drift_survives_a_forge_it_cannot_reach(monkeypatch):
    monkeypatch.setattr("pnix.forges.get", lambda name: FakeForge(fail=True))
    node = {"url": "https://github.com/o/r",
            "patches": [{"kind": "pr", "forge": "github", "number": 7,
                         "head": "h" * 40, "base": "b" * 40}]}
    assert "could not check" in patches.drift(node, "foo")[0]


def test_the_forge_registry_refuses_to_guess_at_gitlab():
    with pytest.raises(forges.UnknownForge) as e:
        forges.get("gitlab")
    assert "never probed" in str(e.value)


def test_gitea_is_forgejo():
    assert forges.get("gitea").name == "forgejo"


def test_forgejo_admits_its_diffs_are_mutable():
    """Verified: codeberg 404s on every compare-diff spelling, so the only
    PR-range diff available moves when the PR does."""
    assert forges.get("forgejo").immutable_diffs is False
    assert forges.get("github").immutable_diffs is True


# --- a patch that lives somewhere other than the pin -----------------------

def test_a_patch_may_name_its_own_repo(monkeypatch, tmp_path):
    """A pin fetched from a mirror can track the upstream pull request."""
    seen = {}

    class Recording(FakeForge):
        def pull(self, host, owner, repo, number):
            seen.update(host=host, owner=owner, repo=repo)
            return self._pull

    monkeypatch.setattr("pnix.forges.get", lambda name: Recording())
    monkeypatch.setattr("pnix.prefetch.file", lambda url: "sha256-PATCH")

    mirrored = {"type": "forgejo",
                "url": "https://forgejo.nimeses.com/NixOS/nixarr"}
    node = patches.resolve_one(
        {"pr": 42, "repo": "https://github.com/rasmus-kirk/nixarr"},
        mirrored, "nixarr", tmp_path)

    assert seen == {"host": "github.com", "owner": "rasmus-kirk",
                    "repo": "nixarr"}
    assert node["owner"] == "rasmus-kirk" and node["host"] == "github.com"
    # and the pin's own url is untouched
    assert mirrored["url"] == "https://forgejo.nimeses.com/NixOS/nixarr"


def test_a_git_pin_can_track_a_pr_by_naming_the_repo(monkeypatch, tmp_path):
    """A `git` pin's host has no PR API as far as pnix is concerned, so the
    patch names the repo whose pull requests it means."""
    monkeypatch.setattr("pnix.forges.get", lambda name: FakeForge())
    monkeypatch.setattr("pnix.prefetch.file", lambda url: "sha256-PATCH")
    git_pin = {"type": "git", "url": "https://forgejo.nimeses.com/NixOS/nixarr.git"}
    node = patches.resolve_one(
        {"pr": 7, "repo": "https://github.com/rasmus-kirk/nixarr"},
        git_pin, "nixarr", tmp_path)
    assert node["kind"] == "pr" and node["repo"] == "nixarr"
    assert node["host"] == "github.com"


def test_a_pin_with_no_repo_and_a_patch_that_names_none_says_so(monkeypatch,
                                                                tmp_path):
    monkeypatch.setattr("pnix.forges.get", lambda name: FakeForge())
    with pytest.raises(patches.PatchError) as e:
        patches.resolve_one({"pr": 7}, {"type": "git"}, "p", tmp_path)
    assert "needs a repo" in str(e.value) and "repo =" in str(e.value)


def test_drift_follows_the_patch_repo_not_the_pin(monkeypatch):
    seen = {}

    class Recording(FakeForge):
        def pull(self, host, owner, repo, number):
            seen.update(owner=owner, repo=repo)
            return self._pull

    monkeypatch.setattr("pnix.forges.get", lambda name: Recording())
    node = {"owner": "NixOS", "repo": "nixarr", "host": "forgejo.nimeses.com",
            "patches": [{"kind": "pr", "forge": "github", "host": "github.com",
                         "owner": "rasmus-kirk", "repo": "nixarr", "number": 42,
                         "head": "h" * 40, "base": "b" * 40}]}
    patches.drift(node, "foo")
    assert seen == {"owner": "rasmus-kirk", "repo": "nixarr"}


def test_a_patch_repo_on_a_known_host_needs_no_forge(monkeypatch, tmp_path):
    """`repo` is a url, so its host settles the forge the same way a pin's
    does. `forge` is left for a self-hosted instance, where no table can know
    what software runs there."""
    seen = {}

    class Recording(FakeForge):
        def pull(self, host, owner, repo, number):
            seen.update(host=host, owner=owner, repo=repo)
            return self._pull

    monkeypatch.setattr("pnix.forges.get", lambda name: Recording())
    monkeypatch.setattr("pnix.prefetch.file", lambda url: "sha256-PATCH")

    node = patches.resolve_one(
        {"pr": 32, "repo": "https://codeberg.org/BANanaD3V/niri-nix"},
        {"type": "git", "url": "https://example.invalid/o/r"}, "n", tmp_path)
    assert seen == {"host": "codeberg.org", "owner": "BANanaD3V",
                    "repo": "niri-nix"}
    assert node["kind"] == "pr"


def test_owner_and_host_are_no_longer_patch_fields(tmp_path):
    """They duplicated what a url says, in a place that could disagree."""
    from pnix import schema
    with pytest.raises(schema.SchemaError) as e:
        schema.validate(
            {"p": {"url": "https://github.com/o/r",
                   "patches": [{"pr": 1, "owner": "up", "host": "github.com"}]}},
            {"p": "/decl.nix"})
    msg = str(e.value)
    assert "'owner' is not a patch field" in msg
    assert "'host' is not a patch field" in msg


def test_a_patch_repo_that_is_not_a_url_is_refused(tmp_path):
    from pnix import schema
    with pytest.raises(schema.SchemaError) as e:
        schema.validate(
            {"p": {"url": "https://github.com/o/r",
                   "patches": [{"pr": 1, "repo": "nixarr"}]}},
            {"p": "/decl.nix"})
    assert "is not a repository URL" in str(e.value)


def test_drift_names_the_command_that_adopts_new_commits(monkeypatch):
    forge = FakeForge(pull=Pull(head="n" * 40, base="b" * 40,
                                state="open", merged=False))
    monkeypatch.setattr("pnix.forges.get", lambda name: forge)
    node = {"url": "https://github.com/o/r",
            "patches": [{"kind": "pr", "forge": "github", "number": 7,
                         "head": "h" * 40, "base": "b" * 40, "merged": False}]}
    lines = patches.drift(node, "finit")
    assert any("pnix update --repatch finit" in line for line in lines)


def test_a_merged_pr_does_not_suggest_repatching(monkeypatch):
    """Adopting more commits is not the fix for a patch upstream already has."""
    forge = FakeForge(pull=Pull(head="h" * 40, base="b" * 40,
                                state="closed", merged=True))
    monkeypatch.setattr("pnix.forges.get", lambda name: forge)
    node = {"url": "https://github.com/o/r",
            "patches": [{"kind": "pr", "forge": "github", "number": 7,
                         "head": "h" * 40, "base": "b" * 40, "merged": False}]}
    assert not any("--repatch" in line for line in patches.drift(node, "finit"))


def test_a_local_patch_records_the_file_s_hash(tmp_path):
    """Without it nothing can tell that an edited patch file changed the tree,
    so a stale patchedHash survives every `pnix update`."""
    (tmp_path / ".pnix").mkdir()
    f = tmp_path / ".pnix" / "fix.patch"
    f.write_text("--- a\n+++ b\n")
    node = patches.resolve_one(str(f), SPEC, "foo", tmp_path)
    assert node["kind"] == "path"
    assert node["hash"].startswith("sha256-")

    f.write_text("--- a\n+++ b\n@@ different @@\n")
    moved = patches.resolve_one(str(f), SPEC, "foo", tmp_path)
    assert moved["hash"] != node["hash"]


def test_a_changed_local_patch_file_counts_as_drift(tmp_path):
    (tmp_path / ".pnix").mkdir()
    f = tmp_path / ".pnix" / "fix.patch"
    f.write_text("one\n")
    node = {"patches": [patches.resolve_one(str(f), SPEC, "foo", tmp_path)]}
    assert not patches.local_patch_drifted(node, tmp_path)
    f.write_text("two\n")
    assert patches.local_patch_drifted(node, tmp_path)


def test_a_patch_node_with_no_local_file_never_drifts(tmp_path):
    node = {"patches": [{"kind": "pr", "url": "u", "hash": "sha256-AAA"}]}
    assert not patches.local_patch_drifted(node, tmp_path)


def test_a_vanished_local_patch_counts_as_drift(tmp_path):
    """Better to re-resolve and report the missing file than to keep a hash for
    a patch that is no longer there."""
    (tmp_path / ".pnix").mkdir()
    node = {"patches": [{"kind": "path", "path": "gone.patch",
                         "hash": "sha256-AAA"}]}
    assert patches.local_patch_drifted(node, tmp_path)
