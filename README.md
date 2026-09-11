# pnix

Nix-native input pinning. Declarations live in the file that consumes them,
fetching is extensible, and nothing needs an experimental Nix feature.

No `lib`, no `evalModules`, no flakes, no runtime dependency beyond `git` and
`nix`.

---

## Running it

```sh
nix-shell --run "pnix --project ~/nixconfig update"    # dev shell
nix-build -A packages.default                          # build the CLI
nix develop -c pnix …                                  # flakes, if you prefer
```

`--project` comes **before** the subcommand.

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
| `--project DIR` | all | project root (default `.`) |
| `--root DIR` | `update`, `look` | where to scan for declarations; repeatable. A file works too. Default: the project root |
| `--force` | `init` | overwrite files that lost their pnix marker |
| `names…` | `update` | update only these pins; the rest keep their locked entry |

There is no `add` or `rm`: declare a pin in the file that uses it, then
`pnix update`. The lock is the only file pnix writes.

**Scope your scan.** `--root modules` is usually right for a config repo — it
keeps `.tack/` and stray files out of collection. `.pnix/` is skipped
unconditionally.

---

## First run

`.pnix/` is the whole of pnix in your repo: ten files of pure-builtins Nix and
the lock beside them, so `rm -rf .pnix` uninstalls it. The copy is stripped of
comments — it is generated code that lands in your diffs, and the reasoning
lives with the source in `pnix/resolver/`. Every file carries a marker line;
delete it and `pnix init` leaves that file alone from then on.

```sh
pnix --project ~/nixconfig init          # writes .pnix/, commit it
# …declare some pins…
pnix --project ~/nixconfig update        # writes .pnix/pins.lock.json, commit it
```

Then in `default.nix`:

```nix
let
  resolved = import ./.pnix {
    allFollow = { nixpkgs = "nixpkgs"; hjem = "hjem"; };
  };
in
  # resolved.nixpkgs, resolved.hjem, …
```

> **Do not name that binding `pins`.** A local binding of that name makes the
> file a declaration candidate, and the collector would force your whole module
> fixpoint looking for an attribute that is not there.

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
  pins.nixpkgs = { owner = "NixOS"; repo = "nixpkgs"; ref = "nixos-unstable"; };
  pins.hjem    = { owner = "feel-co"; repo = "hjem"; };
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

---

## Source types

Ten of them. `type` defaults to `github`.

```nix
pins.a = { owner = "NixOS"; repo = "nixpkgs"; };                        # github
pins.b = { type = "forgejo"; owner = "n"; repo = "r";
           host = "forgejo.nimeses.com"; };                             # or gitea
pins.c = { type = "gitlab"; owner = "o"; repo = "r"; };
pins.d = { type = "sourcehut"; owner = "~sircmpwn"; repo = "scdoc"; };  # ~ is yours to write
pins.e = { type = "git"; url = "https://…/r.git"; submodules = true; };
pins.f = { type = "tarball"; url = "https://…/src.tar.gz"; };
pins.g = { type = "file"; url = "https://…/thing.json"; };
pins.h = { type = "channel"; channel = "nixos-unstable"; };
pins.i = { type = "path"; path = "/home/me/checkout"; };                # warns; see Overrides
```

| type | needs | notes |
|---|---|---|
| `github` | `owner`, `repo` | `host` for GitHub Enterprise |
| `forgejo` / `gitea` | `owner`, `repo` | `host` defaults to `codeberg.org` |
| `gitlab` | `owner`, `repo` | no PR API — see Patches |
| `sourcehut` | `owner`, `repo` | `owner` includes the leading `~` |
| `git` | `url` | **the only one that carries submodules**; no hash stored, the rev is the commitment |
| `tarball` | `url` | redirects followed at lock time |
| `file` | `url` | not unpacked; flat hash |
| `channel` | `channel` | follows `channels.nixos.org`; records a `version` |
| `path` | `path` | no hash, machine-local — use an override instead |

Forge tarballs do **not** carry submodules. If a pin needs them, use `type = "git"`.

### Common extras

```nix
pins.x = {
  owner = "o"; repo = "r";
  dir = "nix";          # the flake.nix lives in a subdirectory
  flake = false;        # fetch it, do not evaluate it — source only
  shallow = true;       # git only
};
```

---

## Choosing a revision

Precedence, most specific first: **`rev` > `tag` > `release` > `ref` >** the
remote's HEAD. Declaring more than one of `tag`/`release`/`ref` is refused as
ambiguous; `rev` may accompany any of them — it pins exactly while the other
records what was being tracked.

```nix
pins.a = { owner = "o"; repo = "r"; };                        # default branch
pins.b = { owner = "o"; repo = "r"; ref = "main"; };          # a branch
pins.c = { owner = "o"; repo = "r"; ref = "refs/heads/x"; };  # fully qualified
pins.d = { owner = "o"; repo = "r"; tag = "v1.2.3"; };        # exactly that tag
pins.e = { owner = "o"; repo = "r"; release = "^1.2"; };      # newest matching tag
pins.f = { owner = "o"; repo = "r"; rev = "abc123…"; };       # frozen
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
pins.sops-nix = { owner = "Mic92"; repo = "sops-nix"; excludeFollow = [ "nixpkgs" ]; };
pins.thing    = { owner = "o"; repo = "r"; follows = { nixpkgs = "nixpkgs-stable"; }; };
```

A sub-input resolves in four steps:

1. an explicit `follows` — on the flake's own declaration or on your pin;
2. the `allFollow` policy, unless this pin lists the name in `excludeFollow`;
3. **the pinned source's own `flake.lock`**, evaluated as a flake;
4. `{ }`.

Step 3 is what makes `excludeFollow` mean something. The policy keeps applying
below it, so a transitive dependency still lands on your nixpkgs unless the pin
opted out.

---

## Patches

```nix
pins.finit = {
  owner = "finit-project"; repo = "finit";
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

For a pin fetched from a mirror whose pull requests live upstream — and the only
way a `git`-type pin can track a PR at all, since it has no `owner`/`repo`:

```nix
pins.nixarr = {
  type = "git";
  url = "https://forgejo.nimeses.com/NixOS/nixarr.git";
  patches = [
    { pr = 42; owner = "rasmus-kirk"; repo = "nixarr"; forge = "github"; }
  ];
};
```

PR tracking works on **github** and **forgejo/gitea** only. GitLab and sourcehut
pins are refused for `{ pr = …; }` — use `commit` or `url`.

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
pins.thing = { owner = "o"; repo = "r"; patches = [ … ]; importable = true; };
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

# Escalating first drops it: sudo and doas both reset the environment, and an
# unset variable is indistinguishable from no override at all. Set it past the
# boundary instead.
sudo env PNIX_OVERRIDE=finix=/home/nimeses/Projects/nix/finix nixos-rebuild switch --file .
```

Absolute paths only (`~/` is expanded in the variable). A remote ref is what the
lock is for; these are the escape hatch for a working tree.

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

## Licence

Apache-2.0. pnix contains no third-party code.
