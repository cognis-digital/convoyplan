"""Smoke tests for CONVOYPLAN. Standard library only, no network."""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from convoyplan import TOOL_NAME, TOOL_VERSION, PlanError, parse_plan, plan_convoy  # noqa: E402
from convoyplan.core import Chokepoint, chokepoint_risk  # noqa: E402
from convoyplan.cli import main  # noqa: E402


DEMO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "demos", "01-basic", "convoy.yaml",
)

BASIC_PLAN = """
mission: test-run
vehicles: 2
fuel_burn_l_per_km: 0.5
onboard_fuel_l: 200
load_factor: 1.0
reserve_pct: 0.10
resupply_l: 150
legs:
  - name: a
    distance_km: 100
    terrain: paved
  - name: b
    distance_km: 50
    terrain: sand
    chokepoint: Gap-1
chokepoints:
  - name: Gap-1
    threat: 9
    congestion: 4
    detour_km: 1
"""


class TestMeta(unittest.TestCase):
    def test_metadata(self):
        self.assertEqual(TOOL_NAME, "convoyplan")
        self.assertTrue(TOOL_VERSION)


class TestParsing(unittest.TestCase):
    def test_parse_basic(self):
        plan = parse_plan(BASIC_PLAN)
        self.assertEqual(plan.mission, "test-run")
        self.assertEqual(plan.vehicles, 2)
        self.assertEqual(len(plan.legs), 2)
        self.assertEqual(plan.legs[1].terrain, "sand")
        self.assertEqual(plan.legs[1].chokepoint, "Gap-1")
        self.assertEqual(len(plan.chokepoints), 1)

    def test_missing_required(self):
        with self.assertRaises(PlanError):
            parse_plan("vehicles: 2\nlegs:\n  - name: x\n    distance_km: 10\n")

    def test_bad_terrain(self):
        bad = BASIC_PLAN.replace("terrain: sand", "terrain: lava")
        with self.assertRaises(PlanError):
            parse_plan(bad)

    def test_empty_legs(self):
        with self.assertRaises(PlanError):
            parse_plan(
                "mission: x\nvehicles: 1\nfuel_burn_l_per_km: 1\n"
                "onboard_fuel_l: 10\nlegs:\n"
            )


class TestEngine(unittest.TestCase):
    def test_fuel_math(self):
        plan = parse_plan(BASIC_PLAN)
        res = plan_convoy(plan)
        # leg a: 100 * 0.5 * 1.0(paved) * 2 vehicles = 100 L
        self.assertAlmostEqual(res.legs[0].fuel_burn_l, 100.0, places=2)
        # leg b: 50 * 0.5 * 1.6(sand) * 2 = 80 L
        self.assertAlmostEqual(res.legs[1].fuel_burn_l, 80.0, places=2)
        self.assertAlmostEqual(res.total_distance_km, 150.0, places=2)
        self.assertAlmostEqual(res.total_fuel_l, 180.0, places=2)

    def test_usable_and_reserve(self):
        plan = parse_plan(BASIC_PLAN)
        res = plan_convoy(plan)
        # 200 * (1 - 0.10) = 180 usable per vehicle
        self.assertAlmostEqual(res.usable_fuel_per_vehicle_l, 180.0, places=2)

    def test_chokepoint_risk_bounds(self):
        low = chokepoint_risk(Chokepoint("a", 0, 0, 100))
        high = chokepoint_risk(Chokepoint("b", 10, 10, 0))
        self.assertGreaterEqual(low, 0.0)
        self.assertLessEqual(high, 100.0)
        self.assertLess(low, high)
        # high threat, zero bypass should be near the ceiling
        self.assertGreater(high, 90.0)

    def test_detour_lowers_risk(self):
        near = chokepoint_risk(Chokepoint("x", 5, 5, 0))
        far = chokepoint_risk(Chokepoint("x", 5, 5, 80))
        self.assertGreater(near, far)

    def test_infeasible(self):
        bad = BASIC_PLAN.replace("onboard_fuel_l: 200", "onboard_fuel_l: 40")
        bad = bad.replace("resupply_l: 150", "resupply_l: 0")
        res = plan_convoy(parse_plan(bad))
        self.assertFalse(res.feasible)
        self.assertTrue(any("INFEASIBLE" in w for w in res.warnings))

    def test_resupply_triggered(self):
        # Small tank but resupply available -> should refuel, stay feasible.
        txt = (
            "mission: r\nvehicles: 1\nfuel_burn_l_per_km: 1.0\n"
            "onboard_fuel_l: 100\nreserve_pct: 0.0\nresupply_l: 100\nlegs:\n"
            "  - name: l1\n    distance_km: 90\n    terrain: paved\n"
            "  - name: l2\n    distance_km: 90\n    terrain: paved\n"
        )
        res = plan_convoy(parse_plan(txt))
        self.assertTrue(res.feasible)
        self.assertGreaterEqual(res.resupply_count, 1)


