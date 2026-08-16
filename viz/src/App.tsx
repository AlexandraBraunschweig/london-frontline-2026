import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import DeckGL from '@deck.gl/react'
import { Map } from 'react-map-gl/maplibre'
import 'maplibre-gl/dist/maplibre-gl.css'

import { Headline } from './components/Headline'
import { Legend } from './components/Legend'
import { TimeControls } from './components/TimeControls'
import { Tooltip } from './components/Tooltip'
import { buildLayers } from './layers'
import { METRICS } from './metrics'
import { usePlanData } from './usePlanData'
import type { HoverInfo, LayerVisibility, MetricKey } from './types'

const INITIAL_VIEW = {
  longitude: 1.35,
  latitude: 51.35,
  zoom: 11.4,
  pitch: 45,
  bearing: -15,
}

const BASEMAP = 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json'

// Nominal seconds advanced per animation frame. At ~60fps this plays the
// 56-minute schematic timeline in roughly a minute and a half.
const SECONDS_PER_FRAME = 40

const LAYER_TOGGLES: [keyof LayerVisibility, string][] = [
  ['areas', 'Output areas'],
  ['flows', 'Pooling flows'],
  ['unmet', 'Left behind'],
  ['animation', 'Animation'],
]

export default function App() {
  const { plan, trips, error } = usePlanData()
  const [visible, setVisible] = useState<LayerVisibility>({
    areas: true,
    flows: false,
    unmet: false,
    animation: false,
  })
  const [metric, setMetric] = useState<MetricKey>('unmet_pct')
  const [time, setTime] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [hover, setHover] = useState<HoverInfo>(null)
  const frame = useRef<number>()

  const maxTime = trips?.maxTime ?? 0

  useEffect(() => {
    if (!playing || !trips) return
    const step = () => {
      setTime((previous) => (previous + SECONDS_PER_FRAME) % trips.maxTime)
      frame.current = requestAnimationFrame(step)
    }
    frame.current = requestAnimationFrame(step)
    return () => {
      if (frame.current) cancelAnimationFrame(frame.current)
    }
  }, [playing, trips])

  const onHover = useCallback((info: HoverInfo) => setHover(info), [])

  const { layers, cuts } = useMemo(() => {
    if (!plan) return { layers: [], cuts: [] as number[] }
    return buildLayers({ plan, trips, visible, metric, time, onHover })
  }, [plan, trips, visible, metric, time, onHover])

  const toggle = (key: keyof LayerVisibility) =>
    setVisible((previous) => ({ ...previous, [key]: !previous[key] }))

  return (
    <>
      <DeckGL initialViewState={INITIAL_VIEW} controller layers={layers}>
        <Map mapStyle={BASEMAP} />
      </DeckGL>

      <aside id="panel">
        <header>
          <h1>Thanet evacuation plan</h1>
          <p className="sub">LAD E07000114 &middot; synthetic population, pooled ride plan</p>
        </header>

        {plan && <Headline areas={plan.areas} />}

        <section>
          <h2>Layers</h2>
          {LAYER_TOGGLES.map(([key, label]) => (
            <label key={key}>
              <input type="checkbox" checked={visible[key]} onChange={() => toggle(key)} />
              {label}
            </label>
          ))}
        </section>

        {visible.areas && plan && (
          <section>
            <h2>Shade areas by</h2>
            <select value={metric} onChange={(event) => setMetric(event.target.value as MetricKey)}>
              {Object.entries(METRICS).map(([key, spec]) => (
                <option value={key} key={key}>
                  {spec.label}
                </option>
              ))}
            </select>
            <Legend cuts={cuts} spec={METRICS[metric]} />
          </section>
        )}

        {visible.animation && (
          <TimeControls
            time={time}
            maxTime={maxTime}
            playing={playing}
            ready={trips !== null}
            onScrub={setTime}
            onTogglePlay={() => setPlaying((previous) => !previous)}
          />
        )}

        <footer id="status">
          {error
            ? `Failed to load: ${error}. Run the pipeline first.`
            : plan
              ? `${plan.areas.features.length} output areas · ${plan.flows.length} pooling flows` +
                (trips ? ` · ${trips.vehicles.length.toLocaleString()} vehicle paths` : '')
              : 'Loading…'}
        </footer>
      </aside>

      <Tooltip info={hover} />
    </>
  )
}
