"""ONS National Statistics UPRN Lookup (NSUL) — the only dwelling dataset used.

NSUL gives, per UPRN, its British National Grid coordinates and its Output Area
allocation. That is everything the pipeline needs from a dwelling source, which
is why OS Open UPRN is not ingested (see design.md).

It is published on the ONS Open Geography portal as a single whole-GB zip
containing one CSV per region. Downloading all 514 MB to read one region is
wasteful, so this module reads the archive's central directory over HTTP range
requests, locates the member for the region of interest, and streams and inflates
just that member — 71 MB compressed for the South East. Rows are filtered to the
target Output Areas as they are decoded, so the 1.4 GB uncompressed member never
lands on disk or in memory.
"""

from __future__ import annotations

import csv
import io
import re
import struct
import zlib
from dataclasses import dataclass
from typing import Iterable, Iterator

import pandas as pd
import requests

ARCGIS_SEARCH = "https://www.arcgis.com/sharing/rest/search"
ARCGIS_ITEM_DATA = "https://www.arcgis.com/sharing/rest/content/items/{item_id}/data"

# How much of the archive tail to read when looking for the central directory.
# The directory holds ~13 entries, so this is generous.
_TAIL_BYTES = 300_000

# Zip structures.
_CENTRAL_DIR_SIGNATURE = b"PK\x01\x02"
_DEFLATE = 8
_STORED = 0

_STREAM_CHUNK = 1 << 20


@dataclass(frozen=True)
class NsulEdition:
    """A published NSUL edition and the archive member for one region."""

    item_id: str
    title: str
    member_name: str
    compressed_size: int
    uncompressed_size: int
    local_offset: int
    compression: int

    @property
    def epoch(self) -> str:
        """The epoch label, e.g. ``Epoch 127``, for provenance reporting."""
        match = re.search(r"Epoch \d+", self.title)
        return match.group(0) if match else self.title


