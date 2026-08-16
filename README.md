# Project Thanet

**Fewer cars, no one left behind.** Synthetic population and evacuation
ride-share planning for UK Local Authority Districts, first targeted at Thanet
District Council (LAD `E07000114`).

Built at the Frontline London 2026 hackathon by Alexandra B & Ed K.

## The problem

When a district has to empty at once, history says three things go wrong:

- **Gridlock.** Everyone leaves by car at the same moment, and the queue stops
  moving while the danger keeps coming: people have died in wildfire
  evacuations stuck in traffic trying to leave. Too many cars is itself the
  hazard.
- **People left behind.** A household without a car has no way out, and the
  carless are disproportionately the elderly and vulnerable: exactly the
  people who most need collecting.
- **Roads blocked for responders.** Emergency services need the streets empty
  as fast as possible so they can drive *into* the area while everyone else
  gets out.

The plan examined here answers all three at once: put fewer, fuller cars on
the road. Use census data to decide who rides with whom, keeping families
together and prioritising vulnerable groups, so the fleet shrinks, the
carless get seats, and the network stays moving.

## What the simulation shows

One hour after notification, on the same road network with the same seeded
mobilisation:

| | One car per household | Ride-shared plan |
| --- | --- | --- |
| Cars on the road | 45,919 | 26,557 (42% fewer) |
| People across the district boundary | 9,197 | 16,144 (76% more) |
| Gridlocked roads | 4,096 | 2,434 |

