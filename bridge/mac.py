#!/usr/bin/env python3
"""Mac 控制页的动作：音箱上点按钮，Mac 本机执行。

只认白名单里的动作名，绝不执行任意命令。谁能发请求由 access.py 把关。

不做的动作和原因：
- 音量：输出走显示器（DisplayPort）时 `get volume settings` 是 missing value，调不动
- 截屏：LaunchAgent 起的 python 没有「屏幕录制」权限，截出来只有壁纸
- 媒体键：合成按键要「辅助功能」权限，后台进程拿不到
"""
import os
import plistlib
import subprocess
import sys
import threading
from pathlib import Path

TIMEOUT = 5

# 打开应用类的按钮：动作名 -> 应用路径。命令和图标都从这一个路径推出来，
# 加一个应用只改这里和 display/index.html 里那个按钮。
# 用完整路径而不是 `open -a 名字`：中文名、重名应用都不会打开错。
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
# 不用 NSWorkspace.iconForFile：新版系统的应用图标经它导出是透明噪点，
# 偏好面板只给通用文档图标；qlmanage 生成缩略图会卡住不返回。
_CORE = "/System/Library/CoreServices/CoreTypes.bundle/Contents/Resources"
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


def _app_icon(app):
    """应用自带的 .icns：Info.plist 的 CFBundleIconFile，可能不带扩展名。"""
    contents = Path(app) / "Contents"
    with (contents / "Info.plist").open("rb") as fh:
        name = plistlib.load(fh).get("CFBundleIconFile")
    if not name:
        raise FileNotFoundError(f"{app} 没有 CFBundleIconFile")
    if not name.endswith(".icns"):
        name += ".icns"
    return contents / "Resources" / name


def icon(name):
    """动作对应图标的 PNG 字节；没有这个动作、源文件不在或转换失败返回 None。"""
    if name not in ICONS and name not in APPS:
        return None
    out = ICON_DIR / f"{name}.png"
    with _icon_lock:  # 页面一次请求十几张，别并发跑一堆 sips 写同一个目录
        try:
            src = ICONS.get(name) or _app_icon(APPS[name])
            if not out.exists() or out.stat().st_mtime < Path(src).stat().st_mtime:
                ICON_DIR.mkdir(parents=True, exist_ok=True)
                tmp = out.with_suffix(".tmp.png")
                subprocess.run(
                    ["sips", "-s", "format", "png", "-Z", str(ICON_PX), str(src), "--out", str(tmp)],
                    capture_output=True, timeout=20, check=True,
                )
                tmp.replace(out)
            return out.read_bytes()
        except (OSError, ValueError, plistlib.InvalidFileException, subprocess.SubprocessError) as exc:
            print(f"[mac] 生成图标 {name} 失败：{exc}", file=sys.stderr)
            return None


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


def state():
    """给 /api/status 的 mac 字段，页面据此画开关状态。"""
    return {"awake": _awake_on()}


def run_action(name):
    """执行一个白名单动作。返回 (ok, 给页面看的信息)。"""
    if name == "awake":
        with _lock:
            on = _toggle_awake()
        return True, "防休眠已开启" if on else "防休眠已关闭"
    cmd = COMMANDS.get(name)
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
