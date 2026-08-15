# london-frontline-2026

Synthetic population and evacuation ride-share planning for UK Local Authority
Districts. First target: Thanet District Council (LAD `E07000114`).

The pipeline generates a synthetic population from Census 2021 Output Area
marginals, places every household at a real UPRN, gives every household car a
parking spot on the nearest road — that spot **is** a muster point — and (once
complete) allocates every person to a seat for a single evacuation departure.
Output is intended to feed a traffic microsimulation comparing a
one-car-per-household baseline against a ride-shared scenario.

Planning artifacts live in `openspec/changes/thanet-evacuation-muster-planning/`.

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
<<<<<<< Updated upstream
                                                                    -> district_exits
   ... seat assignment -> vehicle_routes -> vehicle_departures -> matsim_scenario
```

=======
```

### Sending example messages to a phone

`stop_notifications` records who to contact and by which route — `direct`,
`via_carer`, or `physical_collection` for the people no channel reaches. It stops
short of the words. `london_frontline.messaging` renders those rows into the two
messages a plan actually produces (a leader's full route, a passenger's own
line of it), and `london_frontline.notify` pushes one to an [ntfy](https://ntfy.sh)
topic.

```bash
python scripts/send_example_messages.py --dry-run --database data/warehouse.duckdb
```

Each message carries a Google Maps link in its body: a dropped pin at the
pickup for a collected passenger, walking directions for a walk-in, and for the
leader a single directions link covering the parked car, every pickup in plan
order, and the destination. The links sit in the text rather than in ntfy's
`click` field, so tapping the notification opens the message and the map stays a
deliberate second tap.

Add `--topic <name>` to send. Topics on the public ntfy server are readable by
anyone who knows the name, so the sender refuses topics under 12 characters
unless you pass `--allow-short-topic`.

Every message carries an EXERCISE banner by default: these plans are built from
a synthetic population, and an unmarked evacuation order arriving on a real
phone is indistinguishable from a real one. `--no-exercise-banner` removes it.

ntfy is a demonstration transport, not a dispatch channel — a topic is a shared
secret with no recipient authentication and no delivery receipt.

Before the pipeline has been materialised there is nothing to render, so a
throwaway plan can be built in a couple of seconds instead:

```bash
python scripts/build_demo_warehouse.py
python scripts/send_example_messages.py --dry-run
```

That seeds four households in Margate — a driver whose child has no phone, a
walk-in neighbour, and two residents who cannot walk — then materialises the
real `vehicle_routes` and `stop_notifications` assets against them, so the
messages read genuine asset output rather than a fixture.

Its two collection stops are about 390 m apart, on Thanet Road and Osborne
Terrace. The live plan cannot show that: collection groups are assigned to their
nearest vehicle, so **no two-pickup route in Thanet has its stops more than
123 m apart**, and of the 55 widest, only three have both pickups at real
dwellings on different streets. The demo warehouse exists to show the shape the
tool produces, not the spread the current allocation achieves.

### When somebody does not turn up

A plan assumes everybody keeps the appointment it made for them. Two ways that
fails at the kerb: a driver reaches a door and nobody answers it, or a passenger
waits at a pickup and no car comes. `london_frontline.no_show` closes the loop
over both, and `london_frontline.no_show_web` is the page it is closed from.

```bash
python scripts/run_no_show_tool.py
```

It prints a link of each kind and serves the pages on
<http://127.0.0.1:8420>. Two rules gate every report, because a wrong one takes
a real person's seat away:

- **The wait.** `no_show_wait_minutes` (30) must have passed since the meeting
  time. Tapped earlier, the link shows a countdown and no button.
- **The phone first.** The report button stays disabled until the caller says
  the call failed. Where there is nobody to ring — a passenger with no phone and
  no carer, whom nobody has been able to tell anything — the question becomes
  whether they knocked.

Then the tool answers with a plan rather than a receipt. A driver gets their
remaining route, re-timed from now and cut around the door that failed; a
passenger gets another car if one can still reach them, and a rescue bus sent to
where they are standing if none can.

The links live in the messages themselves — one under each name on a driver's
route, one at the foot of a passenger's message:

```bash
THANET_LINK_SECRET=$(openssl rand -hex 24) python scripts/send_example_messages.py --dry-run --base-url http://127.0.0.1:8420
```

Both processes must sign with the same key, and only one process at a time may
hold the incident database open, so set `THANET_LINK_SECRET` in both while the
tool is running. A link is a bearer capability: whoever holds it can file the
report it carries. The signature only stops one link being renumbered into
another person's name — real dispatch would tie a report to an identity a
control room can challenge.

Reports go to `data/incidents.duckdb`, never to the warehouse: DuckDB permits
one writer, the pipeline is that writer, and a tool holding the lock would stop
the plan being rebuilt underneath it. The log is append-only and every revised
route is derived from it, so re-reading a link cannot book a second seat.

The plan departs at 08:00 on 1 January 2026, so `--now` pins the tool's clock
onto the plan's timeline; it defaults to a moment where the links are live.

**What it shows about the plan.** With the whole fleet leaving at once, no car
can be diverted to a stranded passenger: by the time a 30-minute wait is up,
every other car has been on the road for almost that long, so the answer is
always the bus. Raise `--divert-grace-minutes` to see the other branch, or stage
the departures — the same funnel the SUMO run found.

>>>>>>> Stashed changes
## Inspecting the warehouse

Everything lands in `data/warehouse.duckdb` (gitignored — it is reproducible from
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
writer or one or more readers, never both — so an attached reader, even a
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

<<<<<<< Updated upstream
## The traffic microsimulation scenario

`matsim_scenario` writes a MATSim scenario to `data/exports/matsim/`:

| File | Contents |
| --- | --- |
| `population.xml.gz` | one plan per seated person — 131,420 of them |
| `vehicles.xml.gz` | the departing fleet, one entry per car on the road |
| `households.xml.gz` | households and the cars among them that depart |
| `config.xml` | minimal runnable config, declaring EPSG:27700 |

Drivers get a `car` leg; passengers get a `ride` leg, which MATSim teleports. So
the number of vehicles entering the network is exactly the number of cars that
depart — the quantity the whole ride-share comparison rests on. Note that a
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
several links — `way_position_m` says which one, being the distance from the
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
=======
## Traffic microsimulation (SUMO)

`sumo/` runs the comparison the pipeline exists to feed: the ride-shared plan
against a one-car-per-household baseline, on a real road network.

SUMO ships as pip wheels, so no system install is needed:

```bash
.venv/bin/python -m pip install eclipse-sumo sumolib traci matplotlib
```

Then, with the warehouse materialised:

```bash
curl -sS --max-time 900 -X POST --data-binary @sumo/overpass.ql \
  https://overpass-api.de/api/interpreter -o data/sumo/thanet.osm.xml
./sumo/build_network.sh
.venv/bin/python sumo/make_trips.py baseline
.venv/bin/python sumo/make_trips.py pooled
./sumo/run_scenario.sh baseline
./sumo/run_scenario.sh pooled
.venv/bin/python sumo/analyse.py
.venv/bin/python sumo/render_map.py
.venv/bin/python sumo/build_report.py
```

The two scenarios share one network, one seeded mobilisation curve and one
destination, so the only difference between them is which cars depart.

### What it found

Both plans gridlock the district. An hour after the alarm each is moving at 2%
of the speed limit with about four-fifths of cars stationary. The ride-shared
plan does put 2,385 fewer cars on the road (5.2%) and carries 24,038 more people
— the baseline strands every household that owns no car — but neither changes
the jam.

Two things matter more than the fleet size, and both are worth fixing before
this comparison is run again:

- **The single destination.** Every vehicle routes to the one Canterbury point
  in `PlanningConfig`, so the whole district drains through the same few
  arterials and only ~5,400 of 27,000 road links carry any traffic at all. That
  funnel, not the number of cars, decides the outcome.
- **Simultaneous departure.** `fleet_departure_time` is a single instant.
  Released that way the district seizes completely under *both* plans — 3% of
  the speed limit, ~35,000 cars stationary, 49 of 45,960 arrived after 20
  minutes. The default run therefore spreads departures over a 60-minute
  mobilisation curve; `--instant` reproduces the literal reading.

The 5.2% saving is itself the point `minimise-evacuation-vehicles` makes: the
current tiered assignment puts every car-owning household in its own car, so it
was never going to take many cars off the road. Re-running this against that
change's target of ~25,600 cars is the comparison that would actually test the
ride-share hypothesis.
>>>>>>> Stashed changes

## Known limitations

These are real and recorded, not oversights:

- **Communal establishments are absent.** The population is the *household*
  population, so it sits ~1.4% below the published resident total. Care-home and
  student-hall residents belong to no household — and they are exactly the people
  most likely to need home collection.
- **24% of under-16s have no qualifying co-resident parent.** Attributes are
  paired independently across marginals, so a household's ages are unrelated to
  its composition type. Those children currently form travel groups of one.
  Recorded in `children_without_co_resident_parent`.
- **Snap distances are inflated by OSM gaps.** Many residential access roads,
  service roads and driveways are unmapped, so ~19,000 cars park more than 20 m
  from their home. No vehicle is dropped for this; the distance is reported.
<<<<<<< Updated upstream

### Limitations of the microsimulation scenario

These bear directly on any clearance time quoted from a run, and should travel
with the figure:

- **Exits are infinite-capacity sinks.** The evacuation is scoped to clearing the
  district, because the road network only covers the district — Canterbury is
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
- **The mobilisation curve is provisional.** Its shape is decided — a lognormal
  delay after notification, since the tail governs clearance — but
  `mobilisation_median_minutes` and `mobilisation_sigma` need grounding in
  evacuation response literature. The current 15-minute median spreads departures
  over about 143 minutes. Recalibrating changes no code.
- **Collection-stop dwell is provisional too**, at 120 s.
- **No background traffic.** The scenario contains evacuating vehicles only.
=======
>>>>>>> Stashed changes
