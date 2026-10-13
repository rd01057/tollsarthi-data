#!/usr/bin/env python3
"""Generate per-Geohash-4 fuel/EV station files for TollSarthi.

Fetches OSM ``amenity=fuel`` and ``amenity=charging`` POIs for India via the
Overpass API, groups them by Geohash-4 cell, and writes one ``.json.gz`` per
cell into the output directory.

Output format matches ``FuelEvService.parseFuelJson`` in
``lib/fuel/fuel_service.dart``::

    {"cell": "tdr1", "updated": "2026-10-12", "points": [
        {"name": "HP Petrol", "lat": 26.1, "lon": 89.5,
         "kind": "fuel", "fuel_type": "petrol_cng"}]}

Usage:
    pip install requests
    python generate_cells.py --out output
    # test with a small bbox (south,west,north,east):
    python generate_cells.py --out output --bbox "28.4,76.9,28.9,77.4"
"""

import argparse
import gzip
import json
import sys
import time
from datetime import datetime, timezone

import requests

BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"
# Overpass endpoints tried in order (first healthy one wins per query).
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
OVERPASS_HEADERS = {"User-Agent": "TollSarthi/1.0 (fuel-ev-data-pipeline)"}

# India split into 8 bbox chunks (2 lat bands x 4 lon bands) to keep each
# Overpass query under the timeout. Format: (south, west, north, east).
INDIA_CHUNKS = [
    (6.5, 68.0, 22.0, 75.5),
    (6.5, 75.5, 22.0, 83.0),
    (6.5, 83.0, 22.0, 90.5),
    (6.5, 90.5, 22.0, 98.0),
    (22.0, 68.0, 38.0, 75.5),
    (22.0, 75.5, 38.0, 83.0),
    (22.0, 83.0, 38.0, 90.5),
    (22.0, 90.5, 38.0, 98.0),
]


def geohash(lat, lon, precision=4):
    """Encode lat/lon to a geohash string. Matches tool/build_db.py and the
    Dart geohashEncode() in lib/cloud/geohash.dart."""
    lat_i, lon_i = [-90.0, 90.0], [-180.0, 180.0]
    bits, bit, ch, out = 0, 0, 0, []
    even = True
    while len(out) < precision:
        if even:
            mid = (lon_i[0] + lon_i[1]) / 2
            if lon >= mid:
                ch = (ch << 1) | 1
                lon_i = (mid, lon_i[1])
            else:
                ch = (ch << 1) | 0
                lon_i = (lon_i[0], mid)
        else:
            mid = (lat_i[0] + lat_i[1]) / 2
            if lat >= mid:
                ch = (ch << 1) | 1
                lat_i = (mid, lat_i[1])
            else:
                ch = (ch << 1) | 0
                lat_i = (lat_i[0], mid)
        even = not even
        bit += 1
        if bit == 5:
            out.append(BASE32[ch])
            bit, ch = 0, 0
        bits += 1
        if bits > precision * 5 + 5:
            break
    return "".join(out)


def fuel_type_from_tags(tags, amenity):
    """Map OSM tags to a fuel_type string like 'petrol_diesel' or 'ev'."""
    if amenity == "charging":
        return "ev"
    fuels = []
    if tags.get("fuel:diesel") == "yes":
        fuels.append("diesel")
    petrol = (
        tags.get("fuel:petrol") == "yes"
        or tags.get("fuel:octane_91") == "yes"
        or tags.get("fuel:octane_95") == "yes"
        or tags.get("fuel:octane_98") == "yes"
    )
    if petrol:
        fuels.append("petrol")
    if tags.get("fuel:cng") == "yes":
        fuels.append("cng")
    if tags.get("fuel:lpg") == "yes":
        fuels.append("lpg")
    if tags.get("fuel:lng") == "yes":
        fuels.append("lng")
    return "_".join(fuels) if fuels else None


