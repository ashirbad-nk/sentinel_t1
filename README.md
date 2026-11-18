# sentinel-tool

An open-source toolkit for **monsoon-resilient crop & field monitoring** over India,
built entirely on **free, open satellite data**.

## The thesis

Optical crop monitoring fails in India exactly when it matters most. A live audit over
the Cauvery Delta (kharif 2025) found that although ~44 Sentinel-2 scenes are acquired
per monsoon month, only **3–4 are usable** (<20% cloud) in August/September. Sentinel-1
radar is unaffected by cloud and is available throughout.

**Goal:** fuse Sentinel-1 SAR + Sentinel-2 optical (later NISAR) into a cloud-free,
analysis-ready time series — a reusable primitive for crop health, flood mapping, and
water monitoring — and ship it as a Python library + CLI + light web dashboard.

## Data

All data is free/open via the [Planetary Computer](https://planetarycomputer.microsoft.com/)
STAC API (no account needed for the core collections) and Copernicus.

| Use | Collection |
|---|---|
| Optical, 10 m, surface reflectance | `sentinel-2-l2a` |
| All-weather radar (terrain-corrected) | `sentinel-1-rtc`, `sentinel-1-grd` |
| Land cover / DEM | `esa-worldcover`, `copernicus-dem-30m` |
| Future: air quality | `sentinel-5p-l2-netcdf` |
| Future: L/S-band radar (via ASF/Bhoonidhi) | NISAR |

> Note: Planetary Computer marks some collections as `proprietary` in STAC metadata —
> this is a known quirk; the underlying Copernicus Sentinel data is free/open. Verify
> per-collection before redistribution.

## Layout

```
.
├── orientation.py              # Stage 1: data audit + real raster preview (RUN THIS)
├── requirements.txt
├── src/sentinel_tool/
│   ├── config.py               # AOI, seasons, collection IDs
│   ├── stac.py                 # STAC search helpers (signed assets)
│   └── indices.py              # NDVI/NDWI + cloud-masked mosaic helper
├── notebooks/
│   └── 01_orientation.ipynb    # Guided, teaching notebook
├── outputs/                    # Generated maps + audit CSV
└── data/                       # Cached rasters
```

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install h5py          # netCDF write backend

# Metadata-only audit (fast):
.venv/bin/python orientation.py --skip-raster

# Full run: audit + load a real scene + NDVI/false-colour maps:
.venv/bin/python orientation.py
```

Outputs land in `outputs/`:
- `cloud_gap_audit.csv` — monthly scene counts
- `cloud_gap.png` — optical-vs-radar availability chart
- `s2_preview.png` — true colour, false colour, NDVI of a real scene
- `ndvi_preview.nc` — computed NDVI raster

Open `notebooks/01_orientation.ipynb` for the guided explanation.

## Stage 1 findings (Cauvery Delta, kharif 2025)

| Month | S2 acquired | S2 usable (<20% cloud) | S1 (all-weather) |
|---|---|---|---|
| 2025-06 | 44 | 17 | 6 |
| 2025-07 | 48 | 14 | 4 |
| 2025-08 | 44 | 4 | 4 |
| 2025-09 | 44 | 3 | 2 |

## Full-tile 121 MP gallery

`make_tiles.py` produces **complete, single-scene Sentinel-2 tiles** — no clipping,
no stitching:

- Each tile is **10,980 × 10,980 px = 120.6 MP** at native 10 m (one scene).
- 10 targets: Agra, Rann of Kutch, Mumbai, Delhi, Varanasi, Jaipur, Jaisalmer,
  Hampi, Sundarbans, Darjeeling.
- For each target, **3 cloud-free options** with contrasting solar elevations
  (computed with `astral` from acquisition time + tile centre, since PC's STAC
  omits `view:sun_elevation`), so illumination/season can be compared.
- JPEG only (quality 90). Output: `outputs/tiles/<target>/option*.jpg` +
  `options.jpg` contact sheet + `outputs/tiles/master_overview.jpg`.

```bash
.venv/bin/python make_tiles.py --targets 1   # test one target
.venv/bin/python make_tiles.py               # all 10 (~2.5 h, 30 tiles)
.venv/bin/python make_tiles.py --start 2 --end 4 --skip-existing --no-master
```

Module: `src/sentinel_tool/fulltile.py` reads the native band COGs directly with
rasterio (one band at a time, ~1.5 GB peak RAM); `src/sentinel_tool/solar.py`
computes solar elevation.

## Roadmap

1. ✅ Stage 1 — orientation: discover data, reproduce the monsoon gap, first maps.
2. ✅ Full-tile gallery — 10 targets × 3 sun-angle options, single-scene 120.6 MP JPEGs.
3. Stage 2 — aligned S1 + S2 cube with cloud masking (the fusion primitive).
4. Stage 3 — crop-health vertical app (CLI + map UI) for one Indian district.
5. Stage 4 — India localization (kharif/rabi calendars), validation, NISAR.
6. Stage 5 — open-source release: docs, license, examples, community.
