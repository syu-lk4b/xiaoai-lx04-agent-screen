"""额度解析的回归检查：Codex 混入的空窗口记录、claude-hud 缓存。

用 mibe 的 venv 跑（run.py 依赖 mibe）：
    vendor/mibe/.venv/bin/python -m unittest discover -s bridge -p 'test_*.py' -v
"""
import json
import tempfile
import unittest
from pathlib import Path

import claude
import run

CODEX = {
    "limit_id": "codex",
    "primary": {"used_percent": 10.0, "window_minutes": 300, "resets_at": 1792752285},
    "secondary": {"used_percent": 2.0, "window_minutes": 10080, "resets_at": 1793339085},
    "credits": {"has_credits": False, "unlimited": False, "balance": "0"},
}
PREMIUM = {
    "limit_id": "premium",
    "primary": None,
    "secondary": None,
    "credits": {"has_credits": False, "unlimited": False, "balance": "0"},
}


class CodexUsageTests(unittest.TestCase):
    def test_windows_become_remaining(self):
        u = run._usage(CODEX)
        self.assertEqual(u["primary"]["remaining"], 90)
        self.assertEqual(u["secondary"]["remaining"], 98)

    def test_credits_only_record_is_not_usage(self):
        self.assertIsNone(run._usage(PREMIUM))

    def test_premium_event_keeps_previous_usage(self):
        agent = run._new_agent("Codex")
        agent["usage"] = run._usage(CODEX)
        before = agent["usage"]
        agent["usage"] = run._usage(PREMIUM) or agent["usage"]
        self.assertIs(agent["usage"], before)


class HudUsageTests(unittest.TestCase):
    def write(self, payload):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(payload, tmp)
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink)
        return Path(tmp.name)

    def test_fresh_cache(self):
        path = self.write({
            "data": {"fiveHour": 11, "sevenDay": 36,
                     "fiveHourResetAt": "2026-10-23T13:59:59.533Z",
                     "sevenDayResetAt": "2026-10-28T11:59:59.533Z"},
            "timestamp": 1_792_754_961_092,
        })
        u = claude.read_hud_usage(path, now=1_792_754_961 + 60)
        self.assertEqual(u["primary"]["remaining"], 89)
        self.assertEqual(u["secondary"]["remaining"], 64)
        self.assertEqual(u["primary"]["window_minutes"], 300)
        self.assertAlmostEqual(u["primary"]["resets_at"], 1_792_763_999.533, places=2)

    def test_stale_cache(self):
        path = self.write({"data": {"fiveHour": 11, "sevenDay": 36}, "timestamp": 0})
        self.assertIsNone(claude.read_hud_usage(path, now=7 * 3600))

    def test_failed_fetch_nulls(self):
        path = self.write({"data": {"fiveHour": None, "sevenDay": None}, "timestamp": 1000})
        self.assertIsNone(claude.read_hud_usage(path, now=1))

    def test_missing_or_corrupt_file(self):
        self.assertIsNone(claude.read_hud_usage(Path("/nonexistent/x.json")))
        bad = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        bad.write("{not json")
        bad.close()
        self.addCleanup(Path(bad.name).unlink)
        self.assertIsNone(claude.read_hud_usage(Path(bad.name)))


if __name__ == "__main__":
    unittest.main()
