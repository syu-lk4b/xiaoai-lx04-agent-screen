"""断连不重建、代理身份保护的回归检查。"""
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("adb_bridge", Path(__file__).with_name("adb-bridge.py"))
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class BridgeRecoveryTests(unittest.TestCase):
    def check_health(self, samples, reconnect=False):
        with patch.object(bridge, "connected_lx04", return_value=None if reconnect else "lx04"), \
                patch.object(bridge, "saved_endpoint", return_value="192.168.1.50:5555"), \
                patch.object(bridge, "connect", return_value="lx04"), \
                patch.object(bridge, "mac_ip", return_value="192.168.1.23"), \
                patch.object(bridge, "proxy_ready", side_effect=samples), \
                patch.object(bridge, "install_proxy", return_value=True) as install, \
                patch.object(bridge, "save_endpoint"), \
                patch.object(bridge, "config", return_value={"serialno": "SN1"}), \
                patch.object(bridge, "open_status") as open_page, \
                patch.object(bridge, "run") as adb, patch.object(bridge.time, "sleep"):
            result = bridge.ensure()
            open_page.assert_not_called()
            adb.assert_not_called()
            return result, install.call_count

    def test_unknown_adb_state_does_not_rebuild(self):
        self.assertEqual(self.check_health([None]), (None, 0))

    def test_transient_missing_listener_does_not_rebuild(self):
        self.assertEqual(self.check_health([False, True]), ("lx04", 0))

    def test_unknown_interrupts_failure_confirmation(self):
        self.assertEqual(self.check_health([False, None]), (None, 0))

    def test_three_confirmed_failures_rebuild_once(self):
        self.assertEqual(self.check_health([False, False, False]), ("lx04", 1))

    def test_transport_reconnect_does_not_touch_browser(self):
        self.assertEqual(self.check_health([True], reconnect=True), ("lx04", 0))

    def test_failed_ss_is_unknown(self):
        with patch.object(bridge, "run", return_value=subprocess.CompletedProcess([], 1, "", "timeout")):
            self.assertIsNone(bridge.proxy_ready("lx04", "192.168.1.23"))

    def test_explicit_open_reuses_tab(self):
        with patch.object(bridge, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as adb, \
                patch.object(bridge.time, "sleep"):
            self.assertTrue(bridge.open_status("lx04"))
            commands = [call.args for call in adb.call_args_list]
            self.assertFalse(any("android.intent.action.VIEW" in command for command in commands))
            typed = [c[-1] for c in commands if c[3:5] == ("input", "text")]
            self.assertEqual(len(typed), 1)
            self.assertTrue(typed[0].startswith(bridge.URL))

    def test_explicit_open_forces_reload(self):
        # Via 对同一个 URL 回车不会重新加载，每次打开的地址都得不一样
        typed = []
        for now in (1000.0, 1001.0):
            with patch.object(bridge, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as adb, \
                    patch.object(bridge.time, "sleep"), patch.object(bridge.time, "time", return_value=now):
                self.assertTrue(bridge.open_status("lx04"))
                typed += [c.args[-1] for c in adb.call_args_list if c.args[3:5] == ("input", "text")]
        self.assertEqual(len(typed), 2)
        self.assertNotEqual(typed[0], typed[1])
        # input text 经过设备上的 sh：& ; 空格这类字符会截断或拆开命令
        for url in typed:
            self.assertRegex(url, r"^[A-Za-z0-9:/.?=]+$")

    def test_observer_distinguishes_browser_restart_from_device_boot(self):
        boot_a = "11111111-1111-1111-1111-111111111111"
        boot_b = "22222222-2222-2222-2222-222222222222"
        samples = [subprocess.CompletedProcess([], 0, output, "") for output in
                   (f"{boot_a}\n100\n", f"{boot_a}\n200\n", f"{boot_b}\n300\n")]
        with patch.object(bridge, "run", side_effect=samples), patch.object(bridge, "log") as log:
            state = bridge.observe_device("lx04", None)
            state = bridge.observe_device("lx04", state)
            bridge.observe_device("lx04", state)
            self.assertIn("整机未重启", log.call_args_list[1].args[0])
            self.assertIn("整机重新启动", log.call_args_list[2].args[0])

    def check_pid_guard(self, command_line):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pid_file = root / "proxy.pid"
            pid_file.write_text("456\n")
            proc = root / "proc" / "456"
            proc.mkdir(parents=True)
            (proc / "cmdline").write_bytes(command_line)
            killed = root / "killed"

            def execute(*args):
                command = args[-1].replace("/proc/", str(root / "proc") + "/")
                functions = (
                    f"kill() {{ echo \"$*\" >> '{killed}'; }}; "
                    "sleep() { :; }; ss() { :; }; nohup() { :; }; "
                )
                return subprocess.run(["/bin/sh", "-c", functions + command], capture_output=True, text=True)

            with patch.object(bridge, "PID", str(pid_file)), \
                    patch.object(bridge, "TARGET", str(root / "target")), \
                    patch.object(bridge, "LOG", str(root / "proxy.log")), \
                    patch.object(bridge, "run", side_effect=execute), \
                    patch.object(bridge, "proxy_ready", return_value=True):
                self.assertTrue(bridge.install_proxy("lx04", "192.168.1.23"))
            return killed.read_text().strip() if killed.exists() else None

    def test_stale_pid_does_not_kill_unrelated_process(self):
        self.assertIsNone(self.check_pid_guard(b"/system/bin/sleep\x0060\x00"))

    def test_owned_proxy_can_be_replaced(self):
        command = "/system/bin/toybox nc -s 127.0.0.1 -p 8477 -L /system/bin/toybox nc 192.168.1.10 8477"
        self.assertEqual(self.check_pid_guard(command.replace(" ", "\0").encode() + b"\0"), "456")



class SpeakerIdentityTests(unittest.TestCase):
    """局域网里的假 adbd 能自称 mi_lx04，但不知道音箱的序列号，不能被登记成音箱。"""

    def adb(self, product="mi_lx04", number="SN1"):
        def fake(*args, timeout=8):
            value = {"ro.product.device": product, "ro.serialno": number}[args[-1]]
            return subprocess.CompletedProcess(args, 0, value + "\n", "")
        return patch.object(bridge, "run", side_effect=fake)

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.state = patch.object(bridge, "STATE", Path(self.dir.name) / "adb-endpoint.json")
        self.state.start()

    def tearDown(self):
        self.state.stop()
        self.dir.cleanup()

    def test_impostor_with_wrong_serial_is_rejected(self):
        bridge.STATE.write_text('{"endpoint": "192.168.1.50:5555", "serialno": "SN1"}')
        with self.adb(number="FAKE"):
            self.assertFalse(bridge.is_lx04("192.168.1.66:5555"))
        with self.adb(number="SN1"):
            self.assertTrue(bridge.is_lx04("192.168.1.51:5555"))

    def test_other_products_are_rejected(self):
        with self.adb(product="sdm845"):
            self.assertFalse(bridge.is_lx04("192.168.1.66:5555"))

    def test_first_speaker_is_pinned_and_later_saves_keep_the_pin(self):
        with self.adb(number="SN1"):
            bridge.save_endpoint("192.168.1.50:5555")
        with self.adb(number="SN2"):
            bridge.save_endpoint("192.168.1.51:5555")
        self.assertEqual(bridge.config(), {"endpoint": "192.168.1.51:5555", "serialno": "SN1"})

    def test_setup_can_repin_a_replacement_speaker(self):
        bridge.STATE.write_text('{"serialno": "SN1", "sleep": "off"}')
        with self.adb(number="SN2"):
            bridge.save_endpoint("192.168.1.52:5555", repin=True)
        self.assertEqual(bridge.config(), {"serialno": "SN2", "sleep": "off", "endpoint": "192.168.1.52:5555"})


class SleepScheduleTests(unittest.TestCase):
    NIGHT = (23 * 60, 7 * 60)

    def test_parse_window(self):
        self.assertEqual(bridge.parse_window("23:00-07:00"), self.NIGHT)
        self.assertEqual(bridge.parse_window(" 1:30 - 6:05 "), (90, 365))
        for bad in ("off", "", "25:00-07:00", "23:00", "07:00-07:00", None):
            self.assertIsNone(bridge.parse_window(bad), bad)

    def test_window_across_midnight(self):
        hm = lambda h, m=0: h * 60 + m
        self.assertTrue(bridge.in_window(self.NIGHT, hm(23)))
        self.assertTrue(bridge.in_window(self.NIGHT, hm(3)))
        self.assertFalse(bridge.in_window(self.NIGHT, hm(7)))
        self.assertFalse(bridge.in_window(self.NIGHT, hm(22, 59)))
        self.assertTrue(bridge.in_window((60, 300), hm(2)))
        self.assertFalse(bridge.in_window((60, 300), hm(23)))

    def test_sleeps_once_per_boot_and_lets_people_wake_it(self):
        step = lambda minute, slept, boot="b1": bridge.sleep_step(self.NIGHT, minute, boot, slept)
        self.assertEqual(step(23 * 60, None), ("sleep", "b1"))
        self.assertEqual(step(23 * 60 + 5, "b1"), (None, "b1"))   # 有人点亮了，不再关
        self.assertEqual(step(2 * 60, "b1", boot="b2"), ("sleep", "b2"))  # 半夜重启后再关
        self.assertEqual(step(7 * 60, "b2"), ("wake", None))
        self.assertEqual(step(12 * 60, None), (None, None))      # 白天不碰屏幕

    def test_disabled_schedule_never_touches_screen(self):
        self.assertEqual(bridge.sleep_step(None, 0, "b1", None), (None, None))
        self.assertEqual(bridge.sleep_step(None, 0, "b1", "b1"), ("wake", None))  # 夜里关掉配置：叫醒

    def test_config_reads_sleep_key(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "adb-endpoint.json"
            with patch.object(bridge, "STATE", state):
                self.assertEqual(bridge.sleep_window(), self.NIGHT)  # 没配置用默认
                state.write_text('{"sleep": "off"}')
                self.assertIsNone(bridge.sleep_window())
                state.write_text('{"sleep": "00:30-06:30"}')
                self.assertEqual(bridge.sleep_window(), (30, 390))


if __name__ == "__main__":
    unittest.main()
