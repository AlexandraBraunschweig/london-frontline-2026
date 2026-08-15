# evacuation-seat-assignment Specification

## Purpose
Assigns every person a seat in a specific vehicle for a single evacuation departure — keeping families together, prioritizing those who cannot walk, and tracking anyone who cannot be seated. Because each muster point is exactly one parked vehicle (see `vehicle-and-muster-points`), choosing a person's muster point and choosing their seat are the same decision, made once here.

## Requirements

### Requirement: Collection-mode split
The system SHALL classify each travel group as **home-collection** if any of its members has mobility_status = false, or **walk-in** if every member has mobility_status = true. A home-collection group SHALL NOT be subject to the walking-time ceiling that applies to walk-in groups; it is instead collected from its home address by the vehicle it is assigned to.

#### Scenario: Group with one non-walking member
- **WHEN** a travel group contains at least one member with mobility_status = false
- **THEN** the entire group SHALL be classified home-collection, even if its other members can walk

#### Scenario: All members can walk
- **WHEN** every member of a travel group has mobility_status = true
- **THEN** the group SHALL be classified walk-in and subject to the walking-time requirements below

### Requirement: Assignment tier order
The system SHALL assign occupants to Vehicles in four tiers, in order:

1. the owner household's own travel groups, into that household's own Vehicle;
2. Class A walk-in groups (containing a dependent per the age-banded rule in `synthetic-population`);
3. home-collection groups and individuals;
4. Class B walk-in groups (independent adults with no dependents).

A later tier SHALL NOT be allocated a seat while any group in an earlier tier that could occupy that seat remains unallocated.

#### Scenario: Tier ordering enforced
- **WHEN** a Class A walk-in group has not yet been allocated a seat in a Vehicle within its walking ceiling
- **THEN** no Class B walk-in group SHALL be allocated a seat in that Vehicle ahead of it

### Requirement: Owner household seated in its own vehicle
The travel groups of a Vehicle's owner household SHALL be allocated seats in that Vehicle before any other occupant, up to that Vehicle's capacity.

#### Scenario: Owner household smaller than its vehicle
- **WHEN** a car-owning household's members occupy fewer seats than its Vehicle's capacity
- **THEN** those members SHALL be allocated to that Vehicle and the remaining seats SHALL be offered to later tiers

#### Scenario: Owner household with more members than seats
- **WHEN** a car-owning household's members exceed its Vehicle's total seat capacity
- **THEN** the unseated members SHALL be treated as walk-in or home-collection groups for allocation to another Vehicle, per their collection mode

### Requirement: Walking-time ceiling for walk-in groups
No walk-in travel group, of either class, SHALL be allocated to a Vehicle whose parking location exceeds a configured pedestrian-network walking time (default 10 minutes) from that group's home location. A walk-in group for which no Vehicle within that ceiling has remaining capacity SHALL be recorded as unmet evacuation demand rather than force-assigned beyond the ceiling.

#### Scenario: Nearest vehicle within the ceiling
- **WHEN** a walk-in travel group is allocated a seat
- **THEN** it SHALL be allocated to the nearest Vehicle, by pedestrian-network walking time, that has remaining capacity at the time of allocation and lies within the walking ceiling

#### Scenario: No capacity within the ceiling
- **WHEN** every Vehicle within the walking ceiling of a walk-in travel group's home location is full
- **THEN** that travel group SHALL be recorded as unmet evacuation demand

### Requirement: Home-collection seat assignment
A home-collection group SHALL be allocated to the nearest Vehicle with remaining capacity by straight-line distance from the group's home location, subject to a configured maximum collection distance and a configured maximum number of collection stops per Vehicle. Among Vehicles within the maximum collection distance, the system SHALL prefer the one with the fewest collection stops already assigned, so that unavoidable collection stops are spread across the fleet rather than concentrated on one Vehicle.

#### Scenario: Multiple home-collection groups, sufficient fleet capacity
- **WHEN** more than one home-collection group is within the maximum collection distance of more than one Vehicle with spare capacity
- **THEN** those groups SHALL be distributed across separate Vehicles rather than all allocated to a single Vehicle

#### Scenario: Collection stop cap reached
- **WHEN** a Vehicle has already been assigned the configured maximum number of collection stops
- **THEN** no further home-collection group SHALL be allocated to it, even if it has remaining seats

### Requirement: Families not split
Every member of an indivisible travel group SHALL be allocated to the same Vehicle, unless the group's size exceeds the capacity of every Vehicle available to it, in which case the system SHALL split the group across the fewest Vehicles that together seat all its members, all departing in the same wave.

#### Scenario: Family group not split across vehicles
- **WHEN** a travel group is allocated to a Vehicle with sufficient remaining capacity
- **THEN** every member of that travel group SHALL be allocated to that same Vehicle

#### Scenario: Group larger than any single vehicle
- **WHEN** a travel group has more members than the capacity of any Vehicle available to it
- **THEN** the system SHALL split the group across the fewest Vehicles that together seat all its members, and SHALL record the split

### Requirement: Driver availability
The system SHALL NOT finalize a Vehicle's occupant list unless at least one occupant has license_type = `car`.

#### Scenario: Owner household with no licensed driver
- **WHEN** a Vehicle's owner household contains no member with license_type = `car`
- **THEN** that Vehicle MAY still depart if an occupant allocated from a later tier has license_type = `car`

#### Scenario: No driver available for an otherwise-fillable vehicle
- **WHEN** a Vehicle's finalized occupant list would contain no occupant with license_type = `car`
- **THEN** the system SHALL NOT finalize that Vehicle and SHALL record its candidate occupants as unmet evacuation demand

### Requirement: Single departure wave, unmet demand tracking
The system SHALL model exactly one departure per Vehicle, with no return trips or relay waves. Any person who cannot be allocated a seat in any Vehicle available to them SHALL be recorded as unmet evacuation demand, identified by their home Output Area, collection mode, and assignment tier.

#### Scenario: Insufficient seats
- **WHEN** the total seat capacity of Vehicles reachable by a person is fully allocated
- **THEN** that person SHALL be recorded as unmet evacuation demand, tagged with the tier they belonged to
