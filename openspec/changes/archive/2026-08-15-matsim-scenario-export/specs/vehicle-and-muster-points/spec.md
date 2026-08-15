## ADDED Requirements

### Requirement: Muster point records the road it was snapped to
A muster point SHALL record the identity of the drivable road way it was snapped to, and its position along that way, in addition to its coordinate. These are byproducts of the snap that already determines the parking location; recording them SHALL NOT change which road a vehicle is parked on or where along it.

The purpose is downstream network matching. A simulator that builds its network from the same OpenStreetMap extract can bind a muster point to a network link by looking up the recorded way identity, rather than re-deriving the nearest link and possibly disagreeing with the pipeline about which road the car is on.

#### Scenario: Way identity recorded at snap time
- **WHEN** a Vehicle is parked at its nearest drivable road point
- **THEN** the resulting muster point SHALL record the identifier of the road way that point lies on, and the distance from the start of that way to that point

#### Scenario: Recorded way is the snapped way
- **WHEN** a muster point records a way identifier
- **THEN** that identifier SHALL refer to the same road way whose nearest point produced the muster point's coordinate and snap distance

#### Scenario: Parking behaviour unchanged
- **WHEN** the pipeline is run before and after way identity is recorded, with the same configuration and seed
- **THEN** every Vehicle SHALL be parked at the same coordinate with the same snap distance as before

#### Scenario: Position along the way is measurable
- **WHEN** a muster point's recorded way and position along it are used to interpolate a point on that way's geometry
- **THEN** the interpolated point SHALL coincide with the muster point's recorded coordinate