def find_latest_edition(timeout: int = 60) -> tuple[str, str]:
    """Return the (item_id, title) of the most recent NSUL data release.

    Discovered rather than pinned, because ONS republishes NSUL roughly every six
    weeks and a hardcoded id would silently rot. Titles containing "User Guide"
    are the companion documents, not the data.
    """
    response = requests.get(
        ARCGIS_SEARCH,
        params={
            "q": 'title:"National Statistics UPRN Lookup"',
            "num": 25,
            "sortField": "created",
            "sortOrder": "desc",
            "f": "json",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    for result in response.json().get("results", []):
        title = result.get("title", "")
        if "User Guide" in title or result.get("type") != "CSV Collection":
            continue
        return result["id"], title
    raise RuntimeError("No NSUL data release found in the ArcGIS item search")


def _archive_size(url: str, timeout: int) -> int:
    response = requests.head(url, allow_redirects=True, timeout=timeout)
    response.raise_for_status()
    return int(response.headers["content-length"])


def _fetch_range(url: str, start: int, end: int, timeout: int) -> bytes:
    """Fetch an inclusive byte range."""
    response = requests.get(
        url, headers={"Range": f"bytes={start}-{end}"}, timeout=timeout
    )
    response.raise_for_status()
    return response.content


def locate_region_member(
    item_id: str, title: str, region_code: str, timeout: int = 180
) -> NsulEdition:
    """Find the archive member for ``region_code`` without downloading the archive."""
    url = ARCGIS_ITEM_DATA.format(item_id=item_id)
    size = _archive_size(url, timeout)
    tail = _fetch_range(url, max(0, size - _TAIL_BYTES), size - 1, timeout)

    suffix = f"_{region_code.upper()}.csv".encode()
    for match in re.finditer(_CENTRAL_DIR_SIGNATURE, tail):
        offset = match.start()
        header = tail[offset : offset + 46]
        if len(header) < 46:
            continue
        compression = struct.unpack("<H", header[10:12])[0]
        compressed_size, uncompressed_size = struct.unpack("<II", header[20:28])
        name_len = struct.unpack("<H", header[28:30])[0]
        local_offset = struct.unpack("<I", header[42:46])[0]
        name = tail[offset + 46 : offset + 46 + name_len]
        if name.endswith(suffix):
            return NsulEdition(
                item_id=item_id,
                title=title,
                member_name=name.decode(),
                compressed_size=compressed_size,
                uncompressed_size=uncompressed_size,
                local_offset=local_offset,
                compression=compression,
            )
    raise RuntimeError(
        f"No NSUL member for region {region_code!r} in {title}. Expected an entry "
        f"ending {suffix.decode()!r}."
    )


def _inflated_lines(
    url: str, edition: NsulEdition, timeout: int
) -> Iterator[str]:
    """Stream the member's compressed bytes and yield decoded CSV lines."""
    # The local file header repeats the name and extra fields, whose lengths can
    # differ from the central directory's, so they are read from the header
    # itself rather than assumed.
    head = _fetch_range(
        url, edition.local_offset, edition.local_offset + 4096, timeout
    )
    name_len, extra_len = struct.unpack("<HH", head[26:30])
    data_start = edition.local_offset + 30 + name_len + extra_len
    data_end = data_start + edition.compressed_size - 1

    if edition.compression not in (_DEFLATE, _STORED):
        raise RuntimeError(
            f"Unsupported zip compression method {edition.compression} for "
            f"{edition.member_name}"
        )

    decompressor = (
        zlib.decompressobj(-zlib.MAX_WBITS) if edition.compression == _DEFLATE else None
    )
    response = requests.get(
        url,
        headers={"Range": f"bytes={data_start}-{data_end}"},
        stream=True,
        timeout=timeout,
    )
    response.raise_for_status()

    pending = ""
    for chunk in response.iter_content(chunk_size=_STREAM_CHUNK):
        if not chunk:
            continue
        raw = decompressor.decompress(chunk) if decompressor else chunk
        if not raw:
            continue
        pending += raw.decode("utf-8", errors="replace")
        lines = pending.split("\n")
        pending = lines.pop()
        yield from lines
    if decompressor:
        tail = decompressor.flush()
        if tail:
            pending += tail.decode("utf-8", errors="replace")
    if pending:
        yield pending


def fetch_uprns_for_areas(
    edition: NsulEdition,
    area_codes: Iterable[str],
    timeout: int = 900,
) -> pd.DataFrame:
    """Return NSUL rows whose Output Area is in ``area_codes``.

    Filtering happens during decode, so peak memory is the size of the retained
    subset rather than the 1.4 GB member.
    """
    wanted = set(area_codes)
    url = ARCGIS_ITEM_DATA.format(item_id=edition.item_id)
    lines = _inflated_lines(url, edition, timeout)

    header_line = next(lines).lstrip("﻿").rstrip("\r")
    header = next(csv.reader(io.StringIO(header_line)))
    index = {name: position for position, name in enumerate(header)}
    for required in ("UPRN", "GRIDGB1E", "GRIDGB1N", "OA21CD"):
        if required not in index:
            raise RuntimeError(
                f"NSUL member {edition.member_name} lacks column {required!r}; "
                f"found {header}"
            )

    uprn_at = index["UPRN"]
    easting_at = index["GRIDGB1E"]
    northing_at = index["GRIDGB1N"]
    area_at = index["OA21CD"]

    # Eastings and northings are usually whole metres but NSUL does publish
    # sub-metre values, so they are parsed as floats.
    rows: list[tuple[int, float, float, str]] = []
    scanned = 0
    for line in lines:
        line = line.rstrip("\r")
        if not line:
            continue
        scanned += 1
        # Split manually rather than via csv.reader per row: NSUL fields are
        # unquoted codes and this member has ~10M rows.
        fields = line.split(",")
        area = fields[area_at]
        if area not in wanted:
            continue
        rows.append(
            (
                int(fields[uprn_at]),
                float(fields[easting_at]),
                float(fields[northing_at]),
                area,
            )
        )

    frame = pd.DataFrame(rows, columns=["uprn", "easting", "northing", "area_id"])
    frame.attrs["rows_scanned"] = scanned
    return frame
