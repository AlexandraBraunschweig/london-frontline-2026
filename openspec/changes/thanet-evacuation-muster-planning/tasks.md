## 1. Project scaffolding

- [ ] 1.1 Set up Dagster project structure with a DuckDB resource (spatial extension enabled), following the `northcray/data` asset-organization pattern
- [ ] 1.2 Add an LAD-code config value (default `E07000114`) that parameterizes area selection, replacing the reference project's hardcoded `E00_AREAS` list
- [ ] 1.3 Add project dependencies: `dagster`, `dagster-duckdb`, `duckdb` (spatial extension), `osmnet`, `pandana`

## 2. Geography and Census ingestion

- [ ] 2.1 Asset: resolve all Output Area codes and boundaries for the configured LAD from the ONS geography API
- [ ] 2.2 Asset: pull OA-level Census 2021 marginal tables needed for synthesis — age/sex, household composition, car availability, disability/mobility (TS038 or equivalent)
- [ ] 2.3 Detect and record OAs where OA-level cells are suppressed/rounded per ONS disclosure control; fetch the corresponding LSOA-level marginal as a fallback for those OAs
- [ ] 2.4 Store all geometries as DuckDB spatial tables and export GeoJSON, following the `e00_geometry.py` pattern generalized to the full LAD

## 3. Synthetic population generation

- [ ] 3.1 Implement marginal-only proportional synthesis (IPF-style) generating Person records (age, sex, license_type, has_car) and Household records (composition_type, num_persons, num_cars) per Output Area, matched to the marginals from 2.2/2.3
- [ ] 3.2 Derive `license_type` (none/car/bus) from a configurable minimum UK driving-age threshold and license-holding proportion; set vehicle_type generation to `car` only for now (see 6.2)
- [ ] 3.3 Attribute boolean `mobility_status` per person from the disability/long-term-health marginal
- [ ] 3.4 Generate placeholder `phone_number` and `name` values per person, and a `medical_skill` boolean; explicitly record persons generated without a phone_number (do not default to a placeholder that looks like a real number)
- [ ] 3.5 Write a validation check comparing generated aggregate counts (age/sex, composition, car availability) against source marginals within rounding tolerance, per Output Area
- [ ] 3.6 Represent Output Area / LSOA / LAD geography using the generic `AdminArea{level, parent_id, geometry}` entity rather than UK-specific field names

## 4. Relationships and travel groups

- [ ] 4.1 Create the normalized `person_relationships` table (person_id, related_person_id, relationship_type)
- [ ] 4.2 Implement the age-banded `dependent_of` edge rule: under-10 → both co-resident parents; 10-15 → at least one co-resident parent; 16+ → no mandatory edge
- [ ] 4.3 Compute indivisible travel groups as connected components of the mandatory `dependent_of` edges within a household (e.g. via `networkx` over the edge table, inside a Dagster asset)
- [ ] 4.4 Write a test confirming a child under 10 with both parents present is always in the same travel group as both parents
- [ ] 4.5 Write a test confirming a person aged 16+ is not forced into their household's travel group by a mandatory edge

## 5. Home location assignment

- [ ] 5.1 Asset: pull OS Open UPRN points clipped to each Output Area
- [ ] 5.2 Asset: pull Overture Buildings and OSM address/building points clipped to each Output Area, for use where OS Open UPRN coverage is missing
- [ ] 5.3 Implement assignment: one household per dwelling point, OS Open UPRN preferred, falling back to Overture/OSM per Output Area
- [ ] 5.4 Populate each assigned household's human-readable address string from the linked UPRN address or a reverse-geocode of the fallback point
- [ ] 5.5 Record Output Areas where synthetic household count exceeds available dwelling points as a home-location shortfall, rather than over-assigning silently

## 6. Vehicle generation

- [ ] 6.1 Generate one Vehicle record per car-owning household's `num_cars`, linked to owner household and home location
- [ ] 6.2 Set every Vehicle's `vehicle_type` to `car` and generate a synthetic `license_plate`
- [ ] 6.3 Set every Vehicle's `capacity` to a single configured fixed value

## 7. Muster-point catalog and pedestrian/road network

