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
BROWSER_PACKAGE = os.environ.get("LX04_BROWSER_PACKAGE", "mark.via")
STATE = Path.home() / "Library" / "Application Support" / "lx04-status" / "adb-endpoint.json"
PID = "/data/local/tmp/lx04-status-proxy.pid"
TARGET = "/data/local/tmp/lx04-status-proxy.target"
LOG = "/data/local/tmp/lx04-status-proxy.log"
TOYBOX = "/system/bin/toybox"
# 夜里熄屏的时段（Mac 本地时间），在 adb-endpoint.json 里用 "sleep": "23:30-07:00" 改，"off" 关掉。
# 音箱自己的熄屏时间是「永不」，不靠我们关它就整夜亮着
SLEEP_DEFAULT = "23:00-07:00"
KEY_SLEEP, KEY_WAKEUP = "223", "224"
ADB = next((p for p in (str(Path(__file__).resolve().parents[1] / ".local-tools/platform-tools/adb"), "/opt/homebrew/bin/adb", shutil.which("adb")) if p and Path(p).exists()), None)


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


def config():
    try:
        value = json.loads(STATE.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def serialno(serial):
    result = run("-s", serial, "shell", "getprop", "ro.serialno", timeout=3)
    return result.stdout.strip() if result and result.returncode == 0 else ""


def is_lx04(serial):
    result = run("-s", serial, "shell", "getprop", "ro.product.device", timeout=3)
    if not (result and result.returncode == 0 and result.stdout.strip() == PRODUCT):
        return False
    # 局域网里谁都能跑个假 adbd 自称 mi_lx04，而 access.py 会放行这里登记的地址（相册、Mac 控制）。
    # 所以认过一次音箱就钉住它的硬件序列号，之后扫网段、重连、USB 都只认这一台；换音箱用 setup.sh --device 重新钉
    pinned = config().get("serialno")
    return not pinned or serialno(serial) == pinned


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


def sleep_window():
    """配置里的熄屏时段 -> (开始分钟, 结束分钟)；关掉或写错返回 None。"""
    try:
        text = json.loads(STATE.read_text()).get("sleep", SLEEP_DEFAULT)
    except (OSError, ValueError, AttributeError, json.JSONDecodeError):
        text = SLEEP_DEFAULT
    return parse_window(text)


def parse_window(text):
    try:
        start, end = (part.strip() for part in str(text).split("-"))
        minutes = []
        for part in (start, end):
            hh, mm = part.split(":")
            if not (0 <= int(hh) < 24 and 0 <= int(mm) < 60):
                return None
            minutes.append(int(hh) * 60 + int(mm))
    except ValueError:
        return None  # "off" 和写错的都走这里
    return None if minutes[0] == minutes[1] else tuple(minutes)


def in_window(window, minute):
    start, end = window
    # 23:00-07:00 这种跨午夜的时段，落在两段里的任一段都算
    return start <= minute < end if start < end else minute >= start or minute < end


def sleep_step(window, minute, boot, slept):
    """该不该熄屏/亮屏 -> (动作, 新的 slept)。动作是 None、"sleep" 或 "wake"。

    slept 记的是「这一夜在哪次开机里关过屏」：同一次开机只关一次，
    夜里有人点亮了屏幕就让它亮着，不跟人抢；音箱半夜重启（自己亮屏）则再关一次。
    只有我们关过屏，早上才负责叫醒——白天启动不去碰屏幕。
    """
    if window and in_window(window, minute):
        return ("sleep", boot) if slept != boot else (None, slept)
    return ("wake", None) if slept is not None else (None, None)


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


def save_endpoint(endpoint, repin=False):
    try:
        host, port = endpoint.rsplit(":", 1)
        if ipaddress.ip_address(host).version != 4 or port != str(ADB_PORT):
            return
    except ValueError:
        return  # USB serial numbers must not replace a saved Wi-Fi endpoint.
    STATE.parent.mkdir(parents=True, exist_ok=True)
    state = config()
    state["endpoint"] = endpoint
    if repin or not state.get("serialno"):
        number = serialno(endpoint)
        if number:
            state["serialno"] = number
    STATE.write_text(json.dumps(state) + "\n")
    os.chmod(STATE, 0o600)


def proxy_ready(serial, target):
    """USB reverse, or a loopback-only netcat proxy on old wireless adbd."""
    if ":" not in serial:
        result = run("-s", serial, "reverse", "--list")
        if result is None:
            return None
        if result.returncode == 0 and any(
                line.split()[-2:] == [f"tcp:{STATUS_PORT}", f"tcp:{STATUS_PORT}"]
                for line in result.stdout.splitlines()):
            return True
    result = run("-s", serial, "shell", "cat", "/proc/net/tcp")
    if result is None or result.returncode != 0:
        return None
    address = f"0100007F:{STATUS_PORT:04X}"
    listening = any(len(fields) > 3 and fields[1] == address and fields[3] == "0A"
                    for fields in (line.split() for line in result.stdout.splitlines()))
    if not listening:
        return False
    configured = run("-s", serial, "shell", "cat", TARGET)
    if configured is None:
        return None
    return configured.returncode == 0 and configured.stdout.strip() == target


def install_proxy(serial, target):
    if ":" not in serial:
        result = run("-s", serial, "reverse", f"tcp:{STATUS_PORT}", f"tcp:{STATUS_PORT}")
        if result and result.returncode == 0 and proxy_ready(serial, target) is True:
            return True
    target = str(ipaddress.IPv4Address(target))
    prefix = f"{TOYBOX} nc -s 127.0.0.1 -p {STATUS_PORT} -L {TOYBOX} nc "
    command = (
        f"old=$(cat {PID} 2>/dev/null); "
        "case \"$old\" in ''|*[!0-9]*) ;; *) "
        'actual=$(tr \'\\000\' \' \' <"/proc/$old/cmdline" 2>/dev/null); '
        f'case "$actual" in {shlex.quote(prefix)}*) '
        'kill "$old" 2>/dev/null; sleep 1 ;; esac ;; esac; '
        f"if grep -q '0100007F:{STATUS_PORT:04X} .* 0A ' /proc/net/tcp; then exit 1; fi; "
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
    # Via 对和当前页相同的 URL 回车不会重新加载，改了页面也看不到——
    # 带一个每次不同的查询参数强制加载，页面不认识它，会忽略。
    # 只用一个参数：input text 过设备上的 sh，& 会被当成后台符把命令截断。
    url = f"{URL}?t={int(time.time())}"
    commands = [
        ("am", "start", "-W", "-a", "android.intent.action.MAIN", "-c",
         "android.intent.category.LAUNCHER", "-n", f"{BROWSER_PACKAGE}/mark.via.Shell"),
        ("input", "keyevent", "84"),
        ("input", "text", url),
        ("input", "keyevent", "66"),
        ("input", "swipe", "735", "370", "735", "130", "500"),
    ]
    for command in commands:
        result = run("-s", serial, "shell", *command)
        if result is None or result.returncode != 0:
            return False
        if command == ("input", "keyevent", "84"):
            time.sleep(0.3)
        elif command == ("input", "keyevent", "66"):
            time.sleep(1)
    return True


def set_screen(serial, on):
    result = run("-s", serial, "shell", "input", "keyevent", KEY_WAKEUP if on else KEY_SLEEP)
    return result is not None and result.returncode == 0


def observe_device(serial, previous):
    result = run("-s", serial, "shell",
                 f"cat /proc/sys/kernel/random/boot_id; pidof {BROWSER_PACKAGE}")
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

    # 老配置里没有序列号的，第一次看到音箱就补上
    if recovered or not config().get("serialno"):
        save_endpoint(serial)
    if recovered:
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
    parser.add_argument("--auto-open", action="store_true", help="音箱重启或浏览器未运行时打开状态屏")
    parser.add_argument("--self-test", action="store_true", help="运行不连接设备的自检")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not ADB:
        print("[adb-bridge] 找不到 adb", file=sys.stderr)
        return 1

    device_state = None
    slept = None
    while True:
        serial = ensure()
        night = False
        if serial:
            previous_state = device_state
            device_state = observe_device(serial, device_state)
            if args.auto_open and device_state:
                now = time.localtime()
                minute = now.tm_hour * 60 + now.tm_min
                window = sleep_window()
                night = bool(window and in_window(window, minute))
                action, after = sleep_step(window, minute, device_state[0], slept)
                if action == "sleep" and set_screen(serial, False):
                    slept = after
                    log("到了熄屏时段，关闭屏幕")
                elif action == "wake" and set_screen(serial, True):
                    slept = after
                    log("熄屏时段结束，点亮屏幕")
                    if server_alive() and open_status(serial):
                        log(f"已打开 {URL}")
            # 熄屏时段里不自动开页面：输入按键会把屏幕叫醒
            if (args.auto_open and not night and device_state and server_alive()
                    and (device_state[1] == "未运行"
                         or previous_state is None
                         or previous_state[0] != device_state[0])):
                if open_status(serial):
                    log(f"已自动打开 {URL}")
        if args.open and serial and server_alive() and open_status(serial):
            log(f"已打开 {URL}")
            return 0
        if args.once:
            return 0 if serial else 1
        time.sleep(INTERVAL)


if __name__ == "__main__":
    raise SystemExit(main())
