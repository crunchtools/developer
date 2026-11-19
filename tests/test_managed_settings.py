"""The managed settings keep a seat from parking where no one can answer."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

MANAGED = (
    Path(__file__).resolve().parent.parent / "rootfs/etc/claude-code/managed-settings.d/40-claude-seat.json"
)


class PlanModeTest(unittest.TestCase):
    # A seat that enters plan mode leaves it through ExitPlanMode, whose
    # approval dialog only a person at the pane can answer. A cross-session
    # message cannot, so a dispatched seat sat there overnight (a314). The
    # brief a seat is dispatched with is the approved plan.
    def test_entering_plan_mode_is_denied(self) -> None:
        settings = json.loads(MANAGED.read_text())
        self.assertIn("EnterPlanMode", settings["permissions"]["deny"])


if __name__ == "__main__":
    unittest.main()
