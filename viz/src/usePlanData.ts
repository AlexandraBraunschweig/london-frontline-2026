import { useEffect, useState } from 'react'
import type { PlanData, TripData } from './types'

const DATA_ROOT = './data'

async function loadJson<T>(name: string): Promise<T> {
  const response = await fetch(`${DATA_ROOT}/${name}`)
  if (!response.ok) throw new Error(`${name}: HTTP ${response.status}`)
  return (await response.json()) as T
}

interface LoadState {
  plan: PlanData | null
  trips: TripData | null
  error: string | null
}

/**
 * Loads the exported layers in two waves: the light ones first so the map is
 * usable immediately, then the trip paths, which are an order of magnitude
 * larger and only needed once the animation is switched on.
 */
export function usePlanData(): LoadState {
  const [state, setState] = useState<LoadState>({ plan: null, trips: null, error: null })

  useEffect(() => {
    let cancelled = false

    async function load() {
      try {
        const [areas, flows, unmet] = await Promise.all([
          loadJson<PlanData['areas']>('oa_summary.geojson'),
          loadJson<PlanData['flows']>('arc_flows_oa.json'),
          loadJson<PlanData['unmet']>('unmet.geojson'),
        ])
        if (cancelled) return
        setState((previous) => ({ ...previous, plan: { areas, flows, unmet } }))

        const [vehicles, walks] = await Promise.all([
          loadJson<TripData['vehicles']>('trips_vehicles.json'),
          loadJson<TripData['walks']>('trips_walks.json'),
        ])
        if (cancelled) return
        const maxTime = vehicles.reduce(
          (longest, trip) => Math.max(longest, trip.timestamps.at(-1) ?? 0),
          0,
        )
        setState((previous) => ({ ...previous, trips: { vehicles, walks, maxTime } }))
      } catch (error) {
        if (cancelled) return
        const message = error instanceof Error ? error.message : String(error)
        setState((previous) => ({ ...previous, error: message }))
      }
    }

    void load()
    return () => {
      cancelled = true
    }
  }, [])

  return state
}
