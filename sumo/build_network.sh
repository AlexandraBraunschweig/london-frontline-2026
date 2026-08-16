#!/usr/bin/env bash
# Convert the OSM extract into a SUMO network for the Thanet -> Canterbury
# evacuation corridor. Only passenger-drivable roads are kept; the evacuation
# fleet is all cars.
set -euo pipefail
cd "$(dirname "$0")/.."

VENV="${VENV:-.venv}"
export SUMO_HOME="$PWD/$VENV/lib/python3.11/site-packages/sumo"
OSM="data/sumo/thanet.osm.xml"
NET="data/sumo/thanet.net.xml"

"$VENV/bin/netconvert" \
  --osm-files "$OSM" \
  --type-files "$SUMO_HOME/data/typemap/osmNetconvert.typ.xml" \
  --output-file "$NET" \
  --geometry.remove \
  --roundabouts.guess \
  --ramps.guess \
  --junctions.join \
  --tls.guess-signals --tls.discard-simple --tls.join \
  --keep-edges.by-vclass passenger \
  --remove-edges.isolated \
  --no-turnarounds.tls \
  --offset.disable-normalization false \
  --verbose

ls -lh "$NET"
