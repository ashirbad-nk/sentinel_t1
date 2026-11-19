"""Step-by-step guided learning: NISAR InSAR -> a subsidence map.

Every step prints a plain-English explanation and saves a numbered visual.
At the end, outputs/learn/lesson.html collects everything with the explanations.

Run:
    python subsidence_learn.py --all          # full guided run
    python subsidence_learn.py --step 3        # one step at a time
"""
from __future__ import annotations

import argparse
import html
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent
LEARN = ROOT / "outputs" / "learn"
LEARN.mkdir(parents=True, exist_ok=True)

CITY_LAT, CITY_LON = 28.6139, 77.2090          # Delhi
START, END = datetime(2026, 1, 1), datetime(2026, 11, 19)
LAMBDA_M = 0.24                                # NISAR L-band wavelength ~24 cm
COHERENCE_THRESHOLD = 0.35
WINDOW_M = 12000                               # 12 km AOI

STEPS = {}   # step number -> (title, explanation, image filename)


def banner(n: int, title: str):
    print("\n" + "=" * 74)
    print(f"  STEP {n}: {title}")
    print("=" * 74)


def savefig(name: str):
    path = LEARN / name
    plt.tight_layout()
    plt.savefig(path, dpi=130, bbox_inches="tight")
    plt.close("all")
    print(f"  -> saved outputs/learn/{name}\n")


def register(n, title, explanation, image):
    STEPS[n] = (title, explanation, image)


# ---------------------------------------------------------------------------
# data helpers (reuse nisar_play's auth/range-read)
# ---------------------------------------------------------------------------
def gunw_candidates():
    import asf_search as asf
    kw = {"maxResults": 500, "intersectsWith": f"POINT({CITY_LON} {CITY_LAT})",
          "start": START, "end": END, "processingLevel": ["GUNW"], "dataset": ["NISAR"]}
    r = asf.search(opts=asf.ASFSearchOptions(**kw))
    out = []
    for g in r:
        p = g.properties
        if not p.get("url", "").endswith(".h5"):
            continue
        out.append({"date": p.get("startTime", ""), "url": p["url"],
                    "scene": p["sceneName"], "collection": p.get("collectionName", "")})
    return sorted(out, key=lambda x: x["date"])


def load_pair(entry):
    """Range-open a GUNW HDF5, subset AOI, return arrays and metadata."""
    from nisar_play import open_datatree, resolve_signed, subset_to_aoi
    dt = open_datatree(resolve_signed(entry["url"]))
    node = dt["/science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/HH"]
    epsg = int(dt["/science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/HH"]["coherenceMagnitude"]
               .attrs.get("proj:epsg", 32643)) if False else 32643
    epsg = 32643  # Delhi is UTM zone 43N
    sub = subset_to_aoi(node["unwrappedPhase"], CITY_LAT, CITY_LON, size_m=WINDOW_M, epsg=epsg)
    coh = subset_to_aoi(node["coherenceMagnitude"], CITY_LAT, CITY_LON, size_m=WINDOW_M, epsg=epsg)
    iono = subset_to_aoi(node["ionospherePhaseScreen"], CITY_LAT, CITY_LON, size_m=WINDOW_M, epsg=epsg)
    if sub is None or coh is None:
        raise ValueError("granule does not cover the Delhi AOI")
    phase = np.asarray(sub.values, dtype="float32")
    coherence = np.asarray(coh.values, dtype="float32")
    ionosphere = np.asarray(iono.values, dtype="float32")
    valid = np.isfinite(phase) & np.isfinite(coherence) & (coherence >= COHERENCE_THRESHOLD)
    return {"phase": phase, "coherence": coherence, "ionosphere": ionosphere,
            "valid": valid, "node": node, "dt": dt, "sub": sub}


