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
