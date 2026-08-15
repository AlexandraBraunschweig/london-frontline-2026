## Purpose

Renders, for each vehicle whose occupants have been decided by `evacuation-seat-assignment`, a leader and a concrete route: who drives, which stops the vehicle makes and when, who to expect at each stop, the final destination, and how affected people are notified. This capability makes no allocation decisions — which occupant rides in which vehicle, and which collection stops a vehicle makes, are already fixed upstream.

## ADDED Requirements

### Requirement: Leader selection
For each Vehicle, the system SHALL select exactly one leader from its occupants who has license_type = `car`, preferring a member of the Vehicle's owner household where one qualifies, since the Vehicle is parked at that household's home. Leader selection SHALL use a simple rule-based eligibility check (license_type = `car`) for this iteration; a numeric leadership-scoring model is deferred.

#### Scenario: Owner household member qualifies
- **WHEN** a Vehicle's finalized occupant list contains a member of its owner household with license_type = `car`
- **THEN** the system SHALL select that occupant as the Vehicle's leader

#### Scenario: Only a non-owner occupant qualifies
- **WHEN** no member of a Vehicle's owner household has license_type = `car` but another occupant does
- **THEN** the system SHALL select that occupant as the Vehicle's leader

### Requirement: Route output
For each Vehicle, the system SHALL produce a route consisting of an ordered list of stops followed by a target destination. The first stop SHALL be the Vehicle's own muster point (its parking location), where all walk-in occupants gather. Each subsequent stop SHALL be the home location — UPRN and coordinates — of one home-collection group allocated to that Vehicle, ordered by increasing distance from the muster point. Each stop SHALL carry a meeting_time and the list of people expected there (name, and phone_number where available). Stops are identified by UPRN and coordinates; no human-readable address is produced (see `home-location-assignment`).

#### Scenario: Vehicle with only walk-in occupants
- **WHEN** none of a Vehicle's occupants are classified home-collection
- **THEN** that Vehicle's route SHALL consist of a single stop at its muster point, followed by the destination

#### Scenario: Vehicle with a home-collection occupant
- **WHEN** a Vehicle's occupants include at least one home-collection group
- **THEN** that group's household UPRN and coordinates SHALL appear as a stop after the muster-point stop

### Requirement: Meeting times
All muster-point stops SHALL share a single configured fleet-wide departure time. Each subsequent collection stop's meeting_time SHALL be estimated as the previous stop's meeting_time plus the straight-line distance between them divided by a configured average driving speed. No congestion or network-routed travel time is modeled in this iteration; realistic timings are the concern of the downstream traffic microsimulation.

#### Scenario: Muster-point meeting time
- **WHEN** a Vehicle's route is produced
- **THEN** its muster-point stop's meeting_time SHALL equal the configured fleet-wide departure time

#### Scenario: Collection stop meeting time
- **WHEN** a collection stop follows another stop on a Vehicle's route
- **THEN** its meeting_time SHALL be the preceding stop's meeting_time plus the estimated travel time between them

### Requirement: Evacuation destination
Every Vehicle's route SHALL end at a single configured evacuation destination location, identical across the fleet for this iteration. Multiple destinations and destination-capacity constraints are out of scope (see design.md Non-Goals).

#### Scenario: Destination attached to every route
- **WHEN** a Vehicle's route is produced
- **THEN** it SHALL include the configured evacuation destination as its final location

### Requirement: Notification fallback
WHEN a person has a phone_number, the system SHALL include that person directly in their stop's notified people list. WHEN a person has no phone_number but has a `dependent_of` carer (per `person_relationships`), the system SHALL notify that carer of the plan on the person's behalf instead. WHEN a person has neither a phone_number nor a `dependent_of` carer, the system SHALL mark that person for physical collection rather than relying on notification.

#### Scenario: Person with a phone
- **WHEN** a person included in a route has a phone_number
- **THEN** that person SHALL appear directly in their stop's notified people list

#### Scenario: Person without a phone but with a carer
- **WHEN** a person included in a route has no phone_number but has a `dependent_of` carer
- **THEN** the carer SHALL be notified of the plan on that person's behalf

#### Scenario: Person without a phone and without a carer
- **WHEN** a person included in a route has no phone_number and no `dependent_of` carer
- **THEN** that person SHALL be marked for physical collection rather than notified
