"""The emitted MATSim scenario (tasks 6.8 - 6.14).

Parsed from the files the pipeline actually wrote, against the warehouse they
were written from.
"""

import gzip
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from london_frontline.config import PlanningConfig
from london_frontline.matsim import (
    ACTIVITY_COLLECTION,
    ACTIVITY_EVACUATION,
    ACTIVITY_HOME,
    ACTIVITY_MUSTER,
    MODE_CAR,
    MODE_RIDE,
    MODE_WALK,
)
from london_frontline.paths import resolve
from london_frontline.resources import SpatialDuckDBResource

NAMESPACE = "{http://www.matsim.org/files/dtd}"

# Attribute names that would bind the scenario to one simulator's network build.
# Link ids are assigned by whichever tool converts the OSM extract, so a plan
# carrying them breaks whenever the network is rebuilt.
FORBIDDEN_ATTRIBUTES = {"link", "facility", "start_link", "end_link", "route"}


@pytest.fixture(scope="module")
def scenario_dir():
    directory = resolve(PlanningConfig().data_dir) / "exports" / "matsim"
    if not (directory / "population.xml.gz").exists():
        pytest.skip("scenario not exported; materialise matsim_scenario first")
    return directory


@pytest.fixture(scope="module")
def conn():
    with SpatialDuckDBResource().connect() as connection:
        tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        if not {"person_seats", "vehicle_departures", "route_stops"} <= tables:
            pytest.skip("warehouse not materialised; run the pipeline first")
        yield connection


@pytest.fixture(scope="module")
def population(scenario_dir):
    """Summarise every plan once — 131,000 people is too many to re-parse per test."""
    people = {}
    car_leg_total = 0
    with gzip.open(scenario_dir / "population.xml.gz", "rt") as handle:
        for _, element in ET.iterparse(handle, events=("end",)):
            if element.tag != "person":
                continue
            plan = element.find("plan")
            modes, activities, times = [], [], []
            for child in plan:
                if child.tag == "leg":
                    modes.append(child.get("mode"))
                else:
                    activities.append(
                        (
                            child.get("type"),
                            float(child.get("x")),
                            float(child.get("y")),
                            child.get("end_time"),
                            child.get("max_dur"),
                        )
                    )
                    if child.get("end_time"):
                        hours, minutes, seconds = child.get("end_time").split(":")
                        times.append(
                            int(hours) * 3600 + int(minutes) * 60 + int(seconds)
                        )
            binding = element.find("attributes/attribute")
            people[int(element.get("id"))] = {
                "modes": tuple(modes),
                "activities": tuple(activities),
                "times": tuple(times),
                "vehicle": binding.text if binding is not None else None,
            }
            car_leg_total += modes.count(MODE_CAR)
            element.clear()
    return {"people": people, "car_legs": car_leg_total}


def test_one_driver_per_departing_vehicle(population, conn):
    """Task 6.8: the vehicle count on the network is the quantity under test.

    Counted in drivers, not car legs: a vehicle enters the network once, but its
    driver's plan has one car leg per hop, so a route with three collection stops
    contributes four legs and still one car.
    """
    departing, collection_stops = conn.execute(
        """
        SELECT (SELECT count(*) FROM vehicle_departures),
               (SELECT count(*) FROM route_stops WHERE stop_type = 'home_collection')
        """
    ).fetchone()

    drivers = [p for p in population["people"].values() if MODE_CAR in p["modes"]]
    assert len(drivers) == departing

    # Each collection stop adds exactly one further hop to its driver's plan.
    assert population["car_legs"] == departing + collection_stops


def test_no_passenger_plan_drives(population):
    """Task 6.8: a passenger rides, and ride is teleported — no second vehicle."""
    for person_id, plan in population["people"].items():
        if MODE_RIDE in plan["modes"]:
            assert MODE_CAR not in plan["modes"], f"person {person_id} both rides and drives"


def test_only_drivers_are_bound_to_a_vehicle(population):
    for person_id, plan in population["people"].items():
        drives = MODE_CAR in plan["modes"]
        assert (plan["vehicle"] is not None) == drives, person_id


def test_every_seated_person_has_exactly_one_plan(population, conn):
    """Task 6.9: the scenario is the seated population, no more and no less."""
    seated = {
        row[0] for row in conn.execute("SELECT person_id FROM person_seats").fetchall()
    }
    assert set(population["people"]) == seated


def test_unseated_people_are_absent(population, conn):
    """People with no seat cannot evacuate, and are recorded rather than invented."""
    unseated = conn.execute(
        """
        SELECT count(*) FROM persons p
        WHERE NOT EXISTS (SELECT 1 FROM person_seats s WHERE s.person_id = p.person_id)
        """
    ).fetchone()[0]
    assert unseated > 0
    total = conn.execute("SELECT count(*) FROM persons").fetchone()[0]
    assert len(population["people"]) == total - unseated


