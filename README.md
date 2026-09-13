# pnix

Nix-native input pinning. Declarations live in the file that consumes them,
fetching is extensible, and nothing needs an experimental Nix feature.

No `lib`, no `evalModules`, no flakes, no runtime dependency beyond `git` and
`nix`.

---

## Installing pnix
The recommended way to install `pnix` is within your project's development shell, but you'll need to first bootstrap nixpkgs and pnix in order to do that.

First, add the required pins to any file in your repo (ex. `pins.nix`):
```nix
{
  pins.nixpkgs = {
    url = "https://channels.nixos.org/nixpkgs-unstable/nixexprs.tar.xz";
    type = "tarball";
  };

  pins.pnix = {
    url = "https://github.com/bunny-systems/pnix";
    # pnix's flake isn't intended for consumption.
    flake = false;
  };
}
```

Then, temporarily install pnix and use it to pin nixpkgs and this repo:

Without nix-command enabled:
```sh
nix-build "https://github.com/bunny-systems/pnix/archive/main.tar.gz"
./result/bin/pnix init
./result/bin/pnix update
rm result
```

With nix-command enabled (recommended):
```sh
nix run github:bunny-systems/pnix -- init
nix run github:bunny-systems/pnix -- update
```

Finally, add pnix to your shell:
```nix
# shell.nix
{
  sources ? import ./.pnix { },
  pkgs ? import sources.nixpkgs { },
}:
pkgs.mkShell {
  packages = [
    (pkgs.callPackage "${sources.pnix}/package.nix" { })
  ];
}
```

Now, pnix will be abvailable in your path whenever you run `nix-shell`.

### This repo has no flake inputs

`default.nix` is the entry point; `flake.nix` only forwards to it and declares
**no inputs at all**, so Nix writes no `flake.lock`. nixpkgs is pinned in this
repo's own `.pnix/pins.lock.json`, by pnix, and resolved by the copy of its own
resolver in `.pnix/` — the dev environment is an integration test of the tool.

| file | what it is |
|---|---|
| `default.nix` | entry point: `packages`, `devShells`, `formatter` |
| `package.nix` | the CLI derivation, `callPackage`-able from any nixpkgs |
| `shell.nix` | the dev shell |
| `pins.nix` | this repo's own pin declarations |
| `flake.nix` | passthrough, inputless |
| `.pnix/` | this repo's own vendored resolver and lock |

Every flake command has a flakeless equivalent:

```sh
nix-shell                       nix develop
nix-build -A packages.default   nix build
nix-build -A formatter          nix fmt
```

---

## The three commands

```sh
pnix init                    copy the eval-time resolver into this repo
pnix update [name…]          resolve refs → revs, hash, write the lock
pnix look                    report drift; writes nothing, downloads nothing
```

| flag | on | meaning |
|---|---|---|
| `--project DIR` | all | project root; default: nearest parent with a `.pnix/` (`init`: the working directory) |
| `--root DIR` | `update`, `look` | where to scan for declarations; repeatable. A file works too. Default: the project root |
| `--force` | `init` | overwrite files that lost their pnix marker |
| `--version` | — | print the version and exit |
| `-q`, `--quiet` | `update` | no per-pin progress; warnings and errors still print |
| `-v`, `--verbose` | `update` | also report each download as it starts |
| `--exclude NAME` | `update` | hold this pin at its locked revision; repeatable |
| `names…` | `update` | update only these pins; the rest keep their locked entry |

`update` reports each pin on stderr as it lands, and announces a download
before it starts, since that is where the ~20 s of a full run goes:

```
pnix: resolving 3 pins
  [1/3] systems  new       31732fcf
  [2/3] hjem     updated   d248f0e4 -> e5e30b43
  [3/3] nixpkgs  unchanged 8ce4ef6c
pnix: 3 pins resolved -- 1 new, 1 updated, 1 unchanged, 14.5s
```

Pins resolve concurrently, so the order is whatever finishes first. `-v` adds a
`fetching X...` line before each download, which is where the time goes; `-q`
silences everything. stdout stays empty, so redirecting it captures only what
you asked for.

