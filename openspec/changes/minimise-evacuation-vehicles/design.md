## Context

`thanet-evacuation-muster-planning` is complete and its pipeline runs end to end for LAD `E07000114`. Its measured output is the input to this change:

| | |
|---|---|
| people seated | 127,820 of 138,676 |
| cars departing | 43,514 |
| baseline (one per car-owning household) | 45,960 |
| perfect-packing floor at 5 seats | 25,564 |
| empty seats on the road | 89,750 |
| cars carrying exactly one person | 7,574 |
| unmet demand from having no driver | 10,849 of 10,856 |
| candidate vehicles within a 10-minute walk, mean | 49.4 |

The last row is why this change is tractable: the spatial constraint is weak. Almost every household can reach dozens of vehicles on foot, so which cars depart is close to a free choice.

## Goals / Non-Goals

**Goals:**
- Carry the same people in materially fewer vehicles, by choosing which vehicles depart.
- Remove driver unavailability as a cause of unmet demand, by never activating a car nobody can drive.
- Keep every protection from the previous change: families not split, non-walkers collected from home, the walking ceiling, the collection-stop cap, unmet demand reported rather than hidden.
- Report cars saved against both the baseline and the packing floor.

**Non-Goals:**
- Modelling whether people would in fact abandon their own car. This change assumes full compliance with the plan; that assumption is the scenario, not a claim about behaviour.
- An optimal solve. Minimum-vehicle assignment under walking and family constraints is NP-hard; a greedy coverage heuristic is what this change builds.
- Revisiting the population, homes, vehicles, or networks. All are unchanged.
- Multi-wave departures, congestion, or routed driving — still out of scope, as before.

## Decisions

**Coverage-maximising greedy, not a tiered pass.**
Repeatedly activate the vehicle that would seat the most currently-unseated people within their walking ceiling, fill it, and repeat until nobody reachable remains. Alternatives considered: (a) an exact set-cover / capacitated facility-location solve — rejected as NP-hard at 70,098 candidate vehicles and 114,184 groups, and unnecessary when the constraint is this loose; (b) keeping the tiered pass and merely widening the ceiling — rejected because it cannot help, since the car count is pinned by the owner-household rule rather than by distance; (c) simulated annealing or similar over the greedy result — deferred, worth revisiting only if the greedy lands far from the floor. Greedy max-coverage has a well-understood approximation bound and is inspectable, which matters more here than the last few percent.

**Driver availability becomes a selection constraint, not a post-hoc check.**
A vehicle is eligible only if at least one licensed driver is among the people it would carry. In the previous change the check ran after assignment and dropped whole vehicles, stranding 10,849 people who by then had nowhere else to go. Choosing eligibility up front means those people are simply seated elsewhere. This also removes an entire class of correctness bug: the previous implementation counted drivers per travel group, which let a split family's single licensed member make both of its vehicles look drivable.

**The owner household loses its claim but keeps a tie-break.**
Alternative considered: keep the owner household's first claim and optimise only the remaining seats — rejected, because that is precisely the constraint that pins the car count, and it is the whole subject of this change. But where two vehicles are equally good, the one whose owner is already aboard is preferred: it needs no key handover, and it is the less surprising instruction to give a real person.

**Within an activated vehicle, priority is unchanged.**
Dependent-bearing groups, then home-collection groups, then independent adults. The previous change's tier order was about which *people* get scarce seats, and that reasoning still holds; only the question of which *vehicles* exist to be filled has changed.

**Reporting states the saving directly.**
Cars on the road, against the one-car-per-household baseline and against the packing floor. The previous change reported occupancy but left the reader to infer the saving, which made a 5% reduction easy to mistake for a result.

## Risks / Trade-offs

- [Risk] Greedy coverage could leave a long tail of nearly-empty vehicles activated for one isolated household → Mitigation: report the occupancy distribution as before; a tail of single-occupancy cars is the signal that the heuristic needs a second pass.
- [Risk] Concentrating people into fewer cars lengthens the average walk, pushing more groups against the 10-minute ceiling and converting a car saving into unmet demand → Mitigation: report unmet demand by reason, as now; if ceiling-attributable unmet demand rises materially, the trade is visible rather than silent.
- [Risk] Fewer, fuller cars is a worse baseline comparison if the baseline itself is not modelled explicitly → Mitigation: the baseline is a simple count (one car per car-owning household) and is reported alongside, not inferred.
- [Trade-off] Vehicle activation order makes the result sensitive to tie-breaking → accepted, and made deterministic by seeding and by the owner-aboard tie-break, so runs are reproducible.
- [Trade-off] A household may be told to walk past its own car to reach a fuller one → accepted; this is the pooled scenario, and the walking ceiling bounds how far.

## Open Questions

- Should a vehicle be allowed to activate below some minimum occupancy, or is any activation that seats an otherwise-unreachable person justified? The greedy naturally leaves these to last, so the occupancy distribution will answer it empirically before it needs deciding.
