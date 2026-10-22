#!/usr/bin/env python3
"""维护 LX04 localhost:8477 到 Mac 状态服务的本机代理。"""
import argparse
import ipaddress
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ADB_PORT = 5555
STATUS_PORT = 8477
INTERVAL = 10
PRODUCT = "mi_lx04"
URL = f"http://127.0.0.1:{STATUS_PORT}/"
STATE = Path.home() / "Library" / "Application Support" / "lx04-status" / "adb-endpoint.json"
PID = "/data/local/tmp/lx04-status-proxy.pid"
TARGET = "/data/local/tmp/lx04-status-proxy.target"
LOG = "/data/local/tmp/lx04-status-proxy.log"
TOYBOX = "/system/bin/toybox"
ADB = next((p for p in ("/opt/homebrew/bin/adb", shutil.which("adb")) if p and Path(p).exists()), None)


def log(message):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [adb-bridge] {message}", flush=True)


def run(*args, timeout=8):
    if not ADB:
        return None
    try:
        return subprocess.run([ADB, *args], text=True, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def devices(text):
    return [line.split()[0] for line in text.splitlines()[1:]
            if len(line.split()) >= 2 and line.split()[1] == "device"]


def is_lx04(serial):
    result = run("-s", serial, "shell", "getprop", "ro.product.device", timeout=3)
    return bool(result and result.returncode == 0 and result.stdout.strip() == PRODUCT)


def connected_lx04():
    result = run("devices", "-l")
    if not result:
        return None
    for serial in devices(result.stdout):
        if is_lx04(serial):
            return serial
    return None


def saved_endpoint():
    try:
        endpoint = json.loads(STATE.read_text()).get("endpoint")
        host, port = endpoint.rsplit(":", 1)
        ipaddress.ip_address(host)
        return endpoint if port == str(ADB_PORT) else None
    except (OSError, ValueError, AttributeError, json.JSONDecodeError):
        return None


def connect(endpoint):
    result = run("connect", endpoint, timeout=5)
    return endpoint if result and is_lx04(endpoint) else None


def mac_ip():
    try:
        configured = json.loads(STATE.read_text()).get("mac_ip")
        address = ipaddress.ip_address(configured) if configured else None
        if address and address.version == 4 and address.is_private:
            return str(address)
    except (OSError, ValueError, AttributeError, json.JSONDecodeError):
        pass
    try:
        result = subprocess.run(["ipconfig", "getifaddr", "en0"], text=True,
                                capture_output=True, timeout=3)
        host = result.stdout.strip()
        return host if result.returncode == 0 and ipaddress.ip_address(host).is_private else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def local_network():
    host = mac_ip()
    return ipaddress.ip_network(f"{host}/24", strict=False) if host else None


def port_open(host):
    try:
        with socket.create_connection((host, ADB_PORT), timeout=0.25):
            return host
    except OSError:
        return None


def scan_lx04():
    network = local_network()
    if not network:
        return None
    with ThreadPoolExecutor(max_workers=32) as pool:
        futures = [pool.submit(port_open, str(host)) for host in network.hosts()]
        for future in as_completed(futures):
            host = future.result()
            if host:
                endpoint = f"{host}:{ADB_PORT}"
                if connect(endpoint):
                    return endpoint
    return None


def save_endpoint(endpoint):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    try:
        config = json.loads(STATE.read_text())
        if not isinstance(config, dict):
            config = {}
    except (OSError, ValueError):
        config = {}
    config["endpoint"] = endpoint
    STATE.write_text(json.dumps(config) + "\n")
    os.chmod(STATE, 0o600)


def proxy_ready(serial, target):
    listening = run("-s", serial, "shell", "ss", "-ltn")
    # ADB 超时只表示状态未知，不能据此结束正常代理。
    if listening is None or listening.returncode != 0:
        return None
    if f"127.0.0.1:{STATUS_PORT} " not in listening.stdout:
        return False
    configured = run("-s", serial, "shell", "cat", TARGET)
    if configured is None:
        return None
    return configured.returncode == 0 and configured.stdout.strip() == target


def install_proxy(serial, target):
    target = str(ipaddress.IPv4Address(target))
    prefix = f"{TOYBOX} nc -s 127.0.0.1 -p {STATUS_PORT} -L {TOYBOX} nc "
    command = (
        f"old=$(cat {PID} 2>/dev/null); "
        "case \"$old\" in ''|*[!0-9]*) ;; *) "
        'actual=$(tr \'\\000\' \' \' <"/proc/$old/cmdline" 2>/dev/null); '
        f'case "$actual" in {shlex.quote(prefix)}*) '
        'kill "$old" 2>/dev/null; sleep 1 ;; esac ;; esac; '
        # PID 文件跨重启保留，先核对进程身份；不接管未知来源的监听端口。
        f"if ss -ltn | grep -q '127.0.0.1:{STATUS_PORT} '; then exit 1; fi; "
        f"nohup {TOYBOX} nc -s 127.0.0.1 -p {STATUS_PORT} -L "
        f"{TOYBOX} nc {target} {STATUS_PORT} >{LOG} 2>&1 & "
        f"echo $! >{PID}; echo {target} >{TARGET}"
    )
    result = run("-s", serial, "shell", command)
    return bool(result and result.returncode == 0 and proxy_ready(serial, target) is True)


def server_alive():
    try:
        with socket.create_connection(("127.0.0.1", STATUS_PORT), timeout=1):
            return True
    except OSError:
        return False


def open_status(serial):
    # Via 把每次 ACTION_VIEW 都当成新页签，浏览器标准的复用 extra 在真机上无效。
    # MAIN 先回到原页签；搜索键选中地址栏，在当前页里输入固定地址。
    commands = [
        ("am", "start", "-W", "-a", "android.intent.action.MAIN", "-c",
         "android.intent.category.LAUNCHER", "-n", "mark.via.gp/mark.via.Shell"),
        ("input", "keyevent", "84"),
        ("input", "text", URL),
        ("input", "keyevent", "66"),
    ]
    for command in commands:
        result = run("-s", serial, "shell", *command)
        if result is None or result.returncode != 0:
            return False
        if command == ("input", "keyevent", "84"):
            time.sleep(0.3)
    return True


def observe_device(serial, previous):
    result = run("-s", serial, "shell",
                 "cat /proc/sys/kernel/random/boot_id; pidof mark.via.gp")
    if result is None:
        return previous
    lines = result.stdout.strip().splitlines()
    if not lines or len(lines[0]) != 36:
        return previous
    state = (lines[0], lines[1].strip() if len(lines) > 1 else "未运行")
    if previous is None:
        log(f"设备基线：boot={state[0]}，Via PID={state[1]}")
    elif state[0] != previous[0]:
        log(f"检测到整机重新启动：boot={state[0]}，Via PID={state[1]}")
    elif state[1] != previous[1]:
        log(f"Via 进程变化（整机未重启）：{previous[1]} -> {state[1]}")
    return state


def ensure():
    serial = connected_lx04()
    recovered = False
    if not serial:
        endpoint = saved_endpoint()
        serial = connect(endpoint) if endpoint else None
        recovered = bool(serial)
    if not serial:
        serial = scan_lx04()
        recovered = bool(serial)
    if not serial:
        return None

    target = mac_ip()
    if not target:
        return None
    ready = proxy_ready(serial, target)
    if ready is None:
        return None
    if ready is False:
        # 连续三次确认代理确实失效才修复，短暂网络抖动不重建。
        for _ in range(2):
            time.sleep(1)
            ready = proxy_ready(serial, target)
            if ready is not False:
                break
        if ready is None:
            return None
    if ready is False:
        if not install_proxy(serial, target):
            return None
        recovered = True

    if recovered:
        save_endpoint(serial)
        log(f"已连接 LX04：{serial}，目标 Mac：{target}")
        # ADB 重连不等于网页断线。页面靠自身超时轮询恢复，不发送按键或打开页签。
    return serial


def self_test():
    assert devices("List of devices attached\n1.2.3.4:5555 device\nfoo offline\n") == ["1.2.3.4:5555"]
    assert str(ipaddress.ip_network("192.168.1.42/24", strict=False)) == "192.168.1.0/24"
    print("self_test=ok")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="只检查和修复一次")
    parser.add_argument("--open", action="store_true", help="检查完成后打开状态屏")
    parser.add_argument("--self-test", action="store_true", help="运行不连接设备的自检")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not ADB:
        print("[adb-bridge] 找不到 adb", file=sys.stderr)
        return 1

    device_state = None
    while True:
        serial = ensure()
        if serial:
            device_state = observe_device(serial, device_state)
        if args.open and serial and server_alive() and open_status(serial):
            log(f"已打开 {URL}")
            return 0
        if args.once:
            return 0 if serial else 1
        time.sleep(INTERVAL)


if __name__ == "__main__":
    raise SystemExit(main())
