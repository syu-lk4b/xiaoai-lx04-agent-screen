#!/usr/bin/env python3
"""Mac 状态页的数据：CPU、GPU、内存、磁盘、网速、电池、开机时长、最忙的进程，外加图表用的近几分钟历史。

不认识锁、不认识 HTTP，只暴露 read()。全部来自不需要授权的系统接口：
- CPU：mach host_statistics 的累计 tick，两次采样做差。不用 `top -l 2`——
  它一次要吃 0.7 秒 CPU，两秒一轮的话状态页自己就成了负载
- 内存：vm_stat，口径同「活动监视器」的「已使用内存」= App + 联动 + 压缩。
  top 的 PhysMem used 含文件缓存，128G 的机器常年报九成，没有参考价值
- 网速：netstat -ib 里物理网卡 en* 的累计字节做差。默认路由常走 VPN（ipsec/utun），
  把它也算上会把同一份流量数两遍
- GPU：ioreg 里 IOAccelerator 的 PerformanceStatistics，Apple Silicon 免 root。
  powermetrics 能给功耗和温度，但要 sudo，后台服务拿不到

采集是惰性的：有人拉 /api/status 才采，结果缓存 CACHE_SECONDS。屏幕关着就不花一点 CPU。
"""
import ctypes
import ctypes.util
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

CACHE_SECONDS = 2.0
HISTORY = 120  # 图表上的点数：两秒一个，约 4 分钟
# 两次采样隔得太久（没人看屏），旧历史和新数据之间是一段空白，连成一条线会误导，清掉重来
HISTORY_GAP = 15.0
DISK = "/System/Volumes/Data"  # APFS 下用户数据卷，"/" 是只读系统卷，看不出真实占用
SLOW_SECONDS = 30.0  # 磁盘、电池变化慢，不必每轮都读
TOP_N = 3
TIMEOUT = 3

_HOST_CPU_LOAD_INFO = 3
_libc = ctypes.CDLL(ctypes.util.find_library("c"))
_libc.mach_host_self.restype = ctypes.c_uint


def cpu_ticks():
    """(user, system, idle, nice) 累计 tick，开机以来单调递增。失败返回 None。"""
    buf = (ctypes.c_uint * 4)()
    count = ctypes.c_uint(4)
    if _libc.host_statistics(_libc.mach_host_self(), _HOST_CPU_LOAD_INFO, buf, ctypes.byref(count)) != 0:
        return None
    return tuple(buf)


def cpu_percent(prev, cur):
    """两次 tick 之间非空闲的占比。tick 是 32 位会回绕，回绕那一轮当作没数据。"""
    if not prev or not cur:
        return None
    delta = [b - a for a, b in zip(prev, cur)]
    total = sum(delta)
    if total <= 0 or min(delta) < 0:
        return None
    return round(100 * (total - delta[2]) / total, 1)


def parse_vm_stat(text):
    """vm_stat 输出 -> {"page_size": int, "<名字>": 页数}。"""
    out = {}
    m = re.search(r"page size of (\d+) bytes", text)
    out["page_size"] = int(m.group(1)) if m else 4096
    for line in text.splitlines():
        name, sep, value = line.partition(":")
        value = value.strip().rstrip(".")
        if sep and value.isdigit():
            out[name.strip().strip('"')] = int(value)
    return out


def memory_used(vm):
    """活动监视器的「已使用内存」，单位字节。字段缺失返回 None。"""
    try:
        pages = (vm["Anonymous pages"] - vm["Pages purgeable"]
                 + vm["Pages wired down"] + vm["Pages occupied by compressor"])
    except KeyError:
        return None
    return pages * vm["page_size"]


def parse_netstat(text):
    """netstat -ibn -> 物理网卡 en* 的 (累计收, 累计发) 字节。

    每块网卡会按地址族重复出现多行，只取 <Link#> 那行，否则一块卡算好几遍。
    """
    rx = tx = 0
    for line in text.splitlines():
        cols = line.split()
        if len(cols) < 10 or not cols[0].startswith("en") or not cols[2].startswith("<Link#"):
            continue
        # Link 行有 Address（MAC）列时 11 列，没有时 10 列，从右往左取更稳
        try:
            rx += int(cols[-5])
            tx += int(cols[-2])
        except ValueError:
            continue
    return rx, tx


def parse_ps(text, limit=TOP_N):
    """`ps -Ao pcpu=,comm= -r` -> 前几个进程 [{"name", "cpu"}]，名字只留可执行文件名。"""
    out = []
    for line in text.splitlines():
        cpu, _, comm = line.strip().partition(" ")
        try:
            value = float(cpu)
        except ValueError:
            continue
        name = Path(comm.strip()).name
        if not name or name == "ps":  # 采样那一下的 ps 自己不算
            continue
        out.append({"name": name, "cpu": round(value)})
        if len(out) >= limit:
            break
    return out


