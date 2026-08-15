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
                                                                    -> district_exits
   ... seat assignment -> vehicle_routes -> vehicle_departures -> matsim_scenario
```

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
