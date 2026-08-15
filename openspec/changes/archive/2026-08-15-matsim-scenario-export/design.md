## Context

See proposal.md — Why. The relevant current state:

- `muster_points` holds 70,054 parked vehicles with BNG coordinates and a snap distance, but no reference to the road segment the snap matched. The join that produces it (`vehicles.py:206`) has `road_segments.way_id` in scope and drops it.
- `road_centrelines` covers the LAD bounding box grown by 0.02°, sourced from Overpass. It stops around lon 1.195; the configured destination (Canterbury, lon 1.0789) is roughly 9 km beyond it.
- `vehicle_routes` (26,577 rows at the time of writing) already gives one leader per departing vehicle, and `route_stops` (13,352 home collections plus 26,577 muster stops) already gives the ordered stop sequence with cumulative distance. `person_walks` already gives each seated person's network walk time to their vehicle.
- `person_seats` holds 134,617 seated people; `travel_groups.all_can_walk` distinguishes walk-in groups from home-collection groups.

So the plan is structurally complete. What is missing is a destination inside the network, a departure time worth simulating, and a stable key binding locations to network links.

Thanet is a peninsula, which is the constraint that makes truncation viable. Intersecting `road_centrelines` with the LAD boundary yields 44 crossing ways, of which 34 are `service` (driveways and car-park access). Measuring how far each remaining way continues beyond the boundary separates the real exits from coastal artifacts cleanly:

| Way | Class | m inside | m outside |
|---|---|---|---|
| Island Road (A28) | trunk | 4 | 2,578 |
| Thanet Way (A299) ×2 | trunk | 91 / 86 | 2,424 / 2,422 |
| Ramsgate Road ×2 | trunk | 91 / 122 | 298 / 266 |
| Old Road | unclassified | 319 | 262 |
| Ramsgate Road | tertiary | 50 | 104 |
| Plucks Gutter | tertiary | 3 | 66 |
| Royal Harbour Approach ×2 | trunk | 130 / 395 | 29 / 8 |

The two Royal Harbour Approach ways are the harbour road at Ramsgate clipping a boundary that follows the coastline. Any threshold between 30 m and 60 m separates them from the genuine exits.

## Goals / Non-Goals

**Goals:**

- A scenario that loads into MATSim and runs without hand editing.
- Binding keys that survive a network rebuild, so the plan does not have to be regenerated when the network is.
- A departure profile whose shape is a stated assumption rather than an accident of the pipeline.
- Both the baseline and the pooled scenario emitted by the same code, so the comparison is like-for-like.

**Non-Goals:**

- Building the MATSim `network.xml`. That is MATSim's own OSM reader's job, run outside this pipeline.
- Any change to who rides in which car, which car departs, or which stops it makes. This capability renders decisions; it does not make them.
- Making the export work for MANTA or SUMO. Way-identity keying is chosen partly so those adapters stay thin, but neither is written here.

## Decisions

### Target MATSim rather than SUMO, MANTA or a neutral table

MATSim's input is a population of per-person plans with activities and legs, which is the shape the warehouse is already in: persons, households, travel groups, home coordinates, ordered stops. The translation is close to mechanical.

**SUMO** was rejected because it is a microsimulator built for car-following and lane-changing fidelity. The question here is district clearance time under two fleet sizes; that detail costs runtime and calibration effort without informing the answer. SUMO also has no first-class notion of a person riding in someone else's vehicle, so passengers would have to be dropped from the demand entirely.

**MANTA** is the simulator currently named in `openspec/config.yaml`, and was rejected because it is OD-pair based. The 13,352 home-collection stops have no representation in an OD matrix — they would either be flattened into direct origin-destination pairs, silently deleting the detours that pooling causes, or split into separate trips, silently inflating the vehicle count. Both corrupt exactly the quantity the project exists to measure. The config's stated target should be corrected.

**A simulator-neutral parquet trip table** was rejected as the primary artifact because it defers all the hard translation decisions — passenger representation, stop semantics, plan chronology — rather than resolving them, and produces nothing runnable. The neutral part is kept where it earns its place: the network reference table (below).

### Scope the evacuation to clearing the district, not the journey to Canterbury

Extending `road_centrelines` west to Canterbury would mean abandoning Overpass for a Geofabrik Kent `.pbf`, roughly doubling this change and modifying the table the parking snap depends on — so every vehicle would re-snap and every downstream number would move for reasons unrelated to the export.

