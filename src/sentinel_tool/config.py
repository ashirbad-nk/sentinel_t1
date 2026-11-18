"""Configuration for the sentinel_tool orientation pipeline.

All settings live here so experiments are reproducible and easy to tweak.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Area of interest: Cauvery Delta, Tamil Nadu, India
# bbox order = (west, south, east, north) in EPSG:4326
# ---------------------------------------------------------------------------
AOI_NAME = "Cauvery Delta, Tamil Nadu"
AOI_BBOX = (79.00, 10.80, 79.50, 11.20)

# Kharif (monsoon) season 2025 — the window optical data struggles most.
SEASON_START = "2025-06-01"
SEASON_END = "2025-09-30"
SEASON_WINDOW = f"{SEASON_START}T00:00:00Z/{SEASON_END}T23:59:59Z"

# A short window used for the first raster preview (a clear-ish period).
PREVIEW_START = "2025-08-01"
PREVIEW_END = "2025-08-31"
PREVIEW_WINDOW = f"{PREVIEW_START}T00:00:00Z/{PREVIEW_END}T23:59:59Z"

# Planetary Computer STAC API (free, no account needed for the core data).
STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"

# Collections
S2_COLLECTION = "sentinel-2-l2a"      # optical, 10 m, surface reflectance
S1_GRD_COLLECTION = "sentinel-1-grd"  # raw-ish SAR ground range detected
S1_RTC_COLLECTION = "sentinel-1-rtc"  # terrain-corrected SAR (CC-BY-4.0)

# Cloudiness threshold used in the "usable observation" audit (percent).
CLOUD_THRESHOLD = 20.0
