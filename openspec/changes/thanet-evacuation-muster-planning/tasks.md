## 1. Project scaffolding

- [ ] 1.1 Set up Dagster project structure with a DuckDB resource (spatial extension enabled), following the `northcray/data` asset-organization pattern
- [ ] 1.2 Add an LAD-code config value (default `E07000114`) that parameterizes area selection, replacing the reference project's hardcoded `E00_AREAS` list
- [ ] 1.3 Add the remaining tunable config values in one place: vehicle capacity, parking snap distance (20 m), walking ceiling (10 min), minimum driving age, minimum parent-child age gap (16 y), max collection distance, max collection stops per vehicle, average driving speed, fleet departure time, evacuation destination
- [ ] 1.4 Add project dependencies: `dagster`, `dagster-duckdb`, `duckdb` (spatial extension), `osmnet`, `pandana`, `networkx`

## 2. Geography and Census ingestion

- [ ] 2.1 Asset: resolve all Output Area codes and boundaries for the configured LAD from the ONS geography API
- [ ] 2.2 Asset: pull OA-level Census 2021 marginal tables needed for synthesis — age/sex, household composition, car availability, disability/mobility (TS038 or equivalent)
- [ ] 2.3 Detect and record OAs where OA-level cells are suppressed/rounded per ONS disclosure control; fetch the corresponding LSOA-level marginal as a fallback for those OAs
- [ ] 2.4 Store all geometries as DuckDB spatial tables and export GeoJSON, following the `e00_geometry.py` pattern generalized to the full LAD

## 3. Synthetic population generation

- [ ] 3.1 Implement marginal-constrained independent sampling generating Person records (age, sex, license_type) and Household records (composition_type, num_persons, num_cars) per Output Area, matched to the marginals from 2.2/2.3
- [ ] 3.2 Derive `license_type` (none/car/bus) from the configured minimum driving age and a license-holding proportion
- [ ] 3.3 Attribute boolean `mobility_status` per person from the disability/long-term-health marginal
- [ ] 3.4 Generate placeholder `phone_number` and `name` values per person, and a `medical_skill` boolean; explicitly record persons generated without a phone_number (do not default to a placeholder that looks like a real number)
- [ ] 3.5 Write a validation check comparing generated aggregate counts (age/sex, composition, car availability) against source marginals within rounding tolerance, per Output Area
- [ ] 3.6 Represent Output Area / LSOA / LAD geography using the generic `AdminArea{level, parent_id, geometry}` entity rather than UK-specific field names

## 4. Relationships and travel groups

- [ ] 4.1 Implement co-resident parent identification: the one or two oldest household members at least the configured age gap older than a given under-16; record households where no member qualifies
- [ ] 4.2 Create the normalized `person_relationships` table (person_id, related_person_id, relationship_type)
- [ ] 4.3 Implement the age-banded `dependent_of` edge rule over the parents from 4.1: under-10 → both co-resident parents; 10-15 → at least one co-resident parent; 16+ → no mandatory edge
- [ ] 4.4 Compute indivisible travel groups as connected components of the mandatory `dependent_of` edges within a household (via `networkx` over the edge table, inside a Dagster asset)
- [ ] 4.5 Write a test confirming a child under 10 with both parents present is always in the same travel group as both parents
- [ ] 4.6 Write a test confirming a person aged 16+ is not forced into their household's travel group by a mandatory edge

## 5. Home location assignment

- [ ] 5.1 Asset: pull the ONS National Statistics UPRN Lookup (OA21 version) and filter to the configured LAD, giving the UPRN → Output Area allocation
- [ ] 5.2 Asset: pull OS Open UPRN and join to 5.1 on UPRN for coordinates; count and report UPRNs present in one source but not the other rather than dropping them silently
- [ ] 5.3 Implement assignment: one synthetic household per UPRN, drawn from the candidate UPRNs of that household's Output Area
- [ ] 5.4 Asset: pull OS Open USRN street geometry and names for the LAD
- [ ] 5.5 Populate each household's synthetic address: generated building number plus the name of the nearest USRN street, flagged as synthetic; fall back to Output Area code plus UPRN where the nearest street is unnamed
- [ ] 5.6 Record Output Areas where synthetic household count exceeds available UPRNs as a home-location shortfall, rather than over-assigning silently
- [ ] 5.7 Record the UPRN-to-household ratio per Output Area, as the visible proxy for non-residential UPRN contamination
- [ ] 5.8 Write a test confirming every assigned household's UPRN has an NSUL Output Area code equal to the household's own Output Area code

## 6. Vehicles and muster points

