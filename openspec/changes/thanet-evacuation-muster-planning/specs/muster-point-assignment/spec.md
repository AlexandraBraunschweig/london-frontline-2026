## Purpose

Builds a catalog of candidate muster points and assigns every household/travel group to one — either as a walk-in group prioritizing minimal walking distance for those with dependents, or as a home-collection group for anyone who cannot walk — while letting independent walkable adults absorb distance and capacity slack.

## ADDED Requirements

### Requirement: Muster-point catalog
The system SHALL derive a catalog of candidate muster-point Locations (e.g. school car parks, community centres) from Overture Places / OSM POI data, each with a fixed seat capacity determined by the number and capacity of Vehicles already positioned there.

#### Scenario: Muster point capacity derivation
- **WHEN** a candidate muster point is added to the catalog
- **THEN** its capacity SHALL equal the sum of the capacities of the Vehicles positioned at that muster point

### Requirement: Collection-mode split
The system SHALL classify each travel group as **home-collection** if any of its members has mobility_status = false, or **walk-in** if every member has mobility_status = true. A home-collection group SHALL be assigned to a muster point (for vehicle-fleet purposes) using road driving distance/time rather than pedestrian walking time, and SHALL NOT be subject to the 10-minute walking ceiling that applies to walk-in groups.

#### Scenario: Group with one non-walking member
- **WHEN** a travel group contains at least one member with mobility_status = false
- **THEN** the entire group SHALL be classified home-collection, even if its other members can walk

#### Scenario: All members can walk
- **WHEN** every member of a travel group has mobility_status = true
- **THEN** the group SHALL be classified walk-in and subject to the walking-distance assignment requirements below

### Requirement: Priority classes for walk-in assignment
Among walk-in travel groups, the system SHALL classify each as Class A (contains a dependent per the age-banded rule in `synthetic-population`) or Class B (all members are independent adults with no dependents), and SHALL assign Class A groups to their nearest muster point (by pedestrian-network walking time) before assigning any Class B group to that muster point's remaining capacity.

#### Scenario: Class A group nearest-point assignment
- **WHEN** a Class A walk-in travel group is assigned a muster point
- **THEN** it SHALL be assigned the nearest muster point, by pedestrian-network walking time, that has remaining capacity at the time of assignment

#### Scenario: Class B assignment deferred until Class A satisfied
- **WHEN** any Class A walk-in travel group within a muster point's 10-minute walk catchment has not yet been assigned
- **THEN** no Class B travel group SHALL be assigned to that muster point's capacity ahead of it

### Requirement: Ten-minute walking ceiling
No walk-in travel group, of either class, SHALL be assigned to a muster point whose pedestrian-network walking time from its home location exceeds 10 minutes. A group for which no muster point within that ceiling has remaining capacity SHALL be recorded as unmet muster-point demand rather than force-assigned beyond the ceiling.

#### Scenario: No capacity within ceiling
- **WHEN** every muster point within a 10-minute walk of a walk-in travel group's home location is at capacity
- **THEN** that travel group SHALL be recorded as unmet muster-point demand

### Requirement: Class B load-balancing
Class B travel groups SHALL be assigned to the nearest muster point with remaining capacity within the 10-minute ceiling, and MAY be assigned to a farther muster point than the closest one in order to preserve capacity at the closest muster point for Class A groups.

#### Scenario: Class B rerouted to preserve Class A capacity
- **WHEN** a Class B travel group's nearest muster point has remaining capacity but that capacity is needed to keep a farther Class A group within the 10-minute ceiling
- **THEN** the Class B travel group MAY be assigned to a different muster point within its own 10-minute ceiling instead