class TestCLI(unittest.TestCase):
    def test_demo_exists(self):
        self.assertTrue(os.path.exists(DEMO))

    def test_main_table(self):
        rc = main(["plan", DEMO])
        self.assertEqual(rc, 0)

    def test_main_json(self):
        rc = main(["plan", DEMO, "--format", "json"])
        self.assertEqual(rc, 0)

    def test_main_missing_file(self):
        rc = main(["plan", "does-not-exist.yaml"])
        self.assertEqual(rc, 2)

    def test_subprocess_json_parse(self):
        proc = subprocess.run(
            [sys.executable, "-m", "convoyplan", "plan", DEMO, "--format", "json"],
            capture_output=True, text=True,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertEqual(data["mission"], "FLB-Alpha to DP-Bravo resupply run")
        self.assertIn("legs", data)
        self.assertTrue(data["feasible"])

    def test_no_command(self):
        rc = main([])
        self.assertEqual(rc, 2)


class TestInputValidation(unittest.TestCase):
    """Edge-case and bad-input tests for the hardened validation paths."""

    def test_non_numeric_distance_km(self):
        """Non-numeric distance_km must raise PlanError, not ValueError."""
        bad = BASIC_PLAN.replace("distance_km: 100", "distance_km: oops")
        with self.assertRaises(PlanError) as ctx:
            parse_plan(bad)
        self.assertIn("distance_km", str(ctx.exception))

    def test_non_numeric_vehicles(self):
        """Non-numeric vehicles must raise PlanError, not ValueError."""
        bad = BASIC_PLAN.replace("vehicles: 2", "vehicles: two")
        with self.assertRaises(PlanError) as ctx:
            parse_plan(bad)
        self.assertIn("vehicles", str(ctx.exception))

    def test_non_numeric_reserve_pct(self):
        """Non-numeric reserve_pct must raise PlanError, not ValueError."""
        bad = BASIC_PLAN.replace("reserve_pct: 0.10", "reserve_pct: high")
        with self.assertRaises(PlanError) as ctx:
            parse_plan(bad)
        self.assertIn("reserve_pct", str(ctx.exception))

    def test_zero_vehicles(self):
        """Zero vehicles must raise PlanError."""
        bad = BASIC_PLAN.replace("vehicles: 2", "vehicles: 0")
        with self.assertRaises(PlanError) as ctx:
            parse_plan(bad)
        self.assertIn("vehicles", str(ctx.exception))

    def test_non_numeric_chokepoint_field(self):
        """Non-numeric chokepoint field must raise PlanError, not ValueError."""
        bad = BASIC_PLAN.replace("threat: 9", "threat: extreme")
        with self.assertRaises(PlanError) as ctx:
            parse_plan(bad)
        self.assertIn("Chokepoint", str(ctx.exception))

    def test_cli_malformed_plan_exits_2(self):
        """CLI must print a clean message and exit 2 for malformed numeric field."""
        import tempfile
        bad_plan = BASIC_PLAN.replace("vehicles: 2", "vehicles: not-a-number")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(bad_plan)
            fname = f.name
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "convoyplan", "plan", fname],
                capture_output=True, text=True,
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            )
            self.assertEqual(proc.returncode, 2)
            self.assertIn("error", proc.stderr.lower())
            # Must NOT be a raw traceback
            self.assertNotIn("Traceback", proc.stderr)
        finally:
            os.unlink(fname)

    def test_cli_os_error_exits_2(self):
        """CLI must print a clean message and exit 2 when the path is a directory."""
        import tempfile
        dirpath = tempfile.mkdtemp()
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "convoyplan", "plan", dirpath],
                capture_output=True, text=True,
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            )
            self.assertEqual(proc.returncode, 2)
            self.assertNotIn("Traceback", proc.stderr)
        finally:
            os.rmdir(dirpath)


if __name__ == "__main__":
    unittest.main()