`--exclude NAME` is the inverse of naming pins: everything moves *except* that
one, which is not resolved at all and keeps its entry verbatim. An unknown name
is refused, since a misspelled exclusion would update the pin it was meant to
hold; so is excluding a pin that was never locked, since there is no entry to
keep and it would be dropped instead.

`update` uses `nix flake prefetch` when the flakes feature happens to be
enabled, and falls back to the stable CLI when it is not. Purely a speed
matter — hashing nixpkgs costs ~25 s of unpack-and-NAR-hash through
`nix-prefetch-url`, against 0.4 s from Nix's fetcher cache, which only the
flake fetchers can reach. Both routes are verified to produce the same `hash`
and the same `lastModified`, and the resolver is untouched either way: **nothing
a consumer evaluates requires an experimental feature, ever.**

Availability is settled by trying, because it cannot be asked — every
capability query is itself a `nix <subcommand>`, gated behind the feature being
queried.

There is no `add` or `rm`: declare a pin in the file that uses it, then
`pnix update`. The lock is the only file pnix writes.

**Scope your scan.** `--root modules` is usually right for a config repo — it
keeps `.tack/` and stray files out of collection. `.pnix/` is skipped
unconditionally.

---

## First run

`.pnix/` is the whole of pnix in your repo, so `rm -rf .pnix` uninstalls it:

```
.pnix/
├── default.nix       what you import
├── pins.lock.json    what you review
└── eval/             eight files of pure-builtins Nix; generated, don't edit
```

The copy under `eval/` is stripped of comments — it is generated code that lands
in your diffs, and the reasoning lives with the source in `pnix/resolver/`.
Every file carries a marker line; delete it and `pnix init` leaves that file
alone from then on.

### Step by step

**1. Vendor the resolver.** Run it in the project root; `init` is the one
command that does not search upward for a `.pnix/`, because it is creating one.

```sh
cd ~/myrepo
pnix init                                # writes .pnix/, commit it
```

**2. Declare a pin**, in the file that uses it, or in any file at all if you
have no module system:

```nix
# pins.nix, or lib/foo.nix, or modules/features/editor.nix
{
  pins.nixpkgs = { url = "https://github.com/NixOS/nixpkgs"; ref = "nixos-unstable"; };
  pins.hjem    = { url = "https://github.com/feel-co/hjem"; };
}
```

**3. Lock.** From anywhere inside the repo — `--project` defaults to the nearest
parent holding a `.pnix/`.

```sh
pnix update
```

```
pnix: resolving 2 pins
  [1/2] nixpkgs  new       8ce4ef6c
  [2/2] hjem     new       d248f0e4
pnix: 2 pins resolved -- 2 new, 3.6s
```

**4. Use it.** `import ./.pnix` takes an argument set and returns one input per
pin, shaped like a flake input:

```nix
let
  sources = import ./.pnix {
    allFollow = { nixpkgs = "nixpkgs"; hjem = "hjem"; };
  };
in
  # sources.nixpkgs, sources.hjem, …
```

> **Do not name that binding `pins`.** A local binding of that name makes the
> file a declaration candidate, and the collector would force your whole module
> fixpoint looking for an attribute that is not there.

**5. If — and only if — your declarations live in files a module system also
evaluates**, declare the option, or every declaration is an undeclared one:

```nix
{ lib, ... }:
{
  options.pins = lib.mkOption {
    type = lib.types.attrsOf lib.types.unspecified;   # not `raw`: identical
    default = { };                                    # declarations must merge
    internal = true;
  };
}
```

A plain repo — `default.nix`, `shell.nix`, a `lib/` tree — needs none of this.
Tested on four shapes: single file, scattered plain files, `evalModules`, and a
flake. Only the `evalModules` one needs the option.

**6. Day to day.**

```sh
pnix look                # what would move; no writes, no downloads
pnix update              # everything
pnix update nixpkgs      # one pin; the rest keep their locked entry
pnix update -v           # also report each download as it starts
```

Commit `.pnix/` — resolver and lock both. A fresh clone must build with Nix
alone, without pnix installed.

### Migrating from another pinning tool

