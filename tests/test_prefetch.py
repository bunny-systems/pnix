import subprocess
import tarfile

import pytest

from pnix import prefetch


def test_tarball_returns_sri_hash(local_tarball):
    h, _ = prefetch.tarball(f"file://{local_tarball}")
    assert h.startswith("sha256-")
    assert len(h) > len("sha256-")


def test_tarball_is_deterministic(local_tarball):
    a, _ = prefetch.tarball(f"file://{local_tarball}")
    b, _ = prefetch.tarball(f"file://{local_tarball}")
    assert a == b


def test_tarball_reports_the_archive_mtime(tmp_path):
    """A forge archive stamps its entries with the commit date, which is the
    only token-free source of `lastModified` pnix has."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "f").write_text("x\n")
    tgz = tmp_path / "s.tar.gz"
    with tarfile.open(tgz, "w:gz") as tf:
        info = tf.gettarinfo(src, arcname="src")
        info.mtime = 1788914643
        tf.addfile(info)
    _, mtime = prefetch.tarball(f"file://{tgz}")
    assert mtime == 1788914643


def test_date_of_matches_the_flake_format():
    assert prefetch.date_of(1788914643) == "20260909004403"


def test_missing_url_raises(tmp_path):
    with pytest.raises(prefetch.PrefetchError):
        prefetch.tarball(f"file://{tmp_path}/does-not-exist.tar.gz")


def test_file_hashes_the_bytes_not_the_nar(tmp_path):
    """A patch is fetched with builtins.fetchurl, which wants the flat hash of
    the file. The NAR hash of the same file is a different number."""
    f = tmp_path / "fix.diff"
    f.write_text("diff --git a/x b/x\n")
    flat = prefetch.file(f"file://{f}")
    assert flat.startswith("sha256-")

    proc = subprocess.run(
        ["nix-hash", "--type", "sha256", "--flat", "--sri", str(f),
         *prefetch.NO_EXPERIMENTAL],
        capture_output=True, text=True, check=True,
    )
    assert flat == proc.stdout.strip()


def test_file_and_tarball_disagree_on_purpose(local_tarball):
    nar, _ = prefetch.tarball(f"file://{local_tarball}")
    flat = prefetch.file(f"file://{local_tarball}")
    assert nar != flat


# --- User-Agent ------------------------------------------------------------

def test_every_request_carries_a_user_agent(monkeypatch, tmp_path):
    """Forge instances commonly block `Python-urllib/*` as a scraper. One bare
    `urlopen` in `tarball` was enough to make every fetch from a self-hosted
    Forgejo fail with `HTTP 403: Forbidden` on a URL that curl and Nix both
    fetched fine -- while the other two call sites set a header and looked
    correct. Checked on what actually reaches urlopen, per entry point."""
    from pnix import USER_AGENT

    seen = []

    class Resp:
        url = "https://example.invalid/x"

        def read(self):
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        seen.append(req)
        return Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(prefetch, "_hash_local", lambda *a: "sha256-X")

    prefetch.resolve_redirect("https://example.invalid/x")
    prefetch.tarball("https://example.invalid/x.tar.gz")

    assert len(seen) == 2
    for req in seen:
        assert req.get_header("User-agent") == USER_AGENT


def test_the_user_agent_names_the_tool_and_its_version():
    from pnix import USER_AGENT, __version__

    assert USER_AGENT == f"pnix/{__version__}"
    assert "urllib" not in USER_AGENT


def test_the_forge_client_shares_the_same_user_agent():
    """Two constants meant two things to keep in step, and the one that was
    missed was the one nobody had a test for."""
    from pnix import USER_AGENT
    from pnix.forges import http

    assert http.USER_AGENT == USER_AGENT
