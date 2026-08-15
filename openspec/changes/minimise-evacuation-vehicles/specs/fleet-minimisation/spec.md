## Purpose

Decides which of the parked vehicles actually depart. A vehicle is a seat supply only if the plan activates it, and the plan activates as few as will carry everyone.

## ADDED Requirements

### Requirement: Vehicle activation is a decision
The system SHALL treat departure as a per-vehicle decision rather than a consequence of ownership. A Vehicle SHALL depart only if the assignment activates it, and a car-owning household MAY be seated in another household's Vehicle while its own remains parked.

#### Scenario: Household seated in a neighbour's vehicle
- **WHEN** a car-owning household's members can all be seated in an already-activated Vehicle within their walking ceiling
- **THEN** the system MAY seat them there and leave that household's own Vehicle unactivated

#### Scenario: Unactivated vehicles carry nobody
- **WHEN** a Vehicle is not activated
- **THEN** it SHALL have no occupants, no route, and SHALL NOT count towards cars on the road

### Requirement: Coverage-maximising activation
The system SHALL activate vehicles by repeatedly choosing the Vehicle that would seat the greatest number of currently unseated people who can reach it — within their walking ceiling for walk-in groups, or within the maximum collection distance for home-collection groups — until no unseated person can reach any inactive Vehicle.

#### Scenario: The fuller option is chosen
- **WHEN** two inactive Vehicles are eligible and one can seat more unseated people than the other
- **THEN** the system SHALL activate the one seating more

#### Scenario: Activation stops when nobody is reachable
- **WHEN** no unseated person can reach any inactive Vehicle
- **THEN** activation SHALL stop and the remaining unseated people SHALL be recorded as unmet demand

### Requirement: Driver eligibility constrains activation
A Vehicle SHALL NOT be activated unless at least one person it would carry has license_type = `car`. Driver availability SHALL be evaluated when choosing to activate, not after occupants are fixed, so that a person is never stranded in a vehicle that turns out to be undrivable.

#### Scenario: Undrivable vehicle is never activated
- **WHEN** every person who could reach an inactive Vehicle has license_type ≠ `car`
- **THEN** that Vehicle SHALL NOT be activated, and those people SHALL remain available to be seated in another Vehicle

#### Scenario: Nobody is stranded by a missing driver
- **WHEN** activation completes
- **THEN** no person SHALL be recorded as unmet demand for the reason that their vehicle had no licensed driver

### Requirement: Owner-aboard tie-break
WHEN two candidate Vehicles would seat the same number of unseated people, the system SHALL prefer the Vehicle whose owner household is among those being seated, since that vehicle needs no key handover.

#### Scenario: Equal coverage, one owner aboard
- **WHEN** two inactive Vehicles would each seat the same number of unseated people and exactly one of them would carry a member of its owner household
- **THEN** the system SHALL activate that one

### Requirement: Fleet reporting against baseline and floor
The system SHALL report the number of Vehicles activated alongside the one-car-per-car-owning-household baseline and the theoretical packing floor (people seated divided by vehicle capacity, rounded up), so the saving is stated rather than inferred.

#### Scenario: Saving reported
- **WHEN** the plan is complete
- **THEN** the summary SHALL state vehicles activated, the baseline count, the packing floor, and the occupancy distribution of activated vehicles
