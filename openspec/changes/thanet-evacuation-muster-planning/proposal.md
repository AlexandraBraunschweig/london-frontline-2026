## Why

There is currently no way to test the hypothesis that shared rides reduce road congestion during a mass evacuation, because there is no population to simulate against. This change produces a synthetic population for Thanet District Council (LAD `E07000114`) — homes, households, family relationships, and cars — together with a muster-point and vehicle-packing plan that gives every person a place to gather and a seat out. That output is what a later traffic microsimulation (e.g. UDST's MANTA) would consume to compare a one-car-per-household baseline against a ride-shared scenario. Running that comparison is explicitly out of scope for this change.

## What Changes

- New pipeline generating a synthetic population for a given UK Local Authority District from ONS Census 2021 Output-Area marginals (age, sex, license_type, has-car), matched to OA-level totals. Each person also carries phone_number, name, and medical_skill for coordination purposes.
- New normalized `person_relationships` edge table (not a list-valued field) driving both the dependency graph and indivisible "travel groups": under-10s must travel with both co-resident parents, 10-15s must travel with at least one, and 16+ persons are not mandatorily bound to their household's travel group and may instead join another family's group during assignment.
- New home-location assignment placing each synthetic household at a real dwelling point — OS Open UPRN primary, Overture Buildings / OSM as fallback — within its Output Area, including a human-readable address string for physical collection. Deliberately excludes any council-portal scraping (dropped for portability and ToS reasons; see Impact).
- New vehicle generation: one vehicle per car-owning household, with a vehicle_type (car for this iteration) and license_plate for passenger identification. Seat capacity is hardcoded (e.g. 5) for this iteration; a realistic capacity distribution is deferred.
- New muster-point catalog and capacitated walking-distance assignment for travel groups that can walk: groups containing a dependent are assigned to the nearest muster point within a 10-minute walk; independent adults with no dependents absorb remaining distance/capacity slack up to the same ceiling. A travel group containing any member who cannot walk is instead routed for **home collection** by driving distance, exempt from the walking ceiling.
- New tiered vehicle-packing algorithm per muster point, single departure wave (no return trips/relays): indivisible family groups packed first (best-fit), then unattached individuals requiring home collection, then independent walkable adults fill remaining seats. Every departing vehicle must retain at least one occupant licensed to drive a car. Anyone who doesn't fit is recorded as unmet demand.
- New `leader-route-planning` output: for each vehicle, a chosen leader (a car-licensed occupant), an ordered list of stops (muster point and/or home-collection addresses) each with a meeting time and the list of people expected there (name, phone), and a target destination — minimizing the number of distinct collection stops per vehicle and spreading unavoidable stops across the fleet rather than concentrating them on one vehicle. Includes the notification fallback rule: a person with no phone has their `dependent_of` carer notified instead; a person with neither is marked for physical collection.
- Schema designed to be internationally portable (a generic `AdminArea{level, parent_id}` hierarchy rather than hardcoded UK OA/LSOA/MSOA fields), even though every data source used in this iteration is UK-specific.
- Explicitly deferred to future iterations (not built now): full bus modeling (a bus's route start point differs from any owner's household), in-app route/pickup confirmation mechanisms, and a trust-network model for who someone would prefer to ride with.

## Capabilities

### New Capabilities
- `synthetic-population`: generation of synthetic person and household records (age, sex, license_type, has_car, phone_number, name, medical_skill) matched to OA-level Census marginals, plus the `person_relationships` edge table and the age-banded dependency rule that groups household members into indivisible travel groups.
- `home-location-assignment`: placement of each synthetic household at a real dwelling location — with geometry and a human-readable address — within its Output Area, sourced from open data only.
- `vehicle-generation`: synthesis of one vehicle per car-owning household with a fixed seat capacity, vehicle_type, and license_plate.
- `muster-point-assignment`: candidate muster-point catalog, the collection-mode split (home collection vs. walk-in), and the capacitated, priority-weighted walking-distance assignment of walk-in households/persons to muster points.
- `evacuation-vehicle-packing`: the tiered (family / home-collection individual / flexible walk-in) allocation of muster-point-catchment occupants into vehicles, enforcing driver availability and recording unmet demand.
- `leader-route-planning`: per-vehicle leader selection and route output (stops, meeting times, people lists, destination), the stop-minimization objective, and the phone/carer notification-fallback rule.

### Modified Capabilities
None — this is a greenfield repository with no existing specs.

## Impact

- New external data dependencies: ONS Census 2021 (OA-level tables), ONS geography boundaries API, OS Open UPRN, Overture Buildings, OpenStreetMap (fallback), `osmnet`/`pandana` for the pedestrian and road network used in muster-point walk-time and home-collection driving-time calculations.
- Reuses the Dagster + DuckDB (spatial extension) pipeline pattern from the `northcray/data` reference project, but deliberately drops its Bexley-council-portal Playwright address scrape — that step is fragile, ToS-grey-area, and hard-coupled to one council's site, which conflicts with this change's goal of working for any LAD.
- Does not include the downstream MANTA traffic microsimulation (baseline-vs-pooled congestion comparison) — that is a separate, later change consuming this one's output.
- No existing code or specs are affected; this is the first change in the repository.