def parse_gpu(text):
    """ioreg IOAccelerator -> {"percent", "mem"}；读不到（虚拟机、老 Intel 机型）返回 None。"""
    m = re.search(r'"Device Utilization %"\s*=\s*(\d+)', text)
    if not m:
        return None
    mem = re.search(r'"In use system memory"\s*=\s*(\d+)', text)
    return {"percent": int(m.group(1)), "mem": int(mem.group(1)) if mem else None}


def parse_battery(text):
    """pmset -g batt -> {"percent", "charging", "ac"}；台式机没电池返回 None。"""
    m = re.search(r"(\d+)%;\s*([^;]+);", text)
    if not m:
        return None
    state = m.group(2).strip()
    return {
        "percent": int(m.group(1)),
        "charging": state == "charging",
        "ac": "AC Power" in text,
    }


def _run(*cmd):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"[system] {cmd[0]} 失败：{exc}", file=sys.stderr)
        return ""
    return result.stdout if result.returncode == 0 else ""


def _sysctl(name):
    return _run("sysctl", "-n", name).strip()


def _boot_time():
    m = re.search(r"sec = (\d+)", _sysctl("kern.boottime"))
    return int(m.group(1)) if m else None


class Sampler:
    """持有上一次的 tick 和网卡计数，算出区间速率。线程安全，多个 HTTP 线程可同时调。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._at = 0.0
        self._value = None
        self._ticks = None
        self._net = None  # (时间, 收, 发)
        self._slow_at = 0.0
        self._slow = {}
        self._history = deque(maxlen=HISTORY)
        self._last_sample = None
        # 开机以来不变的，只读一次
        self._cores = os.cpu_count()
        self._mem_total = int(_sysctl("hw.memsize") or 0) or None
        self._model = _sysctl("machdep.cpu.brand_string") or None
        self._boot = _boot_time()

    def _read_slow(self, now):
        if now - self._slow_at < SLOW_SECONDS and self._slow:
            return self._slow
        disk = None
        try:
            u = shutil.disk_usage(DISK)
            disk = {"used": u.used, "total": u.total, "percent": round(100 * u.used / u.total)}
        except OSError as exc:
            print(f"[system] 读磁盘失败：{exc}", file=sys.stderr)
        self._slow = {"disk": disk, "battery": parse_battery(_run("pmset", "-g", "batt"))}
        self._slow_at = now
        return self._slow

    def _sample(self, now):
        ticks = cpu_ticks()
        cpu = cpu_percent(self._ticks, ticks)
        self._ticks = ticks

        used = memory_used(parse_vm_stat(_run("vm_stat")))
        mem = None
        if used is not None and self._mem_total:
            mem = {"used": used, "total": self._mem_total,
                   "percent": round(100 * used / self._mem_total)}

        net = None
        rx, tx = parse_netstat(_run("netstat", "-ibn"))
        if self._net and now > self._net[0]:
            dt = now - self._net[0]
            # 计数器在网卡重连时会归零，负数那一轮不报
            if rx >= self._net[1] and tx >= self._net[2]:
                net = {"rx": int((rx - self._net[1]) / dt), "tx": int((tx - self._net[2]) / dt)}
        self._net = (now, rx, tx)

        gpu = parse_gpu(_run("ioreg", "-r", "-d", "1", "-w", "0", "-c", "IOAccelerator"))

        if self._last_sample is not None and now - self._last_sample > HISTORY_GAP:
            self._history.clear()
        self._last_sample = now
        self._history.append((
            cpu,
            gpu["percent"] if gpu else None,
            mem["percent"] if mem else None,
            net["rx"] if net else None,
            net["tx"] if net else None,
        ))
        # 按指标转置成数组，页面直接拿去画线；缺的点是 null，画线时断开
        columns = list(zip(*self._history))

        return {
            "model": self._model,
            "cores": self._cores,
            "cpu": cpu,  # 第一次采样没有上一轮可比，是 None，页面显示「—」
            "gpu": gpu,
            "history": dict(zip(("cpu", "gpu", "mem", "rx", "tx"), map(list, columns))),
            "load": round(os.getloadavg()[0], 2),
            "mem": mem,
            "net": net,
            "uptime": int(time.time() - self._boot) if self._boot else None,
            "top": parse_ps(_run("ps", "-Ao", "pcpu=,comm=", "-r")),
            **self._read_slow(now),
        }

    def read(self):
        with self._lock:
            now = time.monotonic()
            if self._value is None or now - self._at >= CACHE_SECONDS:
                self._value = self._sample(now)
                self._at = now
            return self._value


_sampler = Sampler()


def read():
    return _sampler.read()
