## Purpose

Synthesizes one vehicle per car-owning household so that downstream muster-point and vehicle-packing stages have a concrete seat supply to allocate against.

## ADDED Requirements

### Requirement: One vehicle per car-owning household
For each synthetic Household with num_cars ≥ 1, the system SHALL generate exactly num_cars Vehicle records, each linked to that household as owner and located at the household's assigned home location.

#### Scenario: Household with one car
- **WHEN** a household's num_cars is 1
- **THEN** exactly one Vehicle record SHALL be created with owner_household_id set to that household

#### Scenario: Household with no car
- **WHEN** a household's num_cars is 0
- **THEN** no Vehicle record SHALL be created for that household

### Requirement: Vehicle type and identification
Each Vehicle SHALL have a vehicle_type attribute (`car` or `bus`) and a license_plate attribute, so that passengers can identify the correct vehicle at a meeting point. For this iteration, every generated Vehicle SHALL have vehicle_type = `car`; bus generation and bus-specific routing (where a bus's start point differs from any owner's household) are out of scope (see design.md Non-Goals).

#### Scenario: Vehicle identification populated
- **WHEN** a Vehicle record is generated
- **THEN** it SHALL have vehicle_type = `car` and a non-empty license_plate value

### Requirement: Fixed seat capacity
Every generated Vehicle SHALL have a capacity attribute set to a single configured fixed value for this iteration, rather than sampled from a distribution.

#### Scenario: Capacity assignment
- **WHEN** a Vehicle record is generated
- **THEN** its capacity SHALL equal the configured fixed capacity value
