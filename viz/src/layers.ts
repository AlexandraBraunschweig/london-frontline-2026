import type { Layer, PickingInfo } from '@deck.gl/core'
import { ArcLayer, GeoJsonLayer, ScatterplotLayer } from '@deck.gl/layers'
import { TripsLayer } from '@deck.gl/geo-layers'

import { colourFor, percentileBreaks, RAMP } from './metrics'
import type {
  AreaFeature,
  OutputAreaProps,
  FlowArc,
  HoverInfo,
  LayerVisibility,
  MetricKey,
  PlanData,
  TripData,
  UnmetFeature,
  VehicleTrip,
  WalkTrip,
} from './types'

interface BuildArgs {
  plan: PlanData
  trips: TripData | null
  visible: LayerVisibility
  metric: MetricKey
  time: number
  onHover: (info: HoverInfo) => void
}

export function buildLayers({
  plan,
  trips,
  visible,
  metric,
  time,
  onHover,
}: BuildArgs): { layers: Layer[]; cuts: number[] } {
  const layers: Layer[] = []
  const cuts = percentileBreaks(
    plan.areas.features.map((feature) => feature.properties[metric]),
    RAMP.length,
  )

  if (visible.areas) {
    layers.push(
      new GeoJsonLayer<OutputAreaProps>({
        id: 'output-areas',
        data: plan.areas,
        filled: true,
        stroked: true,
        getFillColor: (feature) => colourFor(feature.properties[metric], cuts),
        getLineColor: [140, 150, 165, 60],
        lineWidthMinPixels: 0.5,
        pickable: true,
        onHover: (info: PickingInfo<AreaFeature>) =>
          onHover(
            info.object
              ? { kind: 'area', props: info.object.properties, x: info.x, y: info.y }
              : null,
          ),
        updateTriggers: { getFillColor: [metric, cuts] },
      }),
    )
  }

  if (visible.flows) {
    layers.push(
      new ArcLayer<FlowArc>({
        id: 'pooling-flows',
        data: plan.flows,
        getSourcePosition: (d) => [d.source_lon, d.source_lat],
        getTargetPosition: (d) => [d.target_lon, d.target_lat],
        getSourceColor: [70, 130, 220, 170],
        getTargetColor: [240, 144, 44, 210],
        getWidth: (d) => Math.max(1, Math.sqrt(d.people)),
        // Deliberately low. These arcs span hundreds of metres, not hundreds of
        // kilometres; the default height of 1 renders them as vertical hoops.
        getHeight: 0.35,
        pickable: true,
        onHover: (info: PickingInfo<FlowArc>) =>
          onHover(info.object ? { kind: 'flow', props: info.object, x: info.x, y: info.y } : null),
      }),
    )
  }

  if (visible.unmet) {
    layers.push(
      new ScatterplotLayer<UnmetFeature>({
        id: 'unmet',
        data: plan.unmet.features,
        getPosition: (feature) => feature.geometry.coordinates as [number, number],
        getRadius: (feature) => 8 + 4 * feature.properties.people,
        radiusUnits: 'meters',
        radiusMinPixels: 2,
        getFillColor: [235, 70, 70, 200],
        pickable: true,
        onHover: (info: PickingInfo<UnmetFeature>) =>
          onHover(
            info.object
              ? { kind: 'unmet', props: info.object.properties, x: info.x, y: info.y }
              : null,
          ),
      }),
    )
  }

  if (visible.animation && trips) {
    layers.push(
      new TripsLayer<WalkTrip>({
        id: 'walk-trips',
        data: trips.walks,
        getPath: (d) => d.path,
        getTimestamps: (d) => d.timestamps,
        getColor: (d) => (d.own_car ? [90, 150, 235] : [130, 220, 190]),
        widthMinPixels: 1.5,
        trailLength: 90,
        currentTime: time,
      }),
      new TripsLayer<VehicleTrip>({
        id: 'vehicle-trips',
        data: trips.vehicles,
        getPath: (d) => d.path,
        getTimestamps: (d) => d.timestamps,
        getColor: (d) => (d.collection_stops > 0 ? [240, 144, 44] : [225, 225, 235]),
        getWidth: (d) => Math.sqrt(d.occupants),
        widthMinPixels: 2,
        trailLength: 220,
        currentTime: time,
      }),
    )
  }

  return { layers, cuts }
}