def test_activity_times_never_run_backwards(population):
    """Task 6.10: checked over the whole emitted population, not a sample."""
    for person_id, plan in population["people"].items():
        times = plan["times"]
        assert list(times) == sorted(times), f"person {person_id} has times out of order"


def test_every_plan_starts_at_home_and_ends_at_the_evacuation(population):
    for person_id, plan in population["people"].items():
        assert plan["activities"][0][0] == ACTIVITY_HOME, person_id
        assert plan["activities"][-1][0] == ACTIVITY_EVACUATION, person_id
        assert plan["activities"][-1][3] is None, f"person {person_id} ends with a time"


def test_walk_in_passengers_walk_before_they_ride(population, conn):
    """Spec: a walk to the vehicle precedes the drive for a group that can walk."""
    walk_in = conn.execute(
        """
        SELECT s.person_id FROM person_seats s
        JOIN travel_groups g ON g.travel_group_id = s.travel_group_id
        JOIN vehicle_departures d ON d.muster_point_id = s.muster_point_id
        WHERE g.all_can_walk AND d.leader_person_id <> s.person_id
        LIMIT 500
        """
    ).fetchall()
    assert walk_in
    for (person_id,) in walk_in:
        plan = population["people"][person_id]
        assert plan["modes"] == (MODE_WALK, MODE_RIDE), person_id
        assert [a[0] for a in plan["activities"]] == [
            ACTIVITY_HOME, ACTIVITY_MUSTER, ACTIVITY_EVACUATION
        ]


def test_collected_people_do_not_walk_to_the_muster_point(population, conn):
    """Spec: someone collected from home has no walk, and starts at their door."""
    collected = conn.execute(
        """
        SELECT s.person_id FROM person_seats s
        JOIN travel_groups g ON g.travel_group_id = s.travel_group_id
        JOIN vehicle_departures d ON d.muster_point_id = s.muster_point_id
        WHERE NOT g.all_can_walk AND d.leader_person_id <> s.person_id
        LIMIT 500
        """
    ).fetchall()
    assert collected
    for (person_id,) in collected:
        plan = population["people"][person_id]
        assert plan["modes"] == (MODE_RIDE,), person_id
        assert [a[0] for a in plan["activities"]] == [
            ACTIVITY_HOME, ACTIVITY_EVACUATION
        ]


def test_drivers_visit_collection_stops_in_route_order(population, conn):
    """Task 6.12: the route's stop order survives into the plan."""
    routes = conn.execute(
        """
        SELECT d.leader_person_id, s.stop_seq, s.easting, s.northing
        FROM vehicle_departures d
        JOIN route_stops s USING (muster_point_id)
        WHERE s.stop_type = 'home_collection'
          AND d.muster_point_id IN (
            SELECT muster_point_id FROM route_stops
            WHERE stop_type = 'home_collection'
            GROUP BY muster_point_id HAVING count(*) > 1
            LIMIT 300
          )
        ORDER BY d.leader_person_id, s.stop_seq
        """
    ).fetchall()
    assert routes, "expected vehicles making more than one collection stop"

    expected = {}
    for person_id, _, easting, northing in routes:
        expected.setdefault(person_id, []).append((round(easting, 2), round(northing, 2)))

    for person_id, stops in expected.items():
        emitted = [
            (round(a[1], 2), round(a[2], 2))
            for a in population["people"][person_id]["activities"]
            if a[0] == ACTIVITY_COLLECTION
        ]
        assert emitted == stops, f"person {person_id} visits stops out of order"


def test_collection_stops_carry_a_dwell_rather_than_an_arrival_time(population):
    """Arrival is the simulator's to decide; the dwell is ours."""
    dwelt = 0
    for plan in population["people"].values():
        for activity in plan["activities"]:
            if activity[0] == ACTIVITY_COLLECTION:
                assert activity[3] is None, "collection stops must not fix arrival"
                assert activity[4] is not None, "collection stops need a dwell"
                dwelt += 1
    assert dwelt > 0


def test_no_plan_carries_a_network_link_identifier(population, scenario_dir):
    """Task 6.14: keyed on OSM ways, never on a simulator's link ids.

    Link ids are assigned when the network is built, so a scenario carrying them
    could not survive a rebuild.
    """
    with gzip.open(scenario_dir / "population.xml.gz", "rt") as handle:
        for _, element in ET.iterparse(handle, events=("end",)):
            if element.tag in {"activity", "leg"}:
                present = FORBIDDEN_ATTRIBUTES & set(element.keys())
                assert not present, f"{element.tag} carries {present}"
            element.clear()


