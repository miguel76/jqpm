# Example of what a jqpm-compatible package looks like.
# File must be named <repo-name>.jq and live at the repo root.
# Tag releases with git tags like v1.0.0.

def titlecase:
  [splits(" ")]
  | map(if length > 0 then (.[0:1] | ascii_upcase) + .[1:] else . end)
  | join(" ");

def truncate($n):
  if (length > $n) then .[0:$n] + "…" else . end;
