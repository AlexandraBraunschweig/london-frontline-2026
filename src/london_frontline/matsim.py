"""Writing a MATSim scenario.

MATSim's population, household and vehicle formats are small and stable, so they
are written directly rather than through MATSim itself — putting a JVM inside a
Dagster pipeline to perform what is a serialisation task would be a poor trade.

Everything streams. The population is 131,000 people with multi-leg plans, which
is more than is comfortable to hold as a document tree, and gzip is written
inline because MATSim reads compressed inputs natively.

Coordinates are British National Grid throughout. MATSim treats coordinates as
planar metres for routing and scoring, so degrees would corrupt every distance in
the simulation; the CRS is declared in the config so MATSim is told what it is
reading rather than left to assume.

Kept free of Dagster and DuckDB so the emitted documents can be tested directly.
"""

from __future__ import annotations

import gzip
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence
from xml.sax.saxutils import quoteattr

CRS = "EPSG:27700"

MODE_CAR = "car"
MODE_RIDE = "ride"
MODE_WALK = "walk"

ACTIVITY_HOME = "home"
ACTIVITY_MUSTER = "muster"
ACTIVITY_COLLECTION = "collection"
ACTIVITY_EVACUATION = "evacuation"

POPULATION_DOCTYPE = (
    '<!DOCTYPE population SYSTEM "http://www.matsim.org/files/dtd/population_v6.dtd">'
)
HOUSEHOLDS_SCHEMA = (
    'xmlns="http://www.matsim.org/files/dtd" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    'xsi:schemaLocation="http://www.matsim.org/files/dtd '
    'http://www.matsim.org/files/dtd/households_v1.0.xsd"'
)
VEHICLES_SCHEMA = (
    'xmlns="http://www.matsim.org/files/dtd" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    'xsi:schemaLocation="http://www.matsim.org/files/dtd '
    'http://www.matsim.org/files/dtd/vehicleDefinitions_v2.0.xsd"'
)


@dataclass(frozen=True)
class Activity:
    """Somewhere a person is, for a while."""

    type: str
    easting: float
    northing: float
    # Absolute clock time, seconds from midnight. The last activity of a plan
    # carries neither, because there is nowhere for the person to go next.
    end_time_s: float | None = None
    # Used instead of end_time_s where arrival time is the simulator's to decide,
    # as at a collection stop: stay this long, whenever you get here.
    max_duration_s: float | None = None


@dataclass(frozen=True)
class Leg:
    """Travel between two activities."""

    mode: str


@dataclass(frozen=True)
class Plan:
    """One person's day. Elements alternate activity, leg, activity, ..."""

    person_id: int
    elements: tuple[Activity | Leg, ...]
    # Set only for a driver: the vehicle they put on the network.
    vehicle_id: int | None = None

    def validate(self) -> None:
        if not self.elements:
            raise ValueError(f"person {self.person_id} has an empty plan")
        if not isinstance(self.elements[0], Activity):
            raise ValueError(f"person {self.person_id} does not start at an activity")
        if not isinstance(self.elements[-1], Activity):
            raise ValueError(f"person {self.person_id} does not end at an activity")
        for position, element in enumerate(self.elements):
            expected = Activity if position % 2 == 0 else Leg
            if not isinstance(element, expected):
                raise ValueError(
                    f"person {self.person_id} has {type(element).__name__} at "
                    f"position {position}, expected {expected.__name__}"
                )
        if self.elements[-1].end_time_s is not None:
            raise ValueError(
                f"person {self.person_id} has an end time on their final activity"
            )

        last = None
        for element in self.elements:
            if isinstance(element, Activity) and element.end_time_s is not None:
                if last is not None and element.end_time_s < last:
                    raise ValueError(
                        f"person {self.person_id} has activity times running "
                        f"backwards: {element.end_time_s} after {last}"
                    )
                last = element.end_time_s

    @property
    def modes(self) -> tuple[str, ...]:
        return tuple(e.mode for e in self.elements if isinstance(e, Leg))


def format_time(seconds: float) -> str:
    """MATSim clock time. Hours run past 24 rather than wrapping to the next day."""
    if seconds < 0:
        raise ValueError(f"time must not be negative, got {seconds}")
    whole = int(round(seconds))
    return f"{whole // 3600:02d}:{whole % 3600 // 60:02d}:{whole % 60:02d}"


def _coordinate(value: float) -> str:
    return f"{value:.2f}"


@contextmanager
def _document(path: Path) -> Iterator:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") if path.suffix == ".gz" else open(
        path, "w", encoding="utf-8"
    ) as handle:
        handle.write('<?xml version="1.0" encoding="utf-8"?>\n')
        yield handle


