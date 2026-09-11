{
  outputs = { self, ... }: {
    a = "A";
    viaSelf = self.a;
    inputNames = builtins.attrNames self.inputs;
  };
}
