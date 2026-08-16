#!/usr/bin/env bash
# Render the HTML report to PDF.
#
# Chrome rather than a Python HTML-to-PDF library: the page carries inline SVG
# charts and base64 PNG maps, and the print stylesheet in build_report.py is
# written against a real browser's pagination. Anything less faithful would need
# the report rebuilt twice, once per medium.
set -euo pipefail
cd "$(dirname "$0")/.."

HTML="${1:-data/sumo/report.html}"
PDF="${2:-data/sumo/evacuating-thanet.pdf}"

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
[ -x "$CHROME" ] || CHROME="/Applications/Chromium.app/Contents/MacOS/Chromium"
[ -x "$CHROME" ] || { echo "no Chrome or Chromium found" >&2; exit 1; }

[ -s "$HTML" ] || { echo "no report at $HTML — run sumo/build_report.py first" >&2; exit 1; }

# A dedicated profile keeps this out of the user's own Chrome session, and
# virtual-time-budget gives the base64 map images time to decode before capture.
PROFILE="$(mktemp -d)"
trap 'rm -rf "$PROFILE"' EXIT

rm -f "$PDF"

# Chrome is run detached and reaped here rather than simply waited on: headless
# regularly writes the PDF and then keeps its process group alive, which would
# hang this script indefinitely. Only this instance is killed — the profile
# directory makes it distinguishable from the user's own browser.
"$CHROME" \
  --headless=new \
  --disable-gpu \
  --no-sandbox \
  --user-data-dir="$PROFILE" \
  --no-pdf-header-footer \
  --virtual-time-budget=20000 \
  --print-to-pdf="$PWD/$PDF" \
  "file://$PWD/$HTML" >/dev/null 2>&1 &
CHROME_PID=$!

# Wait for the file to appear and stop growing, then stop Chrome.
stable=0
for _ in $(seq 1 120); do
  if [ -s "$PDF" ]; then
    size=$(wc -c < "$PDF")
    [ "$size" = "${last:-}" ] && stable=$((stable + 1)) || stable=0
    last=$size
    [ "$stable" -ge 3 ] && break
  fi
  sleep 1
done

kill "$CHROME_PID" 2>/dev/null || true
pkill -f -- "--user-data-dir=$PROFILE" 2>/dev/null || true
wait "$CHROME_PID" 2>/dev/null || true

[ -s "$PDF" ] || { echo "Chrome produced no PDF" >&2; exit 1; }
echo "wrote $PDF ($(du -h "$PDF" | cut -f1))"
