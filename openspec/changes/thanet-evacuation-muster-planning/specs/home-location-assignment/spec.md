## Purpose

Places each synthetic household at a real dwelling point within its Output Area, using open data only, and gives it a plausible synthetic street address so the drill output has a human-readable location.

## ADDED Requirements

### Requirement: Open-data-only dwelling source
The system SHALL source candidate dwelling points from OS Open UPRN, which provides a UPRN and its coordinates for every addressable object in Great Britain. The system SHALL NOT scrape any council planning portal, and SHALL NOT depend on any authenticated or licensed-only source such as AddressBase.

#### Scenario: Dwelling points sourced for an Output Area
- **WHEN** dwelling points are needed for an Output Area
- **THEN** they SHALL be the OS Open UPRN points allocated to that Output Area, with no other dwelling-point source consulted

### Requirement: UPRN to Output Area allocation
The system SHALL allocate UPRNs to Output Areas using the ONS National Statistics UPRN Lookup (NSUL), joining on the UPRN, rather than by point-in-polygon intersection of UPRN coordinates against Output Area boundaries. ONS performs that allocation in NSUL already, and reproducing it locally would risk disagreeing with the geography the Census marginals are published against.

#### Scenario: Household placed in its Output Area
- **WHEN** a synthetic household generated for an Output Area is assigned a dwelling point
- **THEN** that point SHALL be a UPRN whose NSUL Output Area code equals that household's Output Area code

#### Scenario: UPRN absent from the lookup
- **WHEN** an OS Open UPRN record has no corresponding NSUL row
- **THEN** it SHALL be excluded from the candidate dwelling points and counted in the ingestion summary

### Requirement: One household per dwelling point
The system SHALL NOT assign more than one synthetic household to the same UPRN. Where the number of synthetic households in an Output Area exceeds the number of candidate UPRNs in that Output Area, the system SHALL record the shortfall rather than silently over-assigning.

#### Scenario: More households than dwelling points
- **WHEN** the number of synthetic households generated for an Output Area exceeds the number of candidate UPRNs in that Output Area
- **THEN** the excess SHALL be recorded as an unresolved home-location shortfall for that Output Area

### Requirement: Synthetic street address
Every assigned Household location SHALL carry a human-readable address string alongside its coordinates, so that drill output identifies a findable place. Because OS Open UPRN carries coordinates only and no address, that string SHALL be synthesized from the real street name of the nearest OS Open USRN street to the assigned UPRN, combined with a generated building number. The address SHALL be recorded as synthetic and SHALL NOT be presented as the real address of the assigned UPRN.

#### Scenario: Address populated on assignment
- **WHEN** a household is assigned a UPRN
- **THEN** its Location record SHALL include a non-empty address string composed of a generated building number and the name of the nearest USRN street, alongside its latitude/longitude

#### Scenario: No named street nearby
- **WHEN** the nearest OS Open USRN street to an assigned UPRN has no name
- **THEN** the household's address SHALL fall back to its Output Area code and UPRN, rather than an empty or fabricated street name

### Requirement: Non-residential UPRN inclusion accepted
OS Open UPRN carries no property classification, so residential and non-residential addressable objects cannot be distinguished from it. The system SHALL accept that a minority of synthetic households are placed at non-residential UPRNs, and SHALL report the ratio of candidate UPRNs to synthetic households per Output Area so the scale of the effect is visible. The system SHALL NOT introduce a building-footprint dataset to filter them in this iteration.

#### Scenario: UPRN surplus reported
- **WHEN** dwelling points are assigned for an Output Area
- **THEN** the ratio of available UPRNs to synthetic households SHALL be recorded in the assignment summary
