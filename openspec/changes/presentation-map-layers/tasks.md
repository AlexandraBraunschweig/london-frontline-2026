## 1. Layers

- [x] 1.1 Build `oa_summary`: Output Area polygons joined to population, households, cars owned, cars activated, cars saved vs baseline, people seated, unmet, unmet rate, mean occupancy and median walk
- [x] 1.2 Build `fleet`: every vehicle at its parking point with activation state, occupancy, capacity and owner household
- [x] 1.3 Build `walk_lines`: a line from each walk-in household's home to its assigned vehicle, with distance and whether it is the household's own car
- [x] 1.4 Build `collection_routes`: ordered stops for vehicles that collect non-walkers, with meeting times
- [x] 1.5 Build `unmet`: one point per unseated person at their household location, carrying the reason

## 2. Export

- [x] 2.1 Write each layer as GeoJSON where it is small enough to load whole, FlatGeobuf otherwise
- [x] 2.2 Write a sampled walk-line layer, since the full set is too dense to read
- [x] 2.3 Report feature count and file size per layer
- [x] 2.4 Document the `tippecanoe` command for producing PMTiles, and why the pipeline does not produce them itself

## 3. Verification

- [x] 3.1 Test that every layer carries geometry and is non-empty
- [x] 3.2 Test that the fleet layer contains both activated and parked vehicles
- [x] 3.3 Test that oa_summary totals reconcile with the plan's own reports

## 4. Arc rendering

- [x] 4.1 Write `arc_walks`: flat origin-destination rows with source/target coordinates, walk distance and whether it is the household's own car
- [x] 4.2 Write `arc_flows_oa`: Output Area to Output Area flows between centroids, weighted by people, for a district-scale view
- [x] 4.3 Write arc data as JSON for drop-in use and Parquet for size
- [x] 4.4 Document the ArcLayer configuration, including that `getHeight` must be set low because the default renders 41 m walks as tall loops

## 5. Schematic animation

- [x] 5.1 Write `trips_walks`: home-to-vehicle paths with nominal walk timings
- [x] 5.2 Write `trips_vehicles`: muster point, collection stops and the destination leg the plan never modelled, at a stated assumed speed
- [x] 5.3 Flag every row schematic and record the health warning alongside the export
- [x] 5.4 Test that waypoints and timestamps agree in length and order, and that every trip ends at the destination

## 6. Viewer

- [x] 6.1 Vite + React + TypeScript app under `viz/`, reading the exports through a symlink so a re-run needs no copy step
- [x] 6.2 deck.gl layers: Output Area choropleth with switchable metric, pooling arcs, unmet points, and animated walk and vehicle trips
- [x] 6.3 Headline figures, legend, hover tooltips, and play/scrub controls
- [x] 6.4 Pin `@deck.gl/*` to 9.2.x: 9.3.10 declares type definitions it does not ship
- [x] 6.5 Show the schematic warning whenever the animation layer is on

## 7. Hosting

- [x] 7.1 `web_bundle` asset writing a trimmed copy of the viewer's data into `viz/public/data`, rounded to 6 decimal places and with the walk animation sampled
- [x] 7.2 Commit that directory, anchoring the `/data/` ignore rule to the repository root so it does not also match the viewer's copy
- [x] 7.3 Relative asset base in Vite, so the build works under a project-pages subpath
- [x] 7.4 GitHub Actions workflow building `viz/` and deploying to Pages on push
