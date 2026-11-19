# Sentinel / NISAR Toolkit — Complete Knowledge Base

Compiled from the full OpenCode session that built this repository. The raw, sanitized
session transcript is in [`session_export.json`](session_export.json) (564 messages).

---

## 1. Origin and thesis

The project began with the question: *what useful, open-source tool can be built from
free Sentinel satellite data for the Indian market?*

**Core finding (verified with live data):** optical monitoring fails in India exactly
when it matters most. Over the Cauvery Delta in kharif 2025, ~44 Sentinel-2 scenes are
acquired per monsoon month but only **3–4 are usable** (<20% cloud) in August/September.
Radar (Sentinel-1, NISAR) is unaffected by cloud.

This established two product theses:
1. **Monsoon-resilient crop/field monitoring** using S1 + S2 fusion.
2. **All-weather aquaculture (shrimp pond) intelligence** — later AquaSentinel.

A brutal "Shark Tank" review later exposed that the aquaculture engine was a state
machine fed by synthetic data. The response was to make everything real: real NISAR
access, real InSAR, real validation, honest limitations.

---

## 2. Data sources and access

| Source | Access | What we used |
|---|---|---|
| **Planetary Computer STAC** | Free, no account, signed COGs | `sentinel-2-l2a`, `sentinel-1-grd`, `sentinel-1-rtc`, `esa-worldcover`, `copernicus-dem` |
| **NASA ASF / Earthdata** | Free Earthdata Login (EDL), `~/.netrc` | NISAR GCOV / GUNW HDF5 (L-band SAR) |
| **Copernicus Data Space** | Free account | Alternative Sentinel source (not used in the main pipeline) |
| **ISRO Bhoonidhi** | Free registration | Cartosat (0.28 m), LISS-4 (5.8 m), NISAR S-band (not integrated) |

### Key facts learned
- **Sentinel-2 tile:** 10,980 × 10,980 px at 10 m = **120.6 MP**; tiles are ~110 × 110 km.
- **Revisit:** ~156 distinct dates over Agra in 2025 (median 2-day gap, max 5 days) thanks to S2A/B/C.
- **Coverage:** entire India including Lakshadweep, Andaman, Kashmir, Arunachal (verified).
- **NISAR:** L-band (λ ≈ 24 cm), launched July 2025; public data from ~Oct 2025 (BETA),
  PROVISIONAL early 2026, VALIDATED mid-2026. Each GCOV granule is **~6 GB HDF5** — we
  range-read instead of downloading.
- **Sun elevation** is not in PC's STAC metadata — computed with `astral` from acquisition
  time and tile centre.

---

## 3. Scripts and what they do

| Script | Purpose |
|---|---|
| `orientation.py` | Monsoon cloud-gap audit + NDVI/false-colour preview (Stage 1 teaching) |
| `make_cloudless.py` | 10 regions, ~30 MP mosaics from minimal clear scenes |
| `make_tiles.py` | 10 full 120.6 MP tiles × 3 sun angles (single scene, no stitching) |
| `timelapse.py` | Native-10 m 1080×1920 multi-year timelapse builder; `--build-manifest`, `--fetch --shard`, `--encode`; cloud/sun filters; resumable |
| `serve.py` + `player.html` | Local HTTP viewer + live progress API for frame builds |
| `progress.py` | Terminal progress monitor |
| `clouds.py` | Derive per-date cloud lookup from a manifest |
| `nisar_play.py` | NISAR GCOV via ASF API: `search`, `browse`, `fetch`, `inspect`, `series`, `play` |
| `subsidence_learn.py` | 11-step guided lesson: GUNW phase → subsidence map, with visuals and `lesson.html` |
| `subsidence.py` | Production: ionosphere correction, detrending, multi-pair stacking, uncertainty |
| `src/sentinel_tool/` | Library: config, stac, solar, indices, mosaic, fulltile, aquaculture |

---

## 4. Timeline of the build

