"""Configuration for the sentinel_tool pipeline.

All settings live here so experiments are reproducible and easy to tweak.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Areas of interest:
# bbox order = (west, south, east, north) in EPSG:4326
# ---------------------------------------------------------------------------
# 1. Cauvery Delta, Tamil Nadu (monsoon crop-monitoring baseline)
AOI_NAME = "Cauvery Delta, Tamil Nadu"
AOI_BBOX = (79.00, 10.80, 79.50, 11.20)

# 2. Bhimavaram / Akividu Cluster, West Godavari, Andhra Pradesh (Aquaculture Capital of India)
AQUA_AOI_NAME = "Bhimavaram Aquaculture Cluster, Andhra Pradesh"
AQUA_AOI_BBOX = (81.45, 16.48, 81.65, 16.65)

# 3. Surat / Navsari Coastal Aquaculture Belt, Gujarat
GUJARAT_AQUA_AOI_NAME = "Surat Coastal Aquaculture Belt, Gujarat"
GUJARAT_AQUA_AOI_BBOX = (72.70, 20.95, 72.90, 21.15)

# ---------------------------------------------------------------------------
# Seasons & Windows
# ---------------------------------------------------------------------------
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

# Sentinel-2 L2A radiometric scaling & Scene Classification Layer (SCL) mask.
# Starting with ESA Processing Baseline 04.00 (2022-01-25), L2A surface
# reflectance bands include a +1000 DN offset (BOA_ADD_OFFSET = -1000).
S2_SCALE = 10_000.0
S2_PB04_DN_OFFSET = -1000.0
# SCL classes to mask:
#   0 = No data, 1 = Saturated/defective, 3 = Cloud shadow,
#   8 = Cloud medium prob, 9 = Cloud high prob, 10 = Thin cirrus
S2_CLOUD_SCL = (0, 1, 3, 8, 9, 10)

# ---------------------------------------------------------------------------
# Aquaculture Radar Physics Thresholds & Life-cycle Parameters
# ---------------------------------------------------------------------------
# Water detection in SAR backscatter (linear power converted to decibels dB):
# Smooth water exhibits specular reflection away from radar:
S1_WATER_MAX_DB_VV = -19.5       # Sentinel-1 C-band VV threshold for open water
S1_WATER_MAX_DB_VH = -25.0       # Sentinel-1 C-band VH threshold for open water
NISAR_WATER_MAX_DB_HH = -22.0    # NISAR L-band HH threshold for open water
NISAR_WATER_MAX_DB_HV = -27.0    # NISAR L-band HV threshold for open water

# Dry / exposed earth backscatter threshold:
AQUA_DRY_BED_MIN_DB = -15.0

# Minimum jump in dB (within <= 7 days) indicating sudden pond drainage / harvest:
AQUA_DRAIN_STEP_THRESHOLD_DB = 6.0

# Shrimp farming operational parameters (Litopenaeus vannamei):
# Normal harvest occurs between 75-120 Days of Culture (DOC).
# Drainage before MIN_HARVEST_DOC strongly indicates disease emergency harvest.
AQUA_MIN_HARVEST_DOC = 65        # Days of Culture below which drainage triggers CRITICAL alert
AQUA_MAX_NORMAL_DOC = 120        # Days of Culture above which pond is flagged OVERDUE
AQUA_PREPARATION_DAYS = 15       # Typical sun-drying / pond prep period (days)


def utm_epsg(lon: float, lat: float) -> int:
    """Return the WGS84 UTM EPSG code (326xx north / 327xx south) for (lon, lat)."""
    zone = min(max(int((lon + 180.0) // 6) + 1, 1), 60)
    return (32600 if lat >= 0 else 32700) + zone