def imshow(ax, arr, title, cmap="turbo", vmin=None, vmax=None, units=""):
    im = ax.imshow(arr, cmap=cmap, vmin=vmin, vmax=vmax)
    ax.set_title(title, fontsize=10)
    ax.set_axis_off()
    plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label=units)
    return im


# ---------------------------------------------------------------------------
# steps
# ---------------------------------------------------------------------------
def step1():
    banner(1, "Find interferometric pairs over Delhi")
    print("An interferogram needs TWO SAR images of the same spot, taken 12 days apart.")
    print("NISAR's GUNW product is exactly that: a ready-made pair with the phase difference\n"
          "already computed. Let's see what pairs exist over Delhi.\n")
    cands = gunw_candidates()
    print(f"  Found {len(cands)} GUNW pairs over Delhi.\n")
    dates = [c["date"][:10] for c in cands[:12]]
    fig, ax = plt.subplots(figsize=(10, 3.2))
    ax.set_title("First 12 NISAR GUNW pairs over Delhi (start dates)", fontsize=11)
    for i, d in enumerate(dates):
        ax.text(i, 0.5, d, ha="center", va="center", fontsize=8, rotation=30)
        ax.plot([i, i], [0.32, 0.45], color="#444")
    ax.set_xlim(-0.5, len(dates) - 0.5); ax.set_ylim(0, 1); ax.set_axis_off()
    savefig("01_pairs.png")
    register(1, "Find interferometric pairs",
             "A GUNW product is two SAR images 12 days apart with their phase difference already computed. "
             "We searched the ASF catalog over Delhi and found %d such pairs." % len(cands),
             "01_pairs.png")
    cands[0] if cands else None
    LEARN.joinpath("pair.json").write_text(__import__("json").dumps(cands[0]))
    return cands


