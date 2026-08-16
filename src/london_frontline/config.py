"""Every tunable value for the pipeline, in one place.

The design constraint is that no threshold — capacity, distance, ceiling, age,
speed, or time — appears as a literal inside an algorithm. If a number matters,
it is named here.
"""

from dagster import ConfigurableResource


class PlanningConfig(ConfigurableResource):
    """Tunables for one end-to-end planning run."""

    # --- Area selection -----------------------------------------------------
    # Local Authority District to plan for. E07000114 is Thanet District Council,
    # Kent. Changing this value is the only edit needed to target another LAD.
    lad_code: str = "E07000114"

    # --- Reproducibility ----------------------------------------------------
    # Synthesis draws are seeded so a run is reproducible; two runs with the same
    # seed and the same source data produce the same population.
    random_seed: int = 20260815

    # --- Population synthesis ----------------------------------------------
    minimum_driving_age: int = 17
    # Share of people at or over the minimum driving age holding a full car
    # licence. Order-of-magnitude figure for England; refine against DfT NTS.
    car_license_proportion: float = 0.76
    # Share of people at or over the minimum driving age holding a bus licence.
    # Carried for forward compatibility; bus routing is out of scope.
    bus_license_proportion: float = 0.01
    # Share of generated persons left without a phone number, to exercise the
    # notification-fallback path.
    missing_phone_proportion: float = 0.08
    # Share of generated persons flagged as having a medical skill.
    medical_skill_proportion: float = 0.03
    # TS038 disability categories mapped to mobility_status = false (cannot walk,
    # must be collected from home). Matched as a case-insensitive substring of the
    # category label. "Limited a lot" is the strongest signal the Census offers;
    # "limited a little" is deliberately excluded, as it does not imply being
    # unable to walk to a neighbouring street.
    non_walking_disability_labels: list[str] = ["limited a lot"]
    # Household size is published with an open top category ("8 or more people").
    # This is the size assumed for it.
    open_ended_household_size: int = 8
    # Car availability is published with an open top category ("3 or more").
    open_ended_car_count: int = 3
    # The generated population is the *household* population, so it falls short of
    # the published resident total by the number of people in communal
    # establishments (care homes, halls, prisons), which belong to no household.
    # Validation fails if the shortfall exceeds this fraction, which would mean
    # something beyond communal establishments is wrong.
    max_person_shortfall_fraction: float = 0.05

    # --- Relationships ------------------------------------------------------
    # A household member must be at least this many years older than a child to
    # be treated as that child's co-resident parent.
    minimum_parent_child_age_gap_years: int = 16
    # Upper bounds of the dependency age bands.
    both_parents_required_below_age: int = 10
    one_parent_required_below_age: int = 16

    # --- Home locations -----------------------------------------------------
    # NSUL is published as a whole-GB archive split into region members; only the
    # one covering the LAD is extracted. Codes are NSUL's own: EE, EM, LN, NE,
    # NW, SC, SE, SW, WA, WM, YH. Thanet is in the South East.
    nsul_region_code: str = "SE"

    # --- Vehicles and muster points ----------------------------------------
    vehicle_capacity: int = 5
    # Vehicles snap to their nearest drivable road point with NO distance cap:
    # OSM has not mapped many residential access roads, service roads and
    # driveways, so a large snap distance means missing map data rather than a
    # dwelling with no road access. This threshold is for reporting only — the
    # count of vehicles beyond it is a data-quality signal, never a filter.
    parking_snap_review_distance_m: float = 20.0
    # Search radii, in metres, tried in order when finding each vehicle's nearest
    # road. Expanding progressively keeps the spatial index useful; a single
    # unbounded nearest-neighbour join over the whole segment table would be a
    # cross product.
    parking_search_radii_m: list[float] = [25.0, 60.0, 150.0, 500.0, 2000.0, 10000.0]

    # --- Seat assignment ----------------------------------------------------
    # No walk-in group is assigned to a vehicle further than this walk away.
    walking_ceiling_minutes: float = 10.0
    # Assumed walking speed for converting network distance to walking time.
    walking_speed_kph: float = 4.8
    # How many nearby muster points to precompute per household. A full
    # walking-distance matrix is not viable — at Thanet's vehicle density roughly
    # 1,300 cars sit within a 10-minute walk — so assignment considers this many
    # nearest candidates. Groups that exhaust their candidates are counted
    # separately from genuine capacity shortfalls, so the cap is never mistaken
    # for unmet demand.
    walk_candidates_per_household: int = 50
    # A home-collection group is only assigned to a vehicle within this
    # straight-line distance of it.
    max_collection_distance_m: float = 2000.0
    # Cap on distinct home-collection stops a single vehicle will make, so that
    # unavoidable stops spread across the fleet.
    max_collection_stops_per_vehicle: int = 3
    # Where two vehicles would seat the same number of people, prefer the one
    # whose owner household is aboard: it needs no key handover, and it is a less
    # surprising instruction to give a real person.
    prefer_owner_aboard: bool = True
    # A vehicle is not activated to carry fewer than this many people. The
    # default of 1 activates anything that seats somebody, on the grounds that a
    # person left behind is worse than a near-empty car.
    minimum_vehicle_occupancy: int = 1

    # --- Routes -------------------------------------------------------------
    # Single fleet-wide departure time; every muster-point stop uses it.
    fleet_departure_time: str = "2026-01-01T08:00:00"
    # Used to estimate arrival times at successive collection stops from
    # straight-line distance. No congestion is modelled.
    average_driving_speed_kph: float = 30.0
    # Time allowed at each stop for people to reach the kerb and board, added to
    # the meeting time of every stop after it. Travel time alone cannot carry a
    # schedule here: Thanet's collection legs are 17 m at the median, so a whole
    # route completes inside 97 seconds and every stop on it reads 08:00 at the
    # minute resolution a message quotes. Boarding, not driving, is what a
    # multi-stop pickup actually spends its time on.
    stop_dwell_minutes: float = 5.0
    # Single fleet-wide evacuation destination. Default is Canterbury, the
    # nearest large centre outside Thanet.
    destination_name: str = "Canterbury"
    destination_latitude: float = 51.2802
    destination_longitude: float = 1.0789

    # --- Microsimulation scenario export ------------------------------------
    # Highway classes counted as through routes when qualifying district exits.
    # Service roads, tracks and footways are excluded: a driveway or car-park
    # access crossing the boundary is not an evacuation route.
    #
    # ORDER IS SIGNIFICANT, most major first. The same list decides which
    # crossing represents a cluster of co-located ones, so that a corridor is
    # entered by its largest road. Keeping one ordered list rather than a
    # separate ranking stops the two drifting apart.
    exit_highway_classes: list[str] = [
        "motorway",
        "motorway_link",
        "trunk",
        "trunk_link",
        "primary",
        "primary_link",
        "secondary",
        "secondary_link",
        "tertiary",
        "tertiary_link",
        "unclassified",
        "residential",
    ]
    # A way must continue at least this far beyond the district boundary to be an
    # exit. Much of a coastal district's boundary is coastline, so a seafront or
    # harbour road clips it without ever leaving the district. In Thanet this
    # rejects the two Royal Harbour Approach ways (29 m and 8 m beyond the
    # boundary) while admitting the shortest real exit, Plucks Gutter, at 66 m.
    minimum_exit_length_outside_m: float = 50.0
    # Crossings within this distance of each other describe the same way out of
    # the district and are collapsed into one exit. A dual carriageway crosses
    # once per carriageway, a way can leave and re-enter, and one road is often
    # mapped as several ways of different classes — without this, which crossing
    # a vehicle heads for turns on a few metres. In Thanet, four Ramsgate Road
    # crossings sit within 292 m and would otherwise split a corridor carrying
    # 90% of the fleet across a tertiary way and two trunk ways.
    exit_cluster_radius_m: float = 500.0

    # Time the evacuation order is issued. Distinct from fleet_departure_time,
    # which is when the leader-facing plan says a vehicle sets off: departures
    # here are *drawn* relative to notification rather than fixed.
    evacuation_notification_time: str = "2026-01-01T08:00:00"
    # Delay between notification and a vehicle being ready to move, drawn per
    # vehicle from a lognormal. Right-skewed on purpose: most people leave
    # promptly and a long tail leaves much later, and it is the tail that governs
    # clearance time. A uniform ramp has no tail and would understate it.
    # PROVISIONAL: the shape is decided, but these two values need grounding in
    # evacuation response literature. See design.md, Open Questions.
    mobilisation_median_minutes: float = 15.0
    # Spread, as the sigma of the underlying normal. Setting this to 0 collapses
    # the draw onto the median, so every vehicle mobilises together; combined
    # with mobilisation_median_minutes = 0 and include_driver_access_time =
    # false, that reproduces a single fleet-wide departure at notification.
    mobilisation_sigma: float = 0.6
    # Whether a driver's journey from home to the vehicle delays its departure.
    include_driver_access_time: bool = True
    # How long a vehicle waits at each home-collection stop.
    # PROVISIONAL: needs a defensible value. See design.md, Open Questions.
    collection_stop_dwell_seconds: float = 120.0
    # --- Live re-planning ---------------------------------------------------
    # A live re-plan measures from where the person is standing, in a straight
    # line, because it has to answer at the kerb in under a second — the routed
    # pedestrian graph the original assignment used is not on hand. Street
    # networks make a walk longer than the crow flies; this scales the straight
    # line before it is checked against the walking ceiling, so the tool never
    # sends somebody on a walk the plan itself would have refused.
    walk_detour_factor: float = 1.3
    # How long someone waits at the kerb before the tool will accept "they did
    # not come". Long enough that a slow walk or a wrong door is not reported as
    # an absence; short enough that a driver is not held at a door indefinitely.
    no_show_wait_minutes: float = 30.0
    # How long after its last planned stop a vehicle can still be diverted to
    # pick somebody up. Beyond this it is treated as gone: it is on the trunk
    # road out of the district and turning it round costs more than it saves.
    divert_grace_minutes: float = 15.0
    # A rescue bus is dispatched to the stranded person rather than expecting
    # them to walk to it — the people most likely to be stranded are the ones
    # who could not walk to a muster point in the first place.
    rescue_bus_capacity: int = 50
    # Time from the report to the bus reaching the kerb.
    rescue_bus_delay_minutes: float = 45.0
    # Two reports this far apart share a bus rather than opening a second one.
    rescue_bus_pickup_radius_m: float = 1500.0

    # --- Storage ------------------------------------------------------------
    # Directory for downloaded source extracts and exported outputs.
    data_dir: str = "data"