**Extending the network** was rejected on that basis, not on principle; it remains the right follow-up if the question becomes "how long to reach Canterbury". **Keeping Canterbury as an off-network destination** was rejected because it produces routes ending nowhere. Truncating to district exits makes the scenario self-contained within the network that already exists, and for a peninsula "time to clear the district" is a sharper question than "time to reach a town", because the exits are few and the exit capacity is the binding constraint.

The cost is that queueing *beyond* the boundary is unmodelled — see Risks.

### Qualify exits by road class and length beyond the boundary

**Intersecting with the boundary alone** was rejected: it admits 34 service ways and the two harbour-road artifacts above. **A hand-curated exit list** was rejected because it would not port to another LAD, and the project's stated convention is that changing `lad_code` is the only edit needed to target a new district. Class filtering plus a minimum length outside is derivable anywhere and both thresholds become named config values.

### Collapse co-located crossings into one exit per corridor

Found during implementation, not anticipated when this design was first written. Thanet's nine qualifying crossings include four within 292 m of each other, all named Ramsgate Road: two trunk ways, plus one tertiary way that crosses the boundary twice. Proximity assignment across them put **24,012 of 26,577 vehicles (90.3%) onto the tertiary crossing**, while a trunk crossing 250 m away took seven. The district's entire evacuation would have queued against tertiary-road capacity — an artifact of how many times a corridor happens to cross a generalised boundary, not a property of Thanet.

So crossings within a configured radius are clustered, and the cluster is represented by its most major road class. `exit_highway_classes` is ordered most-major-first and does double duty as that preference order, which keeps the ranking in one place rather than introducing a second list that could drift from the first.

**Leaving proximity assignment alone and documenting the effect** was rejected because the emitted scenario would be actively misleading to whoever runs it: the concentration looks like a finding about Thanet's road network when it is a finding about OSM's way splitting. **Tie-breaking by road class within a tolerance, keeping the crossings as separate exits,** was rejected as treating the symptom — four exit records straddling one junction remain four destinations that a destination-choice model would later have to reason about, and the tolerance does the same work as a clustering radius while hiding it inside the assignment rule.

Clustering is single-linkage, so a chain of crossings closer than the radius merges transitively. At the radius chosen this is the desired behaviour for a corridor crossing repeatedly; it is a hazard only if a district's exits were ever spaced more finely than the radius along a boundary, and the count of crossings each exit represents is reported so that collapsing too much is visible rather than silent.

Representative selection takes an actual crossing rather than a cluster centroid, because a centroid would not lie on any way and the exit would lose the way reference the whole export is keyed on.

### Drivers get car legs, passengers get ride legs

MATSim's `ride` mode is teleported by default: the person travels, but no vehicle enters the network. This gives exactly one vehicle per departing car, which is the property the whole comparison rests on.

**MATSim's carpooling and DVRP contribs** were rejected as far heavier than needed — they exist to *decide* ride-sharing dynamically, and this pipeline has already decided it. **Omitting passengers from the population** was rejected because per-person plans are the main reason for choosing MATSim: dropping 108,000 of 134,617 people would make it impossible to report anything about who evacuated when, and would reduce the scenario to the OD table that MANTA was rejected for.

### Key network locations by OSM way identity, not link id

MATSim's OpenStreetMap readers retain the source way identifier on each link, so a way reference can be resolved against a built network. Link ids themselves are assigned at build time and differ between readers and between builds.

**Emitting MATSim link ids** was rejected because it would require this pipeline to build the network, coupling the plan to one network build — rebuild the network and every reference breaks. **Relying on coordinates alone** was considered seriously, since MATSim assigns activities to the nearest link itself and coordinates are sufficient for the base case. It was rejected as the *only* key because nearest-link assignment is exactly the step the pipeline already performed more carefully: a car parked on a dual carriageway or beside a junction can be assigned to the wrong carriageway, and with coordinates alone there is no way to detect the disagreement. Coordinates are still emitted; the way reference makes the assignment auditable and overridable.

Because MATSim splits ways at junctions, one way maps to several links. The recorded position along the way disambiguates: the adapter picks the link whose span contains it.

### Retain way identity at snap time rather than recomputing it

The snap already computes it. **Having the adapter re-snap** was rejected because it repeats 70,054 nearest-neighbour lookups against a *different* network representation, and can silently reach a different answer than the pipeline did — with no record of the pipeline's answer to compare against.