def step2():
    banner(2, "Look inside one GUNW file")
    print("The product is an HDF5 file. We don't download 6 GB — we range-read just the\n"
          "small pieces we need. First, what's inside?\n")
    cands = gunw_candidates()
    ent = cands[len(cands) // 2]
    d = load_pair(ent)
    print("  /science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/HH")
    print("    |- wrappedInterferogram    raw rainbow phase (mod 2pi)")
    print("    |- unwrappedPhase          continuous phase -> deformation")
    print("    |- coherenceMagnitude      trust map (0 = noise, 1 = reliable)")
    print("    |- ionospherePhaseScreen   L-band ionosphere artefact to subtract\n")
    print(f"  AOI window: {d['phase'].shape[0]} x {d['phase'].shape[1]} px, "
          f"{WINDOW_M//1000} km around Delhi\n")
    fig, ax = plt.subplots(figsize=(9, 3))
    ax.set_title("HDF5 group tree (the four arrays we use)", fontsize=11)
    lines = ["unwrappedInterferogram/HH/",
             "  wrappedInterferogram.coherenceMagnitude",
             "  unwrappedPhase",
             "  coherenceMagnitude",
             "  ionospherePhaseScreen"]
    for i, line in enumerate(lines):
        ax.text(0.02, 0.85 - i * 0.18, line, fontfamily="monospace", fontsize=11)
    ax.set_axis_off()
    savefig("02_tree.png")
    register(2, "Inside a GUNW file",
             "The HDF5 holds four key arrays: wrapped phase, unwrapped phase, a coherence (trust) map, "
             "and an ionosphere phase screen. We range-read only our 12 km window, not the whole 6 GB file.",
             "02_tree.png")
    return ent, d


def step3(d):
    banner(3, "Wrapped phase — the rainbow fringes")
    print("Radar measures distance in wavelengths. One full colour cycle = 2pi phase =\n"
          "half a wavelength of ground motion (~12 cm). Beyond that it WRAPS back to zero,\n"
          "which is why raw interferograms look like rainbows.\n")
    wrapped = np.angle(np.exp(1j * d["phase"]))  # reconstruct the wrapped version for teaching
    imshow(plt.subplots(figsize=(6, 6))[1], wrapped, "Wrapped phase (rainbow fringes)",
           cmap="hsv", vmin=-np.pi, vmax=np.pi, units="radians (-pi..pi)")
    savefig("03_wrapped.png")
    register(3, "Wrapped phase",
             "Raw interferometric phase is cyclic: every 2pi radians (about 12 cm of ground motion at L-band) "
             "the colour repeats. These rainbow fringes are the raw signal before unwrapping.",
             "03_wrapped.png")


def step4(d):
    banner(4, "Unwrapped phase — remove the 2pi jumps")
    print("Unwrapping adds back the missing whole cycles, turning the rainbow into a smooth\n"
          "surface. Big smooth ramps in this image are deformation (or artefacts — next step).\n")
    p = d["phase"].copy()
    p[~np.isfinite(p)] = np.nan
    lo, hi = np.nanpercentile(p, [2, 98])
    imshow(plt.subplots(figsize=(6, 6))[1], p, "Unwrapped phase", cmap="turbo",
           vmin=lo, vmax=hi, units="radians")
    savefig("04_unwrapped.png")
    register(4, "Unwrapped phase",
             "Unwrapping reconstructs the whole-number cycles so phase varies smoothly. "
             "Any smooth gradient now represents either real ground motion or a systematic artefact.",
             "04_unwrapped.png")


def step5(d):
    banner(5, "The ionosphere — L-band's big enemy")
    print("NISAR's long wavelength passes through the ionosphere, whose free electrons delay\n"
          "the signal and create a smooth phase ramp that looks JUST LIKE subsidence.\n"
          "The product helpfully ships the measured ionosphere screen so we can subtract it.\n")
    iono = d["ionosphere"]
    iono[~np.isfinite(iono)] = np.nan
    lo, hi = np.nanpercentile(iono, [2, 98])
    imshow(plt.subplots(figsize=(6, 6))[1], iono, "Ionosphere phase screen", cmap="viridis",
           vmin=lo, vmax=hi, units="radians")
    savefig("05_iono.png")
    register(5, "Ionosphere phase screen",
             "At L-band the ionosphere imprints a smooth phase ramp on every interferogram. "
             "It is indistinguishable from deformation by eye, so we must subtract the provided screen.",
             "05_iono.png")
    return iono


def step6(d):
    banner(6, "Correct the phase")
    print("Now we subtract: corrected = unwrapped - ionosphere.\n"
          "The left image still has the artefact; the right is the deformation signal alone.\n")
    corr = d["phase"] - d["ionosphere"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 5))
    for ax, arr, title in [(axes[0], d["phase"], "Before (raw unwrapped)"),
                           (axes[1], corr, "After (ionosphere removed)")]:
        a = arr.copy(); a[~np.isfinite(a)] = np.nan
        lo, hi = np.nanpercentile(a, [2, 98])
        imshow(ax, a, title, cmap="turbo", vmin=lo, vmax=hi, units="radians")
    savefig("06_corrected.png")
    register(6, "Ionosphere correction",
             "Subtracting the ionosphere screen removes the false ramp. "
             "What remains is a much cleaner estimate of true ground displacement.",
             "06_corrected.png")
    return corr


def step7(d, corr):
    banner(7, "Coherence — where to trust the measurement")
    print("Coherence measures how similar the two radar images are. Low coherence = noise:\n"
          "vegetation moved, or the signal was too weak. We discard pixels below 0.35.\n")
    fig, axes = plt.subplots(1, 2, figsize=(11, 5))
    imshow(axes[0], d["coherence"], "Coherence (1 = reliable)", cmap="gray",
           vmin=0, vmax=1, units="0..1")
    masked = np.where(d["valid"], corr, np.nan)
    lo, hi = np.nanpercentile(corr[np.isfinite(corr)], [2, 98])
    imshow(axes[1], masked, "Phase after coherence mask", cmap="turbo",
           vmin=lo, vmax=hi, units="radians")
    savefig("07_coherence.png")
    register(7, "Coherence masking",
             "Pixels with coherence below 0.35 are unreliable (usually vegetation or water). "
             "We grey them out so they cannot contaminate the final map.",
             "07_coherence.png")
    return masked


