# Declares an input and reads it, so resolution has to actually work.
{
  inputs.dep.url = "github:o/dep";
  outputs = { self, dep, ... }: { got = dep.marker or "none"; };
}
