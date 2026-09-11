# pnix-managed. delete this line to take ownership; pnix will leave it alone.
# SPDX-License-Identifier: EUPL-1.2
rec {

  resolveSpec = doc: spec: if builtins.isList spec then walkPath doc doc.root spec else spec;

  walkPath =
    doc: nodeName: path:
    if path == [ ] then
      nodeName
    else if !(doc.nodes ? ${nodeName}) then
      throw "pnix: follows path dead-end: no node '${nodeName}' in flake.lock"
    else
      let
        key = builtins.head path;
        inputs = doc.nodes.${nodeName}.inputs or { };
      in
      if !(inputs ? ${key}) then
        throw "pnix: follows path dead-end: node '${nodeName}' has no input '${key}'"
      else
        walkPath doc (resolveSpec doc inputs.${key}) (builtins.tail path);

  inputNode =
    doc: nodeName: subName:
    let
      inputs = doc.nodes.${nodeName}.inputs or { };
    in
    if !(inputs ? ${subName}) then
      null
    else
      let
        target = resolveSpec doc inputs.${subName};
      in
      if doc.nodes ? ${target} then target else null;

  normalize =
    locked:
    let
      t = locked.type;
      archiveHost = {
        github = "github.com";
        gitlab = "gitlab.com";
        sourcehut = "git.sr.ht";
      };
      host = locked.host or archiveHost.${t} or null;
    in
    if t == "git" then
      {
        kind = "git";
        inherit (locked) url rev;
      }
      // (if locked ? ref then { inherit (locked) ref; } else { })
      // (if locked.submodules or false then { submodules = true; } else { })
    else if t == "path" then
      {
        kind = "path";
        inherit (locked) path;
      }
    else if t == "tarball" || t == "file" then
      {
        kind = if t == "file" then "file" else "tarball";
        inherit (locked) url;
        hash = locked.narHash;
      }
    else if archiveHost ? ${t} then
      {
        kind = "tarball";
        url = "https://${host}/${locked.owner}/${locked.repo}/archive/${locked.rev}.tar.gz";
        hash = locked.narHash;
      }
    else
      throw "pnix: upstream flake.lock node has type '${t}', which pnix cannot fetch";
}
