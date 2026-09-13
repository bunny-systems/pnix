# Dev shell, usable as `nix-shell` or through `nix develop`.
{
  sources ? import ./.pnix { },
  pkgs ? import sources.nixpkgs {
    config = { };
    overlays = [ ];
  },
}:
pkgs.mkShell {
  packages = [
    pkgs.python314
    pkgs.python314Packages.pytest
    pkgs.ruff
    # tests/test_vendor.py checks the vendored copy is nixfmt-clean; without
    # this it skips, and the check silently stops happening.
    pkgs.nixfmt-rfc-style
    pkgs.git
    # uv is a convenience and must never become a requirement:
    #   rm -rf .venv && nix develop -c pytest
    # has to keep working. Imports resolve through pyproject's
    # [tool.pytest.ini_options] pythonpath, not an editable install.
    pkgs.uv
  ];

  shellHook = ''
    export UV_PYTHON_DOWNLOADS=never
    export UV_NO_MANAGED_PYTHON=1
    echo "pnix devshell — nixpkgs $(cat ${./.pnix/pins.lock.json} | ${pkgs.python314}/bin/python -c 'import json,sys; print(json.load(sys.stdin)["pins"]["nixpkgs"]["rev"][:12])')"
  '';
}
