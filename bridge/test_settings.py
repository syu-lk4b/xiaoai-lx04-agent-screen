"""设置模块单元测试：页面顺序、开关保底、参数边界、持久化与熄屏同步。

    vendor/mibe/.venv/bin/python -m unittest discover -s bridge -p 'test_*.py' -v
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        root = Path(self.dir.name)
        self.settings_file = root / "settings.json"
        self.endpoint_file = root / "adb-endpoint.json"
        p1 = patch.object(settings, "SETTINGS_FILE", self.settings_file)
        p2 = patch.object(settings, "ENDPOINT_FILE", self.endpoint_file)
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def test_defaults_when_no_file(self):
        cfg = settings.read()
        self.assertEqual([p["id"] for p in cfg["pages"]], list(settings.PAGE_IDS))
        self.assertTrue(all(p["enabled"] for p in cfg["pages"]))
        self.assertEqual(cfg["sleep"], "23:00-07:00")

    def test_reorder_and_missing_pages_filled(self):
        cfg = settings.save({
            "pages": [
                {"id": "photo", "enabled": True, "dwell": 180},
                {"id": "codex", "enabled": False, "dwell": 45},
            ]
        })
        ids = [p["id"] for p in cfg["pages"]]
        self.assertEqual(ids, ["photo", "codex", "claude", "sys", "mac"])
        self.assertEqual(cfg["pages"][0]["dwell"], 180)
        self.assertFalse(cfg["pages"][1]["enabled"])

    def test_all_disabled_keeps_first_page_enabled(self):
        cfg = settings.save({
            "pages": [{"id": pid, "enabled": False, "dwell": 60} for pid in settings.PAGE_IDS]
        })
        self.assertTrue(cfg["pages"][0]["enabled"])
        self.assertFalse(any(p["enabled"] for p in cfg["pages"][1:]))

    def test_sleep_syncs_to_endpoint_preserving_other_keys(self):
        self.endpoint_file.write_text(
            json.dumps({"endpoint": "192.168.1.50:5555", "serialno": "ABC123"}),
            encoding="utf-8",
        )
        cfg = settings.save({"sleep": "23:30-06:45"})
        self.assertEqual(cfg["sleep"], "23:30-06:45")
        ep = json.loads(self.endpoint_file.read_text(encoding="utf-8"))
        self.assertEqual(ep["endpoint"], "192.168.1.50:5555")
        self.assertEqual(ep["serialno"], "ABC123")
        self.assertEqual(ep["sleep"], "23:30-06:45")

    def test_invalid_sleep_falls_back_to_default(self):
        for bad in ("25:00-07:00", "23:00-23:00", "garbage", 123):
            self.assertEqual(settings.normalize({"sleep": bad})["sleep"], "23:00-07:00")
        self.assertEqual(settings.normalize({"sleep": "off"})["sleep"], "off")

    def test_focus_increments_seq_and_enables_target_page(self):
        settings.save({
            "pages": [
                {"id": "codex", "enabled": True, "dwell": 60},
                {"id": "sys", "enabled": False, "dwell": 60},
            ]
        })
        before = settings.read()["focus"]["seq"]
        cfg = settings.focus("sys")
        self.assertEqual(cfg["focus"]["page"], "sys")
        self.assertEqual(cfg["focus"]["seq"], before + 1)
        sys_page = next(p for p in cfg["pages"] if p["id"] == "sys")
        self.assertTrue(sys_page["enabled"])
        self.assertIsNone(settings.focus("nonexistent"))

    def test_mac_items_accepts_builtins_and_valid_apps_rejects_traversal(self):
        cfg = settings.save({
            "mac_items": [
                {"id": "display_sleep", "name": "关屏幕"},
                {"id": "safari", "name": "浏览器", "path": "/Applications/Safari.app"},
                {"id": "../../etc/passwd", "name": "BadId", "path": "/Applications/Ghostty.app"},
                {"id": "evil", "name": "Evil", "path": "/tmp/Evil.app"},
                {"id": "trav", "name": "Trav", "path": "/Applications/../etc/passwd.app"},
            ],
            "mac_volume_bar": False,
        })
        self.assertEqual(cfg["mac_items"], [
            {"id": "display_sleep", "name": "关屏幕"},
            {"id": "safari", "name": "浏览器", "path": "/Applications/Safari.app"},
            {"id": "ghostty", "name": "BadId", "path": "/Applications/Ghostty.app"},
        ])
        self.assertFalse(cfg["mac_volume_bar"])
        self.assertEqual(self.settings_file.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