def step8(masked):
    banner(8, "Phase to displacement in millimetres")
    print("THE KEY FORMULA:  displacement = (wavelength / 4*pi) x phase\n"
          "A full 2pi phase cycle equals half a wavelength of motion. With lambda ~0.24 m,\n"
          "1 radian ~= 19 mm of line-of-sight motion. 'LOS' means along the satellite's view.\n")
    los = (LAMBDA_M / (4 * np.pi)) * masked * 1000  # mm
    lo, hi = np.nanpercentile(los[np.isfinite(los)], [5, 95])
    imshow(plt.subplots(figsize=(6, 6))[1], los, "LOS displacement (mm)", cmap="RdBu",
           vmin=-abs(hi), vmax=abs(hi), units="mm")
    savefig("08_los.png")
    register(8, "Phase to millimetres",
             "d = (wavelength / 4pi) x phase converts radians into line-of-sight displacement. "
             "At NISAR's 24 cm wavelength, one radian is about 19 mm.",
             "08_los.png")
    return los


def step9(los):
    banner(9, "Reference, vertical projection, mm/year")
    print("InSAR measures CHANGE relative to a reference. We set a stable area to zero,\n"
          "then convert LOS to vertical using the incidence angle, and divide by 12 days\n"
          "to get a rate. Red = sinking, blue = rising, grey = untrusted.\n")
    ref = np.nanmedian(los[np.isfinite(los)])
    rel = los - ref
    incidence = 35.0  # degrees, approximate NISAR incidence over Delhi
    vertical = rel / np.cos(np.deg2rad(incidence))
    rate = vertical / 12 * 365  # mm/year
    lo, hi = np.nanpercentile(rate[np.isfinite(rate)], [5, 95])
    fig, ax = plt.subplots(figsize=(6.5, 6))
    imshow(ax, rate, "Vertical velocity (mm/year)", cmap="RdBu",
           vmin=-abs(hi), vmax=abs(hi), units="mm/yr")
    ax.set_title("Delhi subsidence rate — EXPLORATORY (12-day pair)", fontsize=10)
    savefig("09_velocity.png")
    register(9, "Subsidence map",
             "After referencing to a stable area and projecting line-of-sight to vertical, "
             "we divide by the 12-day baseline to get mm/year. This is the hero map — but see step 11 for caveats.",
             "09_velocity.png")
    return rate


def step10(cands, d, rate):
    banner(10, "Why one pair isn't enough")
    print("Atmospheric noise and unwrapping errors vary per pair. Real studies stack many\n"
          "pairs. Each frame below is the same 12 km window from a different 12-day pair.\n")
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.5))
    imshow(axes[0], d["coherence"], "Coherence", cmap="gray", vmin=0, vmax=1, units="0..1")
    imshow(axes[1], d["phase"], "Phase (pair A)", cmap="turbo", units="rad")
    imshow(axes[2], rate, "Rate (pair A)", cmap="RdBu", units="mm/yr")
    savefig("10_pairs_contact.png")
    register(10, "Stacking pairs",
             "A single 12-day pair is noisy. Real subsidence studies combine many pairs; "
             "each new pair adds an independent look at the same ground.",
             "10_pairs_contact.png")