At a mean of 4.95 people per car, the plan also carries 20,682 more people in
total, because a household with no car has no way out of the baseline at all.
The full findings, and what now limits clearance instead of the fleet, are in
[What it found](#what-it-found).

## What the pipeline does

The pipeline generates a synthetic population from Census 2021 Output Area
marginals, places every household at a real UPRN, and gives every household car
a parking spot on the nearest road; that spot **is** a muster point. Every
person is then allocated a seat for a single evacuation departure. The output
feeds two traffic microsimulation backends (MATSim and SUMO) comparing a
one-car-per-household baseline against the ride-shared scenario.

Planning artifacts live in `openspec/changes/thanet-evacuation-muster-planning/`.

## The slide deck

`deck/index.html` is a reveal.js deck on what the pipeline does and what it
enables. Open the file directly: reveal.js is vendored, so it needs no server
and no install. Its figures are rendered from the warehouse by
`uv run python deck/make_figures.py`. See `deck/README.md`.

## Setup

Python is pinned to 3.11 because `pandana` publishes no wheels beyond cp311.

```bash
uv sync --extra dev
```

## Running it

### Tests

Unit tests run anywhere. The warehouse tests skip automatically until the
pipeline has been materialised.

```bash
uv run pytest -q
```

### The Dagster UI

```bash
uv run dagster dev
```

Then open <http://127.0.0.1:3000>. The asset graph shows the full lineage; click
any asset to see its materialisation metadata (row counts, snap-distance
distributions, marginal gaps). "Materialize all" runs the whole pipeline.

### Individual assets from the CLI

```bash
uv run dagster asset materialize -m london_frontline.definitions --select admin_areas
uv run dagster asset materialize -m london_frontline.definitions --select "vehicles,muster_points"
```

Assets run in a single process on purpose: every asset writes to the one DuckDB
file and DuckDB permits a single writer, so the default multiprocess executor
would deadlock on the file lock.

### Order to materialise from scratch

```
admin_areas -> admin_areas_geojson
            -> census_marginals -> census_marginals_resolved
                                -> synthetic_population -> population_validation
                                                        -> person_relationships -> travel_groups
            -> dwelling_points
                 (with synthetic_population) -> household_locations -> vehicles
            -> road_centrelines                                     -> muster_points
                                                                    -> district_exits
   ... seat assignment -> vehicle_routes -> vehicle_departures -> matsim_scenario
```

## Inspecting the warehouse

Everything lands in `data/warehouse.duckdb` (gitignored; it is reproducible from
a run). Always connect **read-only**:

```python
import duckdb
con = duckdb.connect("data/warehouse.duckdb", read_only=True)
con.execute("SHOW TABLES").fetchdf()
```

Or from the DuckDB CLI:

```bash
duckdb -readonly data/warehouse.duckdb
```

**Close your session before running the pipeline.** DuckDB allows either one
writer or one or more readers, never both, so an attached reader, even a
read-only one, blocks Dagster from writing and the run fails with:

```
IO Error: Could not set lock on file "data/warehouse.duckdb":
Conflicting lock is held in .../duckdb (PID nnnnn)
```

That includes the DuckDB UI (`duckdb -readonly ... -ui`), which holds the file
until you quit it. Nothing is corrupted; the run simply cannot start. Quit the
reader and re-run.

### Sanity checks worth running

```sql
-- Population totals
SELECT count(*) FROM persons;              -- ~138,700
SELECT count(*) FROM households;           -- ~62,100
SELECT count(*) FROM vehicles;             -- ~70,100

-- Does the synthesis match the Census? Household counts must be exact.
SELECT count(*) FILTER (WHERE households_gap <> 0) AS should_be_zero,
       sum(persons_generated), sum(persons_published)
FROM population_validation;

-- Seat supply against demand
SELECT (SELECT sum(capacity) FROM muster_points) AS seats,
       (SELECT count(*) FROM persons)            AS people;

-- How far from a mapped road did cars end up parking?
SELECT round(median(snap_distance_m), 1) AS median_m,
       round(quantile_cont(snap_distance_m, 0.95), 1) AS p95_m,
       count(*) FILTER (WHERE snap_distance_m > 20) AS beyond_review_threshold
FROM muster_points;

-- Travel groups: families that must not be split.
-- ("groups" is a reserved word in DuckDB, hence group_count.)
SELECT count(*) AS group_count,
       count(*) FILTER (WHERE contains_dependent) AS with_dependent,
       count(*) FILTER (WHERE NOT all_can_walk)   AS home_collection,
       max(group_size) AS largest
FROM travel_groups;

-- Known weakness: children with no plausible co-resident parent
SELECT count(*) FROM children_without_co_resident_parent;
```

### Looking at it on a map

`data/exports/output_areas_E07000114.geojson` opens in any GIS viewer or
<https://geojson.io>. To eyeball parked cars, export a sample:

```sql
COPY (SELECT vehicle_id, snap_distance_m, geometry FROM muster_points USING SAMPLE 2000 ROWS)
TO 'data/exports/muster_sample.geojson' WITH (FORMAT GDAL, DRIVER 'GeoJSON');
```

## The MATSim scenario

`matsim_scenario` writes a MATSim scenario to `data/exports/matsim/`:

| File | Contents |
| --- | --- |
| `population.xml.gz` | one plan per seated person: 131,420 of them |
| `vehicles.xml.gz` | the departing fleet, one entry per car on the road |
| `households.xml.gz` | households and the cars among them that depart |
| `config.xml` | minimal runnable config, declaring EPSG:27700 |

Drivers get a `car` leg; passengers get a `ride` leg, which MATSim teleports. So
the number of vehicles entering the network is exactly the number of cars that
depart: the quantity the whole ride-share comparison rests on. Note that a
driver making collection stops has one `car` leg *per hop*, so legs exceed
vehicles by the number of collection stops.

### Building the network

The pipeline does **not** build `network.xml.gz`. Plans carry British National
Grid coordinates, and MATSim assigns activities to links itself, so the scenario
loads against a network built separately from the same OpenStreetMap data:

```bash
# Using pt2matsim, or matsim-libs' own OSM reader
java -cp pt2matsim.jar org.matsim.pt2matsim.run.Osm2MultimodalNetwork \
     osm-input.osm network.xml.gz EPSG:27700
```

Build it from an extract covering the same area as `road_centrelines`, in
EPSG:27700 so it agrees with the plans.

### Resolving locations to network links

Every location that must bind to a link is published by OpenStreetMap way rather
than by link id, in `scenario_network_refs`:

```sql
SELECT reference_type, reference_id, way_id, way_position_m, easting, northing
FROM scenario_network_refs;
```

Link ids are assigned when the network is built and differ between readers and
between builds, so a scenario keyed on them would break every time the network
was rebuilt. Way ids are stable across both. MATSim's OSM readers keep the source
way id on each link, and because MATSim splits ways at junctions, one way maps to
several links; `way_position_m` says which one, being the distance from the
start of the way. Coordinates are published alongside so a location can fall back
to nearest-link matching if a way is missing from the network build; the count of
references that fail to resolve is recorded in `scenario_shortfalls` rather than
raised.

### Running it

```bash
java -Xmx8g -cp matsim.jar org.matsim.run.Controler data/exports/matsim/config.xml
```

The emitted config is deliberately minimal. Scoring parameters, replanning
strategies and iteration counts are the simulation operator's business, not the
pipeline's.

### Reading the result

`tripinfo` output gives per-vehicle travel time and delay, which is the
comparison metric between the one-car-per-household baseline and the pooled
scenario. Run both against the same network with the same seed and departure
profile, or the comparison measures the configuration rather than the pooling.

## Traffic microsimulation (SUMO)

`sumo/` runs the comparison the pipeline exists to feed: the ride-shared plan
against a one-car-per-household baseline, on a real road network.

SUMO ships as pip wheels, so no system install is needed:

```bash
.venv/bin/python -m pip install eclipse-sumo sumolib traci matplotlib pillow
```

Then, with the warehouse materialised, one command does everything:

```bash
.venv/bin/python sumo/run_all.py
```

It downloads the OSM extract, builds the network, generates both fleets, runs
both simulations, and writes:

| Output | What it is |
| --- | --- |
| `data/sumo/report.html` | the full comparison, one page |
| `data/sumo/evacuating-thanet.pdf` | the same thing, paginated for print |
| `data/sumo/figures/` | every chart as SVG, both maps as PNG, the table as CSV |
| `data/sumo/frames/` | one side-by-side map per simulated minute |
| `data/sumo/timelapse.gif` | those frames animated, minute 0 to minute 60 |
| `data/sumo/out/*.xml` | the raw SUMO record: summary, tripinfo, per-minute edge data |

Stages cost very different amounts: the simulations are tens of minutes, the
charts are seconds. They can therefore be run separately:

```bash
.venv/bin/python sumo/run_all.py --from analyse   # reuse the runs, redo the output
.venv/bin/python sumo/run_all.py --only timelapse # just re-render the animation
.venv/bin/python sumo/run_all.py --sim-end 7200   # simulate two hours instead of one
```

Every stage is also a script in its own right (`make_trips.py`,
`run_scenario.sh`, `analyse.py`, `render_map.py`, `figures.py`, `timelapse.py`,
`make_meta.py`, `build_report.py`, `export_pdf.sh`) if you want to drive one
directly.

### How the comparison is kept fair

Both runs share one network, one seeded mobilisation draw and the same district
exits, so the only difference is which cars depart and who is aboard. The pooled
scenario reads its departures and exits straight from `vehicle_departures`; the
baseline is built in `make_trips.py`, since a one-car-per-household fleet is not
a plan the pipeline produces.

Both simulations stop on the clock rather than when the network empties:
neither clears Thanet in any reasonable time, and a run left to finish would be
compared against one that had simply been given longer. Clearance percentiles
are measured against everyone who set off, not against the subset that happened
to arrive; against the arrived subset they read like clearance times while
describing only the fastest few per cent.

### What it found

Fleet minimisation works, and it shows on the road. Against a one-car-per-household
baseline of 45,919 cars, the plan activates **26,557**, 42% fewer, at a mean of
4.95 people per car, and still carries 20,682 more people, because a household with
no car has no way out of the baseline at all. One hour after notification it has moved
**16,144 people across the district boundary against the baseline's 9,197**, 76% more,
with 2,434 roads gridlocked rather than 4,096, and roughly half the teleports.

Neither run clears the district within the hour, and the reason is no longer the
fleet:

- **One exit carries everything.** Vehicles head for the nearest of Thanet's four
  exits, which sends **90% of them through Ramsgate Road** while Thanet Way takes 579.
  With the fleet already near its packing floor, this is the binding constraint:
  emptier cars cannot widen a single road. Choosing exits by expected travel time, or
  balancing across them, is the next thing worth changing.
- **Simultaneous departure is still fatal.** Released at one instant rather than on
  the mobilisation curve, an earlier run gridlocked the district under both plans: 3%
  of the speed limit, 49 of 45,960 arrived after 20 minutes.

Watch `data/sumo/timelapse.gif` for the hour in one minute: the jam spreads out of
Ramsgate and Margate along the same arterials in both scenarios, and the difference
between them is how much of the network is still green when it does.

## Known limitations

These are real and recorded, not oversights:

- **Communal establishments are absent.** The population is the *household*
  population, so it sits ~1.4% below the published resident total. Care-home and
  student-hall residents belong to no household, and they are exactly the people
  most likely to need home collection.
- **24% of under-16s have no qualifying co-resident parent.** Attributes are
  paired independently across marginals, so a household's ages are unrelated to
  its composition type. Those children currently form travel groups of one.
  Recorded in `children_without_co_resident_parent`.
- **Snap distances are inflated by OSM gaps.** Many residential access roads,
  service roads and driveways are unmapped, so ~19,000 cars park more than 20 m
  from their home. No vehicle is dropped for this; the distance is reported.

### Limitations of the microsimulation scenario

These bear directly on any clearance time quoted from a run, and should travel
with the figure:

- **Exits are infinite-capacity sinks.** The evacuation is scoped to clearing the
  district, because the road network only covers the district: Canterbury is
  about 9 km beyond where `road_centrelines` stops. Vehicles leaving at the
  boundary never queue on the A299 beyond it, so **clearance time is optimistic,
  most so in the tail**, where the real bottleneck would be downstream. Modelling
  it needs a network extended past the boundary.
- **Vehicles head for their nearest exit.** There is no destination-choice model,
  so demand concentrates more than real drivers choosing by travel time would
  allow: 90% of Thanet's fleet is assigned to the Ramsgate Road corridor. The
  loading per exit is reported by `vehicle_departures` for exactly this reason.
- **Co-located crossings are merged.** One corridor rarely crosses a boundary
  once, so crossings within `exit_cluster_radius_m` are collapsed and represented
  by their most major road. Without this, Thanet's four Ramsgate Road crossings
  (all within 292 m) split the corridor and put 90% of the fleet onto a *tertiary*
  way while a trunk way 250 m away took seven vehicles. `crossing_count` on each
  exit says how many crossings it stands for, so over-merging is visible.
- **The mobilisation curve is provisional.** Its shape is decided (a lognormal
  delay after notification, since the tail governs clearance), but
  `mobilisation_median_minutes` and `mobilisation_sigma` need grounding in
  evacuation response literature. The current 15-minute median spreads departures
  over about 143 minutes. Recalibrating changes no code.
- **Collection-stop dwell is provisional too**, at 120 s.
- **No background traffic.** The scenario contains evacuating vehicles only.
