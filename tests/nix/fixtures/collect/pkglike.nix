# A package expression that matches the grep by coincidence: `pins` here is a
# dependency name, not a declaration. Forcing its result to WHNF calls the
# builder on a stubbed argument, so it can only be skipped.
{ lib, python3Packages }:
python3Packages.buildPythonApplication {
  pname = "thing";
  dependencies = [ python3Packages.pins ];
}
