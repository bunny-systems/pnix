"""Resolve a git ref to a revision using `git ls-remote`.

Forge-agnostic and token-free: the same call works for github, gitlab,
codeberg/forgejo, sourcehut and bare git over https or ssh.
"""

import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

from pnix import semver

SHA_LEN = 40

# 21 pins at ~0.86 s each is ~18 s serially. These are independent network
# round-trips, so the pool is pure win; 8 is well under any forge's rate limit
# for ls-remote and keeps the failure output readable.
DEFAULT_WORKERS = 8

# Peeled refs. `git ls-remote <url> <tag>` alone never emits this line —
# measured on git 2.55.0 — so the peel pattern has to be asked for explicitly.
PEELED = "^{}"


class RefError(Exception):
    pass


def _ls_remote(url: str, *patterns: str) -> list[tuple[str, str]]:
    # A leading `--flag` is an option to ls-remote, not a ref pattern.
    flags = [p for p in patterns if p.startswith("--")]
    refs_ = [p for p in patterns if not p.startswith("--")]
    proc = subprocess.run(
        ["git", "ls-remote", *flags, url, *refs_],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        shown = " ".join(patterns)
        raise RefError(f"git ls-remote {url} {shown}: {proc.stderr.strip()}")
    rows = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        sha, _, name = line.partition("\t")
        rows.append((sha.strip(), name.strip()))
    return rows


def _pick(rows: list[tuple[str, str]], ref: str) -> str | None:
    """Choose the row `ref` names, following gitrevisions' precedence.

    Order is refs/tags before refs/heads, matching `git rev-parse`. The peeled
    row wins outright: for an annotated tag `refs/tags/<ref>` is the tag object
    and `refs/tags/<ref>^{}` is the commit, and a lock wants the commit.

    Exact names only. `git ls-remote <url> <pattern>` tail-matches on slash
    boundaries, so asking for `main` can also return `refs/tags/main-thing`;
    taking the first row would then silently lock the wrong ref.
    """
    by_name = {name: sha for sha, name in rows}
    for candidate in (
        f"refs/tags/{ref}{PEELED}",
        f"refs/tags/{ref}",
        f"refs/heads/{ref}",
        f"{ref}{PEELED}",
        ref,
    ):
        if candidate in by_name:
            return by_name[candidate]
    return None


def resolve(url: str, ref: str | None = None) -> str:
    """Return the 40-char sha that `ref` points at.

    `ref` may be a branch name, a tag name, a fully qualified ref, or None for
    the remote's HEAD.
    """
    if ref is None:
        rows = _ls_remote(url, "HEAD")
        if not rows:
            raise RefError(f"{url}: remote has no HEAD")
        return rows[0][0]

    rows = _ls_remote(url, ref, ref + PEELED)
    if not rows:
        raise RefError(f"{url}: no ref matching {ref!r}")

    sha = _pick(rows, ref)
    if sha is None:
        names = ", ".join(sorted(name for _, name in rows))
        raise RefError(
            f"{url}: {ref!r} matched no ref exactly; candidates were {names}"
        )
    return sha


def tags(url: str) -> dict[str, str]:
    """Every tag on the remote, mapped to the **commit** it names.

    `git ls-remote --tags` lists an annotated tag twice: `refs/tags/v2.0.0` is
    the tag *object*, and `refs/tags/v2.0.0^{}` is the commit. The peeled row
    wins, which is the whole reason this is not a two-line comprehension -- a
    semver sort that compares tag-object shas produces a rev no fetcher can get
    a tree from, and nothing downstream notices because it is 40 valid hex
    characters.
    """
    out: dict[str, str] = {}
    for sha, name in _ls_remote(url, "--tags"):
        peeled = name.endswith(PEELED)
        if peeled:
            name = name[: -len(PEELED)]
        if not name.startswith("refs/tags/"):
            continue
        tag = name[len("refs/tags/"):]
        if peeled or tag not in out:
            out[tag] = sha
    return out


def resolve_tag(url: str, tag: str) -> str:
    """The commit a tag names, refusing to fall back to a branch.

    `resolve` follows gitrevisions and will happily answer with `refs/heads/x`
    when asked for `x`. A declaration that says `tag` means a tag.
    """
    rows = _ls_remote(url, f"refs/tags/{tag}", f"refs/tags/{tag}{PEELED}")
    by_name = {name: sha for sha, name in rows}
    for candidate in (f"refs/tags/{tag}{PEELED}", f"refs/tags/{tag}"):
        if candidate in by_name:
            return by_name[candidate]
    raise RefError(f"{url}: no tag {tag!r}")


#: declaration keys that choose a revision, most specific first
STRATEGIES = ("rev", "tag", "release", "ref")


def resolve_for(url: str, spec: dict) -> dict:
    """Apply whichever ref strategy the declaration uses.

    Returns the fields to merge into the locked node. `release` records both the
    range and the tag it picked, so `pnix look` can say *v1.2.3 -> v1.3.0*
    rather than only showing two revisions.

    `git ls-remote` answers all of these with no token and no forge API, which
    is what makes them uniform across forges. That includes `release`: a GitHub
    or Forgejo "release" is always attached to a tag, so the tag list is the
    release list.
    """
    if spec.get("rev"):
        return {"rev": spec["rev"]}

    tag = spec.get("tag")
    if tag:
        return {"tag": tag, "rev": resolve_tag(url, tag)}

    release = spec.get("release")
    if release:
        available = tags(url)
        picked = semver.newest(available, release)
        if picked is None:
            readable = ", ".join(sorted(available)[:12]) or "(none)"
            raise RefError(
                f"{url}: no tag satisfies release {release!r}. tags: {readable}"
            )
        return {"release": release, "tag": picked, "rev": available[picked]}

    ref = spec.get("ref")
    out = {"rev": resolve(url, ref)}
    if ref is not None:
        out["ref"] = ref
    return out


def resolve_many(
    targets: dict[str, tuple[str, str | None]],
    workers: int = DEFAULT_WORKERS,
) -> dict[str, str]:
    """Resolve many (url, ref) pairs concurrently, keyed by caller-chosen name.

    Every failure is collected and reported together: a user with three broken
    pins should learn that in one run, not three.

    Threads rather than processes because the work is entirely waiting on
    `git ls-remote` child processes, and `subprocess.run` releases the GIL.
    """
    if not targets:
        return {}

    results: dict[str, str] = {}
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=min(workers, len(targets))) as pool:
        futures = {
            pool.submit(resolve, url, ref): name
            for name, (url, ref) in targets.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except RefError as err:
                errors.append(f"{name}: {err}")

    if errors:
        raise RefError("\n".join(sorted(errors)))
    return results
