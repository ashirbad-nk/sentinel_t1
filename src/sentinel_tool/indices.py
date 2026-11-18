"""Spectral indices, radiometric calibration, and mosaic helpers for Sentinel-2.

Sentinel-2 L2A stores surface reflectance scaled by 10,000. Starting with ESA
Processing Baseline 04.00 (2022-01-25) — and reprocessed Collection-1 scenes
with baseline >= 04.00 — bands also include a +1000 DN offset:

    reflectance = (DN - 1000) / 10_000   (PB >= 04.00, for DN > 0)
    reflectance = DN / 10_000            (PB <  04.00, for DN > 0)

DN == 0 is always nodata and must be masked before applying the offset.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import numpy as np

from .config import S2_CLOUD_SCL, S2_PB04_DN_OFFSET, S2_SCALE, utm_epsg

if TYPE_CHECKING:
    import xarray as xr

SCALE = S2_SCALE
PB04_LAUNCH_UTC = datetime(2022, 1, 25, tzinfo=timezone.utc)


def s2_dn_offset(item=None, *, baseline: str | float | None = None) -> float:
    """Return the Sentinel-2 L2A additive DN offset (-1000.0 for PB >= 04.00, else 0.0)."""
    pb = baseline
    if pb is None and item is not None:
        props = getattr(item, "properties", None) or {}
        pb = props.get("s2:processing_baseline")

    if pb is not None:
        try:
            return S2_PB04_DN_OFFSET if float(pb) >= 4.0 else 0.0
        except (TypeError, ValueError):
            pass

    if item is not None:
        dt = getattr(item, "datetime", None)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= PB04_LAUNCH_UTC:
                return S2_PB04_DN_OFFSET

    return 0.0


def dn_to_reflectance(arr, item=None, *, baseline: str | float | None = None):
    """Convert Sentinel-2 L2A DN array/DataArray to float32 surface reflectance.

    Masks DN <= 0 as NaN before applying the PB 04.00+ additive offset.
    Works on both numpy arrays and xarray DataArrays.
    """
    offset = s2_dn_offset(item, baseline=baseline)
    if hasattr(arr, "where"):  # xarray.DataArray
        arr_f = arr.astype("float32")
        return ((arr_f + offset) / SCALE).where(arr_f > 0)

    arr_f = np.asarray(arr, dtype="float32")
    return np.where(arr_f > 0, (arr_f + offset) / SCALE, np.nan).astype("float32")


def reflectance(ds, band: str, item=None):
    """Return a band as float32 reflectance with nodata masked and PB offset applied."""
    return dn_to_reflectance(ds[band], item=item)


def _norm_diff(a, b):
    """Compute (a - b) / (a + b), returning NaN where (a + b) <= 1e-6."""
    denom = a + b
    if hasattr(denom, "where"):  # xarray.DataArray
        return (a - b) / denom.where(denom > 1e-6)
    safe_denom = np.where(denom > 1e-6, denom, np.nan)
    return (a - b) / safe_denom


def ndvi(red, nir):
    """Normalized Difference Vegetation Index — greenness / crop vigor."""
    return _norm_diff(nir, red)


def ndwi(green, nir):
    """Normalized Difference Water Index — open water / moisture."""
    return _norm_diff(green, nir)


def median_composite(
    items,
    bands: list[str],
    *,
    bbox: tuple[float, float, float, float] | None = None,
    resolution: int = 20,
    epsg: int | None = None,
) -> "xr.DataArray":
    """Build a cloud-masked, PB-calibrated median reflectance mosaic of items.

    Uses stackstac to lazily stack the items into a (time, band, y, x) cube,
    masks clouds/shadows/cirrus/nodata via SCL, converts DN to surface
    reflectance (accounting for PB 04.00+ offset per item), and reduces over
    time with the median.
    """
    import stackstac
    import xarray as xr

    items = list(items)
    if not items:
        raise ValueError("items must not be empty")

    if epsg is None:
        ref_bbox = bbox if bbox is not None else items[0].bbox
        epsg = utm_epsg((ref_bbox[0] + ref_bbox[2]) / 2.0, (ref_bbox[1] + ref_bbox[3]) / 2.0)

    stack_kwargs = {
        "assets": list(bands) + ["SCL"],
        "resolution": resolution,
        "epsg": epsg,
        "chunksize": 2048,
    }
    if bbox is not None:
        stack_kwargs["bounds_latlon"] = bbox

    stack = stackstac.stack(items, **stack_kwargs)

    scl = stack.sel(band="SCL")
    cloud_mask = scl.isin(list(S2_CLOUD_SCL))

    offsets = xr.DataArray(
        np.array([s2_dn_offset(it) for it in items], dtype="float32"),
        dims=("time",),
        coords={"time": stack.coords["time"]},
    )
    raw = stack.sel(band=bands).astype("float32")
    refl = ((raw + offsets) / SCALE).where((raw > 0) & (~cloud_mask))

    return refl.median(dim="time", skipna=True)
