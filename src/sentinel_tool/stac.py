"""Helpers for searching Planetary Computer's STAC catalog."""
from __future__ import annotations

from typing import Any

import planetary_computer as pc
from pystac_client import Client
from pystac import ItemCollection

from .config import STAC_URL, AOI_BBOX, SEASON_WINDOW


def open_catalog() -> Client:
    """Open the Planetary Computer STAC catalog with asset signing enabled."""
    return Client.open(STAC_URL, modifier=pc.sign_inplace)


def search_items(
    collection: str,
    bbox: tuple[float, float, float, float] = AOI_BBOX,
    datetime_: str = SEASON_WINDOW,
    max_items: int | None = None,
    limit: int = 100,
) -> ItemCollection:
    """Search a single collection over the AOI and time window.

    Returns a pystac ItemCollection (items are already signed by the catalog
    modifier, so their asset hrefs can be opened directly by rasterio).
    """
    catalog = open_catalog()
    search = catalog.search(
        collections=[collection],
        bbox=list(bbox),
        datetime=datetime_,
        limit=limit,
        max_items=max_items,
    )
    return search.item_collection()
