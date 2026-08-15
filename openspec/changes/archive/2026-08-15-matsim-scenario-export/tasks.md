## 1. Muster point way identity

- [x] 1.1 Capture the pre-change baseline: export `muster_point_id, easting, northing, snap_distance_m` from the current warehouse to a fixture file, so the re-materialisation in 1.4 can be verified against it
- [x] 1.2 Carry `way_id` through the snap in `assets/vehicles.py` — it is already in scope in the `nearest` CTE at line 206 and dropped by the `SELECT` at line 208
- [x] 1.3 Compute the position along the way for each muster point (distance from the way's start to the snapped point) and record it alongside `way_id`
- [x] 1.4 Re-materialise `vehicles` onward and assert every parking coordinate and snap distance matches the 1.1 baseline exactly
- [x] 1.5 Test: interpolating each muster point's recorded way at its recorded position reproduces its recorded coordinate, within a tight tolerance, over a sample of the fleet
- [x] 1.6 Test: every muster point's `way_id` exists in `road_centrelines`, and no muster point has a null way reference

## 2. Configuration

- [x] 2.1 Add exit qualification config: the set of through-road highway classes, and the minimum length a way must continue beyond the district boundary
- [x] 2.2 Add mobilisation config: notification time, lognormal delay median and spread, and a switch to ignore driver access time (the degenerate simultaneous-departure case)
- [x] 2.3 Add collection-stop dwell time config, with the provisional value documented as provisional
- [x] 2.5 Add exit clustering radius config, and document that `exit_highway_classes` is ordered most-major-first because it doubles as the within-cluster preference order
- [x] 2.4 Test: configuring the mobilisation distribution with no spread and driver access time ignored yields every vehicle departing at the notification time

## 3. District exits

- [x] 3.1 New `assets/scenario.py` with a `district_exits` asset: intersect `road_centrelines` with the LAD boundary, measure length inside and outside per way, and qualify by class and minimum length outside
- [x] 3.2 Record per exit: crossing coordinate, `way_id`, position along the way, highway class, and the length beyond the boundary that qualified it
- [x] 3.3 Emit a shortfall record and fail the scenario export if no exit qualifies for the district
- [x] 3.4 Test: the Thanet fixture qualifies Island Road, both Thanet Way ways, both trunk Ramsgate Road ways, Old Road, the tertiary Ramsgate Road and Plucks Gutter, and rejects both Royal Harbour Approach ways
- [x] 3.5 Test: every `service`-class boundary crossing is rejected regardless of its length beyond the boundary
- [x] 3.6 Test: a way crossing the boundary with less than the configured minimum length outside is rejected
- [x] 3.7 Asset metadata reports the qualifying exits with their class and length beyond the boundary
- [x] 3.8 Pure clustering module: single-linkage grouping of crossings within the radius, representative chosen by most major road class
- [x] 3.9 Apply clustering in `district_exits`, recording how many crossings each exit represents
- [x] 3.10 Test: crossings within the radius collapse to one exit; crossings beyond it stay separate; linkage is transitive
- [x] 3.11 Test: a cluster of mixed road classes is represented by a crossing of the most major class, and the representative keeps a resolvable way reference
- [x] 3.12 Test: Thanet's four Ramsgate Road crossings collapse to one trunk-represented exit

## 4. Departure times

- [x] 4.1 `vehicle_departures` asset: one row per departing vehicle with its departure time, the mobilisation delay drawn, and the driver access time applied
- [x] 4.2 Draw the mobilisation delay from the configured lognormal, seeded from `random_seed` so runs are reproducible
- [x] 4.3 Source driver access time from `person_walks` for the leader–muster-point pair, defaulting to zero where the leader is already at the vehicle
- [x] 4.4 Test: two runs with the same seed and config produce identical departure times for every vehicle
- [x] 4.5 Test: a leader from the owner household parked at its own kerb gets a departure time of notification plus mobilisation delay alone
- [x] 4.6 Test: departure times are never earlier than the configured notification time
- [x] 4.7 Asset metadata reports the departure distribution and the span from first to last departure

## 5. Exit assignment

- [x] 5.1 Assign each departing vehicle the exit nearest its final stop, recorded in `vehicle_departures`
- [x] 5.2 Test: a vehicle whose final stop is nearest a given exit is assigned that exit, over a constructed fixture with several exits
- [x] 5.3 Report vehicles and people assigned per exit, so proximity-assignment concentration is visible
- [x] 5.4 Re-materialise and confirm the corridor concentration now lands on the corridor's major road, not a tertiary crossing beside it

## 6. MATSim scenario emission

- [x] 6.1 `scenario_network_refs` table: one row per referenced muster point and exit, carrying `way_id`, position along the way, and BNG coordinate
- [x] 6.2 Streaming gzipped writer for MATSim `population.xml.gz` — incremental, not a document tree, at 134k persons
- [x] 6.3 Emit walk-in passenger plans: home activity, walk leg to the muster point, `ride` leg to the assigned exit
- [x] 6.4 Emit driver plans: home activity, walk leg to the muster point, `car` leg with intermediate collection activities in `route_stops` order, ending at the assigned exit
- [x] 6.5 Emit home-collection passenger plans: home activity from which the journey begins at the vehicle's scheduled arrival, then a `ride` leg to the exit — no walk to the muster point
- [x] 6.6 Emit `households.xml.gz` and `vehicles.xml.gz` for every household and vehicle referenced by a plan
- [x] 6.7 Emit a minimal runnable `config.xml` declaring EPSG:27700 as the coordinate reference system
- [x] 6.8 Test: exactly one driver per departing vehicle, and no passenger plan contains a `car` leg (car *legs* exceed vehicles by one per collection stop, so drivers are the invariant)
- [x] 6.9 Test: every person in `person_seats` has exactly one plan, and no person outside it appears
- [x] 6.10 Test: activity and leg times are non-decreasing within every plan, over the full emitted population
- [x] 6.11 Test: every vehicle, household and person id referenced by a plan is defined in the emitted scenario
- [x] 6.12 Test: a driver whose vehicle makes collection stops visits them in `route_stops` order, between the muster point and the exit
- [x] 6.13 Test: every emitted document parses and conforms structurally (DTD validation needs lxml plus vendored DTDs — deferred, and stated as such in the test)
- [x] 6.14 Test: no emitted element carries a simulator-specific network link identifier

## 7. Reporting and wiring

- [x] 7.1 Report people excluded from the scenario for holding no seat, reconciled against `unmet_demand`
- [x] 7.2 Report the count of muster points and exits whose way reference could not be resolved, as a shortfall rather than an exception
- [x] 7.3 Register the new assets in `definitions.py` and add the new tables to the parquet export list in `assets/reporting.py`
- [x] 7.4 Test: the scenario export asset fails loudly, with the district identified, when no exit qualifies

## 8. Documentation

- [x] 8.1 README section: how to build the MATSim network from OSM, resolve way references to link ids, and run the emitted scenario
- [x] 8.2 README: record the exit-sink and nearest-exit limitations wherever a clearance figure would be quoted
- [x] 8.3 Correct the downstream simulator named in `openspec/config.yaml` from UDST MANTA to MATSim, with the reason
