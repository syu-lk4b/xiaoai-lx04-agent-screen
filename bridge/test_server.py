"""HTTP 服务的连接生命周期。

    vendor/mibe/.venv/bin/python -m unittest discover -s bridge -p 'test_*.py' -v
"""
import socket
import threading
import time
import unittest
from unittest.mock import patch

import run


class IdleConnectionTests(unittest.TestCase):
    """音箱夜里熄屏/断网时，toybox nc 来不及发 FIN，Mac 这边留下半开连接。
    没有空闲超时的话每条都占一个 fd 和一个线程，攒满 256 个 fd 后
    读图库、跑 ps 全部 EMFILE——相册页就空了。"""

    def setUp(self):
        patcher = patch.object(run.Handler, "timeout", 0.5)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.server = run._Server(("127.0.0.1", 0), run.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def _closed_by_server(self, sock, within):
        sock.settimeout(within)
        try:
            return sock.recv(1) == b""
        except socket.timeout:
            return False

    def test_silent_connection_is_closed(self):
        with socket.create_connection(("127.0.0.1", self.port)) as s:
            self.assertTrue(self._closed_by_server(s, 3))

    def test_idle_keepalive_after_request_is_closed(self):
        with socket.create_connection(("127.0.0.1", self.port)) as s:
            # 必须是放行的 200 请求：403/404 走 send_error，会自带 Connection: close
            # 当场断开，测不到空闲超时。Host 校验要求端口是 8477，与实际监听端口无关。
            s.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1:8477\r\n\r\n")
            s.settimeout(3)
            head = s.recv(4096)
            self.assertTrue(head.startswith(b"HTTP/1.1 200"), head[:40])
            self.assertNotIn(b"connection: close", head.lower())
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if self._closed_by_server(s, deadline - time.monotonic()):
                    return
            self.fail("空闲的 keep-alive 连接没有被关闭")

    def test_timeout_errors_are_not_logged(self):
        with patch("socketserver.BaseServer.handle_error") as base:
            try:
                raise TimeoutError
            except TimeoutError:
                self.server.handle_error(None, ("127.0.0.1", 1))
            base.assert_not_called()


if __name__ == "__main__":
    unittest.main()
