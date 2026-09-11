# epoch seconds -> the YYYYMMDDHHMMSS string flakes call `lastModifiedDate`.
#
# Nix computes this in C++ and there is no strftime in builtins, so it has to be
# done by hand. It cannot simply be stored in pnix's lock either: a `flake =
# false` input reached through step 3 comes from an *upstream* flake.lock, and
# that format records `lastModified` only. Real consumers read the date string
# off such inputs -- niri-nix's packages/niri.nix builds its version from
# `src.lastModifiedDate` -- so without this the derivation differs.
#
# Howard Hinnant's civil_from_days, which is exact for the whole proleptic
# Gregorian range. Nix's integer division truncates toward zero rather than
# flooring; that differs only for negative operands, and these are timestamps.
epoch:
let
  days = epoch / 86400;
  secs = epoch - days * 86400;

  z = days + 719468;
  era = z / 146097;
  doe = z - era * 146097;
  yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
  y0 = yoe + era * 400;
  doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
  mp = (5 * doy + 2) / 153;

  d = doy - (153 * mp + 2) / 5 + 1;
  m = mp + (if mp < 10 then 3 else -9);
  y = y0 + (if m <= 2 then 1 else 0);

  hh = secs / 3600;
  mm = (secs - hh * 3600) / 60;
  ss = secs - hh * 3600 - mm * 60;

  pad = n: if n < 10 then "0${toString n}" else toString n;
in
"${toString y}${pad m}${pad d}${pad hh}${pad mm}${pad ss}"
