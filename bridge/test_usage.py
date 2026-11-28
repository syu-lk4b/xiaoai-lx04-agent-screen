"""额度解析的回归检查：Codex 混入的空窗口记录、claude-hud 快照与旧缓存。

用 mibe 的 venv 跑（run.py 依赖 mibe）：
    vendor/mibe/.venv/bin/python -m unittest discover -s bridge -p 'test_*.py' -v
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import claude
import codex_usage
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


class HudSnapshotTests(unittest.TestCase):
    """claude-hud 0.11+ 的 externalUsageWritePath 快照（旧的 .usage-cache.json 不再写了）。"""

    def write(self, payload):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        tmp.write(payload if isinstance(payload, str) else json.dumps(payload))
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink)
        return Path(tmp.name)

    SNAP = {
        "updated_at": "2026-11-02T07:00:00.000Z",
        "five_hour": {"used_percentage": 23, "resets_at": "2026-11-02T10:00:00.000Z"},
        "seven_day": {"used_percentage": 41, "resets_at": "2026-11-04T12:00:00.000Z"},
    }
    STAMP = 1_793_602_800  # 2026-11-02T07:00:00Z

    def test_fresh_snapshot(self):
        u = claude.read_hud_snapshot(self.write(self.SNAP), now=self.STAMP + 60)
        self.assertEqual(u["primary"], {"remaining": 77, "window_minutes": 300,
                                        "resets_at": self.STAMP + 3 * 3600})
        self.assertEqual(u["secondary"]["remaining"], 59)
        self.assertEqual(u["secondary"]["window_minutes"], 10080)

    def test_stale_snapshot(self):
        self.assertIsNone(claude.read_hud_snapshot(self.write(self.SNAP), now=self.STAMP + 7 * 3600))

    def test_one_window_missing(self):
        snap = dict(self.SNAP, seven_day={"used_percentage": None, "resets_at": None})
        u = claude.read_hud_snapshot(self.write(snap), now=self.STAMP)
        self.assertEqual(u["primary"]["remaining"], 77)
        self.assertIsNone(u["secondary"])

    def test_bad_updated_at_or_corrupt(self):
        self.assertIsNone(claude.read_hud_snapshot(self.write(dict(self.SNAP, updated_at="x")), now=self.STAMP))
        self.assertIsNone(claude.read_hud_snapshot(self.write("{not json"), now=self.STAMP))
        self.assertIsNone(claude.read_hud_snapshot(Path("/nonexistent/x.json")))


class CodexFallbackUsageTests(unittest.TestCase):
    STAMP_ISO = "2026-11-28T03:00:00.000Z"
    STAMP = 1_795_834_800  # 2026-11-28T03:00:00Z

    def setUp(self):
        codex_usage._wham_state["next_try"] = 0.0

    def write(self, payload):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        tmp.write(payload if isinstance(payload, str) else json.dumps(payload))
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink)
        return Path(tmp.name)

    def _entry(self, provider="codex", read_at=None, used5=20, used7=45,
               resets5="2026-11-28T06:00:00Z", held=False, spans=None):
        return {
            "at": read_at or self.STAMP_ISO,
            "quota": {
                "provider": provider,
                "windows": [
                    {"name": "5 hours", "used": used5, "resetsAt": resets5},
                    {"name": "7 days", "used": used7, "resetsAt": "2026-12-03T04:00:00Z"},
                ],
                "readAt": read_at or self.STAMP_ISO,
                "held": held,
            },
            "spans": spans if spans is not None else [
                {"span": 18_000_000_000_000},
                {"span": 604_800_000_000_000},
            ],
        }

    def test_magpie_parses_codex_and_skips_other_providers(self):
        path = self.write({
            "claude/a": self._entry(provider="claude", used5=99),
            "codex/a": self._entry(provider="codex", used5=20, used7=45),
        })
        u, ts = codex_usage.read_magpie_usage(path, now=self.STAMP + 60)
        self.assertEqual(ts, self.STAMP)
        self.assertEqual(u["primary"], {
            "remaining": 80, "window_minutes": 300, "resets_at": self.STAMP + 3 * 3600,
        })
        self.assertEqual(u["secondary"]["remaining"], 55)
        self.assertEqual(u["secondary"]["window_minutes"], 10080)

    def test_magpie_prefers_non_held_then_newest_and_drops_stale(self):
        path = self.write({
            "codex/held": self._entry(read_at="2026-11-28T03:05:00Z", used5=100, held=True),
            "codex/active": self._entry(read_at="2026-11-28T03:00:00Z", used5=30, held=False),
        })
        u, _ = codex_usage.read_magpie_usage(path, now=self.STAMP + 600)
        self.assertEqual(u["primary"]["remaining"], 70)

        # 全超过 6 小时应判为陈旧返回 None
        self.assertEqual(
            codex_usage.read_magpie_usage(path, now=self.STAMP + 7 * 3600),
            (None, None),
        )

    def test_magpie_reversed_windows_and_reset_secs_fallback(self):
        path = self.write({
            "codex/a": {
                "at": self.STAMP_ISO,
                "quota": {
                    "provider": "codex",
                    "windows": [
                        {"name": "7 days", "used": 15, "resetSecs": 5000},
                        {"name": "5 hours", "used": 40, "resetSecs": 1200},
                    ],
                },
            },
        })
        u, _ = codex_usage.read_magpie_usage(path, now=self.STAMP + 10)
        self.assertEqual(u["primary"]["remaining"], 60)
        self.assertEqual(u["primary"]["window_minutes"], 300)
        self.assertEqual(u["primary"]["resets_at"], self.STAMP + 1200)
        self.assertEqual(u["secondary"]["remaining"], 85)
        self.assertEqual(u["secondary"]["window_minutes"], 10080)

    def test_parse_wham_usage(self):
        u = codex_usage.parse_wham_usage({
            "rate_limit": {
                "primary_window": {
                    "used_percent": 100, "limit_window_seconds": 18000, "reset_at": 1795846877,
                },
                "secondary_window": {
                    "used_percent": 81, "limit_window_seconds": 604800, "reset_at": 1796273611,
                },
            }
        })
        self.assertEqual(u["primary"], {
            "remaining": 0, "window_minutes": 300, "resets_at": 1795846877.0,
        })
        self.assertEqual(u["secondary"]["remaining"], 19)
        self.assertEqual(u["secondary"]["window_minutes"], 10080)
        self.assertIsNone(codex_usage.parse_wham_usage({"rate_limit": {}}))

    def test_session_grace_skips_magpie_and_wham(self):
        qpath = self.write({"codex/a": self._entry(used5=99)})
        session_u = {"primary": {"remaining": 75, "window_minutes": 300, "resets_at": self.STAMP + 900}}
        with patch.object(codex_usage, "fetch_wham_usage") as wham:
            u, ts, src = codex_usage.read_usage(
                session_u, self.STAMP + 50, "session", now=self.STAMP + 100, quotas_path=qpath,
            )
        wham.assert_not_called()
        self.assertEqual((u, ts, src), (session_u, self.STAMP + 50, "session"))

    def test_wham_triggers_after_reset_passed_and_backs_off_on_401(self):
        # resetsAt 在 STAMP + 3h，到了 STAMP + 3h + 60s 时应该触发一次 wham 刷新
        qpath = self.write({"codex/a": self._entry(used5=100, held=True)})
        after_reset = self.STAMP + 3 * 3600 + 60

        with patch.object(codex_usage, "fetch_wham_usage", return_value=(None, 401)) as wham:
            u, ts, src = codex_usage.read_usage(None, 0.0, None, now=after_reset, quotas_path=qpath)
        self.assertEqual(wham.call_count, 1)
        # 401 失败时仍保留未超 6h 的 magpie 保守值，不变成 None
        self.assertEqual((u["primary"]["remaining"], ts, src), (0, self.STAMP, "magpie"))

        # 退避期内不再请求 wham
        with patch.object(codex_usage, "fetch_wham_usage") as wham2:
            codex_usage.read_usage(u, ts, src, now=after_reset + 60, quotas_path=qpath)
        wham2.assert_not_called()

        # 过了退避期后请求成功，更新为 wham
        fresh_wham = {"primary": {"remaining": 100, "window_minutes": 300, "resets_at": None}}
        with patch.object(codex_usage, "fetch_wham_usage", return_value=(fresh_wham, 200)) as wham3:
            u3, ts3, src3 = codex_usage.read_usage(
                u, ts, src, now=after_reset + codex_usage.WHAM_AUTH_BACKOFF + 1, quotas_path=qpath,
            )
        self.assertEqual(wham3.call_count, 1)
        self.assertEqual((u3, src3), (fresh_wham, "wham"))
        self.assertGreater(ts3, after_reset)

    def test_codex_tick_does_not_clobber_newer_session_usage(self):
        saved = dict(run.AGENTS["codex"])
        self.addCleanup(lambda: run.AGENTS["codex"].update(saved))
        run.AGENTS["codex"].update(usage=None, usage_at=100.0, usage_source=None)
        newer = {"primary": {"remaining": 88, "window_minutes": 300, "resets_at": 9999}}

        def fake_read(cur_u, cur_at, cur_src):
            # 模拟轮询在锁外执行期间，observe() 收到了更晚的 session rate_limits
            with run._lock:
                run.AGENTS["codex"].update(usage=newer, usage_at=200.0, usage_source="session")
            return {"primary": {"remaining": 10, "window_minutes": 300, "resets_at": 8888}}, 150.0, "magpie"

        with patch.object(codex_usage, "read_usage", side_effect=fake_read):
            run._codex_usage_tick()
        self.assertIs(run.AGENTS["codex"]["usage"], newer)
        self.assertEqual(run.AGENTS["codex"]["usage_source"], "session")


if __name__ == "__main__":
    unittest.main()
