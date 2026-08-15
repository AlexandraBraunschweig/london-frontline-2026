import type { MetricKey, MetricSpec, OutputAreaProps } from './types'

export const METRICS: Record<MetricKey, MetricSpec> = {
  unmet_pct: { label: 'Unmet demand (%)', unit: '%', decimals: 1 },
  cars_saved: { label: 'Cars saved vs baseline', unit: '', decimals: 0 },
  mean_occupancy: { label: 'Mean occupancy', unit: '', decimals: 2 },
  median_walk_m: { label: 'Median walk (m)', unit: ' m', decimals: 0 },
  people: { label: 'Population', unit: '', decimals: 0 },
}

/** Sequential ramp, dark to bright, legible over a dark basemap. */
export const RAMP: readonly [number, number, number][] = [
  [37, 52, 88],
  [51, 88, 122],
  [70, 128, 145],
  [120, 168, 148],
  [193, 199, 140],
  [235, 195, 110],
  [240, 144, 44],
]

const MISSING_COLOUR: [number, number, number, number] = [60, 65, 75, 120]

/**
 * Percentile breaks rather than equal intervals, so that a handful of extreme
 * Output Areas cannot flatten the rest of the ramp into a single colour.
 */
export function percentileBreaks(values: (number | null)[], buckets: number): number[] {
  const sorted = values
    .filter((value): value is number => value != null && Number.isFinite(value))
    .sort((a, b) => a - b)
  if (sorted.length === 0) return []
  return Array.from(
    { length: buckets },
    (_, index) => sorted[Math.floor(((index + 1) / buckets) * (sorted.length - 1))],
  )
}

export function colourFor(
  value: number | null | undefined,
  cuts: number[],
): [number, number, number, number] {
  if (value == null || !Number.isFinite(value)) return MISSING_COLOUR
  const found = cuts.findIndex((cut) => value <= cut)
  const index = found < 0 ? RAMP.length - 1 : Math.min(found, RAMP.length - 1)
  const [r, g, b] = RAMP[index]
  return [r, g, b, 190]
}

export function metricValue(props: OutputAreaProps, metric: MetricKey): number | null {
  return props[metric] ?? null
}

export function formatMetric(value: number | undefined, spec: MetricSpec): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return value.toFixed(spec.decimals) + spec.unit
}