Keep the old tool in place until the comparison is clean — that is the point of
doing it this way.

1. `pnix init`, then translate the old manifest into declarations. Freeze each
   pin with an explicit `rev` taken from the **old lock**, so upstream drift
   cannot contaminate the diff.
2. `pnix update`, then repoint your entry point at `./.pnix`.
3. Compare `system.build.toplevel.drvPath` for every host, both ways, and only
   then delete the old tool.

Take the old baseline with whatever features that tool needs — tack needs flakes
enabled, for instance. The asymmetry is the point of the exercise, not a flaw in
the measurement.

Three things that are easy to get wrong, all of them observed:

- **A branch the old lock does not record.** Translate from the old *manifest*,
  not its lock, or a pin tracking a non-default branch silently becomes default.
- **Entry points other than the obvious one.** A `flake.nix` wrapper that also
  imported the old resolver has to change too, and it will not fail until you
  run `nix fmt` or `nix build`.
- **Scoping `update` to one file.** `--root pins.nix` is right while
  declarations are central and wrong the moment one moves into the tree: pins it
  cannot see are **pruned from the lock**.

---

## Declaring a pin

A `pins` attribute on whatever a `.nix` file evaluates to. That is the entire
interface — a module, a plain data file, a package expression; the collector
does not know the difference.

```nix
# modules/features/desktop/compositors/niri.nix — beside its only consumer
{ inputs, ... }:
{
  pins.niri-nix = { type = "git"; url = "https://codeberg.org/BANanaD3V/niri-nix"; };

  aspects.desktop.compositors.niri.nixos.imports =
    [ inputs.niri-nix.nixosModules.default ];
}
```

or as a plain sibling data file, which needs no module system at all:

```nix
# modules/builders/pins.nix
{
  pins.nixpkgs = { url = "https://github.com/NixOS/nixpkgs"; ref = "nixos-unstable"; };
  pins.hjem    = { url = "https://github.com/feel-co/hjem"; };
}
```

**The one constraint: a pin declaration must not depend on its file's
arguments.** Everything else in the file may use `inputs`, `lib` or `config`
freely. A violation fails loudly with `PASS1-FORCED-PIN` naming the argument.

Identical declarations of the same pin in two files merge. Differing ones throw,
naming both files.

### Where a declaration may live

Two rules, both about **your** module system rather than pnix:

1. **Declare a `pins` option**, or the module system rejects the assignment:

   ```nix
   # modules/options/pins.nix
   { lib, ... }:
   {
     options.pins = lib.mkOption {
       type = lib.types.attrsOf lib.types.unspecified;   # not `raw`: this merges
       default = { };
       internal = true;
     };
   }
   ```

2. **Put it in a file the *outer* fixpoint evaluates.** A file imported as an
   inner NixOS/finix/hjem module puts `pins.<name>` into *NixOS's* option
   namespace (`Did you mean 'ids', 'fonts' or 'jobs'?`). In `~/nixconfig` the
   `_`-prefix convention already marks those — declare in non-`_` files, or use
   a sibling `pins.nix`.

**Neither rule applies without a module system.** Tested on four repo shapes:

| repo | works | needs |
|---|---|---|
| one `default.nix`, no modules | yes | nothing |
| plain repo, declarations scattered across files | yes | nothing |
| `evalModules` (dendritic or otherwise) | yes | `options.pins` |
| a flake, evaluated purely | yes | nothing |

The last one has one caveat: under a flake's pure evaluation `builtins.getEnv`
returns empty, so **`PNIX_OVERRIDE` is silently inert** — it produces a normal
build off the locked rev with no warning. Use `nix eval --impure`,
`nix-instantiate`, or the `overrides` argument instead.

And do not declare pins in `flake.nix` itself: pnix collects them happily, but
Nix rejects the flake — `error: flake '…' has an unsupported attribute 'pins'`.

**A pin with no consumer has no natural home.** Declarations live beside what
uses them, which says nothing about a pin nothing uses. Park those in one file
and mark them, rather than spreading them somewhere arbitrary.

---

## Source types

