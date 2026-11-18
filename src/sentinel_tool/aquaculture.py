"""Aquaculture (shrimp/fish) pond intelligence engine.

Uses multi-sensor all-weather SAR backscatter (Sentinel-1 RTC C-band VV/VH and
NISAR L2 GCOV L-band HH/HV) plus Sentinel-2 optical data to monitor pond water
levels, track Days of Culture (DOC), and detect premature disease drainage.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any

from .config import (
    AQUA_DRAIN_STEP_THRESHOLD_DB,
    AQUA_DRY_BED_MIN_DB,
    AQUA_MAX_NORMAL_DOC,
    AQUA_MIN_HARVEST_DOC,
    NISAR_WATER_MAX_DB_HH,
    NISAR_WATER_MAX_DB_HV,
    S1_WATER_MAX_DB_VH,
    S1_WATER_MAX_DB_VV,
)


class PondState(str, Enum):
    """Lifecycle state of an aquaculture pond."""

    DRY_BED = "DRY_BED"                  # Sun-drying / pre-season fallow
    FILLING = "FILLING"                  # Water intake initiated
    STOCKED = "STOCKED"                  # Active culture / pond filled with water
    NORMAL_HARVEST = "NORMAL_HARVEST"    # Harvested at maturity (DOC >= min_harvest_doc)
    PREMATURE_DRAIN = "PREMATURE_DRAIN"  # Emergency drained (DOC < min_harvest_doc) -> High Risk
    FALLOW = "FALLOW"                    # Inactive for an extended period (>60 days dry)
    UNKNOWN = "UNKNOWN"


class RiskLevel(str, Enum):
    """Credit and operational risk rating for a pond."""

    NORMAL = "NORMAL"        # Healthy culture progression or normal harvest
    WARNING = "WARNING"      # Stagnant (>120 DOC) or fluctuating water level
    CRITICAL = "CRITICAL"    # Premature drainage detected (likely disease / early salvage)
    INACTIVE = "INACTIVE"    # Pond currently fallow/dry bed


@dataclass
class PondObservation:
    """A single satellite radar/optical observation of a pond."""

    date_str: str                       # YYYY-MM-DD
    sensor: str                         # "S1", "NISAR", or "S2"
    polarization: str                   # "VV", "VH", "HH", "HV", or "OPTICAL"
    backscatter_db: float               # Mean backscatter in dB (or NDWI for optical)
    is_water: bool | None = None        # Evaluated automatically if None
    notes: str = ""

    def __post_init__(self):
        if self.is_water is None:
            self.is_water = is_water_backscatter(
                self.backscatter_db, self.sensor, self.polarization
            )


@dataclass
class CropCycle:
    """A discrete shrimp culture cycle from filling to drainage."""

    cycle_id: int
    stocking_date: str
    drain_date: str | None = None
    days_of_culture: int = 0
    outcome: str = "IN_PROGRESS"         # "IN_PROGRESS", "NORMAL_HARVEST", "PREMATURE_DRAIN"
    is_premature: bool = False


@dataclass
class PondAuditResult:
    """Comprehensive lifecycle and risk audit for an aquaculture pond."""

    pond_id: str
    farmer_name: str
    cluster: str
    current_state: PondState
    risk_level: RiskLevel
    current_doc: int                     # Days of culture in current active cycle (0 if dry)
    stocking_date: str | None = None
    last_drain_date: str | None = None
    credit_at_risk_inr: float = 0.0      # Outstanding seasonal credit amount
    summary: str = ""
    history_cycles: list[CropCycle] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    lat: float | None = None
    lon: float | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["current_state"] = self.current_state.value
        data["risk_level"] = self.risk_level.value
        return data

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def is_water_backscatter(
    val_db: float,
    sensor: str = "S1",
    polarization: str = "VV",
) -> bool:
    """Return True if backscatter dB indicates open water / filled pond.

    Smooth water exhibits specular reflection away from the radar receiver,
    resulting in very low backscatter values.
    """
    s = sensor.upper()
    p = polarization.upper()

    if s == "S2" or p == "OPTICAL":
        # For optical NDWI, values > 0.15 indicate open water
        return val_db > 0.15

    if s.startswith("NISAR"):
        if p in ("HV", "VH"):
            return val_db < NISAR_WATER_MAX_DB_HV
        return val_db < NISAR_WATER_MAX_DB_HH

    # Default: Sentinel-1 C-band
    if p == "VH":
        return val_db < S1_WATER_MAX_DB_VH
    return val_db < S1_WATER_MAX_DB_VV


def parse_iso_date(d_val: str | date | datetime) -> date:
    if isinstance(d_val, datetime):
        return d_val.date()
    if isinstance(d_val, date):
        return d_val
    return datetime.strptime(str(d_val)[:10], "%Y-%m-%d").date()


def audit_pond_observations(
    pond_id: str,
    observations: list[PondObservation],
    *,
    farmer_name: str = "Unknown Farmer",
    cluster: str = "Bhimavaram",
    credit_amount_inr: float = 0.0,
    as_of_date: str | date | None = None,
    min_harvest_doc: int = AQUA_MIN_HARVEST_DOC,
    max_normal_doc: int = AQUA_MAX_NORMAL_DOC,
    drain_step_threshold_db: float = AQUA_DRAIN_STEP_THRESHOLD_DB,
    lat: float | None = None,
    lon: float | None = None,
) -> PondAuditResult:
    """Analyze a chronological time series of SAR observations for a pond.

    Reconstructs the pond's crop cycles, detects stocking dates and drainage
    events, calculates Days of Culture (DOC), and flags premature emergency
    harvests (credit default risk).
    """
    if not observations:
        return PondAuditResult(
            pond_id=pond_id,
            farmer_name=farmer_name,
            cluster=cluster,
            current_state=PondState.UNKNOWN,
            risk_level=RiskLevel.INACTIVE,
            current_doc=0,
            summary="No satellite observations available for this pond.",
            credit_at_risk_inr=credit_amount_inr,
            lat=lat,
            lon=lon,
        )

    # Sort strictly chronologically
    sorted_obs = sorted(observations, key=lambda o: parse_iso_date(o.date_str))
    ref_date = parse_iso_date(as_of_date) if as_of_date else parse_iso_date(sorted_obs[-1].date_str)

    cycles: list[CropCycle] = []
    current_cycle_start: date | None = None
    last_drain_date: str | None = None
    current_cycle_id = 0

    for i, obs in enumerate(sorted_obs):
        obs_d = parse_iso_date(obs.date_str)
        if obs_d > ref_date:
            continue

        prev_obs = sorted_obs[i - 1] if i > 0 else None

        # Transition: DRY -> WATER (Stocking event)
        if obs.is_water:
            if current_cycle_start is None:
                # Water appearance marks stocking
                current_cycle_start = obs_d
                current_cycle_id += 1
        else:
            # Current observation is DRY
            if current_cycle_start is not None:
                # Pond was stocked, now it is dry -> DRAINAGE / HARVEST EVENT
                doc_at_drain = (obs_d - current_cycle_start).days
                is_premature = doc_at_drain < min_harvest_doc
                outcome = "PREMATURE_DRAIN" if is_premature else "NORMAL_HARVEST"

                cycles.append(
                    CropCycle(
                        cycle_id=current_cycle_id,
                        stocking_date=current_cycle_start.strftime("%Y-%m-%d"),
                        drain_date=obs_d.strftime("%Y-%m-%d"),
                        days_of_culture=doc_at_drain,
                        outcome=outcome,
                        is_premature=is_premature,
                    )
                )
                last_drain_date = obs_d.strftime("%Y-%m-%d")
                current_cycle_start = None

    # Determine state on as_of_date
    latest_obs = [o for o in sorted_obs if parse_iso_date(o.date_str) <= ref_date][-1]
    last_obs_date = parse_iso_date(latest_obs.date_str)

    if current_cycle_start is not None:
        # Pond is currently filled/stocked
        current_doc = (ref_date - current_cycle_start).days
        stocking_str = current_cycle_start.strftime("%Y-%m-%d")

        if current_doc > max_normal_doc:
            current_state = PondState.STOCKED
            risk_level = RiskLevel.WARNING
            summary = (
                f"WARNING: Pond actively stocked since {stocking_str} ({current_doc} DOC). "
                f"Exceeds standard 120-day harvest window; verify pond health."
            )
        else:
            current_state = PondState.STOCKED
            risk_level = RiskLevel.NORMAL
            summary = (
                f"NORMAL: Active culture in progress. Stocked on {stocking_str} "
                f"({current_doc} Days of Culture). Healthy water retention."
            )
    else:
        # Pond is currently dry bed
        current_doc = 0
        stocking_str = None

        if cycles and cycles[-1].is_premature:
            last_c = cycles[-1]
            days_since_drain = (ref_date - parse_iso_date(last_c.drain_date or ref_date)).days
            if days_since_drain <= 45:
                # Recent premature drainage is a live critical alarm
                current_state = PondState.PREMATURE_DRAIN
                risk_level = RiskLevel.CRITICAL
                summary = (
                    f"CRITICAL: Premature drainage detected on {last_c.drain_date} "
                    f"at Day {last_c.days_of_culture} of culture (standard harvest is >= {min_harvest_doc} DOC). "
                    f"High probability of emergency disease harvest or dyke breach. "
                    f"₹{credit_amount_inr:,.0f} credit at immediate default risk."
                )
            else:
                current_state = PondState.DRY_BED
                risk_level = RiskLevel.INACTIVE
                summary = (
                    f"INACTIVE: Pond dry bed following premature drainage on {last_c.drain_date} "
                    f"({days_since_drain} days ago)."
                )
        elif cycles and not cycles[-1].is_premature:
            last_c = cycles[-1]
            current_state = PondState.NORMAL_HARVEST
            risk_level = RiskLevel.NORMAL
            summary = (
                f"NORMAL: Harvest completed at Day {last_c.days_of_culture} of culture on {last_c.drain_date}. "
                f"Pond currently in post-harvest sun-drying."
            )
        else:
            current_state = PondState.FALLOW
            risk_level = RiskLevel.INACTIVE
            summary = f"FALLOW: No active culture detected. Pond dry bed as of {ref_date}."

    return PondAuditResult(
        pond_id=pond_id,
        farmer_name=farmer_name,
        cluster=cluster,
        current_state=current_state,
        risk_level=risk_level,
        current_doc=current_doc,
        stocking_date=stocking_str,
        last_drain_date=last_drain_date,
        credit_at_risk_inr=credit_amount_inr if risk_level == RiskLevel.CRITICAL else 0.0,
        summary=summary,
        history_cycles=cycles,
        observations=[
            {
                "date": o.date_str,
                "sensor": o.sensor,
                "pol": o.polarization,
                "db": o.backscatter_db,
                "is_water": o.is_water,
            }
            for o in sorted_obs
            if parse_iso_date(o.date_str) <= ref_date
        ],
        lat=lat,
        lon=lon,
    )


def generate_benchmark_cluster(
    cluster_name: str = "Bhimavaram",
    count: int = 30,
    start_date: str = "2025-05-01",
    end_date: str = "2025-10-31",
    seed: int = 42,
) -> list[dict[str, Any]]:
    """Generate a realistic benchmark dataset of aquaculture ponds over Bhimavaram.

    Creates a controlled distribution of:
      - 60% healthy growing/harvested ponds (75-95 DOC)
      - 25% premature emergency-drained ponds (35-50 DOC disease crash)
      - 15% fallow / dry-bed ponds
    Simulates combined Sentinel-1 (C-band) and NISAR (L-band) 3-4 day revisits.
    """
    import random

    rng = random.Random(seed)
    s_date = datetime.strptime(start_date, "%Y-%m-%d").date()
    e_date = datetime.strptime(end_date, "%Y-%m-%d").date()

    # Base coords: Bhimavaram town center
    base_lat, base_lon = 16.544, 81.523

    farmers = [
        "V. Rama Rao", "K. Suresh Varma", "M. Venkatesh", "P. Satyanarayana",
        "Ch. Subba Raju", "G. Nageswara Rao", "B. Krishna Murthy", "D. Appa Rao",
        "K. Srinivas Raju", "T. Prasad", "Y. Bhaskara Rao", "N. Jagannadham",
    ]

    ponds = []
    for idx in range(1, count + 1):
        p_id = f"AP-BV-{idx:03d}"
        farmer = rng.choice(farmers)
        lat = round(base_lat + rng.uniform(-0.06, 0.06), 5)
        lon = round(base_lon + rng.uniform(-0.06, 0.06), 5)
        credit = float(rng.choice([150000, 250000, 350000, 500000, 750000]))

        # Assign profile
        roll = rng.random()
        if roll < 0.25:
            # Profile: Premature disease drainage
            stock_day_offset = rng.randint(15, 45)
            doc_duration = rng.randint(35, 52)  # Early disease harvest
            profile = "DISEASE_PREMATURE"
        elif roll < 0.85:
            # Profile: Healthy full cycle
            stock_day_offset = rng.randint(10, 40)
            doc_duration = rng.randint(75, 95)  # Normal commercial harvest
            profile = "HEALTHY"
        else:
            # Profile: Fallow pond
            stock_day_offset = None
            doc_duration = 0
            profile = "FALLOW"

        # Generate observation timeline with 3-4 day interval
        observations = []
        curr = s_date
        step = 0
        while curr <= e_date:
            curr_str = curr.strftime("%Y-%m-%d")
            # Alternate sensors: 2 S1 passes for every 1 NISAR pass
            is_nisar = (step % 3 == 2)
            sensor = "NISAR" if is_nisar else "S1"
            pol = "HH" if is_nisar else "VV"

            if profile == "FALLOW" or stock_day_offset is None:
                db = rng.gauss(-13.0, 1.0)
            else:
                stock_d = s_date + timedelta(days=stock_day_offset)
                drain_d = stock_d + timedelta(days=doc_duration)
                if curr < stock_d:
                    # Pre-stocking dry bed
                    db = rng.gauss(-13.2, 0.9)
                elif stock_d <= curr < drain_d:
                    # Water-filled active culture
                    water_baseline = -23.5 if is_nisar else -21.8
                    db = rng.gauss(water_baseline, 0.8)
                else:
                    # Drained post-harvest bed
                    db = rng.gauss(-12.8, 1.1)

            observations.append(
                PondObservation(
                    date_str=curr_str,
                    sensor=sensor,
                    polarization=pol,
                    backscatter_db=round(db, 2),
                )
            )
            curr += timedelta(days=rng.choice([3, 4]))
            step += 1

        ponds.append(
            {
                "pond_id": p_id,
                "farmer_name": farmer,
                "cluster": cluster_name,
                "lat": lat,
                "lon": lon,
                "credit_amount_inr": credit,
                "profile": profile,
                "observations": observations,
            }
        )

    return ponds


def audit_cluster(
    cluster_ponds: list[dict[str, Any]],
    as_of_date: str | date | None = None,
) -> dict[str, Any]:
    """Run lifecycle audits on an entire dealership or cluster of ponds."""
    results: list[PondAuditResult] = []
    for item in cluster_ponds:
        res = audit_pond_observations(
            pond_id=item["pond_id"],
            observations=item["observations"],
            farmer_name=item.get("farmer_name", "Unknown"),
            cluster=item.get("cluster", "Bhimavaram"),
            credit_amount_inr=item.get("credit_amount_inr", 0.0),
            as_of_date=as_of_date,
            lat=item.get("lat"),
            lon=item.get("lon"),
        )
        results.append(res)

    total_ponds = len(results)
    stocked_ponds = sum(1 for r in results if r.current_state == PondState.STOCKED)
    premature_ponds = sum(1 for r in results if r.current_state == PondState.PREMATURE_DRAIN)
    harvested_ponds = sum(1 for r in results if r.current_state == PondState.NORMAL_HARVEST)
    fallow_ponds = sum(1 for r in results if r.current_state in (PondState.DRY_BED, PondState.FALLOW))

    total_credit_exposed = sum(p.get("credit_amount_inr", 0.0) for p in cluster_ponds)
    critical_credit_at_risk = sum(r.credit_at_risk_inr for r in results if r.risk_level == RiskLevel.CRITICAL)

    return {
        "cluster_name": cluster_ponds[0].get("cluster", "Bhimavaram") if cluster_ponds else "Unknown",
        "as_of_date": str(as_of_date or date.today()),
        "total_ponds": total_ponds,
        "active_stocked_count": stocked_ponds,
        "premature_drain_count": premature_ponds,
        "normal_harvest_count": harvested_ponds,
        "fallow_count": fallow_ponds,
        "stocking_rate_pct": round(stocked_ponds / total_ponds * 100, 1) if total_ponds else 0.0,
        "premature_drain_rate_pct": round(premature_ponds / total_ponds * 100, 1) if total_ponds else 0.0,
        "total_credit_portfolio_inr": total_credit_exposed,
        "critical_credit_at_risk_inr": critical_credit_at_risk,
        "ponds": [r.to_dict() for r in results],
    }
