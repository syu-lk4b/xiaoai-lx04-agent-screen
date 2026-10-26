#!/usr/bin/env python3
"""Mac 控制页的动作：音箱上点按钮，Mac 本机执行。

只认白名单里的动作名或用户在 /setting 里登记的本机应用，绝不执行任意命令。
谁能发请求由 access.py 把关。

音量控制说明：
- 优先调节 macOS 系统输出音量（osascript get/set volume settings）
- 当外接 DisplayPort/HDMI 显示器作为默认输出、系统音量返回 missing value 时，
  自动回退调节 Music.app 的应用音量，确保底部音量栏始终可用
"""
import hashlib
import os
import plistlib
import re
import subprocess
import sys
import threading
from pathlib import Path

import settings

TIMEOUT = 5
VOLUME_STEP = 10

# 默认内置的打开应用类按钮：动作名 -> 应用路径。
APPS = {
    "music": "/System/Applications/Music.app",
    "ghostty": "/Applications/Ghostty.app",
    "iterm": "/Applications/iTerm.app",
    "claude": "/Applications/Claude.app",
    "vscode": "/Applications/Visual Studio Code.app",
    "chrome": "/Applications/Google Chrome.app",
    "wechat": "/Applications/WeChat.app",
    "obsidian": "/Applications/Obsidian.app",
    "eastmoney": "/Applications/东方财富.app",
}

# 名字 -> 命令。都是「触发一下就返回」的动作，不等它产生结果
COMMANDS = {
    "display_sleep": ["pmset", "displaysleepnow"],
    # 系统设置了「屏保后立即要求密码」时，这就等于锁屏
    "screensaver": ["open", "-a", "ScreenSaverEngine"],
    **{name: ["open", path] for name, path in APPS.items()},
}

# 按钮图标：直接用本机应用自带的 .icns，运行时转 PNG 缓存，不进仓库——
# 别人的商标图标不该随开源仓库分发，而且应用升级换了图标这里也跟着变。
_CORE = "/System/Library/CoreServices/CoreTypes.bundle/Contents/Resources"
GENERIC_ICON = Path(f"{_CORE}/GenericApplicationIcon.icns")
# 不是应用的按钮，图标单独指定；应用的图标由 _app_icon 从 Info.plist 里找
ICONS = {
    "display_sleep": f"{_CORE}/com.apple.studio-display.icns",
    "screensaver": "/System/Library/CoreServices/ScreenSaverEngine.app/Contents/Resources/AppIcon.icns",
    "awake": f"{_CORE}/Clock.icns",
}
ICON_DIR = Path.home() / "Library" / "Caches" / "lx04-status" / "icons"
ICON_PX = 144  # 页面上画 64px，留两倍余量

_lock = threading.Lock()
_icon_lock = threading.Lock()
_awake = None  # 防休眠的 caffeinate 子进程
_vol_state = {"volume": None, "muted": False, "prev_volume": 50}


def _app_icon(app):
    """应用自带的图标文件：优先读 Info.plist 的 CFBundleIconFile，兼容 iOS Wrapper 应用及兜底扫描。"""
    app_path = Path(app)
    contents = app_path / "Contents"
    plist_path = contents / "Info.plist"
    if plist_path.is_file():
        with plist_path.open("rb") as fh:
            info = plistlib.load(fh)
        name = info.get("CFBundleIconFile") or info.get("CFBundleIconName")
        if name:
            cand = contents / "Resources" / (name if name.endswith(".icns") else f"{name}.icns")
            if cand.is_file():
                return cand
        res_dir = contents / "Resources"
        if res_dir.is_dir():
            icns = sorted(res_dir.glob("*.icns"))
            if icns:
                return icns[0]
    # iOS 移植应用（Wrapper/*.app）
    wrapper = app_path / "Wrapper"
    if wrapper.is_dir():
        for sub in wrapper.glob("*.app"):
            pngs = sorted(sub.glob("AppIcon*.png"), reverse=True)
            if pngs:
                return pngs[0]
    raise FileNotFoundError(f"{app} 没有找到可用图标")


