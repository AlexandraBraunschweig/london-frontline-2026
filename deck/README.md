# The slide deck

Twelve slides, built for a five-minute pitch: what the pipeline does, what it
enables, and honestly what it cannot tell you.

## Presenting it

Open `index.html`. That is the whole instruction — no server, no build, no
network. reveal.js 6.0.1 is vendored in `vendor/`, the figures are plain SVG
files, and every path is relative, so the deck presents from a `file://` URL on a
machine with nothing installed. Copy the `deck/` directory to a USB stick and it
still works.

| Key | Does |
| --- | --- |
| `→` / `space` | next slide |
| `S` | speaker view, with notes and a timer |
| `F` | full screen |
| `Esc` | slide overview |
| `?` | every other shortcut |

Every slide carries speaker notes; press `S` before you start.

## Regenerating the figures

The five figures in `figures/` are rendered from `data/warehouse.duckdb` by
`make_figures.py`, read-only, so a re-materialised pipeline can never leave the
deck quoting stale numbers:

```bash
uv run python deck/make_figures.py
```

It prints every figure it writes and, after them, the headline numbers quoted on
the slides — so the two can be checked against each other by eye. **The numbers in
`index.html` are typed in, not injected**: if a run moves them, the script's
output tells you which slides to edit.

Close any DuckDB session first. DuckDB allows one writer or several readers,
never both, and the deck's script is a reader like any other.

The figures need no plotting dependency: `make_figures.py` writes SVG directly.
Dense point layers are emitted as one `<path>` of small squares rather than tens
of thousands of `<circle>` elements, which is what keeps the fleet map to about a
megabyte.

## Design notes

- **Dark surface throughout** (`#1a1a19`), because it is presented on a projector
  and the figures are rendered against that surface. A light deck would put every
  chart in a box.
- **One hue carries meaning.** The plan is blue (`#3987e5`); everything it is
  measured against is muted grey; the limitations slide is the only place a status
  red appears. That is emphasis encoding, not a categorical palette — the two
  classes on the fleet map are "the answer" and "the background it stands out
  from", and each is labelled, so nothing is carried by colour alone.
- **No number appears without its table.** Each figure slide names the warehouse
  tables it came from in the footer, so a question from the floor can be answered
  by querying rather than by remembering.

## Upgrading reveal.js

```bash
cd deck
npm install reveal.js@<version>
cp node_modules/reveal.js/dist/{reveal.js,reveal.css,reset.css} vendor/
rm -rf node_modules package-lock.json
```

Then update the version in `package.json`. `dist/reveal.js` is the UMD build on
purpose: the ES module build cannot be loaded from a `file://` URL, which would
cost the deck its main property.
