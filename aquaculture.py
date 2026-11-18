"""AquaSentinel CLI: Aquaculture (shrimp/fish) pond intelligence audit tool.

Monitors pond water levels, tracks Days of Culture (DOC), and flags premature
emergency drainage (feed credit default risk) using all-weather Sentinel-1 & NISAR SAR.

Run:
    python aquaculture.py --benchmark --count 30
    python aquaculture.py --benchmark --export-report outputs/bhimavaram_audit.json
    python aquaculture.py --lat 16.544 --lon 81.523 --pond-id AP-WG-01
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from sentinel_tool.aquaculture import (
    PondObservation,
    PondState,
    RiskLevel,
    audit_cluster,
    audit_pond_observations,
    generate_benchmark_cluster,
)

OUT = ROOT / "outputs"
OUT.mkdir(exist_ok=True)


def print_banner():
    print("=" * 78)
    print("  AquaSentinel — All-Weather Shrimp Pond Intelligence (Sentinel-1 + NISAR)")
    print("=" * 78)


def run_benchmark(count: int, export_path: Path | None = None, as_of_date: str = "2025-08-15"):
    print_banner()
    print(f"\n[1/3] Generating realistic Bhimavaram cluster benchmark ({count} ponds)...")
    ponds = generate_benchmark_cluster(cluster_name="Bhimavaram", count=count)

    print(f"[2/3] Auditing all-weather radar time-series as of {as_of_date}...")
    summary = audit_cluster(ponds, as_of_date=as_of_date)

    print(f"[3/3] Audit Complete. Cluster Portfolio Summary:")
    print("-" * 78)
    print(f"  Cluster Name:              {summary['cluster_name']}")
    print(f"  Audit Date:                {summary['as_of_date']}")
    print(f"  Total Ponds Audited:       {summary['total_ponds']}")
    print(f"  Currently Stocked:         {summary['active_stocked_count']} ({summary['stocking_rate_pct']}%)")
    print(f"  Premature Drained (Risk):  {summary['premature_drain_count']} ({summary['premature_drain_rate_pct']}%)")
    print(f"  Normal Harvested:          {summary['normal_harvest_count']}")
    print(f"  Fallow / Dry Bed:          {summary['fallow_count']}")
    print(f"  Total Credit Portfolio:    Rs. {summary['total_credit_portfolio_inr']:,.0f}")
    print(f"  Critical Credit at Risk:   Rs. {summary['critical_credit_at_risk_inr']:,.0f}")
    print("-" * 78)

    # Print top critical alerts
    critical_ponds = [p for p in summary["ponds"] if p["risk_level"] == RiskLevel.CRITICAL.value]
    if critical_ponds:
        print(f"\nCRITICAL EARLY-WARNING ALERTS ({len(critical_ponds)} ponds flagged):")
        for p in critical_ponds[:5]:
            print(f"  [!] {p['pond_id']} ({p['farmer_name']}) | Rs. {p['credit_at_risk_inr']:,.0f} credit")
            print(f"      {p['summary']}")
        if len(critical_ponds) > 5:
            print(f"      ... and {len(critical_ponds) - 5} more.")

    # Export if requested
    if export_path:
        out_file = export_path.resolve()
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        try:
            rel = out_file.relative_to(ROOT)
        except ValueError:
            rel = out_file
        print(f"\nSaved complete cluster audit report: {rel}")


def audit_single_coord(lat: float, lon: float, pond_id: str, farmer: str = "Sample Farmer"):
    print_banner()
    print(f"\nAuditing Pond {pond_id} at ({lat:.4f}, {lon:.4f})...")

    # In a full deployment, this queries live STAC / ASF for the coordinate window.
    # Here we demonstrate the full lifecycle evaluation with representative observations.
    observations = [
        PondObservation("2025-05-15", "S1", "VV", -13.2, notes="Pre-season dry bed"),
        PondObservation("2025-05-28", "S1", "VV", -22.4, notes="Water fill / Stocking event"),
        PondObservation("2025-06-12", "NISAR", "HH", -23.8, notes="Active culture Day 15"),
        PondObservation("2025-06-28", "S1", "VV", -21.9, notes="Active culture Day 31"),
        PondObservation("2025-07-08", "NISAR", "HH", -14.1, notes="Sudden drainage step detected (+9.7 dB)"),
    ]

    result = audit_pond_observations(
        pond_id=pond_id,
        observations=observations,
        farmer_name=farmer,
        cluster="Bhimavaram",
        credit_amount_inr=350000.0,
        as_of_date="2025-07-10",
        lat=lat,
        lon=lon,
    )

    print(f"\nAudit Result for {result.pond_id}:")
    print(f"  Farmer:          {result.farmer_name}")
    print(f"  Current State:   {result.current_state.value}")
    print(f"  Risk Level:      {result.risk_level.value}")
    print(f"  Current DOC:     {result.current_doc} Days")
    print(f"  Credit at Risk:  Rs. {result.credit_at_risk_inr:,.0f}")
    print(f"  Summary:         {result.summary}")
    print("\nObservation History:")
    for o in result.observations:
        water_str = "WATER (Stocked)" if o["is_water"] else "DRY BED"
        print(f"  - {o['date']} | {o['sensor']:5s} {o['pol']:2s} | {o['db']:6.1f} dB | {water_str}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--benchmark", action="store_true", help="run Bhimavaram cluster benchmark")
    ap.add_argument("--count", type=int, default=30, help="number of ponds in benchmark")
    ap.add_argument("--as-of-date", default="2025-08-15", help="audit evaluation date YYYY-MM-DD")
    ap.add_argument("--export-report", type=Path, default=None, help="export JSON audit report")
    ap.add_argument("--lat", type=float, default=None, help="latitude of single pond")
    ap.add_argument("--lon", type=float, default=None, help="longitude of single pond")
    ap.add_argument("--pond-id", default="AP-WG-001", help="pond ID for single audit")
    args = ap.parse_args()

    if args.benchmark:
        run_benchmark(args.count, args.export_report, args.as_of_date)
    elif args.lat is not None and args.lon is not None:
        audit_single_coord(args.lat, args.lon, args.pond_id)
    else:
        # Default behavior: run a quick 20-pond demo audit
        run_benchmark(20, OUT / "aquaculture_benchmark_audit.json", args.as_of_date)


if __name__ == "__main__":
    main()
