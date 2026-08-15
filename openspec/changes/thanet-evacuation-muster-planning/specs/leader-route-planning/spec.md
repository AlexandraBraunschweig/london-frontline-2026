## Purpose

Produces, for each vehicle whose occupants have been decided by `evacuation-vehicle-packing`, a leader and a concrete route: who drives, which stops the vehicle makes (a muster point and/or home-collection addresses) and when, who to expect at each stop, the final destination, and how affected people are notified of the plan.

## ADDED Requirements

### Requirement: Leader selection
For each Vehicle, the system SHALL select exactly one leader from its occupants who has license_type = `car`. Leader selection SHALL use a simple rule-based eligibility check (license_type = `car`) for this iteration; a numeric leadership-scoring model is deferred.

#### Scenario: Vehicle with a qualifying occupant
- **WHEN** a Vehicle's finalized occupant list contains at least one occupant with license_type = `car`
- **THEN** the system SHALL select one such occupant as that Vehicle's leader

### Requirement: Route and meeting-point output
For each Vehicle, the system SHALL produce a route consisting of an ordered list of stops. Each stop SHALL be either the assigned muster point (for walk-in occupants already gathered there) or a home-collection household address (for an occupant classified home-collection), and SHALL include a meeting_time and the list of people expected at that stop (name and phone_number, where available). The route SHALL also include a target destination location.

#### Scenario: Vehicle with only walk-in occupants
- **WHEN** none of a Vehicle's occupants are classified home-collection
- **THEN** that Vehicle's route SHALL consist of a single stop at its assigned muster point

#### Scenario: Vehicle with a home-collection occupant
- **WHEN** a Vehicle's occupants include at least one person classified home-collection
- **THEN** that person's household address SHALL appear as a stop on the Vehicle's route, in addition to any muster-point stop

### Requirement: Minimizing collection stops
The system SHALL minimize, across all vehicles serving a muster point's catchment, the number of distinct home-collection stops per vehicle route — preferring to seat occupants already gathered at a muster point over adding household stops — and SHALL distribute unavoidable home-collection stops across the available vehicle fleet rather than concentrating multiple such stops on a single vehicle's route.

#### Scenario: Multiple home-collection individuals, sufficient fleet capacity
- **WHEN** more than one unattached home-collection individual is assigned to the same muster point's catchment and more than one Vehicle has spare capacity there
- **THEN** those individuals SHALL be distributed across separate Vehicles' routes rather than all assigned to a single Vehicle's route

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
