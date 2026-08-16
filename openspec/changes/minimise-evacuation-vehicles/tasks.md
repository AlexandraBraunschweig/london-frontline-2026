## 1. Reachability

- [ ] 1.1 Build a `vehicle_reach` table: for each Vehicle, the travel groups that could board it — walk-in groups within the walking ceiling (from `walk_candidates`), home-collection groups within the maximum collection distance (from `collection_candidates`)
- [ ] 1.2 Record, per group, how many Vehicles it can reach, so groups reachable by none are identified before activation rather than discovered after
- [ ] 1.3 Add config values: whether the owner-aboard tie-break is enabled, and any minimum occupancy below which a Vehicle is not activated (default: none)

## 2. Coverage-maximising activation

- [ ] 2.1 Implement the greedy activation loop in a pure module alongside `seating.py`: repeatedly pick the inactive Vehicle that would seat the most currently unseated people among those that can reach it
- [ ] 2.2 Enforce driver eligibility as a selection constraint — skip any Vehicle whose reachable-and-unseated candidates include nobody with `license_type = car`
- [ ] 2.3 Implement the owner-aboard tie-break for Vehicles with equal coverage
- [ ] 2.4 Fill each activated Vehicle in tier order (dependent-bearing walk-in, then home-collection, then independent adults), keeping travel groups intact and honouring the collection-stop cap
- [ ] 2.5 Preserve the oversized-group split: a group larger than any activated Vehicle's capacity is split across the fewest that seat it
- [ ] 2.6 Terminate when no unseated person can reach an inactive Vehicle; record the remainder as unmet demand tagged by reason
- [ ] 2.7 Use an efficient candidate structure (e.g. a heap keyed on current coverage with lazy revalidation) so the loop stays tractable over ~70,000 vehicles and ~114,000 groups

## 3. Wiring

- [ ] 3.1 Replace the tiered pass in the `seat_assignments` asset with activation-then-fill, keeping the same output tables (`seat_assignments`, `person_seats`, `unmet_demand`)
- [ ] 3.2 Add an `activated_vehicles` table recording which Vehicles depart, their occupancy, and why they were activated (coverage at selection time)
- [ ] 3.3 Keep the person-level driver invariant that raises if any departing Vehicle carries occupants without a licensed driver among them
- [ ] 3.4 Confirm `vehicle_routes`, `stop_notifications` and all reports still build unchanged from the new assignment

## 4. Tests

- [ ] 4.1 Test that a car-owning household is seated in a neighbour's Vehicle when that yields fewer Vehicles overall
- [ ] 4.2 Test that an unactivated Vehicle has no occupants and no route
- [ ] 4.3 Test that a Vehicle reachable only by unlicensed people is never activated, and those people are seated elsewhere
- [ ] 4.4 Test that no person is ever recorded unmet for want of a driver
- [ ] 4.5 Test the owner-aboard tie-break decides between two equally good Vehicles
- [ ] 4.6 Test that tier order still protects dependent-bearing groups against independent adults within an activated Vehicle
- [ ] 4.7 Test that travel groups remain intact, and that the oversized-group split still fires only on capacity

## 5. Reporting and comparison

- [ ] 5.1 Report vehicles activated against the one-car-per-car-owning-household baseline and the packing floor
- [ ] 5.2 Report the occupancy distribution of activated Vehicles, to expose any tail of near-empty activations
- [ ] 5.3 Report unmet demand by reason, and confirm the driver reason is absent
- [ ] 5.4 Report the walking-distance distribution of seated walk-in groups, to show what the car saving cost in walking
- [ ] 5.5 End-to-end run for LAD `E07000114`, reporting cars on the road against the previous change's 43,514 and the 25,564 floor
