# pnix-managed. delete this line to take ownership; pnix will leave it alone.
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