- [ ] 7.1 Asset: extract the OSM pedestrian network for the LAD area via `osmnet`
- [ ] 7.2 Asset: extract the OSM road network for the LAD area, for home-collection driving-distance queries
- [ ] 7.3 Derive candidate muster-point Locations from Overture Places / OSM POI tags (school, community_centre, car_park); flag this list for manual review per the open question in design.md
- [ ] 7.4 Set each muster point's capacity from the Vehicles positioned there (from step 6)
- [ ] 7.5 Build a `pandana` network graph for walking-time queries from home locations to muster points
- [ ] 7.6 Build a driving-time/distance query path (road network) from home locations to muster points, for home-collection groups

## 8. Collection-mode split and muster-point assignment

- [ ] 8.1 Classify each travel group as home-collection (any member has mobility_status = false) or walk-in (all members have mobility_status = true)
- [ ] 8.2 For walk-in groups, classify Class A (contains a dependent) vs Class B (independent adults only)
- [ ] 8.3 Implement Class A assignment: nearest muster point by network walking time, subject to a 10-minute ceiling and remaining capacity
- [ ] 8.4 Implement Class B assignment: nearest muster point with remaining capacity within the 10-minute ceiling, allowed to be rerouted farther (still within its own ceiling) to preserve capacity for Class A groups
- [ ] 8.5 Implement home-collection group assignment: nearest muster point by driving time/distance, no walking ceiling applied
- [ ] 8.6 Record any walk-in travel group with no muster point available within the 10-minute ceiling as unmet muster-point demand
- [ ] 8.7 Write a test confirming no Class B group is assigned to a muster point ahead of an unassigned Class A group within that point's catchment
- [ ] 8.8 Write a test confirming a group with any non-walking member is always classified home-collection, never split into Class A/B

## 9. Evacuation vehicle packing

- [ ] 9.1 Implement Tier 1 packing: best-fit allocation of indivisible travel groups (containing a dependent) into Vehicles at each muster point
- [ ] 9.2 Implement the oversized-household exception: split a group across the minimum number of Vehicles when it exceeds every available Vehicle's capacity, preferring to separate 16+ non-mandatorily-bound members before separating a dependent from a co-resident parent
- [ ] 9.3 Implement Tier 2 packing: allocate unattached home-collection individuals not already in a Tier 1 group into Vehicles with remaining capacity
- [ ] 9.4 Implement Tier 3 packing: fill remaining seats with independent walk-in adults
- [ ] 9.5 Enforce the driver-availability constraint: do not finalize a Vehicle's occupant list without at least one `license_type = car` occupant; route any group that would violate this to unmet demand instead
- [ ] 9.6 Record any person who cannot be seated at their muster point's catchment as unmet evacuation demand, tagged by muster point and priority tier
- [ ] 9.7 Write a test confirming tier ordering is respected (no Tier 3 allocation at a muster point while a fitting Tier 1 group remains unallocated there)

## 10. Leader and route planning

- [ ] 10.1 Select one leader per finalized Vehicle from occupants with `license_type = car`
- [ ] 10.2 Build each Vehicle's ordered route: muster-point stop for walk-in occupants, plus one stop per distinct home-collection household among its occupants
- [ ] 10.3 Implement the stop-minimization/spreading objective: greedily assign home-collection individuals to the nearest vehicle with spare capacity and the fewest existing extra stops, capping stops per vehicle, across all vehicles serving a muster point's catchment
- [ ] 10.4 Attach a meeting_time and the list of expected people (name, phone_number where available) to each stop
- [ ] 10.5 Implement the notification-fallback rule: person with phone → notify directly; person without phone but with a `dependent_of` carer → notify carer; person with neither → mark for physical collection
- [ ] 10.6 Write a test confirming multiple home-collection individuals in one catchment are spread across separate vehicles when fleet capacity allows

## 11. Reporting and validation

- [ ] 11.1 Summary report: unmet demand rate by muster point and priority tier
- [ ] 11.2 Summary report: household vehicle-split rate (from the oversized-household exception), to catch it becoming common rather than exceptional
- [ ] 11.3 Summary report: home-collection stops per vehicle (distribution), to catch the minimize-and-spread objective failing in practice
- [ ] 11.4 Summary report: Output Areas that required an LSOA marginal fallback (2.3) or a non-UPRN dwelling source (5.3)
- [ ] 11.5 End-to-end run for LAD `E07000114`, producing the full population, home-location, vehicle, muster-point-assignment, vehicle-packing, and leader-route output ready for a later traffic-microsimulation change to consume
