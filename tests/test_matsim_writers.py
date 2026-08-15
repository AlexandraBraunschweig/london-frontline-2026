"""The MATSim writers themselves (task 6.10's rule, and plan well-formedness).

Pure, so these run without a warehouse.
"""

import gzip
import xml.etree.ElementTree as ET

import pytest

from london_frontline.matsim import (
    ACTIVITY_EVACUATION,
    ACTIVITY_HOME,
    ACTIVITY_MUSTER,
    MODE_CAR,
    MODE_RIDE,
    MODE_WALK,
    Activity,
    Leg,
    Plan,
    format_time,
    write_config,
    write_households,
    write_population,
    write_vehicles,
)


def driver_plan(person_id=1, vehicle_id=7):
    return Plan(
        person_id=person_id,
        vehicle_id=vehicle_id,
        elements=(
            Activity(ACTIVITY_HOME, 1.0, 2.0, end_time_s=28800),
            Leg(MODE_WALK),
            Activity(ACTIVITY_MUSTER, 3.0, 4.0, end_time_s=29100),
            Leg(MODE_CAR),
            Activity(ACTIVITY_EVACUATION, 5.0, 6.0),
        ),
    )


def passenger_plan(person_id=2):
    return Plan(
        person_id=person_id,
        elements=(
            Activity(ACTIVITY_HOME, 1.0, 2.0, end_time_s=28800),
            Leg(MODE_RIDE),
            Activity(ACTIVITY_EVACUATION, 5.0, 6.0),
        ),
    )


@pytest.mark.parametrize(
    "seconds,expected",
    [(0, "00:00:00"), (28800, "08:00:00"), (3661, "01:01:01"), (90000, "25:00:00")],
)
def test_time_formatting_runs_past_midnight(seconds, expected):
    """MATSim clock times pass 24:00 rather than wrapping, so a night-time
    evacuation stays ordered."""
    assert format_time(seconds) == expected


def test_negative_time_is_rejected():
    with pytest.raises(ValueError):
        format_time(-1)


def test_a_plan_must_alternate_activities_and_legs():
    with pytest.raises(ValueError, match="expected"):
        Plan(
            person_id=1,
            elements=(
                Activity(ACTIVITY_HOME, 0, 0, end_time_s=0),
                Leg(MODE_CAR),
                Leg(MODE_CAR),
                Activity(ACTIVITY_EVACUATION, 1, 1),
            ),
        ).validate()


def test_a_plan_must_start_and_end_at_an_activity():
    with pytest.raises(ValueError, match="start"):
        Plan(person_id=1, elements=(Leg(MODE_CAR),)).validate()


def test_the_final_activity_must_not_have_an_end_time():
    """There is nowhere to go after the evacuation activity."""
    with pytest.raises(ValueError, match="final activity"):
        Plan(
            person_id=1,
            elements=(
                Activity(ACTIVITY_HOME, 0, 0, end_time_s=100),
                Leg(MODE_CAR),
                Activity(ACTIVITY_EVACUATION, 1, 1, end_time_s=200),
            ),
        ).validate()


def test_times_running_backwards_are_rejected():
    """Task 6.10's rule, enforced at write time rather than only checked after."""
    with pytest.raises(ValueError, match="backwards"):
        Plan(
            person_id=1,
            elements=(
                Activity(ACTIVITY_HOME, 0, 0, end_time_s=500),
                Leg(MODE_WALK),
                Activity(ACTIVITY_MUSTER, 1, 1, end_time_s=100),
                Leg(MODE_CAR),
                Activity(ACTIVITY_EVACUATION, 2, 2),
            ),
        ).validate()


def test_an_empty_plan_is_rejected():
    with pytest.raises(ValueError, match="empty"):
        Plan(person_id=1, elements=()).validate()


