# microsimulation-scenario-export Specification

## Purpose
Turns the finished evacuation plan into a runnable agent-based traffic microsimulation scenario: where the evacuation ends, when each vehicle sets off, and the plan expressed as per-person travel plans bound to the road network by OpenStreetMap way identity. Nothing here decides who rides in which car; it renders decisions already made into something a simulator can load.

## Requirements

### Requirement: District exits are derived from boundary crossings
The system SHALL derive the set of evacuation exits from the points where drivable roads cross the boundary of the target Local Authority District, rather than from a configured external destination. An exit SHALL qualify only if its road is of a configured through-road class and continues beyond the boundary for at least a configured minimum length.

The minimum-length test exists because much of a coastal district's boundary is coastline, so roads running along a seafront or into a harbour clip the boundary without leaving the district. Such crossings SHALL NOT be treated as exits.

#### Scenario: Through road leaving the district
- **WHEN** a drivable road of a qualifying class crosses the district boundary and continues beyond it for at least the configured minimum length
- **THEN** an exit SHALL be recorded at the crossing point, carrying the identity of the road way it lies on

#### Scenario: Road clipping the boundary at the coast
- **WHEN** a road crosses the district boundary but continues beyond it for less than the configured minimum length
- **THEN** no exit SHALL be recorded for that crossing

#### Scenario: Access road crossing the boundary
- **WHEN** a way crossing the boundary is not of a configured through-road class
- **THEN** no exit SHALL be recorded for it, regardless of how far beyond the boundary it continues

#### Scenario: Exits are reported
- **WHEN** exits have been derived
- **THEN** the set of exits SHALL be reported, including each exit's road class and the length of road beyond the boundary that qualified it

### Requirement: Co-located crossings form a single exit
Boundary crossings lying within a configured radius of one another SHALL be treated as one exit, representing the road corridor they share. The system SHALL NOT emit a separate exit per crossing where several crossings describe the same way out of the district.

A single corridor commonly crosses the boundary several times: a dual carriageway crosses once per carriageway, a way may re-enter and leave again, and parallel classifications of the same road are mapped as distinct ways. Assigning by proximity across such a group makes the winner turn on a few metres, so the busiest exit in the district can be decided by map detail rather than by road capacity.

Each cluster SHALL be represented by its most major road class, so that traffic is directed onto the largest road of the corridor rather than whichever crossing happens to be nearest. The representative SHALL be an actual crossing, so that the exit retains a resolvable way reference and position.

#### Scenario: Crossings of one corridor collapse
- **WHEN** several qualifying crossings lie within the configured clustering radius of one another
- **THEN** exactly one exit SHALL be recorded for them

#### Scenario: The corridor is represented by its largest road
- **WHEN** a cluster contains crossings of differing road classes
- **THEN** the exit SHALL take the position and way reference of a crossing of the most major class present

#### Scenario: Distant crossings stay separate
- **WHEN** two qualifying crossings lie further apart than the configured radius, and no chain of crossings within that radius connects them
- **THEN** they SHALL be recorded as separate exits

#### Scenario: Clustering is reported
- **WHEN** exits have been derived
- **THEN** the number of crossings each exit represents SHALL be reported alongside it

### Requirement: Each departing vehicle is assigned an exit
Every departing vehicle SHALL be assigned exactly one district exit as the destination of its journey. Assignment SHALL be by proximity to the vehicle's final stop. The system SHALL NOT model drivers choosing between exits by expected travel time.

The resulting concentration of demand on the nearest exit is a known consequence of proximity assignment and SHALL be reported, so that it is visible as a modelling assumption rather than mistaken for a simulation finding.

#### Scenario: Exit assignment
- **WHEN** a vehicle is scheduled to depart
- **THEN** it SHALL be assigned the exit nearest to its final stop, and that assignment SHALL be recorded

#### Scenario: Exit loading reported
- **WHEN** exits have been assigned across the departing fleet
- **THEN** the number of vehicles and people assigned to each exit SHALL be reported

#### Scenario: No qualifying exit
- **WHEN** no exit qualifies for the district
- **THEN** the scenario SHALL NOT be emitted, and the absence SHALL be reported as a shortfall identifying the district

### Requirement: Departure times follow a mobilisation profile
Each departing vehicle SHALL be assigned a departure time drawn from a configured mobilisation distribution, offset by the time its driver needs to travel from home to the vehicle. The system SHALL NOT assign every vehicle the same departure time by default.

A fleet departing at a single instant loads a microsimulation with an insertion queue whose clearance is a property of the queue rather than of the road network, so any measurement taken from it would describe the assumption instead of the district.

