"""谁能访问 :8477 的检查。

    vendor/mibe/.venv/bin/python -m unittest discover -s bridge -p 'test_*.py' -v
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import access

HOST = "127.0.0.1:8477"
MAC_IP = "192.168.1.23"


class HostTests(unittest.TestCase):
    def test_localhost_and_ip_literals(self):
        for host in ("127.0.0.1:8477", "localhost:8477", "LOCALHOST:8477",
                     "192.168.1.23:8477", "[::1]:8477"):
            self.assertTrue(access.host_ok(host), host)

    def test_rebinding_domain_rejected(self):
        # DNS 重绑定：来源是本机，但 Host 是攻击者的域名
        for host in ("evil.example:8477", "127.0.0.1.nip.io:8477", "evil.example"):
            self.assertFalse(access.host_ok(host), host)

    def test_wrong_or_missing_port_rejected(self):
        for host in (None, "", "127.0.0.1", "127.0.0.1:80", "[::1]"):
            self.assertFalse(access.host_ok(host), host)


class ClientTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"endpoint": "192.168.1.50:5555"}, tmp)
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink)
        patcher = patch.object(access, "ENDPOINT", Path(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_loopback(self):
        self.assertTrue(access.allowed("127.0.0.1", HOST))
        self.assertTrue(access.allowed("::1", "localhost:8477"))

    def test_mac_own_lan_address(self):
        self.assertTrue(access.allowed(MAC_IP, f"{MAC_IP}:8477", local_ip=MAC_IP))

    def test_registered_speaker(self):
        self.assertTrue(access.allowed("192.168.1.50", HOST, local_ip=MAC_IP))

    def test_other_lan_device_rejected(self):
        self.assertFalse(access.allowed("192.168.1.66", f"{MAC_IP}:8477", local_ip=MAC_IP))

    def test_loopback_with_rebinding_host_rejected(self):
        self.assertFalse(access.allowed("127.0.0.1", "evil.example:8477"))

    def test_env_allowlist(self):
        with patch.dict("os.environ", {"LX04_CONTROL_ALLOW": "10.0.0.9, 10.0.0.10"}):
            self.assertTrue(access.allowed("10.0.0.10", HOST))

    def test_missing_endpoint_file(self):
        with patch.object(access, "ENDPOINT", Path("/nonexistent/x.json")):
            self.assertFalse(access.allowed("192.168.1.50", HOST))
            self.assertTrue(access.allowed("127.0.0.1", HOST))

    def test_garbage_ip_rejected(self):
        self.assertFalse(access.allowed("not-an-ip", HOST))

    def test_mac_only_rejects_speaker_and_env_allowlist(self):
        self.assertTrue(access.mac_only("127.0.0.1", HOST))
        self.assertTrue(access.mac_only("::1", "localhost:8477"))
        self.assertTrue(access.mac_only(MAC_IP, f"{MAC_IP}:8477", local_ip=MAC_IP))
        # 已登记音箱与额外放行设备能看状态页，但不能进 /setting
        self.assertFalse(access.mac_only("192.168.1.50", HOST, local_ip=MAC_IP))
        with patch.dict("os.environ", {"LX04_CONTROL_ALLOW": "10.0.0.10"}):
            self.assertFalse(access.mac_only("10.0.0.10", HOST, local_ip=MAC_IP))


if __name__ == "__main__":
    unittest.main()
