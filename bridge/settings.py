#!/usr/bin/env python3
"""状态屏配置管理：页面开关、顺序、停留时长、轮播策略、相册参数与夜间熄屏。

配置落盘在 ~/Library/Application Support/lx04-status/settings.json，
夜间熄屏字段同时同步到同目录的 adb-endpoint.json（供 scripts/adb-bridge.py 读取）。
不引入数据库；文件不存在或字段缺失时自动回退到默认值。
"""
import json
import os
import re
import threading
from pathlib import Path

APP_DIR = Path.home() / "Library" / "Application Support" / "lx04-status"
SETTINGS_FILE = APP_DIR / "settings.json"
ENDPOINT_FILE = APP_DIR / "adb-endpoint.json"

PAGE_IDS = ("codex", "claude", "sys", "mac", "photo")
SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
DEFAULT_DWELL = {
    "codex": 60,
    "claude": 60,
    "sys": 60,
    "mac": 60,
    "photo": 300,
}
DEFAULT_SLEEP = "23:00-07:00"
DEFAULT_MAC_ITEMS = [
    {"id": "display_sleep", "name": "熄屏"},
    {"id": "screensaver", "name": "屏保"},
    {"id": "awake", "name": "防休眠"},
    {"id": "music", "name": "Music", "path": "/System/Applications/Music.app"},
    {"id": "ghostty", "name": "Ghostty", "path": "/Applications/Ghostty.app"},
    {"id": "iterm", "name": "iTerm", "path": "/Applications/iTerm.app"},
    {"id": "claude", "name": "Claude", "path": "/Applications/Claude.app"},
    {"id": "vscode", "name": "VS Code", "path": "/Applications/Visual Studio Code.app"},
    {"id": "chrome", "name": "Chrome", "path": "/Applications/Google Chrome.app"},
    {"id": "wechat", "name": "微信", "path": "/Applications/WeChat.app"},
    {"id": "obsidian", "name": "Obsidian", "path": "/Applications/Obsidian.app"},
    {"id": "eastmoney", "name": "东方财富", "path": "/Applications/东方财富.app"},
]
BUILTIN_MAC_ACTIONS = {
    "display_sleep": "熄屏",
    "screensaver": "屏保",
    "awake": "防休眠",
}
ALLOWED_APP_ROOTS = (
    Path("/Applications"),
    Path("/System/Applications"),
    Path("/System/Cryptexes/App/System/Applications"),
    Path.home() / "Applications",
)
MAX_MAC_ITEMS = 12

_lock = threading.Lock()
_focus = {"page": None, "seq": 0}


def valid_app_path(path_str):
    """校验应用路径必须位于标准 Applications 目录下且以 .app 结尾，拒绝符号链接跳出目录或路径穿越。"""
    if not isinstance(path_str, str) or not path_str.strip():
        return None
    p = Path(path_str.strip())
    if p.suffix.lower() != ".app" or ".." in p.parts:
        return None
    if p.parent not in ALLOWED_APP_ROOTS:
        return None
    try:
        if p.is_symlink():
            target = p.resolve()
            allowed_resolved = {r.resolve() for r in ALLOWED_APP_ROOTS if r.exists()}
            if target.parent not in allowed_resolved and not any(ar in target.parents for ar in allowed_resolved):
                return None
    except OSError:
        return None
    return str(p)


def _safe_id(raw_id, fallback_name):
    """只保留安全的动作 ID 字符 [a-zA-Z0-9_-]，防止缓存路径穿越或 HTML 注入。"""
    s = str(raw_id or "").strip()
    if SAFE_ID_RE.match(s):
        return s
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(fallback_name or "app").lower()).strip("_")
    return (cleaned[:48] or "app")


def _normalize_mac_items(raw_items):
    """清洗控制页按钮列表：支持内置动作和本机 .app 应用，最多 12 个。"""
    if not isinstance(raw_items, list):
        return [dict(item) for item in DEFAULT_MAC_ITEMS]
    out = []
    seen = set()
    used_ids = set()
    for item in raw_items:
        if not isinstance(item, dict) or len(out) >= MAX_MAC_ITEMS:
            continue
        item_id = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()[:24]
        path = valid_app_path(item.get("path"))
        if item_id in BUILTIN_MAC_ACTIONS and not path:
            if item_id in seen:
                continue
            seen.add(item_id)
            used_ids.add(item_id)
            out.append({
                "id": item_id,
                "name": name or BUILTIN_MAC_ACTIONS[item_id],
            })
        elif path:
            key = f"app:{path}"
            if key in seen:
                continue
            seen.add(key)
            default_name = Path(path).stem
            safe_id = _safe_id(item_id, default_name)
            base_id = safe_id
            n = 2
            while safe_id in used_ids or (safe_id in BUILTIN_MAC_ACTIONS):
                safe_id = f"{base_id}_{n}"
                n += 1
            used_ids.add(safe_id)
            out.append({
                "id": safe_id,
                "name": name or default_name,
                "path": path,
            })
    return out


def defaults():
    """返回一份全新的默认配置字典。"""
    return {
        "pages": [
            {"id": pid, "enabled": True, "dwell": DEFAULT_DWELL[pid]}
            for pid in PAGE_IDS
        ],
        "mac_items": [dict(item) for item in DEFAULT_MAC_ITEMS],
        "mac_volume_bar": True,
        "rotate": True,
        "follow_urgent": True,
        "follow_working": True,
        "idle_photo": True,
        "idle_seconds": 600,
        "pin_seconds": 60,
        "slide_seconds": 12,
        "photo_years": 2.0,
        "photo_count": 300,
        "sleep": DEFAULT_SLEEP,
    }


