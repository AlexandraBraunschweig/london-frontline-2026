import type { HoverInfo } from '../types'

function rowsFor(info: NonNullable<HoverInfo>): [string, string | number][] {
  switch (info.kind) {
    case 'area':
      return [
        ['Output area', info.props.area_id],
        ['Population', info.props.people.toLocaleString()],
        ['Cars owned / used', `${info.props.cars_owned} / ${info.props.cars_activated}`],
        ['Seated', info.props.people_seated.toLocaleString()],
        ['Left behind', info.props.people_unmet.toLocaleString()],
      ]
    case 'flow':
      return [
        ['Flow', `${info.props.source_area} → ${info.props.target_area}`],
        ['People', info.props.people],
        ['Mean walk', `${info.props.mean_walk_m} m`],
      ]
    case 'unmet':
      return [
        ['Left behind', info.props.people],
        ['Mode', info.props.collection_mode],
        ['Reason', info.props.reason],
      ]
  }
}

export function Tooltip({ info }: { info: HoverInfo }) {
  if (!info) return null
  return (
    <div id="tooltip" style={{ left: info.x + 14, top: info.y + 14 }}>
      {rowsFor(info).map(([label, value]) => (
        <div key={label}>
          <span className="t">{label}</span> {value}
        </div>
      ))}
    </div>
  )
}