def _configured_app_path(name):
    """从 settings.json 的 mac_items 中查找动态配置的应用路径。"""
    if name in APPS:
        return APPS[name]
    try:
        items = settings.read().get("mac_items") or []
    except Exception:
        items = []
    for item in items:
        if isinstance(item, dict) and item.get("id") == name and item.get("path"):
            return settings.valid_app_path(item.get("path"))
    return None


def _render_icon_bytes(src_path, cache_key):
    out = ICON_DIR / f"{cache_key}.png"
    with _icon_lock:
        try:
            src = Path(src_path)
            if not out.exists() or out.stat().st_mtime < src.stat().st_mtime:
                ICON_DIR.mkdir(parents=True, exist_ok=True)
                tmp = out.with_suffix(".tmp.png")
                subprocess.run(
                    ["sips", "-s", "format", "png", "-Z", str(ICON_PX), str(src), "--out", str(tmp)],
                    capture_output=True, timeout=20, check=True,
                )
                tmp.replace(out)
            return out.read_bytes()
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            print(f"[mac] 生成图标 {cache_key} 失败：{exc}", file=sys.stderr)
            return None


def icon(name):
    """动作对应图标的 PNG 字节；没有这个动作、源文件不在或转换失败返回 None。"""
    if not isinstance(name, str) or not settings.SAFE_ID_RE.match(name):
        return None
    if name in ICONS:
        return _render_icon_bytes(ICONS[name], name)
    app_path = _configured_app_path(name)
    if not app_path:
        return None
    try:
        src = _app_icon(app_path)
    except (OSError, ValueError, plistlib.InvalidFileException) as exc:
        if Path(app_path).exists() and GENERIC_ICON.is_file():
            src = GENERIC_ICON
        else:
            print(f"[mac] 生成图标 {name} 失败：{exc}", file=sys.stderr)
            return None
    key = name if name in APPS else "app_" + hashlib.sha1(app_path.encode("utf-8")).hexdigest()[:12]
    return _render_icon_bytes(src, key)


def icon_for_path(app_path):
    """按本机应用路径返回 PNG 图标（供 /setting 应用选择器预览使用）。"""
    valid = settings.valid_app_path(app_path)
    if not valid or not Path(valid).exists():
        return None
    try:
        src = _app_icon(valid)
    except (OSError, ValueError, plistlib.InvalidFileException):
        if GENERIC_ICON.is_file():
            src = GENERIC_ICON
        else:
            return None
    key = "app_" + hashlib.sha1(valid.encode("utf-8")).hexdigest()[:12]
    return _render_icon_bytes(src, key)


def list_installed_apps():
    """扫描本机 /Applications、/System/Applications、~/Applications 下的应用列表。"""
    seen = set()
    apps = []
    for root in settings.ALLOWED_APP_ROOTS:
        if not root.is_dir():
            continue
        for p in sorted(root.glob("*.app"), key=lambda x: x.stem.lower()):
            path_str = str(p)
            if path_str in seen:
                continue
            seen.add(path_str)
            slug = re.sub(r"[^a-z0-9]+", "_", p.stem.lower()).strip("_") or "app"
            # 若与内置 APPS 路径一致，复用原 id
            for k, v in APPS.items():
                if v == path_str:
                    slug = k
                    break
            apps.append({
                "id": slug,
                "name": p.stem,
                "path": path_str,
            })
    return apps


def _awake_on():
    return _awake is not None and _awake.poll() is None