def _clamp_int(val, lo, hi, default):
    try:
        if isinstance(val, bool):
            return default
        n = int(val)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _clamp_float(val, lo, hi, default):
    try:
        if isinstance(val, bool):
            return default
        n = float(val)
    except (TypeError, ValueError):
        return default
    return round(max(lo, min(hi, n)), 1)


def _valid_sleep(text):
    """校验夜间熄屏格式：'off' 或 'HH:MM-HH:MM'（起止不同）。"""
    if not isinstance(text, str):
        return None
    s = text.strip().lower()
    if s == "off":
        return "off"
    parts = s.split("-")
    if len(parts) != 2:
        return None
    norm = []
    minutes = []
    for part in parts:
        hm = part.strip().split(":")
        if len(hm) != 2:
            return None
        try:
            hh, mm = int(hm[0]), int(hm[1])
        except ValueError:
            return None
        if not (0 <= hh < 24 and 0 <= mm < 60):
            return None
        norm.append(f"{hh:02d}:{mm:02d}")
        minutes.append(hh * 60 + mm)
    if minutes[0] == minutes[1]:
        return None
    return f"{norm[0]}-{norm[1]}"


def normalize(raw):
    """把任意输入字典清洗为合法、完整的配置结构。"""
    base = defaults()
    if not isinstance(raw, dict):
        return base

    seen = set()
    pages = []
    raw_pages = raw.get("pages")
    if isinstance(raw_pages, list):
        for item in raw_pages:
            if not isinstance(item, dict):
                continue
            pid = item.get("id")
            if pid not in PAGE_IDS or pid in seen:
                continue
            seen.add(pid)
            pages.append({
                "id": pid,
                "enabled": bool(item.get("enabled", True)),
                "dwell": _clamp_int(item.get("dwell"), 5, 3600, DEFAULT_DWELL[pid]),
            })
    for pid in PAGE_IDS:
        if pid not in seen:
            pages.append({
                "id": pid,
                "enabled": True,
                "dwell": DEFAULT_DWELL[pid],
            })
    # 至少保留一页开启，避免音箱无页可显示
    if not any(p["enabled"] for p in pages):
        pages[0]["enabled"] = True

    sleep = _valid_sleep(raw.get("sleep")) or DEFAULT_SLEEP
    mac_items = _normalize_mac_items(raw.get("mac_items"))

    return {
        "pages": pages,
        "mac_items": mac_items,
        "mac_volume_bar": bool(raw.get("mac_volume_bar", base["mac_volume_bar"])),
        "rotate": bool(raw.get("rotate", base["rotate"])),
        "follow_urgent": bool(raw.get("follow_urgent", base["follow_urgent"])),
        "follow_working": bool(raw.get("follow_working", base["follow_working"])),
        "idle_photo": bool(raw.get("idle_photo", base["idle_photo"])),
        "idle_seconds": _clamp_int(raw.get("idle_seconds"), 10, 86400, base["idle_seconds"]),
        "pin_seconds": _clamp_int(raw.get("pin_seconds"), 5, 3600, base["pin_seconds"]),
        "slide_seconds": _clamp_int(raw.get("slide_seconds"), 3, 600, base["slide_seconds"]),
        "photo_years": _clamp_float(raw.get("photo_years"), 0.5, 30.0, base["photo_years"]),
        "photo_count": _clamp_int(raw.get("photo_count"), 10, 2000, base["photo_count"]),
        "sleep": sleep,
    }


def _read_endpoint_sleep():
    try:
        data = json.loads(ENDPOINT_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return _valid_sleep(data.get("sleep"))
    except (OSError, ValueError):
        pass
    return None


def _write_endpoint_sleep(sleep_val):
    try:
        ENDPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            state = json.loads(ENDPOINT_FILE.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                state = {}
        except (OSError, ValueError):
            state = {}
        state["sleep"] = sleep_val
        tmp = ENDPOINT_FILE.with_suffix(".tmp.json")
        tmp.write_text(json.dumps(state, ensure_ascii=False) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(ENDPOINT_FILE)
    except OSError:
        pass


def _load_unlocked():
    raw = {}
    try:
        loaded = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            raw = loaded
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        pass
    if "sleep" not in raw:
        ep_sleep = _read_endpoint_sleep()
        if ep_sleep:
            raw["sleep"] = ep_sleep
    return normalize(raw)


def read():
    """读取当前配置（含远程切页指令 focus）。"""
    with _lock:
        cfg = _load_unlocked()
        cfg["focus"] = dict(_focus)
        return cfg


def save(patch):
    """更新并持久化配置，返回最新配置。"""
    if not isinstance(patch, dict):
        raise ValueError("settings payload must be a JSON object")
    with _lock:
        current = _load_unlocked()
        merged = {**current, **patch}
        cfg = normalize(merged)
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_FILE.with_suffix(".tmp.json")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(SETTINGS_FILE)
        _write_endpoint_sleep(cfg["sleep"])
        cfg["focus"] = dict(_focus)
        return cfg


def focus(page_id):
    """发送远程切页指令，让音箱在下一次轮询时立即跳到指定页面。"""
    if page_id not in PAGE_IDS:
        return None
    with _lock:
        cfg = _load_unlocked()
        # 如果点选的页面原先被禁用，顺便开启它，保证音箱能切过去
        changed = False
        for p in cfg["pages"]:
            if p["id"] == page_id and not p["enabled"]:
                p["enabled"] = True
                changed = True
        if changed:
            SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = SETTINGS_FILE.with_suffix(".tmp.json")
            tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.chmod(tmp, 0o600)
            tmp.replace(SETTINGS_FILE)
        _focus["page"] = page_id
        _focus["seq"] += 1
        cfg["focus"] = dict(_focus)
        return cfg
