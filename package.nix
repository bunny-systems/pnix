# The pnix CLI.
#
# Callable with `pkgs.callPackage ./package.nix { }`, which is the point of
# keeping it separate from default.nix: it composes into any nixpkgs, including
# a consumer's own, without going through this repo's entry point.
{
  lib,
  python3Packages,
  makeWrapper,
  git,
  nix,
}:
python3Packages.buildPythonApplication {
  pname = "pnix";
  version = "0.1.0";
  pyproject = true;

  src = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.intersection (lib.fileset.unions [
      ./pnix
      ./pyproject.toml
      ./README.md
      ./LICENSE
    ]) (lib.fileset.fileFilter (f: !(lib.hasSuffix ".pyc" f.name)) ./.);
  };

  build-system = [ python3Packages.setuptools ];

  nativeBuildInputs = [ makeWrapper ];

  # pnix shells out rather than linking: `git ls-remote` for refs,
  # `nix-prefetch-url` and `nix-hash` for hashes, `nix-instantiate` for the
  # collector. git is pinned here because the version matters (annotated-tag
  # peeling); nix deliberately is not wrapped -- pnix must use the daemon and
  # store the user is actually running against, not one from this closure.
  postInstall = ''
    wrapProgram $out/bin/pnix --prefix PATH : ${lib.makeBinPath [ git ]}
  '';

  # The test suite shells out to nix-instantiate and nix-prefetch-url, neither
  # of which exists inside the build sandbox. Run it with `nix develop -c pytest`.
  doCheck = false;

  pythonImportsCheck = [
    "pnix"
    "pnix.cli"
    "pnix.sources"
    "pnix.forges"
  ];

  passthru.nix = nix;

  meta = {
    description = "Nix-native input pinning with per-file declarations and PR-tracked patches";
    license = lib.licenses.eupl12;
    mainProgram = "pnix";
  };
}
