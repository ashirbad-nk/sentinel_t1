"""Step 1 orientation: discover what satellite data is available over the
Cauvery Delta and what we can do with it.

Two parts:
  1. Cloud-gap audit  — quantify why optical-only crop monitoring fails in the
     Indian monsoon (uses STAC metadata only, so it is fast).
  2. Raster preview   — load one real Sentinel-2 scene, compute NDVI and a
     false-colour composite, and save the maps.

Run:
    python orientation.py                # everything
    python orientation.py --skip-raster  # audit only (fast, metadata only)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import matplotlib

matplotlib.use("Agg")  # headless-friendly
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import stackstac
import xarray as xr

from sentinel_tool.config import (
    AOI_BBOX,
    AOI_NAME,
    CLOUD_THRESHOLD,
    PREVIEW_WINDOW,
    S1_GRD_COLLECTION,
    S2_COLLECTION,
    SEASON_WINDOW,
    utm_epsg,
)
from sentinel_tool.indices import dn_to_reflectance, ndvi
from sentinel_tool.stac import search_items

OUT = ROOT / "outputs"
OUT.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Part 1 — cloud-gap audit
# ---------------------------------------------------------------------------
def cloud_gap_audit() -> pd.DataFrame:
    """Compare monthly S2 optical usability against all-weather S1 coverage."""
    print("Searching STAC for Sentinel-2 / Sentinel-1 over", AOI_NAME, "...")
    s2_items = list(search_items(S2_COLLECTION, datetime_=SEASON_WINDOW))
    s1_items = list(search_items(S1_GRD_COLLECTION, datetime_=SEASON_WINDOW))
    print(f"  found {len(s2_items)} S2 scenes and {len(s1_items)} S1 scenes")

    rows = []
    for item in s2_items:
        raw_cloud = item.properties.get("eo:cloud_cover")
        cloud = float(raw_cloud) if raw_cloud is not None else np.nan
        rows.append(
            {
                "month": item.datetime.strftime("%Y-%m"),
                "sensor": "S2",
                "cloud_pct": cloud,
                "usable": bool(np.isfinite(cloud) and cloud < CLOUD_THRESHOLD),
            }
        )
    for item in s1_items:
        rows.append(
            {
                "month": item.datetime.strftime("%Y-%m"),
                "sensor": "S1",
                "cloud_pct": np.nan,
                "usable": True,  # every SAR scene is usable regardless of cloud
            }
        )

    if not rows:
        print("  no scenes found in window; skipping CSV and plot")
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    summary = (
        df.groupby(["month", "sensor"])
        .agg(
            scenes=("sensor", "size"),
            usable_scenes=("usable", "sum"),
            median_cloud_pct=("cloud_pct", "median"),
        )
        .reset_index()
    )

    wide = summary.pivot(index="month", columns="sensor", values=["scenes", "usable_scenes"])
    wide.columns = [f"{a}_{b}" for a, b in wide.columns]
    wide = wide.fillna(0).astype(int)

    wide.to_csv(OUT / "cloud_gap_audit.csv")
    print("\nMonthly audit (usable = cloud < %.0f%% for S2):" % CLOUD_THRESHOLD)
    print(wide.to_string())

    plot_cloud_gap(wide)
    return wide


def plot_cloud_gap(wide: pd.DataFrame) -> None:
    """Plot total vs usable optical scenes against all-weather SAR scenes."""
    months = wide.index.tolist()
    x = np.arange(len(months))
    width = 0.26

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.bar(x - width, wide.get("scenes_S2", 0), width, label="S2 scenes acquired", color="#4C78A8")
    ax.bar(
        x,
        wide.get("usable_scenes_S2", 0),
        width,
        label=f"S2 usable (<{CLOUD_THRESHOLD:.0f}% cloud)",
        color="#F58518",
    )
    ax.bar(x + width, wide.get("scenes_S1", 0), width, label="S1 scenes (all-weather)", color="#54A24B")

    ax.set_xticks(x)
    ax.set_xticklabels(months)
    ax.set_ylabel("Number of scenes")
    ax.set_title(f"Monsoon optical gap vs all-weather radar — {AOI_NAME}")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()

    path = OUT / "cloud_gap.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  saved {path.relative_to(ROOT)}")


# ---------------------------------------------------------------------------
# Part 2 — raster preview (NDVI + false colour of a real scene)
# ---------------------------------------------------------------------------
def raster_preview() -> None:
    """Load the clearest S2 scene in the preview window and make maps."""
    print("\nLoading a real Sentinel-2 scene for raster preview...")
    items = list(search_items(S2_COLLECTION, datetime_=PREVIEW_WINDOW))
    if not items:
        print("  no scenes in preview window; skipping")
        return
    item = min(
        items,
        key=lambda it: float(it.properties["eo:cloud_cover"])
        if it.properties.get("eo:cloud_cover") is not None
        else 100.0,
    )
    cloud = item.properties.get("eo:cloud_cover")
    cloud_str = f"{float(cloud):.1f}%" if cloud is not None else "unknown"
    print(f"  scene {item.id}  cloud={cloud_str}")

    bands = ["B02", "B03", "B04", "B08", "B11"]  # blue, green, red, NIR, SWIR
    epsg = utm_epsg((AOI_BBOX[0] + AOI_BBOX[2]) / 2.0, (AOI_BBOX[1] + AOI_BBOX[3]) / 2.0)
    stack = stackstac.stack(
        [item],
        assets=bands,
        bounds_latlon=AOI_BBOX,
        epsg=epsg,
        resolution=20,
        chunksize=1024,
    )
    scene = dn_to_reflectance(stack.sel(band=bands).isel(time=0).compute(), item=item)

    blue, green, red, nir, swir = (scene.sel(band=b) for b in bands)
    nd = ndvi(red, nir)

    fig, axes = plt.subplots(1, 3, figsize=(17, 5))

    rgb = np.nan_to_num(np.stack([red, green, blue], axis=-1), nan=0.0)
    axes[0].imshow(np.clip(rgb, 0.0, 0.3) / 0.3)
    axes[0].set_title("True colour (RGB)")

    fc = np.nan_to_num(np.stack([swir, nir, red], axis=-1), nan=0.0)
    axes[1].imshow(np.clip(fc, 0.0, 0.4) / 0.4)
    axes[1].set_title("False colour (SWIR-NIR-Red)")

    im = axes[2].imshow(nd, cmap="RdYlGn", vmin=-0.2, vmax=0.8)
    axes[2].set_title("NDVI — crop vigour (red=low, green=high)")
    fig.colorbar(im, ax=axes[2], fraction=0.046, pad=0.04)

    for ax in axes:
        ax.set_axis_off()
    fig.suptitle(f"Sentinel-2 preview — {AOI_NAME} ({item.datetime.date()})", fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    path = OUT / "s2_preview.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {path.relative_to(ROOT)}")

    # stackstac attaches object-valued attrs (RasterSpec, proj:bbox, ...) that
    # netCDF cannot serialize, so write a clean DataArray with explicit coords.
    clean = xr.DataArray(
        nd.values,
        dims=("y", "x"),
        coords={"y": nd.y.values, "x": nd.x.values},
        name="ndvi",
        attrs={
            "long_name": "NDVI (Sentinel-2), derived from B04/B08",
            "crs": f"EPSG:{epsg}",
            "scene_id": item.id,
        },
    )
    clean.to_netcdf(OUT / "ndvi_preview.nc")
    print("  saved outputs/ndvi_preview.nc")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-raster", action="store_true", help="metadata-only cloud audit")
    args = parser.parse_args()

    cloud_gap_audit()
    if not args.skip_raster:
        raster_preview()
    print("\nDone. Outputs are in", OUT.relative_to(ROOT))


if __name__ == "__main__":
    main()
