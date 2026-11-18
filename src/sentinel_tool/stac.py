"""Helpers for searching Planetary Computer's STAC catalog."""
from __future__ import annotations

import planetary_computer as pc
from pystac import ItemCollection
from pystac_client import Client

from .config import AOI_BBOX, SEASON_WINDOW, STAC_URL


def open_catalog(*, sign: bool = True) -> Client:
    """Open the Planetary Computer STAC catalog (with optional asset signing)."""
    modifier = pc.sign_inplace if sign else None
    return Client.open(STAC_URL, modifier=modifier)


def search_items(
    collection: str,
    bbox: tuple[float, float, float, float] = AOI_BBOX,
    datetime_: str = SEASON_WINDOW,
    max_items: int | None = None,
    limit: int = 100,
    sign: bool = True,
) -> ItemCollection:
    """Search a single collection over the AOI and time window.

    Returns a pystac ItemCollection (items are signed by default so their asset
    hrefs can be opened directly by rasterio).
    """
    catalog = open_catalog(sign=sign)
    search = catalog.search(
        collections=[collection],
        bbox=list(bbox),
        datetime=datetime_,
        limit=limit,
        max_items=max_items,
    )
    return search.item_collection()