1. **Orientation** — proved the monsoon gap quantitatively; first real NDVI maps.
2. **121 MP gallery** — 10 iconic regions, full single-scene tiles, 3 sun-angle options.
   - Bug: SAS tokens expire after ~1 h → fixed with per-asset re-signing.
   - Bug: `pkill -f` matched its own shell → use `[s]erve.py` bracket pattern.
3. **Timelapse** — built for multiple coordinates:
   - Gujarat (24.65, 72.78) → Odisha/Ahmedabad (23.05, 72.54) → final (23.05, 72.54).
   - Filters evolved: all scenes → <40% cloud → <10% cloud + 40–60° sun.
   - Live web player built to watch frames arrive during download.
   - Bug: player couldn't show frames because cache-buster `?v=` wasn't stripped in path resolution → fixed.
   - Bug: ffmpeg silently dropped 220 frames → fixed with explicit concat durations + `setpts`.
4. **NISAR exploration** — Earthdata login from `~/Documents/Erthdata_login` → `~/.netrc`.
   - Real browse imagery over Gujarat; real HH/HV pixels via HTTPS range reads.
   - Frame-size inconsistency (600 vs 1200 px) caused by 20 m vs 10 m products → normalised.
   - Full 39-frame Odisha timelapse + MP4.
   - Subsidence lesson (11 steps) and production stacking tool.
5. **Knowledge export** — this document + sanitized session transcript.

---

## 5. Key gotchas (learned the hard way)

- **SAS tokens expire** (~1 h) — re-sign assets at read time, not at search time.
- **Planetary Computer marks collections `proprietary`** in STAC — a metadata quirk; the
  underlying Copernicus data is open.
- **NISAR is NOT on Planetary Computer** — use ASF/Earthdata; requires `~/.netrc`.
- **Direct NISAR S3 access only works inside AWS us-west-2** — use HTTPS range reads locally.
- **fsspec async client ignores proxy env** — pass `client_kwargs={"trust_env": True}`.
- **GCOV metadata scan takes ~48 s** over HTTPS range reads — unavoidable per granule.
- **QA_STATS.h5 is tiny** — pick the largest `.h5` for product size.
- **L-band ionosphere** creates smooth ramps that mimic deformation — must subtract
  `ionospherePhaseScreen`.
- **Detrending removes broad deformation too** — a caveat, not a bug.
- **A single 12-day pair is noisy** — stack many pairs; report spread as uncertainty.
- **Never claim validation without ground truth** — all deformation outputs are labelled exploratory.

---

## 6. Git / GitHub

- Remote: `https://github.com/ashirbad-nk/sentinel_t1`
- Credentials from `~/Documents/codes/github_Details/` — token used transiently, never stored in git config.
- `.gitignore` excludes `.venv/`, `frames*/`, `outputs/`, `data/`, logs, notebooks checkpoints.
- Commit history (last first):
  - `d336409` production subsidence tool
  - `6778b37` 11-step subsidence lesson
  - `55503f3` NISAR location-aware play + web player
  - `0808c85` NISAR range-read tool
  - `915c3dd` AquaSentinel engine (external contributor commit)
  - `8933adb` ffmpeg frame-preservation fix
  - `c415c65` initial source push

---

## 7. Honest status of each product

| Product | Status |
|---|---|
| Monsoon gap analysis | ✅ Verified, reproducible |
| 121 MP tile gallery | ✅ Verified, shipped |
| Native timelapse + player | ✅ Verified, shipped |
| NISAR browse / HH-HV play | ✅ Verified, shipped |
| NISAR Odisha timelapse | ✅ Verified, shipped |
| Subsidence lesson | ✅ Working, educational |
| Production subsidence | ⚠️ Exploratory — no external validation, noisy with few pairs |
| AquaSentinel | ⚠️ Logic only — needs real ingestion + ground truth to be a product |

---

## 8. Next steps (as identified)

1. Improve subsidence: more pairs, better reference areas, atmospheric correction,
   compare against published Delhi subsidence rates.
2. Add deformation (GUNW/GOFF) to the web player as a toggle.
3. Real S1/S2 ingestion for AquaSentinel with hand-labelled ground truth.
4. NISAR soil moisture (SME2) dashboard.
5. Publish as a proper FOSS package with CI and documentation.