#### Scenario: Departure time drawn per vehicle
- **WHEN** a vehicle is scheduled to depart
- **THEN** its departure time SHALL be the configured evacuation notification time, plus a mobilisation delay drawn from the configured distribution, plus the driver's travel time from home to the vehicle

#### Scenario: Driver already at the vehicle
- **WHEN** a vehicle's driver is a member of the household that owns it, and the vehicle is parked at that household's own kerb
- **THEN** the driver's travel time contribution SHALL be zero and the departure time SHALL be the notification time plus the mobilisation delay alone

#### Scenario: Simultaneous departure remains reachable
- **WHEN** the mobilisation distribution is configured to have no spread and driver travel time is configured to be ignored
- **THEN** every vehicle SHALL depart at the configured notification time

#### Scenario: Departure draw is reproducible
- **WHEN** the scenario is generated twice with the same configuration and the same random seed
- **THEN** every vehicle SHALL receive an identical departure time on both runs

#### Scenario: Departure profile reported
- **WHEN** departure times have been assigned
- **THEN** their distribution SHALL be reported, including the span from first to last departure

### Requirement: The plan is emitted as an agent-based scenario
The system SHALL emit the plan as a set of per-person travel plans in the input format of an agent-based traffic microsimulator, together with the household and vehicle records those plans reference.

#### Scenario: One plan per seated person
- **WHEN** the scenario is emitted
- **THEN** every person holding a seat SHALL have exactly one plan, beginning at their home location and ending at their vehicle's assigned exit

#### Scenario: Exactly one vehicle per departing car
- **WHEN** a vehicle departs carrying a driver and passengers
- **THEN** the driver's plan SHALL place a vehicle on the road network and each passenger's plan SHALL NOT, so that the number of vehicles entering the network equals the number of departing vehicles

#### Scenario: Walk to the muster point precedes the drive
- **WHEN** a person's home location differs from the location of the vehicle they are seated in, and their travel group can walk
- **THEN** their plan SHALL contain a leg from their home to the vehicle's location before the vehicle's journey begins

#### Scenario: Home collection appears as an intermediate stop
- **WHEN** a vehicle makes home-collection stops
- **THEN** its driver's plan SHALL visit each collection location, in the order the route defines, between the vehicle's parking location and its assigned exit

#### Scenario: Collected person joins at their own home
- **WHEN** a person's travel group cannot walk and is collected from home
- **THEN** their plan SHALL contain no walk to the muster point, and their journey SHALL begin at their home location at the time their vehicle is scheduled to arrive there

#### Scenario: Unseated people are absent from the scenario
- **WHEN** a person holds no seat
- **THEN** they SHALL NOT appear in the emitted scenario, and the count of people so excluded SHALL be reported against the recorded unmet demand

### Requirement: Network references are carried by way identity
Every location in the emitted scenario that must bind to a road network link — each muster point and each exit — SHALL be accompanied by the identity of the OpenStreetMap way it lies on and its position along that way. The system SHALL NOT emit simulator-specific network link identifiers.

Link identifiers are assigned by whichever tool builds the network from the OpenStreetMap extract, and differ between tools and between builds. Way identity is stable across both, so a scenario keyed on it can be bound to a network built by any simulator, and can be rebound after a network rebuild without regenerating the plan.

#### Scenario: Muster point network reference
- **WHEN** the scenario is emitted
- **THEN** each referenced muster point SHALL be accompanied by its way identifier, its position along that way, and its coordinate

#### Scenario: Exit network reference
- **WHEN** the scenario is emitted
- **THEN** each exit SHALL be accompanied by its way identifier, its position along that way, and its coordinate

#### Scenario: No simulator link identifiers emitted
- **WHEN** the scenario is emitted
- **THEN** it SHALL contain no identifier that depends on a particular simulator's network build

### Requirement: The emitted scenario is well formed and self-consistent
The emitted scenario SHALL be loadable by the target simulator without hand editing, and SHALL reference no entity it does not also define.

#### Scenario: Referential integrity
- **WHEN** the scenario is emitted
- **THEN** every vehicle, household and person referenced by a plan SHALL be defined within the emitted scenario

#### Scenario: Chronological plans
- **WHEN** a plan contains more than one leg
- **THEN** the times attached to its activities and legs SHALL be non-decreasing along the plan

#### Scenario: Coordinate reference system declared
- **WHEN** the scenario is emitted
- **THEN** the coordinate reference system its coordinates are expressed in SHALL be declared within the emitted scenario
