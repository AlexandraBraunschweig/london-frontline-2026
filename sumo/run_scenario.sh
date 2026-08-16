#!/usr/bin/env bash
# Route and simulate one evacuation scenario, recording both the time series and
# the per-edge congestion needed to draw the jam on a map.
#
# Routes are free-flow shortest paths computed before departure and never
# revised: nobody is told where the jams are, which is the right assumption for
# a no-warning evacuation. The jam is therefore an outcome, not an input.
set -euo pipefail
cd "$(dirname "$0")/.."

SCENARIO="$1"
VENV="${VENV:-.venv}"
export SUMO_HOME="$PWD/$VENV/lib/python3.11/site-packages/sumo"

NET="data/sumo/thanet.net.xml"
TRIPS="data/sumo/${SCENARIO}.trips.xml"
ROUTES="data/sumo/${SCENARIO}.rou.xml"
OUT="data/sumo/out"
mkdir -p "$OUT"

echo "== routing $SCENARIO"
"$VENV/bin/duarouter" \
  --net-file "$NET" \
  --route-files "$TRIPS" \
  --output-file "$ROUTES" \
  --ignore-errors \
  --routing-threads 4 \
  --no-step-log

# One aggregation window per minute, so the whole hour can be replayed frame by
# frame rather than only sampled. excludeEmpty keeps it to roads actually
# carrying traffic; at ~6,000 of those the file still runs to ~100 MB.
FREQ="${EDGEDATA_FREQ:-60}"
cat > "data/sumo/${SCENARIO}.edgedata.add.xml" <<XML
<additional>
  <edgeData id="cong" freq="${FREQ}" file="${SCENARIO}.edgedata.xml"
            excludeEmpty="true" minSamples="1"/>
</additional>
XML

echo "== simulating $SCENARIO"
# time-to-teleport is deliberately long rather than off: real gridlock should
# show up as delay, but a permanently deadlocked junction would otherwise stall
# the whole run. Teleports are counted and reported as a gridlock signal.
#
# max-depart-delay is unlimited so that no vehicle is ever silently dropped for
# failing to find a gap — a discarded car would flatter whichever scenario was
# more congested.
# The run ends on the clock, not when the network empties. Neither scenario
# clears Thanet in any reasonable time, and stopping both at the same simulated
# instant is what makes them comparable — a run left to finish would be compared
# against one that had simply been given longer. It also lets SUMO close its own
# output files instead of being killed.
END="${SIM_END:-3600}"
"$VENV/bin/sumo" \
  --net-file "$NET" \
  --route-files "$ROUTES" \
  --additional-files "data/sumo/${SCENARIO}.edgedata.add.xml" \
  --begin 0 --end "$END" --step-length 1 \
  --time-to-teleport 600 \
  --max-depart-delay -1 \
  --summary-output "$OUT/${SCENARIO}.summary.xml" \
  --tripinfo-output "$OUT/${SCENARIO}.tripinfo.xml" \
  --statistic-output "$OUT/${SCENARIO}.stats.xml" \
  --duration-log.statistics \
  --no-step-log --no-warnings \
  --ignore-route-errors

# edgeData writes relative to the additional file's directory.
mv -f "data/sumo/${SCENARIO}.edgedata.xml" "$OUT/${SCENARIO}.edgedata.xml"

echo "== done $SCENARIO"
ls -lh "$OUT/${SCENARIO}".*.xml
