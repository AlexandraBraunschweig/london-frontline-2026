"""The subset of the exports the hosted viewer actually loads.

The full export directory is working data: several formats, full float precision,
layers the viewer never touches. This writes a deliberately smaller copy into the
viewer's own tree, because that copy is committed and therefore lives in the
repository's history for good.

Two economies, both lossless for the purpose: coordinates are rounded to a
precision finer than the model's own accuracy, and the walk animation is sampled
to what reads as texture rather than to completeness.
"""

import json
import shutil

from dagster import AssetExecutionContext, MetadataValue, asset

from london_frontline.assets.trips import trip_paths
from london_frontline.config import PlanningConfig
from london_frontline.paths import PROJECT_ROOT, resolve
from london_frontline.resources import SpatialDuckDBResource

# Six decimal places is about 0.1 m — far finer than a UPRN's own positional
# accuracy, let alone a straight-line walk path.
_COORDINATE_DECIMALS = 6

# Enough walk paths to read as movement; the full set is texture, not detail.
_WALK_SAMPLE = 8_000

_BUNDLE = (
    "oa_summary.geojson",
    "arc_flows_oa.json",
    "unmet.geojson",
    "trips_vehicles.json",
    "trips_walks.json",
)


def _round_floats(value, decimals: int):
    """Round every float in a nested structure, leaving everything else alone."""
    if isinstance(value, float):
        return round(value, decimals)
    if isinstance(value, list):
        return [_round_floats(item, decimals) for item in value]
    if isinstance(value, dict):
        return {key: _round_floats(item, decimals) for key, item in value.items()}
    return value


@asset(deps=[trip_paths], group_name="presentation")
def web_bundle(
    context: AssetExecutionContext,
    planning: PlanningConfig,
    warehouse: SpatialDuckDBResource,
) -> None:
    """Write the viewer's committed data directory."""
    source = resolve(planning.data_dir) / "exports" / "map"
    target = PROJECT_ROOT / "viz" / "public" / "data"

    # An earlier iteration symlinked this directory at the exports. A hosted
    # build needs real files, so replace the link if it is still there.
    if target.is_symlink():
        target.unlink()
    target.mkdir(parents=True, exist_ok=True)

    written: dict[str, tuple[int, int]] = {}
    for name in _BUNDLE:
        payload = json.loads((source / name).read_text())

        if name == "trips_walks.json" and isinstance(payload, list):
            payload = payload[:_WALK_SAMPLE]

        payload = _round_floats(payload, _COORDINATE_DECIMALS)
        # Separators without spaces; the viewer parses this, nobody reads it.
        text = json.dumps(payload, separators=(",", ":"))
        (target / name).write_text(text)
        written[name] = ((source / name).stat().st_size, len(text.encode()))

    before = sum(b for b, _ in written.values())
    after = sum(a for _, a in written.values())

    summary = "\n".join(
        f"  {name:24} {b / 1e6:6.2f} MB -> {a / 1e6:6.2f} MB"
        for name, (b, a) in written.items()
    )
    context.log.info(
        "Web bundle written to %s (%.2f MB -> %.2f MB, %.0f%% smaller):\n%s",
        target.relative_to(PROJECT_ROOT),
        before / 1e6,
        after / 1e6,
        100 * (1 - after / before),
        summary,
    )
    context.add_output_metadata(
        {
            "path": MetadataValue.path(str(target)),
            "files": len(written),
            "source_mb": round(before / 1e6, 2),
            "bundle_mb": round(after / 1e6, 2),
            "reduction_pct": round(100 * (1 - after / before)),
            "note": MetadataValue.md(
                "This directory is committed, so it stays in the repository's "
                "history. Re-running the pipeline rewrites it; commit the change "
                "only when the hosted viewer should be updated, rather than on "
                "every run."
            ),
        }
    )
