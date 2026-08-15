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

### Requirement: A vehicle is driven by its owner
A Vehicle SHALL NOT be activated unless a member of its owner household is among the people it will carry and has license_type = `car`. A car is its owner's property and their keys; it does not depart without them, and no other household drives it. Driver availability SHALL be evaluated when choosing to activate, not after occupants are fixed, so that a person is never stranded in a vehicle that turns out to be undrivable.

This bounds how far pooling can go — a car only leaves if the household that owns it is travelling in it — and it removes any need for key handover between households.

#### Scenario: Owner household cannot drive
- **WHEN** no member of a Vehicle's owner household who could board it has license_type = `car`
- **THEN** that Vehicle SHALL NOT be activated, even if a licensed member of another household could reach it

#### Scenario: A neighbour never drives another household's car
- **WHEN** a licensed person from a household other than the owner's is the only licensed person able to reach a Vehicle
- **THEN** that Vehicle SHALL NOT be activated on their account

#### Scenario: The owner-driver is seated first
- **WHEN** a Vehicle is activated
- **THEN** a licensed member of its owner household SHALL be among its occupants

#### Scenario: Nobody is stranded by a missing driver
- **WHEN** activation completes
- **THEN** no person SHALL be recorded as unmet demand for the reason that their vehicle had no licensed driver

### Requirement: Fleet reporting against baseline and floor
The system SHALL report the number of Vehicles activated alongside the one-car-per-car-owning-household baseline and the theoretical packing floor (people seated divided by vehicle capacity, rounded up), so the saving is stated rather than inferred.

#### Scenario: Saving reported
- **WHEN** the plan is complete
- **THEN** the summary SHALL state vehicles activated, the baseline count, the packing floor, and the occupancy distribution of activated vehicles
