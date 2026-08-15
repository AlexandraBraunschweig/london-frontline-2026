## Purpose

Synthesizes one vehicle per car owned by a synthetic household and parks it at a concrete on-street location beside that household's home. That parking location *is* the muster point, so every muster point is exactly one vehicle with a known seat count.

## ADDED Requirements

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
Each Vehicle SHALL be parked at the nearest point on the drivable road network to its owner household's assigned home location. That parking point SHALL lie within a configured maximum snap distance (default 20 metres) of the home location. Where the nearest road point exceeds that distance, the system SHALL record the Vehicle as having an unresolved parking location rather than placing it at an arbitrary point.

#### Scenario: Home location beside a road
- **WHEN** a Vehicle's owner household's home location has a drivable road point within the configured snap distance
- **THEN** the Vehicle's parking location SHALL be that nearest road point

#### Scenario: Home location with no nearby road
- **WHEN** no drivable road point lies within the configured snap distance of the owner household's home location
- **THEN** the Vehicle SHALL be recorded as having an unresolved parking location and SHALL be excluded from the available fleet

### Requirement: Muster point is a parked vehicle
A muster point SHALL be defined as the parking location of exactly one Vehicle. The system SHALL NOT derive muster points from a separate catalog of sites (schools, community centres, car parks). A muster point's capacity SHALL be the seat capacity of its Vehicle — there is no site-level capacity distinct from seats.

#### Scenario: Muster point derivation
- **WHEN** a Vehicle is assigned a parking location
- **THEN** a muster point SHALL exist at that location, associated with exactly that Vehicle, with capacity equal to that Vehicle's capacity

#### Scenario: No muster point without a vehicle
- **WHEN** a location has no Vehicle parked at it
- **THEN** it SHALL NOT be a muster point, regardless of its suitability as a gathering site
