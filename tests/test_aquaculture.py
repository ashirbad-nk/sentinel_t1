"""Unit tests for the aquaculture pond intelligence module."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from sentinel_tool.aquaculture import (
    CropCycle,
    PondAuditResult,
    PondObservation,
    PondState,
    RiskLevel,
    audit_cluster,
    audit_pond_observations,
    generate_benchmark_cluster,
    is_water_backscatter,
)


class TestAquacultureWaterPhysics(unittest.TestCase):
    def test_sentinel1_water_detection(self):
        # S1 VV: Water is < -19.5 dB, dry bed is >= -19.5 dB
        self.assertTrue(is_water_backscatter(-23.4, "S1", "VV"))
        self.assertTrue(is_water_backscatter(-20.1, "S1", "VV"))
        self.assertFalse(is_water_backscatter(-18.5, "S1", "VV"))
        self.assertFalse(is_water_backscatter(-12.0, "S1", "VV"))

        # S1 VH: Water is < -25.0 dB
        self.assertTrue(is_water_backscatter(-27.0, "S1", "VH"))
        self.assertFalse(is_water_backscatter(-22.0, "S1", "VH"))

    def test_nisar_water_detection(self):
        # NISAR L-band HH: Water is < -22.0 dB
        self.assertTrue(is_water_backscatter(-25.5, "NISAR", "HH"))
        self.assertFalse(is_water_backscatter(-16.0, "NISAR", "HH"))

        # NISAR L-band HV: Water is < -27.0 dB
        self.assertTrue(is_water_backscatter(-29.0, "NISAR", "HV"))
        self.assertFalse(is_water_backscatter(-24.0, "NISAR", "HV"))

    def test_optical_ndwi_water_detection(self):
        self.assertTrue(is_water_backscatter(0.45, "S2", "OPTICAL"))
        self.assertFalse(is_water_backscatter(-0.10, "S2", "OPTICAL"))


class TestPondLifecycleStateMachine(unittest.TestCase):
    def test_empty_observations(self):
        res = audit_pond_observations("TEST-01", [])
        self.assertEqual(res.current_state, PondState.UNKNOWN)
        self.assertEqual(res.risk_level, RiskLevel.INACTIVE)
        self.assertEqual(res.current_doc, 0)

    def test_healthy_in_progress_stocked_pond(self):
        obs = [
            PondObservation("2025-06-01", "S1", "VV", -13.0),   # dry bed
            PondObservation("2025-06-15", "S1", "VV", -22.5),   # stocked (Day 0)
            PondObservation("2025-07-01", "NISAR", "HH", -24.0),# Day 16
            PondObservation("2025-07-25", "S1", "VV", -21.8),   # Day 40
        ]
        res = audit_pond_observations(
            pond_id="AP-WG-001",
            observations=obs,
            credit_amount_inr=250000,
            as_of_date="2025-07-25",
        )
        self.assertEqual(res.current_state, PondState.STOCKED)
        self.assertEqual(res.risk_level, RiskLevel.NORMAL)
        self.assertEqual(res.current_doc, 40)
        self.assertEqual(res.stocking_date, "2025-06-15")
        self.assertEqual(res.credit_at_risk_inr, 0.0)

    def test_premature_emergency_drainage_triggers_critical_alert(self):
        # Stocked on June 10, suddenly drained on July 20 (DOC = 40 days < 65 min DOC)
        obs = [
            PondObservation("2025-06-01", "S1", "VV", -13.5),
            PondObservation("2025-06-10", "S1", "VV", -22.8),   # Stocked
            PondObservation("2025-07-05", "NISAR", "HH", -24.1),  # Active
            PondObservation("2025-07-20", "S1", "VV", -13.8),   # Sudden drain!
        ]
        res = audit_pond_observations(
            pond_id="AP-WG-ALERT",
            farmer_name="V. Rama Rao",
            observations=obs,
            credit_amount_inr=500000.0,
            as_of_date="2025-07-22",
        )
        self.assertEqual(res.current_state, PondState.PREMATURE_DRAIN)
        self.assertEqual(res.risk_level, RiskLevel.CRITICAL)
        self.assertEqual(res.credit_at_risk_inr, 500000.0)
        self.assertEqual(len(res.history_cycles), 1)
        self.assertTrue(res.history_cycles[0].is_premature)
        self.assertEqual(res.history_cycles[0].days_of_culture, 40)
        self.assertIn("CRITICAL: Premature drainage detected", res.summary)

    def test_normal_harvest_at_maturity(self):
        # Stocked on May 01, harvested on July 30 (DOC = 90 days >= 65 min DOC)
        obs = [
            PondObservation("2025-04-25", "S1", "VV", -13.5),
            PondObservation("2025-05-01", "S1", "VV", -22.5),   # Stocked
            PondObservation("2025-06-15", "NISAR", "HH", -23.5),  # Day 45
            PondObservation("2025-07-30", "S1", "VV", -13.2),   # Harvested Day 90
        ]
        res = audit_pond_observations(
            pond_id="AP-WG-GOOD",
            observations=obs,
            credit_amount_inr=300000.0,
            as_of_date="2025-08-05",
        )
        self.assertEqual(res.current_state, PondState.NORMAL_HARVEST)
        self.assertEqual(res.risk_level, RiskLevel.NORMAL)
        self.assertEqual(res.credit_at_risk_inr, 0.0)
        self.assertEqual(res.history_cycles[0].days_of_culture, 90)
        self.assertFalse(res.history_cycles[0].is_premature)

    def test_overdue_harvest_warning(self):
        # Stocked for 130 days without harvest (>120 DOC)
        obs = [
            PondObservation("2025-01-01", "S1", "VV", -22.5),
            PondObservation("2025-05-11", "S1", "VV", -22.0),
        ]
        res = audit_pond_observations(
            pond_id="AP-WG-OLD",
            observations=obs,
            as_of_date="2025-05-11",
        )
        self.assertEqual(res.current_state, PondState.STOCKED)
        self.assertEqual(res.risk_level, RiskLevel.WARNING)
        self.assertEqual(res.current_doc, 130)


class TestClusterAuditingAndBenchmarking(unittest.TestCase):
    def test_generate_and_audit_cluster(self):
        ponds = generate_benchmark_cluster(cluster_name="Bhimavaram", count=20, seed=123)
        self.assertEqual(len(ponds), 20)
        self.assertTrue(all(p["pond_id"].startswith("AP-BV-") for p in ponds))

        summary = audit_cluster(ponds, as_of_date="2025-08-15")
        self.assertEqual(summary["total_ponds"], 20)
        self.assertEqual(summary["cluster_name"], "Bhimavaram")
        self.assertGreater(summary["total_credit_portfolio_inr"], 0)
        self.assertEqual(
            summary["active_stocked_count"]
            + summary["premature_drain_count"]
            + summary["normal_harvest_count"]
            + summary["fallow_count"],
            20,
        )

        # JSON serialization check
        serialized = json.dumps(summary)
        self.assertIn("Bhimavaram", serialized)


if __name__ == "__main__":
    unittest.main()