def test_population_round_trips_through_xml(tmp_path):
    path = tmp_path / "population.xml.gz"
    written = write_population(path, [driver_plan(), passenger_plan()])
    assert written == 2

    with gzip.open(path, "rt") as handle:
        root = ET.parse(handle).getroot()

    assert root.tag == "population"
    people = root.findall("person")
    assert [p.get("id") for p in people] == ["1", "2"]

    driver_modes = [leg.get("mode") for leg in people[0].iter("leg")]
    assert driver_modes == [MODE_WALK, MODE_CAR]
    assert people[0].find("attributes/attribute").get("name") == "vehicles"

    # A passenger puts no vehicle on the network, so carries no vehicle binding.
    assert [leg.get("mode") for leg in people[1].iter("leg")] == [MODE_RIDE]
    assert people[1].find("attributes") is None


def test_the_doctype_is_declared(tmp_path):
    """MATSim selects its parser from the declared DTD."""
    path = tmp_path / "population.xml.gz"
    write_population(path, [passenger_plan()])
    with gzip.open(path, "rt") as handle:
        head = handle.read(400)
    assert "<!DOCTYPE population" in head
    assert "population_v6.dtd" in head


def test_an_invalid_plan_stops_the_write(tmp_path):
    """A malformed plan must not reach the file to be discovered by MATSim."""
    broken = Plan(person_id=3, elements=(Leg(MODE_CAR),))
    with pytest.raises(ValueError):
        write_population(tmp_path / "population.xml.gz", [driver_plan(), broken])


def test_identifiers_are_escaped(tmp_path):
    """Ids are numeric today; escaping means that staying true is not load-bearing."""
    path = tmp_path / "population.xml.gz"
    plan = Plan(
        person_id='a"b&c',
        elements=(
            Activity('ho"me', 1.0, 2.0, end_time_s=0),
            Leg(MODE_RIDE),
            Activity(ACTIVITY_EVACUATION, 3.0, 4.0),
        ),
    )
    write_population(path, [plan])
    with gzip.open(path, "rt") as handle:
        root = ET.parse(handle).getroot()
    assert root.find("person").get("id") == 'a"b&c'


def test_vehicles_declare_one_type_and_every_vehicle(tmp_path):
    path = tmp_path / "vehicles.xml.gz"
    written = write_vehicles(path, [3, 1, 2], seats=5)
    assert written == 3

    with gzip.open(path, "rt") as handle:
        root = ET.parse(handle).getroot()
    namespace = "{http://www.matsim.org/files/dtd}"
    assert len(root.findall(f"{namespace}vehicleType")) == 1
    assert [v.get("id") for v in root.findall(f"{namespace}vehicle")] == ["3", "1", "2"]
    capacity = root.find(f"{namespace}vehicleType/{namespace}capacity")
    assert capacity.get("seats") == "5"


def test_households_carry_members_and_owned_vehicles(tmp_path):
    path = tmp_path / "households.xml.gz"
    written = write_households(path, [(10, [1, 2], [7]), (11, [3], [])])
    assert written == 2

    with gzip.open(path, "rt") as handle:
        root = ET.parse(handle).getroot()
    namespace = "{http://www.matsim.org/files/dtd}"
    households = root.findall(f"{namespace}household")
    assert [h.get("id") for h in households] == ["10", "11"]
    assert len(households[0].findall(f"{namespace}members/{namespace}personId")) == 2
    # A household whose cars all stayed parked owns nothing in this scenario.
    assert households[1].find(f"{namespace}vehicles") is None


def test_config_declares_the_coordinate_system(tmp_path):
    """MATSim cannot infer the CRS, and degrees would corrupt every distance."""
    path = tmp_path / "config.xml"
    write_config(path, "network.xml.gz", "population.xml.gz", "vehicles.xml.gz",
                 "households.xml.gz")
    root = ET.parse(path).getroot()

    globals_ = root.find("module[@name='global']")
    crs = globals_.find("param[@name='coordinateSystem']")
    assert crs.get("value") == "EPSG:27700"

    plans = root.find("module[@name='plans']/param[@name='inputPlansFile']")
    assert plans.get("value") == "population.xml.gz"
