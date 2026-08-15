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
    # Single fleet-wide evacuation destination. Default is Canterbury, the
    # nearest large centre outside Thanet.
    destination_name: str = "Canterbury"
    destination_latitude: float = 51.2802
    destination_longitude: float = 1.0789

    # --- Storage ------------------------------------------------------------
    # Directory for downloaded source extracts and exported outputs.
    data_dir: str = "data"
