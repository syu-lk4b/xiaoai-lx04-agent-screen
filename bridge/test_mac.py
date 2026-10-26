"""Mac 控制页的白名单与图标检查。不真的熄屏、开应用。

    vendor/mibe/.venv/bin/python -m unittest discover -s bridge -p 'test_*.py' -v
"""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import mac


class ActionTests(unittest.TestCase):
    def test_unknown_action_never_runs(self):
        with patch.object(mac.subprocess, "run") as run:
            self.assertEqual(mac.run_action("rm -rf /"), (False, "未知动作"))
            run.assert_not_called()

    def test_whitelisted_command(self):
        done = subprocess.CompletedProcess([], 0, "", "")
        with patch.object(mac.subprocess, "run", return_value=done) as run:
            self.assertEqual(mac.run_action("ghostty"), (True, "已执行"))
            self.assertEqual(run.call_args[0][0], ["open", "/Applications/Ghostty.app"])

    def test_failure_reported(self):
        failed = subprocess.CompletedProcess([], 1, "", "boom")
        with patch.object(mac.subprocess, "run", return_value=failed):
            self.assertEqual(mac.run_action("claude"), (False, "执行失败"))

    def test_awake_toggle_really_spawns_and_stops(self):
        self.assertFalse(mac.state()["awake"])
        self.assertEqual(mac.run_action("awake"), (True, "防休眠已开启"))
        self.assertTrue(mac.state()["awake"])
        self.assertEqual(mac.run_action("awake"), (True, "防休眠已关闭"))
        self.assertFalse(mac.state()["awake"])

    def test_custom_configured_app_runs_open(self):
        done = subprocess.CompletedProcess([], 0, "", "")
        custom = [{"id": "safari", "name": "Safari", "path": "/Applications/Safari.app"}]
        with patch.object(mac.settings, "read", return_value={"mac_items": custom}), \
                patch.object(mac.subprocess, "run", return_value=done) as run:
            self.assertEqual(mac.run_action("safari"), (True, "已执行"))
            self.assertEqual(run.call_args[0][0], ["open", "/Applications/Safari.app"])

    def test_volume_controls_system_and_fallback_to_music(self):
        done = subprocess.CompletedProcess([], 0, "", "")
        with patch.object(mac, "_read_volume", return_value=(40, False, "system")), \
                patch.object(mac.subprocess, "run", return_value=done) as run:
            self.assertEqual(mac.run_action("volume_up"), (True, "音量 50%"))
            self.assertIn("output volume 50", run.call_args[0][0][-1])
            self.assertEqual(mac.run_action("volume_mute"), (True, "已静音"))
            self.assertTrue(mac.state()["muted"])

        with patch.object(mac, "_read_volume", return_value=(30, False, "music")), \
                patch.object(mac.subprocess, "run", return_value=done) as run:
            self.assertEqual(mac.run_action("volume_down"), (True, "Music 音量 20%"))
            self.assertIn("sound volume to 20", run.call_args[0][0][-1])


class IconTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        patcher = patch.object(mac, "ICON_DIR", Path(self.dir.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_unknown_name_never_touches_disk(self):
        # 名字来自 URL，不在表里的一律不处理，路径穿越无从谈起
        with patch.object(mac.subprocess, "run") as run:
            self.assertIsNone(mac.icon("../../etc/passwd"))
            run.assert_not_called()

    def test_real_system_icon_converts_and_caches(self):
        png = mac.icon("awake")  # 系统自带的 Clock.icns，每台 Mac 都有
        self.assertTrue(png.startswith(b"\x89PNG"))
        with patch.object(mac.subprocess, "run") as run:
            self.assertEqual(mac.icon("awake"), png)
            run.assert_not_called()  # 第二次走缓存

    def test_missing_app_returns_none(self):
        with patch.dict(mac.APPS, {"ghostty": "/nonexistent/Ghostty.app"}):
            self.assertIsNone(mac.icon("ghostty"))

    def test_app_icon_from_info_plist(self):
        # Music 是系统自带应用，每台 Mac 都有；它的 CFBundleIconFile 不带 .icns 后缀
        src = mac._app_icon(mac.APPS["music"])
        self.assertEqual(src.suffix, ".icns")
        self.assertTrue(src.is_file())
        self.assertTrue(mac.icon("music").startswith(b"\x89PNG"))

    def test_every_app_has_a_command(self):
        for name, path in mac.APPS.items():
            self.assertEqual(mac.COMMANDS[name], ["open", path])


if __name__ == "__main__":
    unittest.main()