Ten of them. **A pin says where the thing is; `type` says how to get it, and
only when the URL cannot settle it.** `github.com`, `codeberg.org`, `gitlab.com`
and `git.sr.ht` are known hosts, so those need no `type` at all.

```nix
pins.a = { url = "https://github.com/NixOS/nixpkgs"; };
pins.b = { url = "https://codeberg.org/n/r"; };
pins.c = { url = "https://gitlab.com/o/r"; };
pins.d = { url = "https://git.sr.ht/~sircmpwn/scdoc"; };
pins.e = { type = "forgejo"; url = "https://forgejo.example.com/n/r"; };  # self-hosted
pins.f = { type = "git"; url = "https://…/r"; submodules = true; };
pins.g = { type = "tarball"; url = "https://…/src.tar.gz"; };
pins.h = { type = "file"; url = "https://…/thing.json"; };
pins.i = { type = "channel"; channel = "nixos-unstable"; };
pins.j = { type = "path"; path = "/home/me/checkout"; };                # warns; see Overrides
```

`owner`, `repo` and `host` are **not** declaration fields. They said the same
thing the URL says, in a second place that could disagree with it. They are
derived and kept in the lock as provenance, which is where `pnix look` reads
them from.

An unknown host with no `type` is refused rather than guessed — assuming a
self-hosted domain runs Forgejo is how you get a 404 at lock time:

```
pins.nix: pin 'x' has no `type` and its host is not one pnix knows, so it
cannot tell what runs there. Add `type`: forgejo, github, gitlab, sourcehut,
or `git` for a plain clone.
```

| type | needs | notes |
|---|---|---|
| `github` | `url` | inferred for `github.com` |
| `forgejo` / `gitea` | `url` | inferred for `codeberg.org`; state it for a self-hosted instance |
| `gitlab` | `url` | inferred for `gitlab.com`; no PR API — see Patches |
| `sourcehut` | `url` | inferred for `git.sr.ht`; the `~` is part of the URL |
| `git` | `url` | **the only one that carries submodules**; no hash stored, the rev is the commitment |
| `tarball` | `url` | redirects followed at lock time |
| `file` | `url` | not unpacked; flat hash |
| `channel` | `channel` | follows `channels.nixos.org`; records a `version` |
| `path` | `path` | no hash, machine-local — use an override instead |

Forge tarballs do **not** carry submodules, and asking for one is an error
rather than a silent omission:

```
pins.nix: pin 'hyprland' sets submodules, which a 'github' archive cannot
carry. Add `type = "git"` to clone it instead.
```

### Common extras

```nix
pins.x = {
  url = "https://github.com/o/r";
  dir = "nix";          # the flake.nix lives in a subdirectory
  flake = false;        # fetch it, do not evaluate it — source only

  # git only — passed straight to builtins.fetchGit
  shallow = true;
  submodules = true;
  lfs = true;           # without it an LFS repo yields pointer files, silently
  exportIgnore = true;  # honour .gitattributes export-ignore, as a tarball does
};
```

**`exportIgnore` is how a `git` pin and a forge tarball of the same rev
disagree.** A codeload archive honours `.gitattributes export-ignore`;
`fetchGit` ignores it unless told. Same commit, different tree, different store
path — so if you swap a pin between `git` and a forge type and the drvPath
moves, this is the first thing to check.

A `fetch` node reaches the builtin whole, so any `fetchGit` option Nix accepts
works once pnix's schema knows its name — `name`, `verifyCommit`, `publicKeys`
and the rest are one entry in `pnix/sources/git.py` each, with no change to the
vendored resolver.

---

## Choosing a revision

Precedence, most specific first: **`rev` > `tag` > `release` > `ref` >** the
remote's HEAD. Declaring more than one of `tag`/`release`/`ref` is refused as
ambiguous; `rev` may accompany any of them — it pins exactly while the other
records what was being tracked.

