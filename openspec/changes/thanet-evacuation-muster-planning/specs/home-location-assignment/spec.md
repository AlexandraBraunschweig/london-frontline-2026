## Purpose

Places each synthetic household at a real, open-data-sourced dwelling location within its Output Area, without relying on scraped or licensed-only data sources.

## ADDED Requirements

### Requirement: Open-data-only dwelling source
The system SHALL source candidate dwelling points from OS Open UPRN as the primary source, falling back to Overture Buildings or OpenStreetMap address/building data when no OS Open UPRN point is available within an Output Area. The system SHALL NOT scrape any council planning-portal or other authenticated/licensed-only source to resolve addresses.

#### Scenario: Output Area with OS Open UPRN coverage
- **WHEN** an Output Area has one or more OS Open UPRN points
- **THEN** each synthetic household in that Output Area SHALL be assigned to a distinct OS Open UPRN point within its boundary

#### Scenario: Output Area without OS Open UPRN coverage
- **WHEN** an Output Area has no OS Open UPRN points
- **THEN** synthetic households in that Output Area SHALL be assigned to Overture Buildings or OpenStreetMap-derived points within its boundary

### Requirement: Household address string
In addition to its geometry (latitude/longitude), every assigned Household location SHALL carry a human-readable address string, populated from the source dwelling record (the linked UPRN address where available, otherwise a reverse-geocoded address from the Overture/OSM fallback), so that a person requiring physical collection can be found.

#### Scenario: Address populated on assignment
- **WHEN** a household is assigned a dwelling point
- **THEN** that household's Location record SHALL include a non-empty address string alongside its latitude/longitude

### Requirement: One household per dwelling point
The system SHALL NOT assign more than one synthetic household to the same dwelling point unless the number of synthetic households in an Output Area exceeds the number of available dwelling points, in which case it SHALL record the shortfall rather than silently over-assigning without tracking it.

#### Scenario: More households than dwelling points
- **WHEN** the number of synthetic households generated for an Output Area exceeds the number of candidate dwelling points in that Output Area
- **THEN** the excess SHALL be recorded as an unresolved home-location shortfall for that Output Area
