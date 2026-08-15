"""Project-root-anchored path resolution.

Relative paths in config are resolved against the project root rather than the
current working directory. Dagster is routinely launched from somewhere other
than the repo root (the UI, a scheduler, a subdirectory shell), and a
cwd-relative warehouse path silently creates a second, empty database instead of
failing loudly.
"""

from pathlib import Path

# .../src/london_frontline/paths.py -> .../
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def resolve(path: str | Path) -> Path:
    """Resolve ``path`` against the project root, leaving absolute paths alone."""
    candidate = Path(path)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate
