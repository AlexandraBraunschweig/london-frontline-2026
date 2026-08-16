## Purpose

Turns a completed plan into layers that can be put on a map without further joins.

## ADDED Requirements

### Requirement: Layers are self-contained
Each exported layer SHALL carry geometry together with every attribute needed to style and label it, so that no join is required at presentation time. The system SHALL NOT export the normalised planning tables as map layers, because most of them carry no geometry and the figures worth showing are spread across them.

#### Scenario: Choropleth without a join
- **WHEN** the Output Area summary layer is opened in a map tool
- **THEN** it SHALL already carry population, cars owned, cars activated, people seated, unmet demand and mean occupancy as attributes of each polygon

#### Scenario: Normalised tables are not layers
- **WHEN** a planning table carries no geometry
- **THEN** it SHALL NOT be written as a map layer; its figures SHALL instead be joined onto a layer that does

### Requirement: The five layers
The system SHALL produce an Output Area summary polygon layer, a fleet point layer covering every vehicle with its activation state and occupancy, a walk-line layer joining each walk-in household to its assigned vehicle, a collection-route layer for vehicles that detour to collect non-walkers, and an unmet-demand point layer locating people who could not be seated.

#### Scenario: Fleet shows both states
- **WHEN** the fleet layer is produced
- **THEN** it SHALL include vehicles that did not depart as well as those that did, distinguished by an activation attribute, so the saving against the baseline is visible

#### Scenario: Unmet demand has a location
- **WHEN** a person cannot be seated
- **THEN** they SHALL appear in the unmet layer at their household's location, with the reason they were not seated

### Requirement: Formats match layer size
Layers SHALL be written in formats a browser can consume: GeoJSON where the layer is small enough to load whole, and FlatGeobuf where it is not. A sampled version of any layer too dense to read as a picture SHALL also be written.

#### Scenario: Dense layer is sampled
- **WHEN** a layer contains more features than can be read as a picture
- **THEN** a sampled version SHALL be written alongside the full one, and the sample size SHALL be recorded

#### Scenario: Sizes reported
- **WHEN** layers are exported
- **THEN** the feature count and file size of each SHALL be reported, so a layer that will not load is evident before presenting it

### Requirement: Origin-destination data for arc rendering
Where a layer represents movement from one place to another, the system SHALL additionally write it as a flat origin-destination table carrying source and target coordinates as attributes, rather than only as line geometry. Arc renderers take endpoints as attributes, so line geometry forces a conversion and costs several times the size.

Because individual walks are short enough to be sub-pixel at district zoom, the system SHALL also write an area-to-area aggregation of the same movement, so the pattern is legible without zooming to street level.

#### Scenario: Arc data carries endpoints
- **WHEN** movement is exported for arc rendering
- **THEN** each row SHALL carry source and target longitude and latitude as plain numeric attributes

#### Scenario: Aggregated flows for district view
- **WHEN** individual movements are too short to read at the scale being presented
- **THEN** an aggregation between administrative areas SHALL be written alongside, weighted by the number of people moving

### Requirement: Animated paths are marked schematic
The plan contains no time model: stops share one departure time, offsets use straight-line distance over an assumed speed, and the journey to the destination is not modelled at all. Where the system writes paths for animation, it SHALL append the missing destination leg at a stated assumed speed, stretch timings to a plausible duration, and mark every row as schematic.

The system SHALL NOT present these as simulated movement. Clearance times read off them would be meaningless, because the congestion that determines them is the subject of a separate, downstream model.

#### Scenario: Every animated path is flagged
- **WHEN** trip paths are written
- **THEN** each row SHALL carry a flag identifying it as schematic rather than simulated

#### Scenario: Waypoints and timestamps agree
- **WHEN** a trip path is written
- **THEN** it SHALL carry exactly one timestamp per waypoint, in non-decreasing order

### Requirement: A viewer accompanies the layers
The system SHALL provide a viewer that reads the exported layers directly, so the plan can be shown without anyone reconstructing styling or joins. The viewer SHALL display the schematic warning wherever animated paths are shown, so an animation cannot be presented without the caveat that governs it.

#### Scenario: Viewer reads the exports unmodified
- **WHEN** the pipeline is re-run
- **THEN** the viewer SHALL show the new output without any copy or conversion step

#### Scenario: Warning travels with the animation
- **WHEN** animated paths are displayed
- **THEN** the schematic warning SHALL be visible alongside them

### Requirement: The hosted bundle is a deliberate artefact
The data the hosted viewer serves SHALL be a trimmed copy written into the viewer's own tree, distinct from the working export directory. It is committed and therefore permanent in the repository's history, so it SHALL carry only the layers the viewer loads, at a coordinate precision finer than the model's own accuracy but no finer.

#### Scenario: Bundle is smaller than the working exports
- **WHEN** the hosted bundle is written
- **THEN** it SHALL contain only the layers the viewer loads, and its size reduction against the source SHALL be reported

#### Scenario: Hosting needs no build-time pipeline run
- **WHEN** the site is built for hosting
- **THEN** it SHALL require only the committed bundle, not a pipeline run, because the pipeline depends on large external downloads
