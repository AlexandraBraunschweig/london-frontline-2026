## Why

The completed `thanet-evacuation-muster-planning` change seats 127,820 people in 43,514 cars — but a one-car-per-household baseline would use 45,960. A 5% reduction is not a ride-share scenario, and measuring it against the baseline would tell us almost nothing.

The cause is structural rather than incidental. That change's first assignment tier puts every car-owning household into its own car, so the number of cars on the road is pinned near the number of car-owning households regardless of how many people are in them. The consequences are visible in its own output: **7,574 cars carry a single person**, 11,657 cars are activated by one-person households, and **89,750 empty seats leave the district**. Packing the same 127,820 people at 5 per car needs 25,564 cars.

The same rule also strands people. 10,849 of the 10,856 unmet demand arise because a household's own car has no licensed driver — and because the household is committed to that car, its occupants have nowhere else to go. Only 7 people are unseated for want of a seat, and none for walking distance.

This change replaces "everyone boards their own car" with "choose which cars depart". Both problems dissolve into the same decision: activate the cars that cover the most people and have someone able to drive them.

## What Changes

- **Vehicle activation becomes a decision, not a given.** A vehicle departs only if the assignment chooses it. A car-owning household may be seated in a neighbour's car and leave its own parked — that is the pooled behaviour the project exists to test.
- The tiered assignment in `evacuation-seat-assignment` is replaced by a **coverage-maximising allocation**: repeatedly activate the vehicle that seats the most currently-unseated people within their walking ceiling, then fill it, until everyone reachable is seated. Priority within a vehicle still protects dependents and non-walkers.
- **Driver availability moves from a post-hoc check to a selection constraint.** A vehicle is only eligible to activate if a licensed member of its owner household will be aboard, so a car nobody may drive is never chosen and never strands anyone.
- **A car is driven by its owner.** A vehicle is activated only when a licensed member of the owning household is aboard, so no car departs without its owner and no keys change hands. The owner household loses its claim to a *seat* — it may ride with a neighbour — but nobody else drives its car.
- Unmet demand, family integrity, the walking ceiling, home-collection routing, and the collection-stop cap are all unchanged.
- New reporting: cars-on-the-road against both the baseline and the theoretical packing floor, so the ride-share saving is stated directly rather than inferred.

## Capabilities

### Modified Capabilities
- `evacuation-seat-assignment`: the tiered owner-household-first allocation is replaced by coverage-maximising vehicle activation with driver availability as a selection constraint.
- `leader-route-planning`: unchanged in behaviour, but the leader is no longer usually a member of the owner household, so the preference order is restated.

### New Capabilities
- `fleet-minimisation`: the vehicle-activation decision itself — which cars depart, the coverage objective, the driver-eligibility constraint, and the reporting of cars saved against baseline and floor.

## Impact

- **Sequencing: `couple-cars-and-licences` should land first.** Much of the driver crisis motivating this change is an artifact of the synthesis, which draws licence holding independently of car ownership — 18.9% of one-car households currently have nobody able to drive. Fixing that removes most of the 10,849 stranded people on its own, and changes what this change is optimising against. The occupancy case stands regardless: 7,574 cars carrying one person is a property of the owner-household rule, not of the licence draw.
- No new external data. Everything needed is already in the warehouse: `walk_candidates` (mean 49.4 vehicles within a 10-minute walk per household), `collection_candidates`, `travel_groups`, `muster_points`.
- `seat_assignments`, `person_seats`, `unmet_demand`, and every downstream route and report are recomputed; the population, homes, vehicles and networks are untouched.
- Expected direction, not a promise: cars on the road fall from 43,514 toward the 25,564 floor, and unmet demand falls from 10,856 toward the 7 attributable to genuine seat shortage. The walking ceiling, not seat supply, becomes the binding constraint.
- The 20-minute-plus walk that pooling implies for some people is a real behavioural assumption. It is the scenario under test, and the walking ceiling remains the guard against it becoming unreasonable.