```nix
pins.a = { url = "https://github.com/o/r"; };                        # default branch
pins.b = { url = "https://github.com/o/r"; ref = "main"; };          # a branch
pins.c = { url = "https://github.com/o/r"; ref = "refs/heads/x"; };  # fully qualified
pins.d = { url = "https://github.com/o/r"; tag = "v1.2.3"; };        # exactly that tag
pins.e = { url = "https://github.com/o/r"; release = "^1.2"; };      # newest matching tag
pins.f = { url = "https://github.com/o/r"; rev = "abc123…"; };       # frozen
```

`release` ranges: `*`, `^1.2`, `~1.9`, `>=1.2`, `>1`, `<=2`, `<2.0.0`, or a bare
`1.2.3` for exact. Rules that matter:

- ordering is **by component**: `^1.2` picks `v1.10.0`, not `v1.9.0`;
- a range **never selects a prerelease** unless it names one, so `^1.2` will not
  jump onto `1.3.0-rc1`;
- tags that are not versions (`nixos-24.05`) are ignored, not fatal;
- annotated tags resolve to the **commit**, not the tag object. This is not
  exotic: 233 of NixOS/nix's 251 tags are annotated.

`release` needs no forge API and no token — a release is always a tag.

---

## Follows

```nix
import ./.pnix { allFollow = { nixpkgs = "nixpkgs"; hjem = "hjem"; }; }
```

`allFollow` maps an *input name* to the *pin* it should resolve to, globally. A
pin opts out per name, or overrides one explicitly:

```nix
pins.sops-nix = { url = "https://github.com/Mic92/sops-nix"; excludeFollow = [ "nixpkgs" ]; };
pins.thing    = { url = "https://github.com/o/r"; follows = { nixpkgs = "nixpkgs-stable"; }; };
```

A sub-input resolves in four steps:

1. an explicit `follows` — on the flake's own declaration or on your pin;
2. the `allFollow` policy, unless this pin lists the name in `excludeFollow`;
3. **the pinned source's own `flake.lock`**, evaluated as a flake;
4. nothing left — and what happens depends on how the input was asked for:
   a **declared** input becomes `{ }`, since `follows = ""` is a legitimate way
   to say "deliberately nothing"; an input the flake's `outputs` merely asks for,
   never declaring or locking it, **throws**:

   ```
   pnix: 'up' needs input 'nixpkgs', which it neither declares nor locks.
   Add it to `allFollow`, or `follows.nixpkgs` on the 'up' pin.
   ```

   Handing that case `{ }` made the upstream fail somewhere far away —
   `nixpkgs.legacyPackages.…` on an empty set — instead of where the remedy is.

Step 3 is what makes `excludeFollow` mean something. The policy keeps applying
below it, so a transitive dependency still lands on your nixpkgs unless the pin
opted out.

---

## Patches

```nix
pins.finit = {
  url = "https://github.com/finit-project/finit";
  patches = [
    { pr = 181; }                       # tracked to its head and base
    { commit = "abc123…"; }             # one commit's diff
    { url = "https://…/fix.diff"; }     # any URL
    ./patches/local.diff                # a file in your repo
  ];
};
```

Local `.diff` and `.patch` both work; prefer `.diff` (smaller, no author
metadata). Local paths are recorded **relative to the project root**, so a clone
elsewhere still finds them.

### A patch from a different repo

A patch does not have to live in the repo it applies to. `repo` names the one
whose pull requests you mean, as a url — same shape as a pin:

```nix
pins.nixarr = {
  type = "forgejo";
  url = "https://forgejo.nimeses.com/NixOS/nixarr";       # your mirror
  patches = [
    { pr = 42; repo = "https://github.com/rasmus-kirk/nixarr"; }   # upstream
  ];
};
```

Its host settles the forge, so `forge` is only needed when *that* host is
self-hosted too. It is also how a `git` pin tracks a PR: `git` means "pnix knows
nothing about this host", so the patch supplies a repo it does know.

`owner` and `host` are not patch fields — `repo` says both.

PR tracking works on **github** and **forgejo/gitea** only. GitLab and sourcehut
are refused for `{ pr = …; }` — use `commit` or `url`.

### Two things that will bite

**A patch applies exactly or the build fails.** Fuzz is forbidden
(`-F0 --no-backup-if-mismatch`). A fuzzed hunk lands where the context merely
looked similar — a wrong tree that builds.

