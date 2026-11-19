"""Production-style subsidence mapping from NISAR GUNW interferograms.

Improvements over subsidence_learn.py:
  * ionosphere correction (same)
  * coherence masking (same)
  * PLANAR DETRENDING to remove residual orbit/atmosphere ramps
  * uses the real incidence angle from metadata for LOS->vertical
  * STACKS several 12-day pairs with a robust median
  * outputs a per-pixel uncertainty (spread across pairs)

Run:
    python subsidence.py --pairs 5                 # Delhi, 5 pairs
    python subsidence.py --city Gujarat --pairs 5
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
import rasterio
from rasterio.transform import from_origin

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs" / "subsidence"
OUT.mkdir(parents=True, exist_ok=True)

CITIES = {"Delhi": (28.6139, 77.2090), "Gujarat": (23.0507771, 72.5448828),
          "Kolkata": (22.5726, 88.3639)}
LAMBDA_M = 0.24
COH_THRESHOLD = 0.35
WINDOW_M = 12000
EPSG = 32643  # all three cities are in UTM 43N / 44N (approx; Delhi 43N)


def detrend_plane(arr: np.ndarray, valid: np.ndarray, degree: int = 1) -> np.ndarray:
    """Fit a low-order polynomial to valid pixels and subtract it.

    Removes long-wavelength orbital/atmospheric ramps. Caveat: also removes
    very broad deformation (a wide subsidence bowl), so treat as exploratory.
    """
    ys, xs = np.indices(arr.shape)
    y = arr[valid].astype("float64")
    nx, ny = xs[valid].astype("float64") / arr.shape[1], ys[valid].astype("float64") / arr.shape[0]
    cols = [np.ones_like(nx), nx, ny]
    if degree >= 2:
        cols += [nx * ny, nx ** 2, ny ** 2]
    A = np.column_stack(cols)
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    full_cols = [np.ones(arr.shape), xs / arr.shape[1], ys / arr.shape[0]]
    if degree >= 2:
        full_cols += [(xs / arr.shape[1]) * (ys / arr.shape[0]),
                      (xs / arr.shape[1]) ** 2, (ys / arr.shape[0]) ** 2]
    trend = np.zeros(arr.shape, dtype="float64")
    for c, f in zip(coef, full_cols):
        trend += c * f
    out = arr.astype("float64") - trend
    out[~valid] = np.nan
    return out.astype("float32")


def pair_days(dt) -> int:
    """Number of days between reference and secondary, from an open datatree."""
    try:
        ident = dt["/science/LSAR/identification"]
        r = str(ident["referenceZeroDopplerStartTime"].values)
        s = str(ident["secondaryZeroDopplerStartTime"].values)
        d1 = datetime.fromisoformat(r[:19])
        d2 = datetime.fromisoformat(s[:19])
        return max(1, (d2 - d1).days)
    except Exception:
        return 12


def process_pair(entry, lat, lon):
    """Return (rate_mm_per_year, coherence, incidence_angle, days) arrays."""
    from nisar_play import open_datatree, resolve_signed, subset_to_aoi
    dt = open_datatree(resolve_signed(entry["url"]))
    grid = dt["/science/LSAR/GUNW/grids/frequencyA"]
    node = grid["unwrappedInterferogram/HH"]

    epsg = int(grid["unwrappedInterferogram/HH"]["coherenceMagnitude"]
               .attrs.get("proj:epsg", 0)) or 32643
    ph = subset_to_aoi(node["unwrappedPhase"], lat, lon, WINDOW_M, epsg)
    co = subset_to_aoi(node["coherenceMagnitude"], lat, lon, WINDOW_M, epsg)
    io = subset_to_aoi(node["ionospherePhaseScreen"], lat, lon, WINDOW_M, epsg)
    if ph is None or co is None or io is None:
        raise ValueError("no AOI coverage")

    phase = np.asarray(ph.values, dtype="float32")
    coh = np.asarray(co.values, dtype="float32")
    iono = np.asarray(io.values, dtype="float32")
    valid = np.isfinite(phase) & np.isfinite(coh) & np.isfinite(iono) & (coh >= COH_THRESHOLD)
    if valid.sum() < 100:
        raise ValueError("too few coherent pixels")

    # 1) ionosphere correction, 2) detrend residual ramps
    corrected = phase - iono
    corrected = detrend_plane(corrected, valid, degree=1)

    # 3) phase -> LOS mm, 4) vertical projection, 5) rate
    los_mm = (LAMBDA_M / (4 * np.pi)) * corrected * 1000
    inc_node = grid["unwrappedInterferogram/HH"]
    inc = 47.0
    try:
        inc_node2 = dt["/science/LSAR/GUNW/metadata/radarGrid"]
        ia = np.asarray(inc_node2["incidenceAngle"].values, dtype="float32")
        inc = float(np.nanmedian(ia))
    except Exception:
        pass
    vertical = los_mm / np.cos(np.deg2rad(inc))
    days = pair_days(dt)
    rate = vertical / days * 365.0
    return rate, coh, inc, days


def write_geotiff(path: Path, arr: np.ndarray, cell: float, epsg: int = 32643):
    h, w = arr.shape
    with rasterio.open(path, "w", driver="GTiff", height=h, width=w, count=1,
                        dtype="float32", nodata=np.nan,
                        crs=f"EPSG:{epsg}",
                        transform=from_origin(0, 0, cell, cell)) as dst:
        dst.write(arr.astype("float32"), 1)


def save_map(arr, path: Path, title: str, cmap="RdBu", units="mm/yr"):
    fig, ax = plt.subplots(figsize=(7, 6))
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return
    lo, hi = np.nanpercentile(finite, [5, 95])
    im = ax.imshow(arr, cmap=cmap, vmin=-max(abs(lo), abs(hi)), vmax=max(abs(lo), abs(hi)))
    ax.set_title(title, fontsize=11)
    ax.set_axis_off()
    plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label=units)
    plt.tight_layout(); plt.savefig(path, dpi=140); plt.close("all")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--city", choices=list(CITIES), default="Delhi")
    ap.add_argument("--pairs", type=int, default=5)
    ap.add_argument("--degree", type=int, default=1, choices=[1, 2])
    args = ap.parse_args()
    lat, lon = CITIES[args.city]

    from subsidence_learn import gunw_candidates
    cands = gunw_candidates()
    if len(cands) > args.pairs:
        idx = [round(i * (len(cands) - 1) / (args.pairs - 1)) for i in range(args.pairs)]
        picks = [cands[i] for i in idx]
    else:
        picks = cands
    print(f"{args.city}: stacking {len(picks)} GUNW pairs "
          f"({picks[0]['date'][:10]} -> {picks[-1]['date'][:10]})\n")

    rates, infos = [], []
    for i, ent in enumerate(picks, 1):
        print(f"  [{i}/{len(picks)}] {ent['date'][:10]} ...", flush=True)
        try:
            rate, coh, inc, days = process_pair(ent, lat, lon)
            rates.append(rate)
            infos.append({"date": ent["date"][:10], "incidence": round(inc, 1),
                          "days": days, "coherent_px": int(np.isfinite(rate).sum()),
                          "collection": ent["collection"]})
            print(f"      {np.isfinite(rate).sum()} px, incidence {inc:.1f}deg, {days}d", flush=True)
        except Exception as exc:
            print(f"      ERROR {type(exc).__name__}: {exc}", flush=True)

    if not rates:
        print("No pairs processed."); return

    stack = np.stack(rates)
    median = np.nanmedian(stack, axis=0)
    spread = np.nanstd(stack, axis=0)

    for name, arr, title in [("velocity_median.tif", median, "median")]:
        write_geotiff(OUT / name, arr, WINDOW_M / median.shape[1])
    save_map(median, OUT / "velocity_median.png",
             f"{args.city} subsidence (median of {len(rates)} pairs, exploratory)")
    save_map(spread, OUT / "uncertainty.png", "Across-pair spread (mm/yr)",
             cmap="magma", units="mm/yr")

    fig, ax = plt.subplots(figsize=(9, 3.5))
    means = [float(np.nanmedian(r[np.isfinite(r)])) for r in rates]
    ax.plot([x["date"] for x in infos], means, marker="o", color="#2ea043")
    ax.set_title("Median velocity per pair — do they agree?")
    ax.set_ylabel("mm/yr"); ax.grid(alpha=.3); fig.autofmt_xdate(); fig.tight_layout()
    fig.savefig(OUT / "pair_agreement.png", dpi=140); plt.close("all")

    f = finite = median[np.isfinite(median)]
    print(f"\nRESULT: median rate {np.nanmedian(f):.1f} mm/yr "
          f"(5-95%: {np.nanpercentile(f,5):.0f} .. {np.nanpercentile(f,95):.0f})")
    print(f"        across-pair spread (median): {np.nanmedian(spread[np.isfinite(spread)]):.1f} mm/yr")
    print(f"Outputs in outputs/subsidence/")

    parts = [f"<h1>{args.city} subsidence — exploratory</h1>",
             f"<p>Stacked {len(rates)} NISAR GUNW 12-day pairs. "
             "Detrended, ionosphere-corrected, masked at coherence >= 0.35.</p>",
             "<h2>Velocity (median)</h2>",
             "<img src='velocity_median.png' style='width:600px'>",
             "<h2>Uncertainty (spread across pairs)</h2>",
             "<img src='uncertainty.png' style='width:600px'>",
             "<h2>Do pairs agree?</h2>",
             "<img src='pair_agreement.png' style='width:700px'>",
             "<h2>Caveats</h2><ul>",
             "<li>No external validation (GNSS/leveling).</li>",
             "<li>Detrending removes long-wavelength deformation as well as ramps.</li>",
             "<li>~1 year of data; rates are noisy.</li></ul>"]
    (OUT / "report.html").write_text("<html><body style='font-family:system-ui'>" +
                                     "\n".join(parts) + "</body></html>")


if __name__ == "__main__":
    main()