- [ ] 6.1 Generate one Vehicle record per household car (`num_cars`), linked to its owner household
- [ ] 6.2 Set every Vehicle's `vehicle_type` to `car`, generate a synthetic `license_plate`, and set `capacity` to the configured fixed value
- [ ] 6.3 Asset: extract OSM drivable road centrelines for the LAD as DuckDB spatial line geometry — geometry only, no routed driving graph is built this iteration
- [ ] 6.4 Snap each Vehicle to the nearest point on those centrelines from its owner household's home location; that point is the Vehicle's parking location and its muster point
- [ ] 6.5 Record Vehicles whose nearest road point exceeds the configured snap distance as unresolved parking, and exclude them from the available fleet
- [ ] 6.6 Write a test confirming every muster point maps to exactly one Vehicle and has capacity equal to that Vehicle's capacity

## 7. Pedestrian network

- [ ] 7.1 Asset: extract the OSM pedestrian network for the LAD area via `osmnet`
- [ ] 7.2 Build a `pandana` network graph and precompute walking times from each home location to muster points within the configured ceiling

## 8. Evacuation seat assignment

- [ ] 8.1 Classify each travel group as home-collection (any member has mobility_status = false) or walk-in (all members have mobility_status = true)
- [ ] 8.2 For walk-in groups, classify Class A (contains a dependent) vs Class B (independent adults only)
- [ ] 8.3 Implement Tier 1: seat each owner household's travel groups in its own Vehicle, up to capacity; overflow members re-enter allocation as walk-in or home-collection groups
- [ ] 8.4 Implement Tier 2: allocate Class A walk-in groups to the nearest Vehicle with remaining capacity within the walking ceiling
- [ ] 8.5 Implement Tier 3: allocate home-collection groups to the nearest Vehicle with remaining capacity by straight-line distance, preferring the one with fewest existing collection stops, subject to the max-collection-distance and max-stops-per-vehicle caps
- [ ] 8.6 Implement Tier 4: fill remaining seats with Class B walk-in groups within their own walking ceiling
- [ ] 8.7 Implement the oversized-group split: where a travel group exceeds the capacity of every Vehicle available to it, split across the fewest Vehicles that seat all its members, and record the split
- [ ] 8.8 Enforce the driver-availability constraint: do not finalize a Vehicle's occupant list without at least one `license_type = car` occupant; record that Vehicle's candidate occupants as unmet demand instead
- [ ] 8.9 Record any person who cannot be allocated a seat as unmet evacuation demand, tagged by Output Area, collection mode, and tier
- [ ] 8.10 Write a test confirming tier ordering is respected (no Class B group seated in a Vehicle while a Class A group within its ceiling remains unallocated)
- [ ] 8.11 Write a test confirming a group with any non-walking member is always classified home-collection, never split into Class A/B
- [ ] 8.12 Write a test confirming multiple home-collection groups near the same set of Vehicles are spread across separate Vehicles when fleet capacity allows

## 9. Leader and route output

- [ ] 9.1 Select one leader per finalized Vehicle from occupants with `license_type = car`, preferring a member of the owner household
- [ ] 9.2 Build each Vehicle's ordered route: its muster point first, then one stop per home-collection household allocated to it, ordered by increasing distance from the muster point, ending at the configured destination
- [ ] 9.3 Attach meeting_times: the configured fleet-wide departure time at the muster point, then each subsequent stop offset by straight-line distance over the configured average driving speed
- [ ] 9.4 Attach the list of expected people (name, phone_number where available) to each stop
- [ ] 9.5 Implement the notification-fallback rule: person with phone → notify directly; person without phone but with a `dependent_of` carer → notify carer; person with neither → mark for physical collection
- [ ] 9.6 Write a test confirming a Vehicle with only walk-in occupants produces a single-stop route

## 10. Reporting and validation

- [ ] 10.1 Summary report: unmet demand rate by Output Area and tier
- [ ] 10.2 Summary report: seat utilisation and occupants-per-vehicle distribution — the headline number the ride-share hypothesis turns on
- [ ] 10.3 Summary report: travel-group split rate (from the oversized-group exception), to catch it becoming common rather than exceptional
- [ ] 10.4 Summary report: collection stops per vehicle (distribution), to catch the spreading rule failing in practice
- [ ] 10.5 Summary report: Vehicles excluded for unresolved parking (6.5), households with no qualifying co-resident parent (4.1), home-location shortfalls and UPRN-to-household ratios (5.6, 5.7), UPRN/NSUL join misses (5.2), and Output Areas that required an LSOA marginal fallback (2.3)
- [ ] 10.6 End-to-end run for LAD `E07000114`, producing the full population, home-location, vehicle/muster-point, seat-assignment, and leader-route output ready for a later traffic-microsimulation change to consume