**A forge renders a PR diff from the *merge base*, not from the rev you pinned.**
`pnix update` warns when they differ:

```
pnix: finit: PR #181: its diff is generated against c8d6ad65, but this pin is
at 64e41e06. The patch may not apply; pnix forbids fuzz, so a mismatch fails
the build.
```

Pin the merge base, or pick a rev the patch actually applies to.

### Patched pins and evaluation

By default a patched pin is a **source only** — `applyPatches` produces a
derivation and nothing reads inside it, so there is no import-from-derivation.
Use it as `src = inputs.finit;`, then `overrideAttrs` or an overlay.

If you need its *modules*, you must realise it first:

```nix
pins.thing = { url = "https://github.com/o/r"; patches = [ … ]; importable = true; };
```

That costs an IFD: it stalls the rebuild while it builds and fails outright
under `--option allow-import-from-derivation false`. Patching an input to change
a module is usually the wrong tool when `mkForce`, overlays and `disabledModules`
exist.

Patches need a nixpkgs to apply them; pnix uses the pin named `nixpkgs`. Rename
with `import ./.pnix { nixpkgsPin = "nixpkgs-stable"; }`.

---

## Overrides

Two layers, lowest to highest — the second is narrower in scope than the first.

```nix
# 1. programmatic, this caller
import ./.pnix { overrides = { finix = ../finix; }; }
```

```sh
# 2. PNIX_OVERRIDE — this command
PNIX_OVERRIDE=finix=/home/nimeses/Projects/nix/finix nh os switch
PNIX_OVERRIDE="finix=~/Projects/nix/finix,hjem=/tmp/hjem" nix-instantiate …

# a repo URL works too, optionally #<ref-or-rev> — try a PR without locking it
PNIX_OVERRIDE=finix=https://github.com/finix-community/finix#refs/pull/181/head nh os switch
PNIX_OVERRIDE=finix=https://github.com/finix-community/finix#34156d814e6d nh os switch

# Escalating first drops it: sudo and doas both reset the environment, and an
# unset variable is indistinguishable from no override at all. Set it past the
# boundary instead.
sudo env PNIX_OVERRIDE=finix=/home/nimeses/Projects/nix/finix nixos-rebuild switch --file .
```

An absolute path (`~/` is expanded) or a repository URL. `#` separates the ref,
because it cannot appear in a git URL where `@` can (`git@github.com:o/r`);
a 7–40 character hex fragment is taken as a rev, anything else as a ref.

**The line is transient versus recorded, not local versus remote.** Nothing here
reaches the lock in either form. A source you keep reaching for belongs in a
declaration — which is equally true of a path, and why a `path` pin warns.

To *lock* a revision, declare it. `rev` wins over `tag`/`release`/`ref`, and the
strategy it accompanies is kept as provenance, so a pin frozen at a pull-request
head still records the PR it came from:

```nix
pins.finix = {
  url = "https://github.com/finix-community/finix";
  ref = "refs/pull/181/head";   # what it tracks
  rev = "34156d814e6d…";        # frozen here
};
```

A near-miss variable name is caught rather than ignored — `PNIX_OVERRIDES` and
`TACK_OVERRIDES` both throw, since pnix cannot warn about a variable it does not
read and the plural is what a tack migrant's fingers type.

All of them override **resolution, not declaration** — nothing is re-locked, and
changing a rev is still `pnix update`. Every failure throws: an unknown pin name
lists the known ones, a relative path and a missing directory are both refused.
An override that silently did nothing would be the worst possible outcome.

There is deliberately no machine-local override *file*. One host building
differently from what the committed tree says, with nothing in a diff to explain
it, is worse than retyping the variable. An override should be visible in the
command that causes it.

---

## What an input looks like

A pin that is a flake evaluates like one:

```nix
resolved.hjem.nixosModules.default
resolved.nixpkgs.legacyPackages.x86_64-linux.hello
resolved.hjem.inputs.nixpkgs.rev          # its sub-inputs, resolved
resolved.hjem._type                       # "flake"
```

A pin that is not — `flake = false`, a `file`, a patched source — is a
sourceInfo attrset:

