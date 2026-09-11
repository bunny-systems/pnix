import subprocess
import tarfile
from pathlib import Path

import pytest


def _run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


# Committer identity is needed for `git tag -a`: an annotated tag is a real
# object with an author, unlike a lightweight tag.
_IDENT = ["-c", "user.email=t@e", "-c", "user.name=t"]


@pytest.fixture
def local_repo(tmp_path: Path) -> Path:
    """A real git repo on disk, reachable as file://<path>. No network.

    Carries one tag of each kind on purpose. `refs/tags/v1.0.0` points straight
    at the commit; `refs/tags/v2.0.0` points at a tag *object*, and the commit
    is only on the peeled `refs/tags/v2.0.0^{}` line. Most real releases are
    annotated, so a fixture with only a lightweight tag lets a broken resolver
    pass.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-q", "-b", "main"], repo)
    (repo / "flake.nix").write_text("{ outputs = _: { }; }\n")
    _run(["git", "add", "."], repo)
    _run(["git", *_IDENT, "commit", "-qm", "init"], repo)
    _run(["git", "tag", "v1.0.0"], repo)
    _run(["git", *_IDENT, "tag", "-a", "v2.0.0", "-m", "annotated"], repo)
    return repo


@pytest.fixture
def tagged_repo(tmp_path: Path) -> Path:
    """A repo with a realistic spread of tags: several releases, a prerelease,
    an annotated tag, and one tag that is not a version at all.

    Every release tag here is **annotated**, which is what real releases are and
    what makes `ls-remote --tags` report a tag object rather than a commit.
    """
    repo = tmp_path / "tagged"
    repo.mkdir()
    _run(["git", "init", "-q", "-b", "main"], repo)
    for i, tag in enumerate(
        ["v1.2.3", "v1.9.0", "v1.10.0", "v2.0.0-rc1", "nixos-24.05"]
    ):
        (repo / "f").write_text(f"{i}\n")
        _run(["git", "add", "-A"], repo)
        _run(["git", *_IDENT, "commit", "-qm", f"c{i}"], repo)
        _run(["git", *_IDENT, "tag", "-a", tag, "-m", tag], repo)
    return repo


@pytest.fixture
def local_repo_head(local_repo: Path) -> str:
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=local_repo,
                         check=True, capture_output=True, text=True)
    return out.stdout.strip()


@pytest.fixture
def local_tarball(tmp_path: Path) -> Path:
    """A .tar.gz on disk, fetchable as file://<path>."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "hello.txt").write_text("hello\n")
    tgz = tmp_path / "src.tar.gz"
    with tarfile.open(tgz, "w:gz") as tf:
        tf.add(src, arcname="src")
    return tgz
