interface Props {
  time: number
  maxTime: number
  playing: boolean
  ready: boolean
  onScrub: (time: number) => void
  onTogglePlay: () => void
}

function clock(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  const rest = Math.floor(seconds % 60)
  return `${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`
}

export function TimeControls({ time, maxTime, playing, ready, onScrub, onTogglePlay }: Props) {
  return (
    <section id="time-section">
      <h2>Animation</h2>
      {ready ? (
        <>
          <div className="row">
            <button onClick={onTogglePlay}>{playing ? 'Pause' : 'Play'}</button>
            <span id="clock">{clock(time)}</span>
          </div>
          <input
            type="range"
            min={0}
            max={maxTime}
            value={time}
            onChange={(event) => onScrub(Number(event.target.value))}
          />
        </>
      ) : (
        <p className="loading">Loading trip paths&hellip;</p>
      )}
      <p className="warning">
        <strong>Schematic.</strong> Paths are straight lines, not roads, and timings are
        nominal &mdash; the plan models no congestion and never computed a drive time to
        the destination. This shows the structure of the plan.{' '}
        <strong>It cannot tell you how long an evacuation takes.</strong>
      </p>
    </section>
  )
}
