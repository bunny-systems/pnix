# Forces an input in a top-level `imports`. Harmless: nothing reads `imports`.
{ inputs, ... }: {
  pins.baz.type = "github";
  imports = [ inputs.baz ];
}
