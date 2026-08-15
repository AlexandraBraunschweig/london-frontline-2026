## Context

See proposal.md - Why. This is a greenfield repository (no existing code or specs). A related prior project, `northcray/data`, demonstrates a working Dagster + DuckDB(spatial extension) pipeline pattern for a small UK area (5 hand-picked Output Areas in Bexley): OA geometry from `statistics.data.gov.uk`, Overture Buildings clipped via `pmtiles`/`ogr2ogr`, OS UPRN/USRN assets, and a Playwright scrape of a council planning portal to resolve full addresses. This change reuses the orchestration/storage pattern but generalizes the area selector to "all Output Areas within a given LAD code" and drops the council-portal scrape (see Decisions).

## Goals / Non-Goals

**Goals:**
- Produce a synthetic population, household, and relationship graph for LAD `E07000114` matched to ONS Census 2021 Output-Area marginals, including contact/identification attributes (phone, name), a skill attribute (medical_skill), and a boolean mobility_status.
- Assign every synthetic household a real, open-data-sourced home location, including a human-readable address.
- Generate a vehicle per car-owning household, with type and identification (license plate).
- Split travel groups into home-collection (any member can't walk) vs. walk-in, and build a muster-point catalog plus a capacitated, priority-weighted walking-distance assignment for walk-in groups.
- Produce a tiered, single-wave vehicle-packing plan per muster point's catchment, with unmet demand tracked and reported rather than hidden.
- Produce a per-vehicle leader and route (stops, meeting times, people lists, destination) that minimizes and spreads home-collection stops across the fleet, plus the phone/carer notification-fallback rule.
- Keep the schema portable to other countries' census geography, even though every data source used here is UK-specific.

**Non-Goals:**
- Running the downstream traffic microsimulation (e.g. MANTA) that compares baseline vs. pooled-ride congestion — a separate, later change consumes this one's output.
- Modeling day/night activity-based starting positions (work/school locations) — starting position equals home location for this iteration.
- Modeling return trips or relay waves — single departure per vehicle only.
- A realistic vehicle-capacity distribution — a single fixed capacity value is used.
- Resolving the UK census microdata (SARs) licensing question for a full IPU-style seed-sample synthesis — resolved in favor of marginal-only synthesis for this iteration (see Decisions).
- Numeric vulnerability_score or leadership_score models — both use simple rule-based checks this iteration (mobility_status/age bands for vulnerability, license_type = car for leader eligibility).
- Full bus modeling (a bus's route start point differing from any owner's household), in-app route/pickup confirmation mechanisms (leader/passenger "confirm route" and pickup logging), and a trust-network model for who someone would prefer to ride with — all explicitly future work, not built now.

## Decisions

**Pipeline pattern: Dagster + DuckDB (spatial extension), reusing the `northcray/data` pattern.**
Alternative considered: a different orchestrator (e.g. Airflow) or a plain script pipeline. Rejected — the Dagster/DuckDB combination is already proven in the reference project and introduces no new infrastructure.

**Address source: OS Open UPRN → Overture Buildings → OSM fallback chain, explicitly not the council-portal scrape.**
Alternative considered: keep the `northcray/data` Playwright scrape of the Bexley planning portal for richer per-property detail. Rejected — it is fragile, ToS-grey-area, hard-coupled to one council's website, and the extra detail (e.g. property description) isn't needed for muster-point planning. Open data also generalizes to any LAD without bespoke per-council code.

**Synthesis granularity: Output Area (OA), not LSOA.**
OA is the finest published UK census geography and gives muster-point catchments household-level spatial precision. Trade-off: OA-level 2021 Census tables carry more disclosure-control suppression/rounding than LSOA-level tables — accepted (see Risks).

**Population-synthesis method: marginal-only proportional synthesis (IPF-style), matching ONS Census 2021 OA tables directly, with no household+person seed sample.**
Alternative considered: full IPU (as used by UDST's `synthpop`, adapted from US ACS/PUMS to UK inputs), which would need a household+person joint seed sample — the UK equivalent being ONS Census 2021 microdata / Samples of Anonymised Records. Rejected for this iteration: SARs access/licensing is an unresolved external dependency that would block synthesis entirely until cleared, whereas marginal-only proportional fitting needs no seed sample and matches the same externally observable behavior specified in `synthetic-population/spec.md` (aggregate marginal match, dependency relationships). Trade-off: weaker joint-attribute realism (e.g. exact age-by-composition correlations) than a full IPU synthesis would give. Revisiting with a real seed sample later is an isolated change to this capability's implementation, not to its spec.

**Two-tier muster-point assignment (Class A/B) and three-tier vehicle packing (family/vulnerable/flexible) as the same priority principle applied at two granularities.**
Chosen for consistency and because it directly encodes the proposal's rule set: protect people who can't compensate (dependents, vulnerable), let flexible independent adults absorb distance and capacity slack. Alternative considered: a single combined optimization (e.g. joint facility-location + bin-packing solved together) — rejected for this iteration as unnecessary complexity; the two-stage greedy approach is simpler to validate and matches how the rules were originally specified.

**No return waves: exactly one departure per vehicle.**
Removes the feedback loop between wave-scheduling and congestion-dependent realized cycle time, which would otherwise require resolving this change jointly with the out-of-scope traffic microsimulation. Trade-off: any shortfall becomes unmet demand rather than being resolved by a second trip — accepted, and made a first-class, visible output (see `evacuation-vehicle-packing/spec.md`) rather than an implicit gap.

**Vehicle capacity: single hardcoded value, not a distribution.**
Keeps the packing algorithm's correctness validation deterministic while it's first being proven, without capacity variance as a confounding variable. Swapping in a sampled distribution later is a small, isolated change.

**Vulnerability/collection-mode signal: boolean `mobility_status` (can walk / can't walk), no separate age threshold or numeric score.**
`mobility_status = false` is now the single trigger for home-collection routing (see below), not a general-purpose "vulnerability" flag. Keeps one source of truth per person rather than several independently defined signals that could disagree. Trade-off, accepted for this iteration: a walkable person living alone (e.g. an independent elderly resident with no mobility impairment) receives no special muster-point or seating priority beyond Class A/B — this is a direct consequence of deferring vulnerability_score, not an oversight, and is easy to revisit once scoring is in scope.

**Person relationships: a normalized `person_relationships` edge table (person_id, related_person_id, relationship_type), not a list-valued `dependent_on` field on Person.**
Alternative considered: storing dependents as a list column (DuckDB supports a native `LIST` type). Rejected — a list column can't have referential integrity enforced on its elements, makes reverse lookups ("who depends on me") an unnest-and-scan instead of an indexed join, and the spec already requires computing indivisible travel groups as graph connected components, which wants an edge list feeding a Python graph library (e.g. `networkx`) inside a Dagster asset, not a SQL unnest. The same table shape also accommodates the future trust-network extension (a different `relationship_type` value) with no schema change.

**Collection-mode split: any travel group containing a member with `mobility_status = false` is routed entirely as home-collection, not split by member.**
A group can't be partly walked in and partly driven-and-collected without contradicting the "families aren't split" goal, so group-level mobility (any non-walker forces the whole group to home-collection) was chosen over person-level routing within a group. This also means `muster-point-assignment`'s Class A/B walking-distance logic only ever applies to groups where everyone can walk.

**Leader and vehicle-occupant driver checks use `license_type = car`, not a separate `can_drive` boolean.**
`license_type` (none/car/bus) subsumes the earlier simpler `can_drive` boolean from this same schema's first draft; bus licensing is carried in the schema now for forward compatibility with future bus modeling, even though bus routing itself is out of scope this iteration.

## Risks / Trade-offs

- [Risk] OA-level Census 2021 marginals may be suppressed or rounded by ONS disclosure control for small/rare cells, making exact marginal matching impossible for some Output Areas → Mitigation: fall back to LSOA-level marginals as a disaggregation prior for affected OAs, and record which OAs required the fallback.
- [Risk] OS Open UPRN coverage may be incomplete in some Thanet Output Areas (e.g. recent new-build areas) → Mitigation: the Overture/OSM fallback chain already covers this; record which OAs used it.
- [Risk] The oversized-household vehicle-split exception could become common in dense terraced-housing OAs with large multi-generational households and only one car, undermining the "families aren't split" goal in practice → Mitigation: report the split rate as a summary metric rather than letting it pass silently.
- [Risk] Treating unmet demand as a terminal, un-resolved output could make results hard to interpret if a large share of an OA ends up unseated → Mitigation: report unmet-demand rate per muster point and per priority tier as a first-class output.
- [Trade-off] The portable `AdminArea` schema adds a layer of indirection over using ONS OA/LSOA codes directly → accepted, since international reuse is an explicit goal of this change.
- [Risk] Home-collection routing turns part of vehicle assignment into a small vehicle-routing problem (multiple stops, minimize-and-spread objective) rather than pure bin-packing → Mitigation: keep it a greedy assignment (nearest available vehicle with spare capacity, capped stops-per-vehicle) for this iteration rather than a full VRP solve; revisit only if the greedy approach visibly concentrates stops in practice (see the split-rate-style summary metric in tasks.md).

## Open Questions

- Should the muster-point catalog be auto-derived purely from Overture Places / OSM POI tags (school, community_centre, car park), or does it need manual curation against real evacuation-planning knowledge of Thanet? Tasks.md includes an auto-derivation step plus a manual-review checkpoint so this can be resolved without changing the spec or approach.