def test_every_referenced_vehicle_is_defined(population, scenario_dir):
    """Task 6.11: a plan may not name a vehicle the scenario does not define."""
    with gzip.open(scenario_dir / "vehicles.xml.gz", "rt") as handle:
        root = ET.parse(handle).getroot()
    defined = {v.get("id") for v in root.findall(f"{NAMESPACE}vehicle")}

    referenced = {
        plan["vehicle"].split('"')[3]
        for plan in population["people"].values()
        if plan["vehicle"]
    }
    assert referenced
    assert referenced <= defined


def test_every_household_member_has_a_plan(population, scenario_dir):
    """Task 6.11: household membership may not name people who never evacuate."""
    with gzip.open(scenario_dir / "households.xml.gz", "rt") as handle:
        root = ET.parse(handle).getroot()

    members = {
        int(person.get("refId"))
        for person in root.iter(f"{NAMESPACE}personId")
    }
    assert members == set(population["people"])


def test_every_household_vehicle_is_defined(scenario_dir):
    """Task 6.11: a household may not own a car absent from the scenario."""
    with gzip.open(scenario_dir / "vehicles.xml.gz", "rt") as handle:
        defined = {
            v.get("id")
            for v in ET.parse(handle).getroot().findall(f"{NAMESPACE}vehicle")
        }
    with gzip.open(scenario_dir / "households.xml.gz", "rt") as handle:
        owned = {
            v.get("refId")
            for v in ET.parse(handle).getroot().iter(f"{NAMESPACE}vehicleDefinitionId")
        }
    assert owned
    assert owned <= defined


@pytest.mark.parametrize(
    "filename,root_tag",
    [
        ("population.xml.gz", "population"),
        ("vehicles.xml.gz", f"{NAMESPACE}vehicleDefinitions"),
        ("households.xml.gz", f"{NAMESPACE}households"),
        ("config.xml", "config"),
    ],
)
def test_every_document_parses_with_the_expected_root(scenario_dir, filename, root_tag):
    """Task 6.13, in part.

    This is well-formedness and structural conformance, NOT validation against
    the MATSim DTDs: that needs lxml and a vendored copy of each DTD, since
    fetching them from matsim.org would make the suite depend on that host.
    """
    path: Path = scenario_dir / filename
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt") as handle:
        assert ET.parse(handle).getroot().tag == root_tag


def test_the_config_declares_the_coordinate_reference_system(scenario_dir):
    """Spec: coordinates are metres, and MATSim must be told so."""
    root = ET.parse(scenario_dir / "config.xml").getroot()
    crs = root.find("module[@name='global']/param[@name='coordinateSystem']")
    assert crs is not None
    assert crs.get("value") == "EPSG:27700"


def test_network_references_resolve_to_real_ways(conn):
    """Task 7.2's invariant: every bound location names a way that exists."""
    unresolved = conn.execute(
        """
        SELECT count(*) FROM scenario_network_refs r
        WHERE r.way_id IS NULL OR r.way_position_m IS NULL
           OR NOT EXISTS (SELECT 1 FROM road_centrelines c WHERE c.way_id = r.way_id)
        """
    ).fetchone()[0]
    assert unresolved == 0


def test_network_references_cover_every_muster_point_and_exit(conn):
    covered, expected = conn.execute(
        """
        SELECT (SELECT count(*) FROM scenario_network_refs),
               (SELECT count(DISTINCT muster_point_id) FROM vehicle_departures)
               + (SELECT count(*) FROM district_exits)
        """
    ).fetchone()
    assert covered == expected


def test_the_scenario_accounts_for_everyone(conn):
    """Task 7.1: people absent from the scenario are exactly those recorded unseated.

    A mismatch would mean people going missing between the plan and the export
    rather than being recorded as unable to evacuate.
    """
    coverage = dict(
        conn.execute(
            "SELECT measure, value FROM report_scenario_coverage"
        ).fetchall()
    )
    assert (
        coverage["people holding a seat"] + coverage["people with no seat"]
        == coverage["people in the population"]
    )
    assert coverage["people with no seat"] == coverage["people recorded as unmet demand"]


def test_unresolved_network_references_are_recorded_not_raised(conn):
    """Task 7.2: consistent with the project's rule that shortfalls are outputs."""
    tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
    assert "scenario_shortfalls" in tables

    recorded = conn.execute(
        "SELECT coalesce(sum(affected), 0) FROM scenario_shortfalls "
        "WHERE reason LIKE '%could not be resolved%'"
    ).fetchone()[0]
    actual = conn.execute(
        """
        SELECT count(*) FROM scenario_network_refs r
        WHERE r.way_id IS NULL OR r.way_position_m IS NULL
           OR NOT EXISTS (SELECT 1 FROM road_centrelines c WHERE c.way_id = r.way_id)
        """
    ).fetchone()[0]
    assert recorded == actual
