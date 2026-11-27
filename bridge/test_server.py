"""HTTP 服务的连接生命周期。

    vendor/mibe/.venv/bin/python -m unittest discover -s bridge -p 'test_*.py' -v

前两组要绑 loopback 端口；在禁止 bind 的沙箱里会报 PermissionError，不是代码问题。
"""
import socket
import threading
import time
import unittest
from unittest.mock import patch

import run

TIMEOUT = 0.5
HOST = b"Host: 127.0.0.1:8477\r\n"  # Host 校验要求端口是 8477，与实际监听端口无关


def _read_response(sock):
    """读完一个响应，返回 (头, 体)。按 Content-Length 读，不靠对端关连接。"""
    buf = b""
    while b"\r\n\r\n" not in buf:
        part = sock.recv(65536)
        if not part:
            raise AssertionError("响应头没读完连接就断了")
        buf += part
    head, _, body = buf.partition(b"\r\n\r\n")
    length = next(int(line.split(b":", 1)[1]) for line in head.split(b"\r\n")
                  if line.lower().startswith(b"content-length:"))
    while len(body) < length:
        part = sock.recv(65536)
        if not part:
            break  # 被截断，交给调用方比长度
        body += part
    return head, body


class _SmallBufferHandler(run.Handler):
    # loopback 的发送缓冲会自动长到好几 MB，整个 mp4 一下塞进内核，sendall 瞬间返回，
    # 测不出写超时。压小缓冲，模拟音箱 Wi-Fi 这种真正的慢链路。
    def setup(self):
        self.request.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 16 * 1024)
        super().setup()


class _ServerCase(unittest.TestCase):
    handler = run.Handler

    def setUp(self):
        patcher = patch.object(run.Handler, "timeout", TIMEOUT)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.server = run._Server(("127.0.0.1", 0), self.handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def _connect(self, rcvbuf=None):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(s.close)
        if rcvbuf:  # 必须在 connect 前设，握手时就定了窗口
            s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
        s.connect(("127.0.0.1", self.port))
        return s

    @staticmethod
    def _wait_eof(sock, within):
        sock.settimeout(within)
        try:
            return sock.recv(1) == b""
        except socket.timeout:
            return False


class IdleConnectionTests(_ServerCase):
    """音箱夜里熄屏/断网时，toybox nc 来不及发 FIN，Mac 这边留下半开连接。
    没有空闲超时的话每条都占一个 fd 和一个线程，攒满 256 个 fd 后
    读图库、跑 ps 全部 EMFILE——相册页就空了。"""

    def test_silent_connection_is_closed(self):
        self.assertTrue(self._wait_eof(self._connect(), 3))

    def test_idle_keepalive_after_request_is_closed_by_timeout(self):
        s = self._connect()
        # 必须是放行的 200 请求：403/404 走 send_error，会自带 Connection: close
        # 当场断开，测不到空闲超时
        s.sendall(b"GET / HTTP/1.1\r\n" + HOST + b"\r\n")
        s.settimeout(3)
        head, _ = _read_response(s)
        self.assertTrue(head.startswith(b"HTTP/1.1 200"), head[:40])
        self.assertNotIn(b"connection: close", head.lower())
        start = time.monotonic()
        self.assertTrue(self._wait_eof(s, 3), "空闲的 keep-alive 连接没有被关闭")
        # 是超时关的，不是响应完立刻关的
        self.assertGreaterEqual(time.monotonic() - start, TIMEOUT * 0.8)


class SlowReaderTests(_ServerCase):
    handler = _SmallBufferHandler

    def setUp(self):
        super().setUp()
        # 超时从 60 秒缩到 0.5 秒，块也得跟着缩：小缓冲下 recv 每次只回几 KB，
        # 64KB 一块在 0.5 秒里刚好擦边。线上是 64KB/60 秒，约 1KB/s 才会断。
        patcher = patch.object(run.Handler, "WRITE_CHUNK", 8 * 1024)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_slow_but_steady_reader_gets_whole_video(self):
        """Chromium 按播放进度慢慢读视频，整段读完的总时长远超超时。
        一次 sendall 发完会在总时长到点时被截断；分块写只要一直有进展就不断。"""
        expected = (run.DISPLAY / "canyon.mp4").stat().st_size
        s = self._connect(rcvbuf=16 * 1024)
        s.sendall(b"GET /canyon.mp4 HTTP/1.1\r\n" + HOST + b"Range: bytes=0-\r\n\r\n")
        s.settimeout(TIMEOUT * 4)
        buf, start = b"", time.monotonic()
        while True:
            try:
                part = s.recv(16 * 1024)
            except socket.timeout:
                break
            if not part:
                break
            buf += part
            head, sep, body = buf.partition(b"\r\n\r\n")
            if sep and len(body) >= expected:
                break
            time.sleep(0.03)  # 每次间隔远小于超时，总时长远大于超时
        elapsed = time.monotonic() - start
        self.assertTrue(head.startswith(b"HTTP/1.1 206"), head[:40])
        self.assertGreater(elapsed, TIMEOUT * 1.5, "读得太快，测不到总时长超时")
        self.assertEqual(len(body), expected)


class HandleErrorTests(unittest.TestCase):
    """不起 socket，沙箱里也能跑。"""

    def setUp(self):
        self.server = run._Server.__new__(run._Server)  # 不 bind，只测 handle_error

    def _logged(self, exc):
        with patch("socketserver.BaseServer.handle_error") as base:
            try:
                raise exc
            except type(exc):
                self.server.handle_error(None, ("127.0.0.1", 1))
            return base.called

    def test_peer_gone_is_quiet(self):
        for exc in (BrokenPipeError(), ConnectionResetError(), ConnectionAbortedError()):
            self.assertFalse(self._logged(exc), type(exc).__name__)

    def test_real_bugs_still_logged(self):
        # ConnectionRefusedError 是出站连接失败，不是对端走了，不能吞
        for exc in (ValueError(), ConnectionRefusedError(), OSError()):
            self.assertTrue(self._logged(exc), type(exc).__name__)


if __name__ == "__main__":
    unittest.main()
