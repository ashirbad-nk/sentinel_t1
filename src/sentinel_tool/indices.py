"""Spectral indices and mosaic helpers for Sentinel-2 surface reflectance.

Sentinel-2 L2A stores reflectance scaled by 10,000, so we divide by that
before computing indices.
"""
from __future__ import annotations

import numpy as np
import xarray as xr

SCALE = 10_000.0


def reflectance(ds: xr.DataArray | xr.Dataset, band: str) -> xr.DataArray:
    """Return a band as float reflectance in [0, 1]."""
    return ds[band].astype("float32") / SCALE


def ndvi(red: xr.DataArray, nir: xr.DataArray) -> xr.DataArray:
    """Normalized Difference Vegetation Index — greenness / crop vigor."""
    return (nir - red) / (nir + red + 1e-6)


def ndwi(green: xr.DataArray, nir: xr.DataArray) -> xr.DataArray:
    """Normalized Difference Water Index — open water / moisture."""
    return (green - nir) / (green + nir + 1e-6)


def median_composite(items, bands: list[str]) -> xr.DataArray:
    """Build a cloud-masked median mosaic of the given items.

    Uses stackstac to lazily stack the items into a (time, band, y, x) cube,
    masks clouds via the SCL scene-classification band, then reduces over time
    with the median (robust to leftover cloud/shadow).
    """
    import stackstac

    stack = stackstac.stack(
        items,
        assets=bands + ["SCL"],
        resolution=20,
        epsg=32644,  # UTM zone 44N covers ~79 deg E (Cauvery Delta)
        chunksize=2048,
    )

    # Cloud / shadow classes in SCL: 3 (cloud shadow), 8 (cloud), 9 (cirrus)
    scl = stack.sel(band="SCL")
    cloud_mask = scl.isin([3, 8, 9])
    refl = stack.sel(band=bands).where(~cloud_mask)

    return refl.median(dim="time", skipna=True)
