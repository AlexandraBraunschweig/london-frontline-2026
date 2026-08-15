import type { Feature, FeatureCollection, Point, Polygon, MultiPolygon } from 'geojson'

/** One Output Area, carrying every figure needed to shade it. */
export interface OutputAreaProps {
  area_id: string
  people: number
  households: number
  cars_owned: number
  baseline_cars: number
  cars_activated: number
  cars_saved: number
  people_seated: number
  people_unmet: number
  unmet_pct: number | null
  mean_occupancy: number | null
  median_walk_m: number | null
}

export type OutputAreaCollection = FeatureCollection<Polygon | MultiPolygon, OutputAreaProps>

/** Aggregated Output Area to Output Area pooling flow, for arc rendering. */
export interface FlowArc {
  source_area: string
  target_area: string
  people: number
  mean_walk_m: number
  source_lon: number
  source_lat: number
  target_lon: number
  target_lat: number
}

export interface UnmetProps {
  travel_group_id: number
  area_id: string
  people: number
  collection_mode: string
  reason: string
}

export type UnmetCollection = FeatureCollection<Point, UnmetProps>

/**
 * A schematic trip. Paths are straight lines and timestamps are nominal — the
 * plan models no congestion — which is why every row carries `schematic`.
 */
export interface Trip {
  path: [number, number][]
  timestamps: number[]
  schematic: boolean
}

export interface VehicleTrip extends Trip {
  muster_point_id: number
  occupants: number
  capacity: number
  license_plate: string
  collection_stops: number
}

export interface WalkTrip extends Trip {
  household_id: number
  muster_point_id: number
  walk_m: number
  own_car: boolean
}

/** Which Output Area figure drives the choropleth. */
export type MetricKey =
  | 'unmet_pct'
  | 'cars_saved'
  | 'mean_occupancy'
  | 'median_walk_m'
  | 'people'

export interface MetricSpec {
  label: string
  unit: string
  decimals: number
}

export interface LayerVisibility {
  areas: boolean
  flows: boolean
  unmet: boolean
  animation: boolean
}

export interface PlanData {
  areas: OutputAreaCollection
  flows: FlowArc[]
  unmet: UnmetCollection
}

export interface TripData {
  vehicles: VehicleTrip[]
  walks: WalkTrip[]
  maxTime: number
}

export type HoverInfo =
  | { kind: 'area'; props: OutputAreaProps; x: number; y: number }
  | { kind: 'flow'; props: FlowArc; x: number; y: number }
  | { kind: 'unmet'; props: UnmetProps; x: number; y: number }
  | null

export type AreaFeature = Feature<Polygon | MultiPolygon, OutputAreaProps>
export type UnmetFeature = Feature<Point, UnmetProps>
