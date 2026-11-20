# What we are actually doing — explained simply

Read this start to finish. It assumes no satellite background.

---

## The big picture in one paragraph

Satellites take pictures of Earth. India is cloudy during the monsoon, so ordinary
photos (like Sentinel-2) go blind for months. **Radar** satellites (Sentinel-1, NISAR)
see through cloud because microwaves pass through it. We built tools that download this
free satellite data, turn the raw numbers into pictures and measurements, and show them
in a browser. The two things we measure are: (1) how land changes over time (a timelapse),
and (2) how much the ground is sinking (subsidence). Everything is free, local, and open-source.

---

## The data (where pictures come from)

Think of two "catalogs" you can search with code:

1. **Planetary Computer** — a free library of Sentinel satellite images. We ask it:
   "give me all images over this GPS point in this date range." It hands back signed URLs
   to small tiles of the image. No account needed.
2. **NASA ASF / Earthdata** — a free account (which you already have) that gives access
   to **NISAR**, a radar satellite with a longer wavelength that sees through cloud and
   even through vegetation. Its products are big HDF5 files (~6 GB), but we never download
   the whole thing — we read only the tiny window we need over the internet (range reads).

**Optical vs radar in one line:**
- Sentinel-2 = a camera. Beautiful colours, but clouds block it.
- NISAR / Sentinel-1 = a radar. Looks like black-and-white speckle, but works in any weather.

---

## The simple mental model of radar

Radar sends a microwave pulse and listens for the echo. Two things it measures:

1. **Backscatter strength (brightness)** — rough things (cities, rocks) echo strongly and
   look bright; smooth things (water) reflect away and look black. We call this HH or HV
   depending on the antenna orientation. We used this to make the Gujarat/Odisha timelapse.
2. **Phase (timing of the wave)** — the exact position within a wave cycle. Compare two
   images taken 12 days apart: if the ground moved even a few millimetres, the wave timing
   shifts. This is **interferometry**, and it is how we measure sinking ground.

---

## The projects, in order

### 1. The monsoon gap (the original discovery)
We counted images over a delta in Tamil Nadu and found: 44 optical images per monsoon
month, but only 3–4 usable because of cloud. This proved radar was necessary.

### 2. Pretty pictures (the galleries)
We made 30 MP and 120 MP images of Indian landmarks from single Sentinel-2 scenes —
no stitching, real pixels, chosen for good sun angle. This was about learning to handle
satellite data properly.

### 3. Timelapses (watch a place change)
Given a GPS coordinate, we pull every clear satellite image from 2015 to now and stack
them as frames. A browser player plays them like a video while the images are still
downloading. This was built for Gujarat and Odisha. The NISAR version uses radar, so it
works even in cloud.

### 4. NISAR playground (`nisar_play.py`)
This is where we learned to read the giant radar files without downloading them.
It can list available images, show preview thumbnails, and render real HH/HV radar
pictures over any coordinate.

### 5. Subsidence — the most advanced thing (the lesson + production tool)
This is measuring ground sinking, and it is the 11-step walkthrough you just saw.
The chain of reasoning:

- Two radar images 12 days apart → their phase difference is a **rainbow** (step 3).
- Unwrap the rainbow into a smooth **phase surface** (step 4).
- But the **ionosphere** adds a fake ramp that looks like sinking — subtract it (steps 5–6).
- **Coherence** tells us which pixels are trustworthy; throw away the rest (step 7).
- Convert phase to millimetres: **d = (wavelength / 4π) × phase** (step 8).
- Choose a stable reference, convert the viewing geometry to vertical, divide by 12 days
  → **mm per year** map (step 9).
- One pair is noisy; **stack many pairs** and take the median (step 10).
- Always report how much you **cannot** claim (step 11).

The production tool (`subsidence.py`) does all of this automatically for 8 pairs and
adds an uncertainty map.

---

## What each file is for (plain English)

| File | Plain purpose |
|---|---|
| `orientation.py` | Prove India's monsoon clouds break optical monitoring |
| `make_tiles.py` | Make big beautiful single-scene images of India |
| `timelapse.py` | Build a frame-per-clear-day timelapse for any coordinate |
| `serve.py` + `player.html` | The browser that plays those frames live |
| `nisar_play.py` | Search, preview and read NISAR radar data over any coordinate |
| `subsidence_learn.py` | The 11-step teaching demo (with pictures) |
| `subsidence.py` | The automatic, multi-pair ground-sinking mapper |
| `docs/WALKTHROUGH.html` (in outputs/) | **Open this in a browser** — all pictures embedded |

---

## The honest caveats we keep repeating

- Radar pictures are speckly and hard to read — they are measurements, not photos.
- NISAR only has data since late 2025 — about one year, not decades.
- We have **no ground truth** (no survey measurements), so our sinking numbers are
  **exploratory**, not validated science.
- The ionosphere and atmosphere can still fool us — that is why we stack pairs and show
  an uncertainty map.

---

## How to actually see the demo

Open this file in any browser (double-click it, or use File → Open):

```
/home/ashirbad/Documents/codes/sentinel_t1/outputs/WALKTHROUGH.html
```

It contains all 11 step-by-step pictures and explanations embedded inside it — no
server, no internet, no terminal needed.
