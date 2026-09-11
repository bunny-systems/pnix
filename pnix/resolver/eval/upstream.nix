# Reading a pinned source's own flake.lock.
#
# Step 3 of sub-input resolution needs no experimental feature and no extra
# lock entries: the upstream lock lives inside a source pnix already pinned by
# hash, so reading it is a pure `readFile` of a store path.
#
# Shared by follows.nix (which decides *whether* to consult it) and resolve.nix
# (which evaluates what it points at), because those two must agree on how the
# graph is shaped.
rec {

  # `nodes.<X>.inputs.<Y>` is either a node name or a path to walk from root.
  # The list form is how `follows` edges are encoded, so every real lock
  # containing a follows has it -- a fixture with only the string form leaves
  # this branch untested and unimplemented.
  resolveSpec =
    doc: spec:
    if builtins.isList spec then walkPath doc doc.root spec else spec;

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

  # The node a given input of `nodeName` points at, or null if there is none.
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

  # Map a flake.lock `locked` node onto a pnix fetch primitive.
  #
  # This is the one place the vendored half still knows forge names, and it is
  # unavoidable: an upstream flake.lock is flake-shaped, not pnix-shaped, so
  # something has to translate. It is bounded in a way pnix's own source types
  # are not -- the `type` set here is fixed by Nix's flake.lock schema, not by
  # anything pnix adds, so this table does not grow when pnix gains a forge.
  #
  # narHash is the NAR hash of the fetched tree, which is exactly what
  # fetchTarball's sha256 wants.
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
