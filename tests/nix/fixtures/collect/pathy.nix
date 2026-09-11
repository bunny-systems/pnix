# A path literal in a declaration. --strict --json would copy this into the
# store and report the store path; the collector must report the path the
# declaration named instead.
{
  pins.withpath = {
    type = "github";
    owner = "o";
    repo = "r";
    patches = [ ./a.nix ];
  };
}
