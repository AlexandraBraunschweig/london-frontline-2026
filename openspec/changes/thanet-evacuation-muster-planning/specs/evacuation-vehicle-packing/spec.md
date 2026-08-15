## Purpose

Allocates every person assigned to a muster point's catchment into a specific vehicle for a single evacuation departure, keeping families together and prioritizing those who cannot walk, while tracking anyone who cannot be seated.

## ADDED Requirements

### Requirement: Tiered seating priority
For each muster point, the system SHALL allocate occupants to Vehicles in three tiers, in order: (1) indivisible travel groups containing a dependent, packed to maximize vehicle occupancy; (2) unattached individuals classified home-collection (mobility_status = false, not part of a Tier 1 group); (3) remaining independent walk-in adults filling any leftover seats. A later tier SHALL NOT begin allocation at a muster point until every group or individual in the prior tier that can fit has been allocated.

#### Scenario: Family group not split across vehicles
- **WHEN** a Tier 1 travel group is allocated to a Vehicle
- **THEN** every member of that travel group SHALL be allocated to the same Vehicle, unless the group's size exceeds every available Vehicle's capacity at that muster point

#### Scenario: Tier ordering enforced
- **WHEN** any Tier 1 travel group at a muster point has not yet been allocated to a Vehicle with sufficient remaining capacity
- **THEN** no Tier 2 or Tier 3 occupant SHALL be allocated ahead of it at that muster point

### Requirement: Oversized household exception
WHEN a travel group's size exceeds the capacity of every available Vehicle at its muster point, the system SHALL split that group across the minimum number of Vehicles required, all departing in the same wave from the same muster point, and SHALL prefer separating independent adult (16+, non-mandatorily-bound) members of the group before separating a dependent from either co-resident parent.

#### Scenario: Group larger than any single vehicle
- **WHEN** a travel group has more members than the capacity of the largest available Vehicle at its muster point
- **THEN** the system SHALL split the group across the fewest Vehicles that together seat all its members, without separating any dependent from a co-resident parent unless no other split is possible

### Requirement: Driver availability
The system SHALL NOT finalize a Vehicle's occupant list unless at least one occupant has license_type = `car`.

#### Scenario: Home-collection individual with no driver in vehicle
- **WHEN** a Tier 2 home-collection individual with license_type ≠ `car` is allocated to a Vehicle
- **THEN** that Vehicle SHALL already contain, or be assigned, at least one other occupant with license_type = `car` before its occupant list is finalized

#### Scenario: No driver available for an otherwise-fillable vehicle
- **WHEN** a Vehicle's only candidate occupants all have license_type ≠ `car`
- **THEN** the system SHALL NOT finalize that Vehicle's occupant list and SHALL instead record those candidate occupants as unmet evacuation demand

### Requirement: Single departure wave, unmet demand tracking
The system SHALL model exactly one departure per Vehicle, with no return trips or relay waves. Any person present at, or assigned to, a muster point's catchment who cannot be seated in any Vehicle at that muster point SHALL be recorded as unmet evacuation demand, identified by muster point and priority tier.

#### Scenario: Insufficient seats at a muster point
- **WHEN** the total seat capacity of Vehicles at a muster point is less than the number of people assigned to that muster point's catchment
- **THEN** the unseated remainder SHALL be recorded as unmet evacuation demand for that muster point, tagged with the priority tier each unseated person belonged to