### Departure time = notification + mobilisation delay + driver access time

A right-skewed mobilisation curve (a lognormal delay after notification) is the standard finding in evacuation response research: most people leave fairly promptly, a long tail leaves much later, and the tail governs clearance.

**Retaining the single fleet-wide instant** was rejected for the reason in the proposal — it measures the insertion queue. **A uniform ramp over a fixed window** was rejected because it has no tail, and the tail is what clearance time is sensitive to. It remains reachable as a configuration if someone wants to test that sensitivity.

The driver access term is currently near zero: leaders are drawn from the owner household, so median walk to the vehicle is 0.0 minutes and p90 is 0.45. That is precisely why it must be a term rather than a hardcoded zero — under `minimise-evacuation-vehicles`, leaders will frequently be walking to a neighbour's car, and the term becomes material without any change to this code.

### Write MATSim XML directly from Python

**Calling MATSim or pt2matsim through JPype** was rejected as pulling a JVM into a Dagster pipeline to perform what is a serialisation task. MATSim's population, household and vehicle DTDs are small and stable.

Writing streams incrementally rather than building a document tree — 134,617 persons with multi-leg plans is too large to hold as a DOM comfortably — and output is gzipped, which MATSim reads natively.

### Emit coordinates in British National Grid

**Emitting WGS84** was rejected because MATSim's routing and scoring treat coordinates as planar metres; degrees would corrupt every distance in the simulation. EPSG:27700 is already the warehouse's metric CRS, so no transform is needed, and the CRS is declared in the emitted config so MATSim is told what it is reading.

## Risks / Trade-offs

- **Nearest-exit assignment over-concentrates demand.** Thanet Way is the obvious route west for most of the district, so proximity assignment will load it beyond what real drivers would tolerate before diverting. → Exit loading is reported as a first-class output, so the concentration is visible as an input assumption. A destination-choice model is a named follow-up, not a hidden gap.
- **Exits are infinite-capacity sinks.** Vehicles leaving the network at the boundary never queue on the A299 beyond it, so clearance time is optimistic — most so in the tail, where the real bottleneck would be downstream. → Report exit throughput over time so the sink assumption can be inspected. Modelling it properly needs the network extension deferred here; this should be stated wherever a clearance figure is quoted.
- **Coastal boundary precision.** ONS boundaries follow generalised mean high water, so which coastal roads clip the boundary is partly an artifact of generalisation. → The minimum-length-outside rule is the guard, and qualifying exits are reported with their measurements so the filter's behaviour is checkable rather than assumed.
- **The `muster_points` schema change re-materialises everything downstream.** Two added columns force `vehicles` onward to rebuild. → The columns are additive and the snap is unchanged, so the parking coordinates must come out identical; a spec scenario asserts exactly that, which makes the re-materialisation verifiable rather than merely expected.
- **Way-to-link resolution can still fail.** A way present in the pipeline's Overpass fetch might be absent or differently split in the extract MATSim's network was built from. → Emit the coordinate alongside every way reference so the adapter can fall back to nearest-link, and require the count of failed resolutions to be reported rather than silently absorbed, consistent with the project's convention that shortfalls are outputs.
- **Zero-duration collection stops.** MATSim may handle an activity with no duration unexpectedly. → A configured dwell time per collection stop, which is also more realistic than instantaneous pickup.

## Migration Plan

1. Add the two columns to `muster_points` and re-materialise from `vehicles` onward. Assert parked coordinates and snap distances are unchanged against the pre-change values.
2. Add the scenario assets downstream of `stop_notifications`; they read existing tables only, so nothing already in the warehouse changes because of them.
3. Rollback is asymmetric and cheap in the useful direction: the scenario assets can be removed with no effect on the plan, and the two `muster_points` columns are additive, so nothing reads them unless the scenario assets exist.

## Open Questions

- **Mobilisation distribution parameters.** The shape is decided (lognormal after notification); the median and spread need grounding in evacuation response literature. Config values with a documented provisional source are enough to build against, and recalibrating changes no code.
- **Collection-stop dwell time.** Needs a value; any plausible one is fine to build with.
- **Whether the emitted `config.xml` should carry more than the minimum.** Scoring parameters, replanning strategies and iteration counts are arguably the simulation operator's business rather than the pipeline's. Deferrable — a minimal config is runnable, and adding to it later changes no spec.