def write_population(path: Path, plans: Iterable[Plan]) -> int:
    """Stream plans out, returning how many were written."""
    written = 0
    with _document(path) as handle:
        handle.write(f"{POPULATION_DOCTYPE}\n<population>\n")
        for plan in plans:
            plan.validate()
            handle.write(f"\t<person id={quoteattr(str(plan.person_id))}>\n")
            if plan.vehicle_id is not None:
                # How MATSim binds a driver to the vehicle they take.
                handle.write(
                    "\t\t<attributes>\n"
                    '\t\t\t<attribute name="vehicles" '
                    'class="org.matsim.vehicles.PersonVehicles">'
                    f'{{"{MODE_CAR}":"{plan.vehicle_id}"}}'
                    "</attribute>\n"
                    "\t\t</attributes>\n"
                )
            handle.write('\t\t<plan selected="yes">\n')
            for element in plan.elements:
                if isinstance(element, Leg):
                    handle.write(f"\t\t\t<leg mode={quoteattr(element.mode)}/>\n")
                    continue
                attributes = [
                    f"type={quoteattr(element.type)}",
                    f'x="{_coordinate(element.easting)}"',
                    f'y="{_coordinate(element.northing)}"',
                ]
                if element.end_time_s is not None:
                    attributes.append(f'end_time="{format_time(element.end_time_s)}"')
                if element.max_duration_s is not None:
                    attributes.append(
                        f'max_dur="{format_time(element.max_duration_s)}"'
                    )
                handle.write(f"\t\t\t<activity {' '.join(attributes)}/>\n")
            handle.write("\t\t</plan>\n\t</person>\n")
            written += 1
        handle.write("</population>\n")
    return written


def write_vehicles(
    path: Path, vehicle_ids: Iterable[int], seats: int, vehicle_type: str = MODE_CAR
) -> int:
    written = 0
    with _document(path) as handle:
        handle.write(f"<vehicleDefinitions {VEHICLES_SCHEMA}>\n")
        handle.write(
            f"\t<vehicleType id={quoteattr(vehicle_type)}>\n"
            f'\t\t<capacity seats="{seats}" standingRoomInPersons="0"/>\n'
            '\t\t<length meter="7.5"/>\n'
            '\t\t<width meter="1.0"/>\n'
            '\t\t<networkMode networkMode="car"/>\n'
            "\t</vehicleType>\n"
        )
        for vehicle_id in vehicle_ids:
            handle.write(
                f"\t<vehicle id={quoteattr(str(vehicle_id))} "
                f"type={quoteattr(vehicle_type)}/>\n"
            )
            written += 1
        handle.write("</vehicleDefinitions>\n")
    return written


def write_households(
    path: Path, households: Iterable[tuple[int, Sequence[int], Sequence[int]]]
) -> int:
    """Each entry is (household id, member person ids, owned vehicle ids)."""
    written = 0
    with _document(path) as handle:
        handle.write(f"<households {HOUSEHOLDS_SCHEMA}>\n")
        for household_id, members, vehicles in households:
            handle.write(f"\t<household id={quoteattr(str(household_id))}>\n")
            handle.write("\t\t<members>\n")
            for person_id in members:
                handle.write(f"\t\t\t<personId refId={quoteattr(str(person_id))}/>\n")
            handle.write("\t\t</members>\n")
            if vehicles:
                handle.write("\t\t<vehicles>\n")
                for vehicle_id in vehicles:
                    handle.write(
                        "\t\t\t<vehicleDefinitionId "
                        f"refId={quoteattr(str(vehicle_id))}/>\n"
                    )
                handle.write("\t\t</vehicles>\n")
            handle.write("\t</household>\n")
            written += 1
        handle.write("</households>\n")
    return written


def write_config(
    path: Path,
    network_file: str,
    plans_file: str,
    vehicles_file: str,
    households_file: str,
    last_iteration: int = 0,
) -> None:
    """A minimal runnable config.

    Deliberately minimal: scoring parameters, replanning strategies and iteration
    counts are the simulation operator's business, not the pipeline's. The one
    thing that is not optional is the coordinate system, because MATSim cannot
    infer it and would otherwise treat these metres as whatever it assumed.

    The network file is named but NOT produced here — it is built from the same
    OpenStreetMap extract by MATSim's own reader.
    """
    modules = {
        "global": {"coordinateSystem": CRS, "randomSeed": "4711"},
        "network": {"inputNetworkFile": network_file},
        "plans": {"inputPlansFile": plans_file},
        "vehicles": {"vehiclesFile": vehicles_file},
        "households": {"inputFile": households_file},
        "controler": {
            "firstIteration": "0",
            "lastIteration": str(last_iteration),
            "outputDirectory": "./output",
        },
        "qsim": {
            "startTime": "00:00:00",
            "endTime": "36:00:00",
            "mainMode": MODE_CAR,
        },
    }
    with _document(path) as handle:
        handle.write(
            '<!DOCTYPE config SYSTEM '
            '"http://www.matsim.org/files/dtd/config_v2.dtd">\n<config>\n'
        )
        for module, parameters in modules.items():
            handle.write(f"\t<module name={quoteattr(module)}>\n")
            for name, value in parameters.items():
                handle.write(
                    f"\t\t<param name={quoteattr(name)} value={quoteattr(value)}/>\n"
                )
            handle.write("\t</module>\n")
        handle.write("</config>\n")
