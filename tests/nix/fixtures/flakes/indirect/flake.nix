# Declares no inputs at all, yet outputs asks for one.
{
  outputs = { self, nixpkgs, ... }: {
    fromInput = nixpkgs.marker or "missing";
    ownPath = self.outPath;
  };
}
