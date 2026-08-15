## 1. Car assignment constrained by adults

- [ ] 1.1 Compute each household's adult count (members at or above the configured minimum driving age) before car counts are assigned; this requires generating persons and ages ahead of the car draw, so reorder the synthesis accordingly
- [ ] 1.2 Replace the unconstrained car apportionment with an assignment that respects `num_cars <= adults`, preserving each Output Area's published category counts exactly
- [ ] 1.3 Handle the case where an Output Area's car marginal cannot be satisfied under the constraint (too few large households); record the shortfall rather than silently violating either the marginal or the cap
- [ ] 1.4 Write a test confirming no household holds more cars than adults
- [ ] 1.5 Write a test confirming a household with no adult holds no cars
- [ ] 1.6 Write a test confirming the per-Output-Area car marginal still matches exactly after the constraint is applied

## 2. Licences conditioned on cars

- [ ] 2.1 Assign at least `num_cars` car licences among each household's adults, choosing which adults at random
- [ ] 2.2 Distribute the remaining licence budget across other adults so the LAD-wide licence-holding proportion still matches the configured target
- [ ] 2.3 Handle the case where the configured proportion is too low to cover every car; report the conflict explicitly rather than silently breaching either constraint
- [ ] 2.4 Keep bus licences as they are — an independent draw among adults, unconnected to car ownership
- [ ] 2.5 Write a test confirming every car-owning household has at least `num_cars` licensed drivers
- [ ] 2.6 Write a test confirming the overall licence proportion still matches within tolerance

## 3. Reporting

- [ ] 3.1 Report car-owning households with no licensed driver (expected zero) and households with more cars than adults (expected zero)
- [ ] 3.2 Report the distribution of licensed drivers per car, and licensed drivers in carless households
- [ ] 3.3 Extend `population_validation` to fail if either invariant is breached, so this cannot silently regress

## 4. Downstream

- [ ] 4.1 Re-materialise the full pipeline from `synthetic_population` onward
- [ ] 4.2 Confirm unmet demand attributable to a missing driver falls to zero, and report what the binding constraint becomes instead
- [ ] 4.3 Record the new baseline numbers (vehicles departing, people seated, unmet by reason) so `minimise-evacuation-vehicles` is measured against a credible population rather than the current one
