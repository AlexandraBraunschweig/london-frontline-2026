## Purpose

Assigns every person a seat in a specific vehicle, now drawn only from the vehicles that `fleet-minimisation` has activated.

## MODIFIED Requirements

### Requirement: Assignment tier order
The system SHALL assign occupants to activated Vehicles in three tiers, in order:

1. walk-in groups containing a dependent per the age-banded rule in `synthetic-population`;
2. home-collection groups and individuals;
3. walk-in groups of independent adults only.

A later tier SHALL NOT be allocated a seat while any group in an earlier tier that could occupy that seat remains unallocated. The owner household no longer takes a tier of its own: which vehicles exist to be filled is decided by `fleet-minimisation`, and ownership survives only as its tie-break.

#### Scenario: Tier ordering enforced
- **WHEN** a walk-in group containing a dependent has not yet been allocated a seat in an activated Vehicle within its walking ceiling
- **THEN** no group of independent adults SHALL be allocated a seat in that Vehicle ahead of it

#### Scenario: Ownership confers no seating priority
- **WHEN** a Vehicle is activated and its owner household's members compete for its seats with a dependent-bearing group from another household
- **THEN** the dependent-bearing group SHALL be seated first

### Requirement: Driver availability
The system SHALL NOT finalize an activated Vehicle's occupant list unless at least one occupant has license_type = `car`. This is guaranteed by the activation constraint in `fleet-minimisation` rather than enforced by dropping vehicles after the fact, so occupants are never committed to a vehicle that turns out to be undrivable.

#### Scenario: Driver present in every departing vehicle
- **WHEN** a Vehicle's occupant list is finalized
- **THEN** it SHALL contain at least one occupant with license_type = `car`

#### Scenario: Owner household with no licensed driver
- **WHEN** a Vehicle's owner household contains no member with license_type = `car`
- **THEN** that Vehicle MAY still be activated if a licensed driver is among the people it would carry, and SHALL NOT be activated otherwise

#### Scenario: No driver available for an otherwise-fillable vehicle
- **WHEN** every person who could reach a Vehicle has license_type ≠ `car`
- **THEN** that Vehicle SHALL NOT be activated, and those people SHALL remain available for another Vehicle rather than being recorded as unmet demand

## REMOVED Requirements

### Requirement: Owner household seated in its own vehicle
**Reason**: This is the constraint that pinned cars on the road to the number of car-owning households, holding the fleet at 43,514 against a 45,960 baseline and a 25,564 packing floor, while 7,574 cars carried one person each. It also left 10,849 people stranded in vehicles with no licensed driver, because they were committed to a car before drivability was known.

**Migration**: Ownership is now a tie-break during activation (see `fleet-minimisation`), not a seating claim. Households whose own vehicle is not activated are seated in a neighbour's within the same walking ceiling that already applied to them.
