# The one real violation: the pin's VALUE reads a stubbed argument.
{ inputs, ... }: {
  pins.baz = {
    type = "github";
    rev = inputs.other.rev;
  };
}