def query_overpass(south, west, north, east, retries=4):
    """Run one Overpass query for a bbox. Tries each endpoint in order,
    retries on 429/5xx with backoff."""
    ql = (
        "[out:json][timeout:180];"
        "("
        f'node["amenity"="fuel"]({south},{west},{north},{east});'
        f'way["amenity"="fuel"]({south},{west},{north},{east});'
        f'node["amenity"="charging"]({south},{west},{north},{east});'
        f'way["amenity"="charging"]({south},{west},{north},{east});'
        ");"
        "out center;"
    )
    last_err = None
    for url in OVERPASS_URLS:
        for attempt in range(retries):
            try:
                resp = requests.post(url, data={"data": ql}, timeout=240,
                   headers=OVERPASS_HEADERS)
            except requests.RequestException as e:
                last_err = e
                wait = 10 * (attempt + 1)
                print(f"  request failed ({e}); retrying in {wait}s", flush=True)
                time.sleep(wait)
                continue
            if resp.status_code in (429, 502, 503, 504):
                last_err = RuntimeError(f"HTTP {resp.status_code}")
                wait = 20 * (attempt + 1)
                print(
                    f"  {url} HTTP {resp.status_code}; backing off {wait}s "
                    f"(attempt {attempt + 1}/{retries})",
                    flush=True,
                )
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                # Endpoint rejecting the query (e.g. 406) — try the next one.
                print(f"  {url} HTTP {resp.status_code}; trying next endpoint",
                      flush=True)
                last_err = RuntimeError(f"HTTP {resp.status_code}")
                break
            return resp.json().get("elements", [])
    raise RuntimeError(
        f"All Overpass endpoints failed for bbox {south},{west},{north},{east}. "
        f"Last error: {last_err}"
    )


def element_to_point(el):
    """Convert an Overpass element to a FuelPoint dict, or None to skip."""
    tags = el.get("tags", {})
    amenity = tags.get("amenity")
    if amenity not in ("fuel", "charging"):
        return None
    if el["type"] == "node":
        lat, lon = el.get("lat"), el.get("lon")
    else:  # way — use its computed center
        center = el.get("center") or {}
        lat, lon = center.get("lat"), center.get("lon")
    if lat is None or lon is None:
        return None
    return {
        "osm_id": f"{el['type']}/{el['id']}",  # for dedupe; stripped later
        "name": tags.get("name"),
        "lat": round(float(lat), 6),
        "lon": round(float(lon), 6),
        "kind": amenity,
        "fuel_type": fuel_type_from_tags(tags, amenity),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="output",
                    help="output directory for .json.gz files")
    ap.add_argument("--bbox", default=None,
                    help="test mode: single bbox as south,west,north,east")
    ap.add_argument("--sleep", type=float, default=5.0,
                    help="seconds to sleep between Overpass queries")
    args = ap.parse_args()

    import os
    os.makedirs(args.out, exist_ok=True)

    if args.bbox:
        s, w, n, e = (float(x) for x in args.bbox.split(","))
        chunks = [(s, w, n, e)]
        print(f"Test mode: single bbox {args.bbox}", flush=True)
    else:
        chunks = INDIA_CHUNKS
        print(f"Fetching {len(chunks)} India chunks from Overpass...", flush=True)

    points = {}  # osm_id -> point (dedupe across chunk boundaries)
    for i, (s, w, n, e) in enumerate(chunks):
        print(f"[{i + 1}/{len(chunks)}] bbox {s},{w},{n},{e} ...", flush=True)
        elements = query_overpass(s, w, n, e)
        new = 0
        for el in elements:
            p = element_to_point(el)
            if p is None:
                continue
            if p["osm_id"] not in points:
                points[p["osm_id"]] = p
                new += 1
        print(f"  got {len(elements)} elements, {new} new POIs "
              f"(total {len(points)})", flush=True)
        if i < len(chunks) - 1:
            time.sleep(args.sleep)

    # Group by Geohash-4 cell.
    cells = {}
    for p in points.values():
        cell = geohash(p["lat"], p["lon"], 4)
        cells.setdefault(cell, []).append(p)

    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    written = 0
    total_points = 0
    for cell in sorted(cells):
        doc = {
            "cell": cell,
            "updated": updated,
            "points": [
                {k: v for k, v in p.items() if k != "osm_id"}
                for p in cells[cell]
            ],
        }
        path = os.path.join(args.out, f"{cell}.json.gz")
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
        written += 1
        total_points += len(cells[cell])

    print(f"Done: {total_points} POIs across {written} cells -> {args.out}/",
          flush=True)


if __name__ == "__main__":
    sys.exit(main())