def _toggle_awake():
    global _awake
    if _awake_on():
        _awake.terminate()
        try:
            _awake.wait(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            _awake.kill()
        _awake = None
    else:
        # -w 绑定到本进程：状态服务挂了或重启，防休眠跟着失效，不会遗留
        _awake = subprocess.Popen(
            ["caffeinate", "-di", "-w", str(os.getpid())],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    return _awake_on()


def _read_volume():
    """读取当前音量与静音状态 -> (volume: int|None, muted: bool, target: 'system'|'music'|None)。"""
    try:
        r = subprocess.run(
            ["osascript", "-e", "get volume settings"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0:
            m_vol = re.search(r"output volume:(\d+)", r.stdout)
            m_mute = re.search(r"output muted:(true|false)", r.stdout)
            if m_vol:
                vol = int(m_vol.group(1))
                muted = (m_mute.group(1) == "true") if m_mute else (vol == 0)
                return vol, muted, "system"
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    # DisplayPort/HDMI 外接显示器系统音量为 missing value 时，回退读写 Music.app 音量
    try:
        r = subprocess.run(
            ["osascript", "-e", 'tell application "Music" to get sound volume'],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0 and r.stdout.strip().isdigit():
            vol = int(r.stdout.strip())
            return vol, vol == 0, "music"
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return None, False, None


def _adjust_volume(action):
    """执行 volume_down / volume_up / volume_mute，返回 (ok, message)。"""
    vol, muted, target = _read_volume()
    if target is None:
        return False, "当前音频设备不支持调节"
    label = "音量" if target == "system" else "Music 音量"
    try:
        if action == "volume_mute":
            if target == "system":
                next_mute = not muted
                subprocess.run(
                    ["osascript", "-e", f"set volume {'with' if next_mute else 'without'} output muted"],
                    capture_output=True, text=True, timeout=TIMEOUT, check=True,
                )
                _vol_state["muted"] = next_mute
                _vol_state["volume"] = vol
                return True, "已静音" if next_mute else f"{label} {vol}%"
            else:
                if vol > 0:
                    _vol_state["prev_volume"] = vol
                    new_vol = 0
                else:
                    new_vol = _vol_state.get("prev_volume") or 50
                subprocess.run(
                    ["osascript", "-e", f'tell application "Music" to set sound volume to {new_vol}'],
                    capture_output=True, text=True, timeout=TIMEOUT, check=True,
                )
                _vol_state["volume"] = new_vol
                _vol_state["muted"] = new_vol == 0
                return True, "已静音" if new_vol == 0 else f"{label} {new_vol}%"

        delta = VOLUME_STEP if action == "volume_up" else -VOLUME_STEP
        base = 0 if (muted and action == "volume_up") else (vol or 0)
        new_vol = max(0, min(100, base + delta))
        if target == "system":
            subprocess.run(
                ["osascript", "-e", f"set volume without output muted output volume {new_vol}"],
                capture_output=True, text=True, timeout=TIMEOUT, check=True,
            )
        else:
            if new_vol > 0:
                _vol_state["prev_volume"] = new_vol
            subprocess.run(
                ["osascript", "-e", f'tell application "Music" to set sound volume to {new_vol}'],
                capture_output=True, text=True, timeout=TIMEOUT, check=True,
            )
        _vol_state["volume"] = new_vol
        _vol_state["muted"] = new_vol == 0
        return True, f"{label} {new_vol}%"
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[mac] {action} 执行失败：{exc}", file=sys.stderr)
        return False, "音量调节失败"


def state():
    """给 /api/status 的 mac 字段，页面据此画开关与静音状态。"""
    return {
        "awake": _awake_on(),
        "muted": bool(_vol_state["muted"]),
        "volume": _vol_state["volume"],
    }


def run_action(name):
    """执行一个白名单动作或已配置的应用。返回 (ok, 给页面看的信息)。"""
    if name == "awake":
        with _lock:
            on = _toggle_awake()
        return True, "防休眠已开启" if on else "防休眠已关闭"
    if name in ("volume_down", "volume_up", "volume_mute"):
        with _lock:
            return _adjust_volume(name)
    cmd = COMMANDS.get(name)
    if cmd is None:
        app_path = _configured_app_path(name)
        if app_path:
            cmd = ["open", app_path]
    if cmd is None:
        return False, "未知动作"
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"[mac] {name} 执行失败：{exc}", file=sys.stderr)
        return False, "执行失败"
    if result.returncode != 0:
        print(f"[mac] {name} 返回 {result.returncode}：{result.stderr.strip()}", file=sys.stderr)
        return False, "执行失败"
    return True, "已执行"
