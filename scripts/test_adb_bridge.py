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
            self.assertIn(("-s", "lx04", "shell", "input", "text", bridge.URL), commands)

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


if __name__ == "__main__":
    unittest.main()
