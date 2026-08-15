## Purpose

Places each synthetic household at a real dwelling point within its Output Area, using open data only. A household's home is identified by its UPRN and coordinates; no human-readable address is produced.

## ADDED Requirements

### Requirement: Open-data-only dwelling source
The system SHALL source candidate dwelling points from the ONS National Statistics UPRN Lookup (NSUL), which carries, for every addressable object in Great Britain, its UPRN, its British National Grid coordinates, and its Output Area allocation. The system SHALL NOT scrape any council planning portal, and SHALL NOT depend on any authenticated or licensed-only source such as AddressBase.

#### Scenario: Dwelling points sourced for an Output Area
- **WHEN** dwelling points are needed for an Output Area
- **THEN** they SHALL be the NSUL records whose Output Area code equals that Output Area, with no other dwelling-point source consulted

### Requirement: Single source for location and allocation
NSUL supplies both the coordinates and the Output Area allocation, so the system SHALL NOT ingest a second dwelling dataset for either purpose. In particular it SHALL NOT ingest OS Open UPRN, whose only contribution over NSUL would be coordinates NSUL already carries, and SHALL NOT allocate UPRNs to Output Areas by point-in-polygon intersection, because ONS performs that allocation in NSUL against the same geography the Census marginals are published on.

#### Scenario: Household placed in its Output Area
- **WHEN** a synthetic household generated for an Output Area is assigned a dwelling point
- **THEN** that point SHALL be a UPRN whose NSUL Output Area code equals that household's Output Area code

### Requirement: Home location identity
A Household's assigned location SHALL be identified by its UPRN and its coordinates, in both British National Grid as published and WGS84 for downstream network queries. The system SHALL NOT produce a human-readable address string. Anyone requiring physical collection is located by UPRN and coordinates.

#### Scenario: Location recorded on assignment
- **WHEN** a household is assigned a dwelling point
- **THEN** its Location record SHALL carry that point's UPRN, its easting and northing, and its latitude and longitude, and no address field

### Requirement: One household per dwelling point
The system SHALL NOT assign more than one synthetic household to the same UPRN. Where the number of synthetic households in an Output Area exceeds the number of candidate UPRNs in that Output Area, the system SHALL record the shortfall rather than silently over-assigning.

#### Scenario: More households than dwelling points
- **WHEN** the number of synthetic households generated for an Output Area exceeds the number of candidate UPRNs in that Output Area
- **THEN** the excess SHALL be recorded as an unresolved home-location shortfall for that Output Area

### Requirement: Non-residential UPRN inclusion accepted
NSUL carries no property classification, so residential and non-residential addressable objects cannot be distinguished from it. The system SHALL accept that a minority of synthetic households are placed at non-residential UPRNs, and SHALL report the ratio of candidate UPRNs to synthetic households per Output Area so the scale of the effect is visible. The system SHALL NOT introduce a building-footprint dataset to filter them in this iteration.

#### Scenario: UPRN surplus reported
- **WHEN** dwelling points are assigned for an Output Area
- **THEN** the ratio of available UPRNs to synthetic households SHALL be recorded in the assignment summary
