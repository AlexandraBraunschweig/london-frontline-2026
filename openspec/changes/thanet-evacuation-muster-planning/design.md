## Context

See proposal.md - Why. This is a greenfield repository (no existing code or specs). A related prior project, `northcray/data`, demonstrates a working Dagster + DuckDB(spatial extension) pipeline pattern for a small UK area (5 hand-picked Output Areas in Bexley): OA geometry from `statistics.data.gov.uk`, Overture Buildings clipped via `pmtiles`/`ogr2ogr`, OS UPRN/USRN assets, and a Playwright scrape of a council planning portal to resolve full addresses. This change reuses the orchestration/storage pattern but generalizes the area selector to "all Output Areas within a given LAD code" and drops the council-portal scrape (see Decisions).

## Goals / Non-Goals

**Goals:**
- Produce a synthetic population, household, and relationship graph for LAD `E07000114` matched to ONS Census 2021 Output-Area marginals, including contact/identification attributes (phone, name), a skill attribute (medical_skill), and a boolean mobility_status.
- Assign every synthetic household a real, open-data-sourced home location, including a human-readable address.
- Generate a vehicle per household car, park it on the road beside its owner's home, and treat that parking spot as a muster point.
- Split travel groups into home-collection (any member can't walk) vs. walk-in, and allocate every person to a specific vehicle's seat in one tiered pass.
- Track and report unmet demand rather than hiding it.
- Produce a per-vehicle leader and route (stops, meeting times, people lists, destination) plus the phone/carer notification-fallback rule.
- Keep the schema portable to other countries' census geography, even though every data source used here is UK-specific.

**Non-Goals:**
- Running the downstream traffic microsimulation (e.g. MANTA) that compares baseline vs. pooled-ride congestion — a separate, later change consumes this one's output.
- Modeling day/night activity-based starting positions (work/school locations) — starting position equals home location for this iteration.
- Modeling return trips or relay waves — single departure per vehicle only.
- Network-routed driving times or congestion modeling — straight-line estimates only.
- Multiple evacuation destinations, or destination capacity constraints — one fleet-wide destination.
- A realistic vehicle-capacity distribution — a single fixed capacity value is used.
- Resolving the UK census microdata (SARs) licensing question for a full IPU-style seed-sample synthesis — resolved in favor of marginal-only synthesis for this iteration (see Decisions).
- Numeric vulnerability_score or leadership_score models — both use simple rule-based checks this iteration (mobility_status for collection mode, license_type = car for leader eligibility).
- Full bus modeling (a bus's route start point differing from any owner's household), in-app route/pickup confirmation mechanisms (leader/passenger "confirm route" and pickup logging), and a trust-network model for who someone would prefer to ride with — all explicitly future work, not built now.

## Decisions

**A muster point is a parked car, not a site.**
Each vehicle is parked at the nearest drivable road point within a configured snap distance (default 20 m) of its owner household's home, and that point is a muster point with capacity equal to that one vehicle's seats. Alternative considered, and rejected: a catalog of candidate gathering sites (schools, community centres, car parks) derived from Overture Places / OSM POI tags, each holding many vehicles. That version was rejected on three grounds. First, it defined a site's capacity as "the sum of the capacities of the vehicles positioned there" while vehicles only arrive at a site because their household was assigned to it — a circular definition with no fixed point. Second, it required a POI catalog plus a manual-review checkpoint against real Thanet evacuation knowledge, an unresolved external dependency. Third, it introduced two granularities (site, then vehicle) for what is one decision. Parking each car beside its owner's home makes seat supply well-defined by construction, needs no catalog, and models the actual ride-share behavior being tested: you walk to a neighbour's car.

**Muster-point assignment and vehicle packing are a single capability.**
A direct consequence of the decision above: with exactly one vehicle per muster point, "which muster point does this person go to" and "which seat does this person take" are the same question. Keeping them as two capabilities meant two specs owning one decision — and in the first draft they disagreed, with home-collection individuals allocated to a vehicle once by the packing tier and again by the route planner's stop-spreading objective. They are now one tiered pass in `evacuation-seat-assignment`, and `leader-route-planning` makes no allocation decisions at all.

**Pipeline pattern: Dagster + DuckDB (spatial extension), reusing the `northcray/data` pattern.**
Alternative considered: a different orchestrator (e.g. Airflow) or a plain script pipeline. Rejected — the Dagster/DuckDB combination is already proven in the reference project and introduces no new infrastructure.

**Address source: OS Open UPRN → Overture Buildings → OSM fallback chain, explicitly not the council-portal scrape.**
Alternative considered: keep the `northcray/data` Playwright scrape of the Bexley planning portal for richer per-property detail. Rejected — it is fragile, ToS-grey-area, hard-coupled to one council's website, and the extra detail (e.g. property description) isn't needed for evacuation planning. Open data also generalizes to any LAD without bespoke per-council code.

**Synthesis granularity: Output Area (OA), not LSOA.**
OA is the finest published UK census geography and gives seat-assignment catchments household-level spatial precision. Trade-off: OA-level 2021 Census tables carry more disclosure-control suppression/rounding than LSOA-level tables — accepted (see Risks).

**Population-synthesis method: independent per-attribute sampling from ONS Census 2021 OA marginals, with no household+person seed sample.**
Named precisely: without a seed contingency table this is not IPF, it is independent sampling from marginals constrained to OA totals. Alternative considered: full IPU (as used by UDST's `synthpop`, adapted from US ACS/PUMS to UK inputs), which would need a household+person joint seed sample — the UK equivalent being ONS Census 2021 microdata / Samples of Anonymised Records. Rejected for this iteration: SARs access/licensing is an unresolved external dependency that would block synthesis entirely until cleared, whereas marginal-only sampling needs no seed and matches the same externally observable behavior specified in `synthetic-population/spec.md` (aggregate marginal match, dependency relationships). Trade-off: weaker joint-attribute realism (e.g. exact age-by-composition correlations) than a full IPU synthesis would give. Revisiting with a real seed sample later is an isolated change to this capability's implementation, not to its spec.

**Co-resident parenthood is derived by an explicit rule, because marginal-only synthesis does not produce kinship.**
The dependency graph needs to know who a child's parents are, but sampling from marginals yields only household membership and ages. A person under 16's co-resident parents are therefore defined as the one or two oldest household members at least a configured age gap (default 16 years) older. Households where no member qualifies are reported rather than given an implausible parent. Alternative considered: leaving parenthood implicit and treating whole households as travel groups — rejected because it would bind 16+ members mandatorily, contradicting the explicit goal that independent adults can join another family's car.

**Person relationships: a normalized `person_relationships` edge table (person_id, related_person_id, relationship_type), not a list-valued `dependent_on` field on Person.**
Alternative considered: storing dependents as a list column (DuckDB supports a native `LIST` type). Rejected — a list column can't have referential integrity enforced on its elements, makes reverse lookups ("who depends on me") an unnest-and-scan instead of an indexed join, and the spec already requires computing indivisible travel groups as graph connected components, which wants an edge list feeding a Python graph library (e.g. `networkx`) inside a Dagster asset, not a SQL unnest. The same table shape also accommodates the future trust-network extension (a different `relationship_type` value) with no schema change.

**Car ownership lives only on Household.**
The first draft carried both a Person `has_car` and a Household `num_cars`. Census car availability (TS045) is a household-level table, so the person-level field was a derived duplicate that could disagree with its source and had no consuming requirement. Dropped.

**Tier order: owner household → walk-in with dependents → home-collection → independent walk-in adults.**
The owner household goes first because the car is theirs and is parked at their door; putting anyone else in it first would be both unfair and physically odd. After that the order encodes the proposal's priority principle: protect people who can't compensate, let flexible independent adults absorb distance and capacity slack. Alternative considered: a single combined optimization (joint facility-location + bin-packing) — rejected for this iteration as unnecessary complexity; the greedy tiered pass is simpler to validate.

**Class B load-balancing is not a separate requirement.**
The first draft had a requirement letting Class B (independent-adult) groups be rerouted to a farther muster point to preserve capacity for Class A groups. Since all Class A groups are now assigned before any Class B group across the whole LAD, that behavior falls out of the tier order automatically. The requirement was also written with MAY, making it untestable while still carrying a test task. Deleted.

**No return waves: exactly one departure per vehicle.**
Removes the feedback loop between wave-scheduling and congestion-dependent realized cycle time, which would otherwise require resolving this change jointly with the out-of-scope traffic microsimulation. Trade-off: any shortfall becomes unmet demand rather than being resolved by a second trip — accepted, and made a first-class, visible output rather than an implicit gap.

**Straight-line distance for home collection and stop timing; no routed driving graph.**
Home-collection proximity only ranks candidate vehicles, and meeting times are advisory in a model with no congestion. Building an OSM road graph and `pandana` driving-time queries to serve those two uses would add an external dependency for no observable gain. Road data is still ingested, but only as line geometry for the 20 m parking snap. The routed driving model belongs to the downstream microsimulation, which needs it anyway.

**Vehicle capacity: single hardcoded value, not a distribution.**
Keeps the packing algorithm's correctness validation deterministic while it's first being proven, without capacity variance as a confounding variable. Swapping in a sampled distribution later is a small, isolated change.

**Collection-mode signal: boolean `mobility_status` (can walk / can't walk), no separate age threshold or numeric score.**
`mobility_status = false` is the single trigger for home-collection routing, not a general-purpose "vulnerability" flag. Keeps one source of truth per person rather than several independently defined signals that could disagree. Trade-off, accepted for this iteration: a walkable person living alone (e.g. an independent elderly resident with no mobility impairment) receives no seating priority beyond the tier order — a direct consequence of deferring vulnerability_score, easy to revisit once scoring is in scope.

**Collection-mode split is group-level: any travel group containing a member with `mobility_status = false` is routed entirely as home-collection.**
A group can't be partly walked in and partly collected without contradicting the "families aren't split" goal, so group-level mobility was chosen over person-level routing within a group.

**Leader and driver checks use `license_type = car`, not a separate `can_drive` boolean.**
`license_type` (none/car/bus) subsumes the earlier `can_drive` boolean; bus licensing is carried in the schema for forward compatibility with future bus modeling, even though bus routing itself is out of scope this iteration.

**`medical_skill` is carried without a consuming requirement this iteration.**
Generated on Person but read by nothing here. Kept deliberately (it is one cheap column, and triage-aware seating is plausible future work) but noted so it is not mistaken for an orphan attribute a requirement forgot to reference.

## Risks / Trade-offs

- [Risk] OA-level Census 2021 marginals may be suppressed or rounded by ONS disclosure control for small/rare cells, making exact marginal matching impossible for some Output Areas → Mitigation: fall back to LSOA-level marginals as a disaggregation prior for affected OAs, and record which OAs required the fallback.
- [Risk] OS Open UPRN coverage may be incomplete in some Thanet Output Areas (e.g. recent new-build areas) → Mitigation: the Overture/OSM fallback chain already covers this; record which OAs used it.
- [Risk] The 20 m parking snap may fail for homes set back from the road (rural properties, large estates, flats behind service roads), removing their vehicle from the fleet entirely → Mitigation: report the unresolved-parking rate as a summary metric; if it is material, the snap distance is a single config value to revisit.
- [Risk] With one vehicle per muster point, seat supply is fragmented — a carless household surrounded by full cars is unseated even if spare seats exist just outside its 10-minute ceiling → Mitigation: this is a real property of the scenario being modeled, not an artifact; report unmet demand per Output Area so its spatial pattern is visible.
- [Risk] The oversized-group split could become common in dense terraced-housing OAs with large multi-generational households and only one car, undermining the "families aren't split" goal in practice → Mitigation: report the split rate as a summary metric rather than letting it pass silently.
- [Risk] Treating unmet demand as a terminal, un-resolved output could make results hard to interpret if a large share of an OA ends up unseated → Mitigation: report unmet-demand rate per Output Area and per tier as a first-class output.
- [Trade-off] The portable `AdminArea` schema adds a layer of indirection over using ONS OA/LSOA codes directly → accepted, since international reuse is an explicit goal of this change.

## Open Questions

- None blocking. The muster-point catalog curation question from the first draft is resolved by defining muster points as parked cars, which removes the catalog entirely.
