## Purpose

Generates a synthetic population of persons and households for a UK Local Authority District, matched to Census 2021 Output Area marginals, including the contact/identification attributes and dependency relationships needed to keep families together and coordinate them in downstream assignment.

## ADDED Requirements

### Requirement: Person and household generation matched to Census marginals
The system SHALL generate synthetic Person records (age, sex, license_type) and Household records (composition_type, num_persons, num_cars) for each Output Area within a specified LAD, such that aggregate counts match published ONS Census 2021 Output-Area-level marginal tables for age/sex distribution, household composition, and car availability.

#### Scenario: Aggregate age/sex match
- **WHEN** the population is generated for a given Output Area
- **THEN** the count of synthetic persons by age band and sex SHALL match that Output Area's published Census marginal within rounding tolerance

#### Scenario: Household composition match
- **WHEN** households are generated for an Output Area
- **THEN** the distribution of household composition types SHALL match that Output Area's published household composition marginal within rounding tolerance

### Requirement: License type and car ownership
Each synthetic Person SHALL have a license_type attribute with value `none`, `car`, or `bus`, derived from a configured minimum UK driving-age threshold and a license-holding proportion. Car ownership SHALL be represented only at household level, as the Household `num_cars` attribute matched to its Output Area's car-availability marginal; Person SHALL NOT carry a separate car-ownership attribute.

#### Scenario: Under-driving-age person
- **WHEN** a synthetic person's age is below the configured minimum driving age
- **THEN** license_type SHALL be `none`

#### Scenario: Car ownership has a single source of truth
- **WHEN** the number of cars available to a person is needed downstream
- **THEN** it SHALL be read from that person's Household `num_cars`, there being no person-level car attribute that could disagree with it

### Requirement: Contact, identification, and skill attributes
Each synthetic Person SHALL have phone_number and name attributes to support coordination and physical identification at a meeting point, and a medical_skill boolean attribute.

#### Scenario: Person without a phone number
- **WHEN** a synthetic person is generated without a phone_number
- **THEN** that absence SHALL be recorded explicitly (not defaulted to a placeholder value), so downstream notification logic can detect it

### Requirement: Mobility status
Each synthetic Person SHALL have a boolean mobility_status attribute indicating whether that person can walk (true) or cannot walk and needs to be collected from home (false).

#### Scenario: Mobility status drives routing mode
- **WHEN** a person's mobility_status is false
- **THEN** that person SHALL be excluded from walking-based seat assignment and SHALL instead require home collection (see `evacuation-seat-assignment` and `leader-route-planning`)

### Requirement: Co-resident parent identification
Marginal-only synthesis produces household membership but not kinship, so the system SHALL derive co-resident parenthood by rule. Within a household containing a person under 16, that person's co-resident parents SHALL be identified as the one or two oldest household members who are at least a configured minimum parent-child age gap (default 16 years) older than that person. A household with no member meeting that condition SHALL be recorded as having no co-resident parent for that child, rather than assigning an implausible parent.

#### Scenario: Two qualifying adults present
- **WHEN** a household contains a person under 16 and two or more members at least the configured age gap older
- **THEN** the two oldest such members SHALL be recorded as that person's co-resident parents

#### Scenario: No qualifying adult present
- **WHEN** a household contains a person under 16 and no member is at least the configured age gap older
- **THEN** that person SHALL be recorded as having no co-resident parent, and the household SHALL be reported in the synthesis summary

### Requirement: Person relationships and indivisible travel groups
The system SHALL represent person-to-person relationships in a normalized `person_relationships` table (person_id, related_person_id, relationship_type) rather than as a list-valued field on Person. Within each household, the system SHALL derive `dependent_of` edges by age band, using the co-resident parents identified above:
- a person under 10 SHALL have a mandatory `dependent_of` edge to both co-resident parents where present;
- a person aged 10 up to (not including) 16 SHALL have a mandatory `dependent_of` edge to at least one co-resident parent;
- a person aged 16 or over SHALL NOT have a mandatory `dependent_of` edge to any household member, and MAY be linked instead to a different travel group during downstream assignment (see `evacuation-seat-assignment`).

The system SHALL compute indivisible travel groups as the connected components formed by mandatory `dependent_of` edges within a household.

#### Scenario: Child under 10 with both parents present
- **WHEN** a household contains a person under 10 and both of that person's parents are present in the same household
- **THEN** the child and both parents SHALL belong to the same travel group and SHALL never be separated by downstream assignment

#### Scenario: Child aged 10-15 with one parent present
- **WHEN** a household contains a person aged 10-15 and only one of that person's parents is present in the household
- **THEN** the child and that parent SHALL belong to the same travel group

#### Scenario: Person aged 16 or over
- **WHEN** a household member is aged 16 or over
- **THEN** that person SHALL NOT be mandatorily bound to their household's travel group by a `dependent_of` edge, and downstream assignment MAY place them in a different travel group

### Requirement: Portable admin-area hierarchy
The system SHALL represent Census geography (Output Area and above) using a generic AdminArea entity with level, parent_id, and geometry fields rather than hardcoded UK-specific field names, so the same schema can be re-seeded from another country's census geography.

#### Scenario: Non-UK reuse
- **WHEN** the pipeline is pointed at marginal tables and boundaries for a non-UK geography
- **THEN** no schema change SHALL be required to represent that geography's admin hierarchy
