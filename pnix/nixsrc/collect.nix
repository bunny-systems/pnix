# Read pin declarations out of ordinary Nix files without a module system.
#
# Each file is imported and, if it is a function, called with a throwing stub
# for exactly the arguments `builtins.functionArgs` reports -- which is valid
# whether or not the pattern ends in `...`, because we pass no extras. Reading
# `.pins` off the result forces nothing else, so a file may use `inputs`,
# `lib`, `config` or anything else freely everywhere except inside its own pin
# declarations.
#
# Deliberately not evalModules: that lives in nixpkgs' lib, and depending on it
# would mean fetching nixpkgs before pnix can read a single declaration.
#
# The only constraint: a pin declaration must not depend on its module's
# arguments. That case throws PASS1-FORCED-PIN naming the argument -- but only
# once the declaration is forced, which is why the CLI evaluates this with
# --strict and why the tests wrap those cases in builtins.deepSeq.
#
# **The tryEval guard is a WHNF probe, and only that.** Measured over all 44,024 .nix
# files in nixpkgs: the grep narrows to 56 candidates, and **49 of those 56 cannot be
# forced at all** with stubbed arguments. They are ordinary package files --
# `{ lib, python3Packages, ... }: python3Packages.buildPythonApplication { ... }` -- and
# they match the grep because `pins` is a real Python package that things depend on.
# Checking whether the result has a `pins` attribute means forcing it to weak head
# normal form, and for those files the WHNF *is* the builder call.
#
# So an unforceable candidate must be skipped rather than abort the run; at nixpkgs
# scale, aborting means pnix cannot be pointed at a large tree at all. But a skipped
# file must not be silent either, or a real declaration file with a real error
# contributes zero pins and nobody notices. Both are satisfied because the two cases are
# separable by *when* the throw happens:
#
#   * forcing the file's result to WHNF throws -> it is not a declaration file. Skip it,
#     and record it in `skipped` so the CLI can say so.
#   * WHNF succeeds and it has `pins`, but forcing a pin's *value* throws -> that is the
#     one collector constraint being violated. Fatal, with the PASS1-FORCED-PIN message,
#     because that force happens outside this tryEval (the CLI evaluates --strict).
#
# The probe covers exactly the first case: `tryEval` catches `throw`, which is what the
# argument stubs raise. It remains no use as a general safety net -- it catches neither
# `{ }.nope` nor calling a non-function -- which is why discovery must keep the candidate
# list short regardless.
#
# A file that evaluates to something other than an attrset contributes nothing without
# erroring -- that check is total and costs nothing.
{
  files,
  attr ? "pins",
}:
let
  stub =
    name:
    throw "PASS1-FORCED-PIN: a pin declaration read the module argument '${name}'. Pin declarations must be plain data.";

  # Can this file's result be forced at all, and is it an attrset?
  probe =
    path:
    let
      m = import path;
      value =
        if builtins.isFunction m then m (builtins.mapAttrs (n: _: stub n) (builtins.functionArgs m)) else m;
      forced = builtins.tryEval (builtins.isAttrs value);
    in
    if forced.success && forced.value then
      {
        ok = true;
        inherit value;
      }
    else
      { ok = false; };

  perFile = map (
    p:
    let
      r = probe p;
    in
    {
      file = toString p;
      ok = r.ok;
      # Read but not forced: a pin value that reads a stubbed argument must throw
      # out of the caller's --strict evaluation, not be swallowed by the probe above.
      pins = if r.ok then r.value.${attr} or { } else { };
    }
  ) files;

  skipped = map (e: e.file) (builtins.filter (e: !e.ok) perFile);

  # Path values become strings before they leave Nix.
  #
  # `nix-instantiate --strict --json` *copies a path literal into the store* and
  # renders it as `/nix/store/095hsr7…-local.patch`. Writing that into the lock
  # would record something machine-local, which is precisely the property the
  # committed lock must not have -- a fresh clone on another host has no such
  # path. `toString` gives the path the declaration actually named, which the
  # CLI can then make relative to the project root.
  unpath =
    v:
    let
      t = builtins.typeOf v;
    in
    if t == "path" then
      toString v
    else if t == "list" then
      map unpath v
    else if t == "set" then
      builtins.mapAttrs (_: unpath) v
    else
      v;

  # Same semantics as the module system's mergeEqualOption: identical
  # definitions merge silently, differing ones fail naming both files.
  merge =
    acc: entry:
    builtins.foldl' (
      a: name:
      let
        new = entry.pins.${name};
      in
      if a ? ${name} && a.${name}.value != new then
        throw "pnix: '${name}' is declared differently in ${a.${name}.file} and ${entry.file}"
      else
        a
        // {
          ${name} = {
            value = new;
            inherit (entry) file;
          };
        }
    ) acc (builtins.attrNames entry.pins);

  merged = builtins.foldl' merge { } perFile;
in
{
  pins = builtins.mapAttrs (_: v: unpath v.value) merged;
  provenance = builtins.mapAttrs (_: v: v.file) merged;
  inherit skipped;
}
