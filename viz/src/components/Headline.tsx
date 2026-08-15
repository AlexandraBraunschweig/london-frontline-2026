import type { OutputAreaCollection } from '../types'

interface Props {
  areas: OutputAreaCollection
}

/** The numbers the plan exists to produce, summed from the Output Area layer. */
export function Headline({ areas }: Props) {
  const total = (key: 'baseline_cars' | 'cars_activated' | 'people_seated' | 'people_unmet') =>
    areas.features.reduce((sum, feature) => sum + (feature.properties[key] ?? 0), 0)

  const baseline = total('baseline_cars')
  const activated = total('cars_activated')
  const reduction = baseline > 0 ? Math.round((1 - activated / baseline) * 100) : 0

  const rows: [string, string, boolean][] = [
    ['Cars on the road', activated.toLocaleString(), true],
    ['Baseline (one per household)', baseline.toLocaleString(), false],
    ['Reduction', `${reduction}%`, true],
    ['People carried', total('people_seated').toLocaleString(), false],
    ['Left behind', total('people_unmet').toLocaleString(), false],
  ]

  return (
    <section id="headline">
      {rows.map(([label, value, emphasis]) => (
        <div className="stat" key={label}>
          <span className="k">{label}</span>
          <span className={emphasis ? 'v big' : 'v'}>{value}</span>
        </div>
      ))}
    </section>
  )
}
