# vehicle-and-muster-points Specification

## Purpose
Synthesizes one vehicle per car owned by a synthetic household and parks it at a concrete on-street location beside that household's home. That parking location *is* the muster point, so every muster point is exactly one vehicle with a known seat count.

## Requirements

### Requirement: One vehicle per car-owning household
For each synthetic Household with num_cars ≥ 1, the system SHALL generate exactly num_cars Vehicle records, each linked to that household as owner.

#### Scenario: Household with one car
- **WHEN** a household's num_cars is 1
- **THEN** exactly one Vehicle record SHALL be created with owner_household_id set to that household

#### Scenario: Household with no car
- **WHEN** a household's num_cars is 0
- **THEN** no Vehicle record SHALL be created for that household

### Requirement: Vehicle type and identification
Each Vehicle SHALL have a vehicle_type attribute (`car` or `bus`) and a license_plate attribute, so that passengers can identify the correct vehicle at a muster point. For this iteration, every generated Vehicle SHALL have vehicle_type = `car`; bus generation and bus-specific routing (where a bus's start point differs from any owner's household) are out of scope (see design.md Non-Goals).

#### Scenario: Vehicle identification populated
- **WHEN** a Vehicle record is generated
- **THEN** it SHALL have vehicle_type = `car` and a non-empty license_plate value

### Requirement: Fixed seat capacity
Every generated Vehicle SHALL have a capacity attribute set to a single configured fixed value for this iteration, rather than sampled from a distribution.

#### Scenario: Capacity assignment
- **WHEN** a Vehicle record is generated
- **THEN** its capacity SHALL equal the configured fixed capacity value

### Requirement: Vehicle parking location
Each Vehicle SHALL be parked at the nearest point on the drivable road network to its owner household's assigned home location, whatever that distance. The system SHALL NOT exclude a Vehicle from the fleet for being far from a mapped road: OpenStreetMap's coverage of residential access roads, service roads and driveways is incomplete, so a large snap distance indicates a gap in the map rather than a dwelling with no road access. The snap distance SHALL be recorded per Vehicle and reported as a distribution, so the effect of that incompleteness stays visible.

#### Scenario: Home location beside a mapped road
- **WHEN** a Vehicle's owner household's home location has a mapped drivable road nearby
- **THEN** the Vehicle's parking location SHALL be the nearest point on that road

#### Scenario: Home location far from any mapped road
- **WHEN** the nearest mapped drivable road is far from the owner household's home location
- **THEN** the Vehicle SHALL still be parked at that nearest point and SHALL remain in the available fleet, with its snap distance recorded

#### Scenario: Snap distances reported
- **WHEN** vehicles have been parked
- **THEN** the distribution of snap distances SHALL be reported, including the count exceeding a configured review threshold

### Requirement: Muster point is a parked vehicle
A muster point SHALL be defined as the parking location of exactly one Vehicle. The system SHALL NOT derive muster points from a separate catalog of sites (schools, community centres, car parks). A muster point's capacity SHALL be the seat capacity of its Vehicle — there is no site-level capacity distinct from seats.

#### Scenario: Muster point derivation
- **WHEN** a Vehicle is assigned a parking location
- **THEN** a muster point SHALL exist at that location, associated with exactly that Vehicle, with capacity equal to that Vehicle's capacity

#### Scenario: No muster point without a vehicle
- **WHEN** a location has no Vehicle parked at it
- **THEN** it SHALL NOT be a muster point, regardless of its suitability as a gathering site
