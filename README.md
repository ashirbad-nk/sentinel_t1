# sentinel-tool

An open-source toolkit for **monsoon-resilient crop & field monitoring** and **all-weather coastal aquaculture intelligence** over India, built entirely on **free, open satellite data** (Sentinel-1, Sentinel-2, and NISAR).

---

## The Two Core Applications

### 1. Monsoon-Resilient Crop & Field Monitoring (Agriculture)
Optical crop monitoring fails in India exactly when it matters most. A live audit over the Cauvery Delta (kharif 2025) found that although ~44 Sentinel-2 scenes are acquired per monsoon month, only **3–4 are usable** (<20% cloud) in August/September. Sentinel-1 and NISAR radar are unaffected by clouds and available throughout.

### 2. AquaSentinel: Coastal Aquaculture (Shrimp) Pond Intelligence
An all-weather satellite surveillance engine for **shrimp feed manufacturers, input dealers, and aquaculture lenders** in coastal Andhra Pradesh (Bhimavaram, Nellore) and Gujarat (Surat). Tracks pond water filling, Days of Culture (DOC), and flags **premature emergency disease drainage** within 72 hours through monsoon clouds — preventing feed credit defaults.

---

## Data

All data is free/open via the [Planetary Computer](https://planetarycomputer.microsoft.com/) STAC API and [NASA Earthdata Cloud / ASF DAAC](https://asf.alaska.edu/).

| Sensor | Band / Resolution | Role in Toolkit |
|---|---|---|
| **`sentinel-2-l2a`** | 10 m Optical (RGB, NIR, Red Edge) | Field greenness (NDVI), water index (NDWI), high-res galleries |
| **`sentinel-1-rtc` / `sentinel-1-grd`** | 10 m C-band SAR (All-weather) | Monsoon-proof crop backscatter and pond water/dry contrast |
| **`NISAR_L2_GCOV_V1`** | 10–20 m L-band SAR (All-weather) | Sub-canopy flood penetration, tall-crop biomass, InSAR subsidence |

---

## Layout

```
.
├── pyproject.toml                     # Editable package metadata (src/ layout)
├── requirements.txt                   # Pinned-floor Python dependencies
├── README.md
├── docs/
│   └── AQUACULTURE.md                 # Complete aquaculture physics, state machine & API doc
├── aquaculture.py                     # AquaSentinel CLI: pond audit & cluster benchmark
├── orientation.py                     # Stage 1: monsoon cloud-gap audit + NDVI preview
├── make_cloudless.py                  # 10 iconic Indian AOIs (~30 MP mosaics @ 10 m)
├── make_tiles.py                      # 10 full Sentinel-2 tiles (~121 MP × 3 sun angles)
├── timelapse.py                       # Native-10 m 1080×1920 multi-year timelapse builder
├── serve.py                           # Local HTTP viewer + live progress API (127.0.0.1)
├── player.html                        # Browser player for live timelapse builds
├── progress.py                        # Terminal progress monitor for timelapse builds
├── clouds.py                          # Derive clouds.json lookup from a timelapse manifest
├── src/sentinel_tool/
│   ├── __init__.py
│   ├── config.py                      # AOIs (Cauvery, Bhimavaram, Surat), thresholds, UTM helper
│   ├── aquaculture.py                 # Pond state machine, SAR water detection, risk audit
│   ├── stac.py                        # STAC catalog & search helpers
│   ├── indices.py                     # PB 04.00+ reflectance calibration, NDVI/NDWI, composites
│   ├── mosaic.py                      # Multi-scene cloudless AOI mosaicking
│   ├── fulltile.py                    # Low-RAM full-tile 120.6 MP COG loader
│   └── solar.py                       # Solar elevation from acquisition time + tile centre
├── notebooks/
│   ├── 01_orientation.ipynb           # Guided teaching notebook (monsoon cloud gap)
│   └── 02_aquaculture_audit.ipynb     # Guided aquaculture SAR backscatter & pond audit
├── tests/
│   ├── test_sentinel_tool.py          # Core calibration, mosaic & server unit tests
│   └── test_aquaculture.py            # Aquaculture water physics & state machine unit tests
└── outputs/                           # Generated maps, galleries, MP4s, audit CSV/JSON (gitignored)
```

---

## Quick Start

```bash
# Clone & install editable package
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e .
```

---

## AquaSentinel CLI Usage

```bash
# 1. Run full 30-pond portfolio audit benchmark for Bhimavaram cluster
python aquaculture.py --benchmark --count 30 --export-report outputs/bhimavaram_audit.json

# 2. Audit a specific pond by GPS coordinates
python aquaculture.py --lat 16.544 --lon 81.523 --pond-id AP-WG-101

# 3. Explore the interactive notebook
jupyter notebook notebooks/02_aquaculture_audit.ipynb
```

---

## Agriculture & Gallery Tools

```bash
# Metadata-only monsoon cloud audit (fast):
python orientation.py --skip-raster

# Full orientation run (audit + NDVI preview map):
python orientation.py

# Generate 30 MP cloudless regional mosaics:
python make_cloudless.py --regions 2

# Generate 120.6 MP native Sentinel-2 tiles across contrasting sun angles:
python make_tiles.py --targets 1

# Build multi-year 1080x1920 native timelapse:
python timelapse.py --tag gandhinagar --lat 23.0507771 --lon 72.5448828 --build-manifest
python timelapse.py --tag gandhinagar --fetch --shard 0 --nshards 2
python serve.py --tag gandhinagar --port 8767
```

---

## Testing

```bash
# Run all 25 unit tests:
python -m unittest discover -s tests -v
```

---

## Roadmap

1. ✅ **Stage 1 — Orientation:** Discover data, reproduce Cauvery Delta monsoon gap, initial NDVI maps.
2. ✅ **Full-Tile Gallery & Timelapse Player:** Native 120.6 MP tiles, live browser player, PB 04.00+ calibration.
3. ✅ **AquaSentinel Engine:** All-weather SAR pond water tracking, DOC calculator, premature drain alert, Bhimavaram benchmark.
4. **Stage 2 — Operational Cloud-Free Cube:** Aligned S1 + S2 + NISAR multi-sensor data cube primitive.
5. **Stage 3 — Vertical Deployment:** Commercial dealer pilot portal & WhatsApp alerts for Andhra Pradesh shrimp clusters.
