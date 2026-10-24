#!/usr/bin/env python3
"""谁能访问 :8477。所有请求都过这里，不只是 Mac 控制。

服务必须监听 0.0.0.0：无线 ADB 下，音箱上的 netcat 代理要经局域网连到 Mac。
所以「监听」管不住，只能在请求里看来源：
- 来源 IP：本机、Mac 自己的局域网地址、adb-bridge 登记的音箱，或 LX04_CONTROL_ALLOW
- Host：只认 localhost 和 IP 字面量。DNS 重绑定的 Host 一定是攻击者的域名，
  不校验的话，Mac 浏览器里的恶意网页能「同源」读状态、点按钮
写操作（POST）另要求自定义请求头 HEADER：它会触发 CORS 预检，服务端不应答预检，
跨站网页就没法借浏览器发出来。
"""
import ipaddress
import json
import os
import sys
from pathlib import Path

ENDPOINT = Path.home() / "Library" / "Application Support" / "lx04-status" / "adb-endpoint.json"
HEADER = "X-LX04-Action"
PORT = 8477


def _extra_hosts():
    """adb-bridge 记下的音箱地址 + LX04_CONTROL_ALLOW（逗号分隔）。每次现读，音箱换 IP 立即生效。"""
    hosts = {h.strip() for h in os.environ.get("LX04_CONTROL_ALLOW", "").split(",") if h.strip()}
    try:
        endpoint = json.loads(ENDPOINT.read_text()).get("endpoint") or ""
        hosts.add(endpoint.rsplit(":", 1)[0])
    except FileNotFoundError:
        pass
    except (OSError, ValueError, AttributeError) as exc:
        print(f"[access] 读取音箱地址失败：{exc}", file=sys.stderr)
    return hosts


def host_ok(host):
    """Host 头必须是 localhost 或 IP 字面量，端口必须是 PORT。"""
    if not host:
        return False
    host = host.strip().lower()
    if host.startswith("["):  # [::1]:8477
        name, _, rest = host[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else ""
    else:
        name, _, port = host.rpartition(":")
        if not name:  # 没写端口
            name, port = port, ""
    if port != str(PORT):
        return False
    if name == "localhost":
        return True
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False


def client_ok(client_ip, local_ip=None):
    """本机（含 USB adb reverse 转进来的）、Mac 自己的局域网地址，或已登记的音箱。"""
    try:
        if ipaddress.ip_address(client_ip).is_loopback:
            return True
    except ValueError:
        return False
    # 从 Mac 自己用局域网地址访问时，来源 IP 就是本机网卡地址
    if local_ip and client_ip == local_ip:
        return True
    return client_ip in _extra_hosts()


def allowed(client_ip, host, local_ip=None):
    return host_ok(host) and client_ok(client_ip, local_ip)