```nix
resolved.thing.outPath                    # the store path
resolved.thing.rev  .shortRev  .lastModified  .lastModifiedDate  .narHash
resolved.chan.version                     # channel pins only
```

`import resolved.systems` works for anything importable.

---

## `pnix look`

```
foo: 1a2b3c4d -> 5e6f7a8b                       a pin moved
bar: not locked yet                             declared, never updated
baz: locked but no longer declared              gone from the tree
finit: PR #181 is merged upstream; bump the pin's rev and drop the patch
finit: PR #181 has new commits since you locked (fa14ed16 -> 9c3b2a10)
finit: PR #181 was rebased onto a new base (64e41e06 -> 7d18036f)
all pins current
```

It resolves refs but never downloads, so it is one `git ls-remote` per pin plus
one request per tracked PR. Merged-PR advice is read straight from the lock and
needs no network at all.

---

## Development

```sh
nix develop
pytest              # unit tests, no network
pytest -m network   # the gate: needs ~/nixconfig and the network
ruff check .
```

uv is a convenience, never a requirement — `rm -rf .venv && nix develop -c pytest`
must keep working.

## Prior art

pnix exists because no single tool did all of this, not because the others do it
badly. In the end its just anther (barley) competing standard.

**[tack](https://github.com/manic-systems/tack)** — the closest relative, and
the tool pnix replaced in the config it was built for. The global follows policy
is tack's: `all_follow` with a per-pin opt-out collapses the boilerplate flakes
make you repeat once per input, and `allFollow`/`excludeFollow` are that idea
with different spelling. Its `default.nix` is the best reference anywhere for
walking an upstream `flake.lock` — better than the alternatives, which flatten
the transitive graph.

**[with-inputs](https://github.com/denful/with-inputs)** (Apache-2.0) — a
hand-rolled flake evaluator in ~200 lines with no `lib` and no experimental
features, which is the proof that `resolve.nix` was possible at all. Also
carries vendored adapters for npins, niv, lon, nixtamal, unflake, tack and
flakes, which is the clearest map of this whole design space. Read and diffed
against; nothing imported.

**[npins](https://github.com/andir/npins)** (MIT) — the functionality bar for
*fetching*. Git, GitHub, GitLab, channels, PyPI, semver releases, `verify`,
freeze. If you do not need flake evaluation, per-file declarations or patches,
npins is the mature answer and pnix is overkill.

**nixtamal** — the only other tool that applies patches (`fetchpatch2` /
`applyPatches`), and the source of two things still on pnix's list: mirrors with
failover, and freshness commands. Its patches are a flat manifest with no PR
tracking, which is the one gap pnix set out to fill.

**flake-file** — per-file input declarations, rendered into a generated
`flake.nix` (or npins, unflake, tack). The same instinct as pnix about *where*
declarations belong, resolved the other way: generate a manifest rather than not
need one.

**niv, lon, unflake, nixlock** — the single-manifest lock-and-fetch lineage this
all descends from.

**Nix itself** — `flake.inputs` is the schema being mirrored, and pnix does not
try to replace it. The four-step sub-input resolution is an attempt to reproduce
what flakes already do, minus the experimental feature.

## Licence

**EUPL-1.2.** Reciprocal: distribute a modified pnix and the modifications go
out under the EUPL or one of its [compatible
licences](https://joinup.ec.europa.eu/collection/eupl) (GPL-2.0/3.0, AGPL-3.0,
MPL-2.0, LGPL, CeCILL, LiLiQ-R, EUPL-1.1). Article 5.

**This reaches further than usual, because `pnix init` vendors.** It copies
`.pnix/` — around 570 lines — into your repository, which is redistribution. So
every vendored file is stamped, directly under the marker that `init` uses to
recognise its own work:

```nix
# pnix-managed. delete this line to take ownership; pnix will leave it alone.
# SPDX-License-Identifier: EUPL-1.2
```

Both lines survive the comment stripping, and a test asserts the stamped
identifier matches `pyproject.toml`, so the two cannot drift.

pnix contains no third-party code — see the note on tack under Prior art.
