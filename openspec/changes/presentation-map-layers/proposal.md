## Why

The plan is finished but not showable. Of the fourteen exported tables only three carry geometry — `household_locations`, `muster_points` and `admin_areas` — while every number worth presenting (cars activated, occupancy, unmet demand, walking distance) lives in tables with no location at all. Anyone opening the export in a map tool has to reconstruct the joins before seeing anything.

Normalised tables are the right shape for analysis and the wrong shape for a map. This change adds a small set of denormalised, pre-joined layers built for looking at rather than querying.

## What Changes

- New `presentation-map-layers` capability producing five layers, each geometry plus the attributes needed to style it directly:
  - **`oa_summary`** (461 polygons) — population, cars owned vs activated, cars saved against baseline, people seated, unmet demand and rate, mean occupancy, median walk. One layer driving every headline choropleth.
  - **`fleet`** (70,054 points) — every car at its parking location, flagged activated or parked, with occupancy. The headline made visual: cars owned against cars departing.
  - **`walk_lines`** — straight lines from each walk-in household to the car it was assigned, showing who walks past their own street to pool.
  - **`collection_routes`** — the vehicles that detour to collect non-walkers, as ordered stops.
  - **`unmet`** (points) — people left behind, at their home locations, so failure has a geography.
- Layers are written in web-consumable formats. GeoJSON for layers small enough to load whole, FlatGeobuf for the large ones, which streams and is readable by `loaders.gl` and QGIS.
- Walk lines are additionally written as a sampled layer, because a hundred thousand overlapping lines is noise rather than a picture.

## Capabilities

### New Capabilities
- `presentation-map-layers`: denormalised, pre-joined spatial layers for visualising a completed plan, and the rule that each layer carries the attributes needed to style it without further joins.

## Impact

- Read-only over the existing warehouse; no stage of the plan changes.
- No new dependencies. DuckDB's bundled GDAL writes GeoJSON and FlatGeobuf.
- **PMTiles is not produced.** DuckDB's GDAL exposes only *layer* creation options, while the PMTiles writer takes `MINZOOM`/`MAXZOOM` as *dataset* options, so it emits a valid but empty archive — measured at 345 bytes for 461 polygons. `pmtiles convert` accepts only MBTiles, which DuckDB cannot write either. Producing real tiles needs `tippecanoe`; the command is documented rather than run, since it is not installed and is not a dependency this pipeline should acquire.
- At this scale tiling is mostly unnecessary: only the full walk-line layer exceeds what a browser will load directly.
