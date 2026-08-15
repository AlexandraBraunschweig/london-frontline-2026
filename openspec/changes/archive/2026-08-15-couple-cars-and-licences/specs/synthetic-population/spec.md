## Purpose

Generates synthetic persons and households matched to Census marginals, with car ownership and licence holding coupled to the household's adults rather than drawn independently of them.

## MODIFIED Requirements

### Requirement: License type and car ownership
Each synthetic Person SHALL have a license_type attribute with value `none`, `car`, or `bus`. Car ownership SHALL be represented only at household level, as the Household `num_cars` attribute matched to its Output Area's car-availability marginal; Person SHALL NOT carry a separate car-ownership attribute.

A Person below the configured minimum driving age SHALL have license_type `none`. Above it, licence holding SHALL be conditioned on the household's car count rather than drawn independently: within each household, the number of adults holding license_type = `car` SHALL be at least the household's `num_cars`. Remaining licences SHALL be distributed across the other adults in the LAD so that the overall proportion of adults holding a car licence still matches its configured target. Licences are redistributed to satisfy this, never created — the aggregate proportion is a constraint, not an outcome.

#### Scenario: Under-driving-age person
- **WHEN** a synthetic person's age is below the configured minimum driving age
- **THEN** license_type SHALL be `none`

#### Scenario: Every car-owning household can drive
- **WHEN** a household has `num_cars` ≥ 1
- **THEN** at least `num_cars` of its members SHALL have license_type = `car`

#### Scenario: Licence proportion preserved
- **WHEN** the population is generated for the LAD
- **THEN** the share of people at or above the minimum driving age holding license_type = `car` SHALL match the configured proportion within rounding tolerance

#### Scenario: Car ownership has a single source of truth
- **WHEN** the number of cars available to a person is needed downstream
- **THEN** it SHALL be read from that person's Household `num_cars`, there being no person-level car attribute that could disagree with it

### Requirement: Person and household generation matched to Census marginals
The system SHALL generate synthetic Person records (age, sex, license_type) and Household records (composition_type, num_persons, num_cars) for each Output Area within a specified LAD, such that aggregate counts match published ONS Census 2021 Output-Area-level marginal tables for age/sex distribution, household composition, and car availability.

A Household SHALL NOT be assigned more cars than it has members at or above the minimum driving age. The car-availability marginal SHALL still be matched exactly per Output Area; the constraint governs *which* households receive a given car count, not how many households receive it, with the choice made at random among those eligible to hold that count.

#### Scenario: Aggregate age/sex match
- **WHEN** the population is generated for a given Output Area
- **THEN** the count of synthetic persons by age band and sex SHALL match that Output Area's published Census marginal within rounding tolerance

#### Scenario: Household composition match
- **WHEN** households are generated for an Output Area
- **THEN** the distribution of household composition types SHALL match that Output Area's published household composition marginal within rounding tolerance

#### Scenario: Cars never exceed adults
- **WHEN** a household is assigned a car count
- **THEN** that count SHALL NOT exceed the number of its members at or above the minimum driving age

#### Scenario: Household with no adult
- **WHEN** a household contains no member at or above the minimum driving age
- **THEN** it SHALL be assigned zero cars

#### Scenario: Car marginal still matched exactly
- **WHEN** car counts are assigned across an Output Area
- **THEN** the number of households in each car-availability category SHALL equal the published marginal, as it did before the adult constraint was applied

## ADDED Requirements

### Requirement: Coupling is reported
The system SHALL report the coupling between cars, adults and licences so that a regression is visible: the number of car-owning households with no licensed driver, the number of households holding more cars than adults, and the distribution of licensed drivers per car.

#### Scenario: Coupling summary produced
- **WHEN** the population is generated
- **THEN** the summary SHALL state car-owning households with no licensed driver and households with more cars than adults, both of which SHALL be zero
