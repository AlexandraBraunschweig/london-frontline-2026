## Context

`thanet-evacuation-muster-planning` synthesises each attribute by apportioning it to match its published marginal exactly and then pairing attributes at random. That is a faithful reading of "marginal-only synthesis", and it is right for attributes with no strong real-world dependence. It is wrong for these three, which are causally linked: people buy cars because they can drive, and they buy more cars when more of the household drives.

Measured on the current population:

| | |
|---|---|
| one-car households with no licensed driver | 18.9% |
| three-car households with no licensed driver | 16.8% |
| households with more cars than adults | 9,847 |
| households with no adult at all | 4,578 |
| licensed drivers in carless households | 21,481 |
| unmet demand caused by a vehicle having no driver | 10,849 of 10,856 |

The last row is why this matters. The completed plan's headline failure mode is an artifact of the pairing, and the follow-up change proposed to fix it (`minimise-evacuation-vehicles`) is partly motivated by it.

## Goals / Non-Goals

**Goals:**
- No household owns a car it has nobody to drive.
- No household owns more cars than it has adults.
- Both published marginals — car availability per Output Area, and the LAD-wide licence-holding proportion — continue to be matched.
- The coupling is asserted in validation, so it cannot silently regress.

**Non-Goals:**
- Coupling household size or composition to age. That is why 4,578 households contain no adult and 24% of under-16s have no plausible co-resident parent. It is a larger change to the synthesis method — realistically a seed sample or a joint rule set — and is out of scope here. This change only ensures such households cannot own cars.
- Modelling licence holding by age, sex or area. It remains one configured proportion.
- Changing bus licences, which have no ownership to be coupled to.
- Any change downstream of the population.

## Decisions

**Cars are capped by adults, not equated to them.**
The user's framing was "roughly one car per person". Implemented literally that would break the published marginal: 14,212 households must hold exactly two cars and 4,963 exactly three, whatever their size. The constraint adopted instead is `num_cars <= adults`, with the choice of which eligible household receives which count made at random. That removes every implausible case — three cars and one adult — without forcing a correlation stronger than the Census supports. Alternative considered: rank households by adult count and hand the largest counts to the largest households, which would maximise correlation. Rejected as too rigid; it would make every three-car household one of the district's largest, which is also false.

**Feasibility is comfortable, and checked rather than assumed.**
4,963 households need three cars and 13,787 have three or more adults; 14,212 need two and 33,251 have two or more. The constraint binds on the tail, not the bulk. The implementation still has to handle an Output Area where it cannot be met, and records a shortfall rather than quietly breaching the marginal or the cap.

**Licences are redistributed, not created.**
Each car-owning household takes `num_cars` licences off the top; the remainder of the configured budget is spread across other adults. Covering every car needs 54,938 of the 85,586 car licences that a 76% rate produces across 112,522 adults, leaving 30,648. Alternative considered: raise the licence proportion until the constraint is satisfiable without redistribution. Rejected — the proportion is an external statistic, not a free parameter, and inflating it to fix an internal inconsistency would hide the problem in a different number.

**Carless households will hold fewer drivers, and that is correct.**
Today 21,481 licensed drivers live in carless households; after redistribution that figure falls. This is the realistic direction — licence holding and car access are correlated in the population, not just in the household — but it is worth stating, because it is the visible cost of the fix and someone will notice the number move.

**Synthesis order changes.**
Ages must exist before car counts can be capped by adult count, so persons are generated before household car counts are assigned rather than alongside them. This is an implementation reordering; no requirement about marginal matching changes.

## Risks / Trade-offs

- [Risk] An Output Area with an unusually old or small population may not have enough multi-adult households to absorb its published car marginal → Mitigation: task 1.3 records the shortfall per Output Area explicitly; if it is widespread the cap is the wrong constraint and should be revisited.
- [Risk] Forcing `num_cars` licences per car-owning household could push the licence distribution away from realistic per-household patterns, e.g. every two-car household having exactly two drivers and no more → Mitigation: report drivers-per-car; a distribution pinned exactly at 1.0 indicates the remaining budget is not being spread.
- [Trade-off] Fixing this reduces the apparent value of `minimise-evacuation-vehicles`, since much of the unmet demand it addresses will disappear → accepted; a change should be justified against a credible population, and finding out now is cheaper than after implementing it.
- [Trade-off] The cap does not reproduce genuine cases of one adult owning several vehicles → accepted as a small and conservative loss, against removing 9,847 clearly wrong households.

## Open Questions

- Should carless households keep a floor of licensed drivers, reflecting people who can drive but do not own a car? The reported drivers-in-carless-households figure will show whether redistribution pushes it implausibly low, and it is a one-line adjustment if so.
