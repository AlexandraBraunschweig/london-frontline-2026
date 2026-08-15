## Why

The pipeline exists to feed a traffic microsimulation, but nothing it currently produces can be loaded into one. The plan is a static allocation: who sits in which car, and where that car is parked. It has no network identity, no destination inside any modelled network, and no departure times worth simulating.

Three specific gaps block it:

- **Muster points carry no road identity.** `muster_points` snaps every vehicle to its nearest road segment — the join at `vehicles.py:206` has `road_segments.way_id` in hand — and then discards it, keeping only the snapped coordinate. Any simulator adapter must therefore re-snap 70,054 points against a network the pipeline already matched them to, and has no way to check its answer against the one the pipeline computed.
- **The destination is outside the modelled area.** `destination_latitude/longitude` is Canterbury, roughly 9 km west of where `road_centrelines` stops. Every route in `vehicle_routes` ends at a point on no network.
- **Every vehicle departs at the same instant.** `fleet_departure_time` is a single timestamp applied fleet-wide. Loading 26,577 simultaneous departures into a microsimulation measures the insertion queue, not the road network — the result would be an artifact of the departure assumption rather than a finding about evacuation.

Fixing these makes the existing plan simulable without changing a single allocation decision.

## What Changes

- **Muster points retain the road they were snapped to.** `way_id` and the distance along that way are recorded alongside the coordinate. No new computation — both are available in the snap that already runs.
- **A new derived set of district exits replaces the single external destination.** Exits are the points where drivable roads cross the LAD boundary, filtered to through-road classes with a configured minimum length outside the district. In Thanet this yields a handful of real exits (Thanet Way, Island Road, Ramsgate Road, Old Road, Plucks Gutter) and correctly rejects coastal artifacts such as the two Royal Harbour Approach ways, which cross the boundary by 29 m and 8 m at the harbour. The evacuation is thereby scoped to **clearing the district**, which the existing network can support, rather than the journey to Canterbury, which it cannot.
- **A departure-time profile replaces the fleet-wide instant.** Each departing vehicle gets a departure time drawn from a configured mobilisation distribution, offset by the time its driver needs to reach the car. The existing single-instant behaviour remains reachable as the degenerate configuration.
- **A new export asset emits a MATSim scenario**: `population.xml` (one plan per person), `households.xml`, `vehicles.xml`, and a mapping table binding muster points and exits to their OSM way identities. Drivers get a car leg; passengers get a `ride` leg, so exactly one vehicle per departing car enters the network.
- `destination_name`, `destination_latitude` and `destination_longitude` become unused by the scenario export. They are retained for the leader-facing route output, where naming a real town is still the useful thing to tell a driver.

### Explicitly deferred

- **No network build.** The pipeline emits plans in British National Grid coordinates plus OSM way references; converting OSM to a MATSim `network.xml` is a MATSim-side step (its own OSM reader) and is out of scope. The export is documented against that step, not a substitute for it.
- **No destination choice model.** Each vehicle is assigned its nearest exit. Real drivers choose by expected travel time, and a nearest-exit rule will over-concentrate demand at the closest exit; this is recorded as a limitation, not modelled.
- **No background traffic.** The scenario contains evacuating vehicles only. Ambient non-evacuating trips are a later addition.
- **No MATSim config tuning.** A minimal runnable `config.xml` is emitted; calibration of scoring, replanning strategies and iteration counts belongs to whoever runs the simulation.
- **No SUMO or MANTA adapter.** The mapping table is deliberately simulator-neutral so those adapters stay thin, but neither is built here.

## Capabilities

### New Capabilities
- `microsimulation-scenario-export`: derivation of district exits, assignment of departure times from a mobilisation profile, and emission of the plan as a MATSim scenario keyed on OSM way identities.

### Modified Capabilities
- `vehicle-and-muster-points`: a muster point additionally records the identity of the road it was snapped to, and the position along that road, so downstream network matching is a lookup rather than a re-snap.

## Impact

- **Code**: `assets/vehicles.py` (retain `way_id` and offset through the snap); new `assets/scenario.py` (district exits, departure times, MATSim writer); `config.py` (exit filtering thresholds, mobilisation distribution parameters); `assets/reporting.py` (export list). `definitions.py` picks up the new assets.
- **Warehouse**: `muster_points` gains two columns — a schema change, so the vehicles asset and everything downstream of it re-materialises. New tables `district_exits`, `vehicle_departures`, `scenario_network_refs`.
- **Dependencies**: no new data sources. MATSim XML is written directly; no MATSim Java dependency is introduced into the Python pipeline.
- **Sequencing against `minimise-evacuation-vehicles`**: independent. The export consumes `person_seats`, `vehicle_routes` and `route_stops`, all of which survive that change. The figures it produces will shift substantially when fleet minimisation lands — that is the comparison the export exists to enable, and both scenarios can be exported from the same code.
- **Figures cited above are the current warehouse state** (26,577 departing vehicles, 13,352 home-collection stops, 70,054 parked vehicles) and are expected to move under `minimise-evacuation-vehicles`.
- **Note on `openspec/config.yaml`**: it records the downstream simulator as UDST MANTA. MATSim is chosen here instead, because MANTA is OD-pair based and has no representation for the intermediate home-collection stops this plan produces. The project context should be updated to match.
