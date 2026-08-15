## Why

The synthetic population draws household size, car count and licence holding as three independent marginals and pairs them at random. Nothing connects them, and the results are not credible:

- **18.9% of one-car households and 16.8% of three-car households have nobody licensed to drive.** That the rate barely falls as cars rise is the signature of independence; in reality it should be near zero, because people do not buy cars they cannot drive.
- **9,847 households own more cars than they have adults**, including 3,705 three-car households — 6,142 two-car households have fewer than two adults.
- **4,578 households contain no adult at all** and are nonetheless eligible for cars.
- Meanwhile **21,481 licensed drivers live in carless households**.

This is not a cosmetic realism issue. It is the direct cause of the single largest result in the completed plan: 10,849 of 10,856 unmet demand arises because a household's own car has no licensed driver. That number is an artifact of the sampling, not a finding about Thanet, and any conclusion drawn from it — including the case for `minimise-evacuation-vehicles` — currently rests on it.

## What Changes

- **Car counts are capped by adults.** A household SHALL NOT be assigned more cars than it has members of driving age. The published car-availability marginal is unchanged; only which households receive which count changes, drawn at random among those eligible to hold that many.
- **Licences are conditioned on cars.** Within each household, at least as many adults hold a car licence as the household has cars. Remaining licences are distributed across other adults so the overall licence-holding proportion still matches its configured target — licences are redistributed, never invented.
- **Adultless households own nothing.** A household with no member of driving age receives no cars, which follows from the cap.
- Both constraints are verified: the car marginal must still match exactly per Output Area, and the licence proportion must still hold across the LAD.
- New reporting on the coupling itself — car-owning households with no driver (which should become zero), households with more cars than adults (zero), and the distribution of drivers per car — so a regression is visible rather than buried.

## Capabilities

### Modified Capabilities
- `synthetic-population`: licence attribution becomes conditional on the household's car count rather than an independent per-person draw, and car assignment gains an adult-count constraint. The published marginals both continue to be matched exactly.

## Impact

- No new data sources. This is a change to how existing marginals are combined.
- The whole pipeline downstream of `synthetic_population` is recomputed: relationships, homes, vehicles, muster points, assignment, routes, reports.
- Expected direction, not a promise: unmet demand attributable to missing drivers falls from 10,849 towards zero, and the binding constraint on the plan moves from licensing to geography — which is where the research question actually lives.
- **This should land before `minimise-evacuation-vehicles`.** That change is motivated in part by the driver crisis, and its coverage objective treats driver availability as a scarce resource. Fixing the synthesis first changes what it is optimising against, and may reduce how much it buys.
- Licence budget is sufficient: 85,586 car licences exist across 112,522 adults, and covering every car needs 54,938, leaving 30,648 for carless households. Carless households will hold fewer drivers than they do today, which is itself the realistic direction.
- Not addressed here: household *size* and *composition* are still drawn independently of age, which is why 4,578 households contain no adult and 24% of under-16s have no plausible parent. That is a larger change to the synthesis method and is left alone deliberately; this change only ensures such households cannot own cars.
