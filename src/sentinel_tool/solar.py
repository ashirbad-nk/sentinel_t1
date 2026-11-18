"""Solar elevation at a Sentinel-2 scene's acquisition time.

Planetary Computer's STAC does not expose view:sun_elevation, so we compute it
astronomically from the acquisition timestamp (UTC) and the tile-centre lat/lon.
"""
from __future__ import annotations

from datetime import timezone

from astral import Observer
from astral.sun import elevation as _elevation

try:
    from shapely.geometry import shape

    def tile_center(item):
        """Return (lat, lon) of the tile centroid."""
        c = shape(item.geometry).centroid
        return c.y, c.x

except ImportError:  # pragma: no cover
    def tile_center(item):
        b = item.bbox
        return (b[1] + b[3]) / 2, (b[0] + b[2]) / 2


def solar_elevation(item, lat: float | None = None, lon: float | None = None) -> float:
    """Sun elevation in degrees above the horizon at acquisition time."""
    if lat is None or lon is None:
        lat, lon = tile_center(item)
    dt = item.datetime
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return float(_elevation(Observer(latitude=lat, longitude=lon), dt))