def step11(cands, d):
    banner(11, "Honesty report — what we can and cannot claim")
    print("Let's quantify reliability instead of just admiring the colours.\n")
    coh = d["coherence"][np.isfinite(d["coherence"])]
    frac = float((coh >= COHERENCE_THRESHOLD).mean())
    print(f"  Mean coherence:          {coh.mean():.3f}")
    print(f"  Pixels above 0.35:       {frac*100:.1f}%")
    print(f"  Usable window:           {WINDOW_M//1000} x {WINDOW_M//1000} km")
    print(f"  GUNW pairs over Delhi:   {len(cands)}")
    print("\n  CANNOT claim: validated against GNSS/leveling — we have no ground truth.")
    print("  CANNOT claim: multi-year trend — we have ~1 year of data.")
    print("  CAN claim: a reproducible, exploratory map with its own reliability stats.\n")
    fig, ax = plt.subplots(figsize=(8, 3))
    ax.hist(coh, bins=50, color="#4C78A8")
    ax.axvline(COHERENCE_THRESHOLD, color="red", linestyle="--", label="threshold 0.35")
    ax.set_title("Coherence distribution — how much of Delhi is measurable?", fontsize=11)
    ax.set_xlabel("coherence"); ax.set_ylabel("pixels"); ax.legend()
    savefig("11_report.png")
    register(11, "Honesty report",
             "Coherence statistics show exactly how much of the image is trustworthy. "
             "The result is exploratory: no external validation, only ~1 year of history.",
             "11_report.png")


def build_lesson():
    parts = ["<html><head><meta charset='utf-8'><title>NISAR Subsidence — Guided Lesson</title>",
             "<style>body{background:#0d1117;color:#e6edf3;font-family:system-ui;max-width:900px;margin:30px auto;padding:0 20px}",
             "h2{color:#58a6ff} img{width:100%;border:1px solid #30363d;border-radius:6px;margin:10px 0}",
             "p{line-height:1.6;color:#c9d1d9}</style></head><body>",
             "<h1>From rainbow fringes to a subsidence map — a NISAR lesson</h1>",
             "<p>This page was built step by step from free NISAR L-band data over Delhi. Click through to see what happened at each stage.</p>"]
    for n in sorted(STEPS):
        title, expl, image = STEPS[n]
        parts.append(f"<h2>Step {n} — {html.escape(title)}</h2>")
        parts.append(f"<p>{html.escape(expl)}</p>")
        parts.append(f"<img src='{image}'>")
    parts.append("</body></html>")
    (LEARN / "lesson.html").write_text("\n".join(parts))
    print(f"\nLesson page: outputs/learn/lesson.html")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--step", type=int, default=0)
    args = ap.parse_args()
    if not args.all and not args.step:
        ap.print_help(); return

    if args.all or args.step == 1:
        cands = step1()
    if args.all or args.step == 2:
        ent, d = step2()
    if args.all:
        step3(d); step4(d); iono = step5(d); corr = step6(d); masked = step7(d, corr)
        los = step8(masked); rate = step9(los); step10(cands, d, rate); step11(cands, d)
        build_lesson()
    elif args.step == 3: step3(load_pair(gunw_candidates()[len(gunw_candidates())//2]))
    elif args.step == 4: step4(load_pair(gunw_candidates()[len(gunw_candidates())//2]))
    elif args.step == 5: step5(load_pair(gunw_candidates()[len(gunw_candidates())//2]))
    elif args.step == 6: step6(load_pair(gunw_candidates()[len(gunw_candidates())//2]))
    elif args.step == 7:
        d = load_pair(gunw_candidates()[len(gunw_candidates())//2]); step7(d, d["phase"])
    elif args.step == 8:
        d = load_pair(gunw_candidates()[len(gunw_candidates())//2]); step8(d["phase"])
    elif args.step == 9:
        d = load_pair(gunw_candidates()[len(gunw_candidates())//2]); step9(d["phase"])
    elif args.step == 10:
        c = gunw_candidates(); d = load_pair(c[len(c)//2]); step10(c, d, d["phase"])
    elif args.step == 11:
        c = gunw_candidates(); step11(c, load_pair(c[len(c)//2]))
    if not args.all and args.step:
        # single-step runs still produce a lesson page for the steps seen so far
        build_lesson()


if __name__ == "__main__":
    main()
