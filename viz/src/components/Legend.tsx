import { formatMetric, RAMP } from '../metrics'
import type { MetricSpec } from '../types'

interface Props {
  cuts: number[]
  spec: MetricSpec
}

export function Legend({ cuts, spec }: Props) {
  return (
    <div id="legend">
      <div className="ramp">
        {RAMP.map(([r, g, b]) => (
          <span key={`${r}-${g}-${b}`} style={{ background: `rgb(${r},${g},${b})` }} />
        ))}
      </div>
      <div className="ramp-labels">
        <span>{formatMetric(cuts[0], spec)}</span>
        <span>{formatMetric(cuts.at(-1), spec)}</span>
      </div>
    </div>
  )
}
