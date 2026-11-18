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

    def tile_center(item) -> tuple[float, float]:
        """Return (lat, lon) of the tile centroid."""
        if getattr(item, "geometry", None):
            c = shape(item.geometry).centroid
            return float(c.y), float(c.x)
        b = item.bbox
        return (b[1] + b[3]) / 2.0, (b[0] + b[2]) / 2.0

except ImportError:  # pragma: no cover
    def tile_center(item) -> tuple[float, float]:
        b = item.bbox
        return (b[1] + b[3]) / 2.0, (b[0] + b[2]) / 2.0


def solar_elevation(item, lat: float | None = None, lon: float | None = None) -> float:
    """Sun elevation in degrees above the horizon at acquisition time."""
    if lat is None or lon is None:
        lat, lon = tile_center(item)
    dt = item.datetime
    if dt is None:
        raise ValueError(f"STAC item {getattr(item, 'id', '<unknown>')} has no datetime")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return float(_elevation(Observer(latitude=lat, longitude=lon), dt))
