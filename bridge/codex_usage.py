"""Codex 额度兜底采集。

Codex CLI 直连 OpenAI 时，会话 jsonl 的 token_count 事件自带 rate_limits；
改走 magpie 等本地网关后，会话里的 primary/secondary 全是 null，屏幕只剩「—」。

采集优先级：
1. 会话事件里刚收到的 rate_limits（5 分钟内活跃、且确实含窗口数据，由 run.py 写入）
2. magpie 本地持久化文件 ~/.config/magpie/quotas.json（纯本地读，不碰凭据）
3. ChatGPT 网页额度接口 /backend-api/wham/usage（读 ~/.codex/auth.json 的登录态，
   仅在本地来源缺失、陈旧超过 6 小时、或已过 resets_at 尚未刷新时按冷却期请求一次）
"""
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

MAX_AGE = 6 * 3600
SESSION_GRACE = 300       # 会话自身刚报过额度，5 分钟内不被外部轮询覆盖
WHAM_INTERVAL = 300       # 正常轮询最小间隔（秒）
WHAM_AUTH_BACKOFF = 900   # 401/403 退避，避免拿过期 token 反复敲接口

MAGPIE_QUOTAS = Path(
    os.environ.get("LX04_MAGPIE_QUOTAS")
    or Path.home() / ".config" / "magpie" / "quotas.json"
)
CODEX_AUTH = Path(
    os.environ.get("LX04_CODEX_AUTH")
    or Path.home() / ".codex" / "auth.json"
)
WHAM_URL = "https://chatgpt.com/backend-api/wham/usage"

_wham_state = {"next_try": 0.0}


def _iso(raw):
    """兼容带 Z 结尾的 ISO 时间字符串。"""
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()


def _num(val):
    return isinstance(val, (int, float)) and not isinstance(val, bool)


def _infer_minutes(name):
    low = str(name or "").lower()
    if "5" in low and "hour" in low:
        return 300
    if ("7" in low and "day" in low) or "week" in low:
        return 10080
    return None


def _magpie_window(win, span_ns, stamp):
    if not isinstance(win, dict) or win.get("text"):
        return None
    used = win.get("used")
    if not _num(used):
        return None
    minutes = None
    if _num(span_ns) and span_ns > 0:
        minutes = round(span_ns / 60_000_000_000)
    else:
        minutes = _infer_minutes(win.get("name"))
    resets_at = None
    if win.get("resetsAt"):
        try:
            resets_at = _iso(win["resetsAt"])
        except ValueError:
            pass
    if resets_at is None and _num(win.get("resetSecs")):
        resets_at = stamp + win["resetSecs"]
    return {
        "remaining": round(100 - used),
        "window_minutes": minutes,
        "resets_at": resets_at,
    }


def read_magpie_usage(path=None, now=None):
    """从 magpie 的 quotas.json 读取最新的 Codex 额度，返回 (usage, stamp)。

    读不到、格式异常或全部超过 6 小时返回 (None, None)。
    """
    path = MAGPIE_QUOTAS if path is None else Path(path)
    now = time.time() if now is None else now
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError) as exc:
        print(f"[codex] 读取 magpie quotas 失败：{exc}", file=sys.stderr)
        return None, None
    if not isinstance(raw, dict):
        return None, None

    best = None  # ((not held, stamp), usage, stamp)
    for entry in raw.values():
        if not isinstance(entry, dict):
            continue
        quota = entry.get("quota")
        if not isinstance(quota, dict) or quota.get("provider") != "codex":
            continue
        stamp_raw = quota.get("readAt") or entry.get("at")
        if not stamp_raw:
            continue
        try:
            stamp = _iso(stamp_raw)
        except ValueError:
            continue
        if now - stamp > MAX_AGE:
            continue

        windows = quota.get("windows")
        if not isinstance(windows, list) or not windows:
            continue
        spans = entry.get("spans") if isinstance(entry.get("spans"), list) else []

        parsed = []
        for i, win in enumerate(windows):
            span_ns = (
                spans[i].get("span")
                if i < len(spans) and isinstance(spans[i], dict)
                else None
            )
            item = _magpie_window(win, span_ns, stamp)
            if item:
                parsed.append(item)
        if not parsed:
            continue

        out = {}
        for item in parsed:
            if item["window_minutes"] == 300 and "primary" not in out:
                out["primary"] = item
            elif item["window_minutes"] == 10080 and "secondary" not in out:
                out["secondary"] = item
        rest = [
            item for item in parsed
            if item is not out.get("primary") and item is not out.get("secondary")
        ]
        if "primary" not in out and rest:
            out["primary"] = rest.pop(0)
        if "secondary" not in out and rest:
            out["secondary"] = rest.pop(0)

        # 多个 Codex 账号时优先未被限流冻结的，其次按采集时间最新
        rank = (not bool(quota.get("held")), stamp)
        if best is None or rank > best[0]:
            best = (rank, out, stamp)

    if best is None:
        return None, None
    return best[1], best[2]


def parse_wham_usage(data):
    """解析 chatgpt.com/backend-api/wham/usage 的响应。"""
    if not isinstance(data, dict):
        return None
    rl = data.get("rate_limit")
    if not isinstance(rl, dict):
        return None

    def window(key, default_minutes):
        w = rl.get(key)
        if not isinstance(w, dict) or not _num(w.get("used_percent")):
            return None
        secs = w.get("limit_window_seconds")
        minutes = round(secs / 60) if _num(secs) and secs > 0 else default_minutes
        reset = w.get("reset_at")
        return {
            "remaining": round(100 - w["used_percent"]),
            "window_minutes": minutes,
            "resets_at": float(reset) if _num(reset) else None,
        }

    out = {}
    p = window("primary_window", 300)
    s = window("secondary_window", 10080)
    if p:
        out["primary"] = p
    if s:
        out["secondary"] = s
    return out or None


def fetch_wham_usage(auth_path=None, timeout=5):
    """用 ~/.codex/auth.json 的登录态查 ChatGPT 后端额度，返回 (usage, status)。"""
    auth_path = CODEX_AUTH if auth_path is None else Path(auth_path)
    try:
        auth = json.loads(auth_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, 0
    except (OSError, ValueError):
        return None, 0
    tokens = auth.get("tokens") if isinstance(auth, dict) else None
    if not isinstance(tokens, dict):
        return None, 0
    tok = tokens.get("access_token")
    if not tok:
        return None, 0

    headers = {
        "Authorization": f"Bearer {tok}",
        "User-Agent": "codex-cli",
        "Accept": "application/json",
    }
    acct = tokens.get("account_id")
    if acct:
        headers["ChatGPT-Account-Id"] = acct

    req = urllib.request.Request(WHAM_URL, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            return parse_wham_usage(payload), resp.status
    except urllib.error.HTTPError as exc:
        if exc.code not in (401, 403):
            print(f"[codex] wham/usage HTTP {exc.code}", file=sys.stderr)
        return None, exc.code
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None, 0


def _reset_passed(usage, stamp, now):
    """当前记录的 5h 重置时间已过、但采集时间仍停在重置前，说明该重拉一次了。"""
    if not isinstance(usage, dict):
        return False
    p = usage.get("primary")
    if not isinstance(p, dict):
        return False
    resets = p.get("resets_at")
    return _num(resets) and stamp < resets <= now - 30


def read_usage(
    current_usage=None,
    current_at=0.0,
    current_source=None,
    now=None,
    quotas_path=None,
    auth_path=None,
):
    """结合 session / magpie / wham 三路来源，返回 (usage, stamp, source)。"""
    now = time.time() if now is None else now

    # Codex 直连时 session 事件最实时，5 分钟内直接保留
    if current_usage and current_source == "session" and now - current_at < SESSION_GRACE:
        return current_usage, current_at, "session"

    cand_usage, cand_at, cand_source = current_usage, current_at, current_source

    mag_usage, mag_at = read_magpie_usage(path=quotas_path, now=now)
    if mag_usage and (
        not cand_usage or now - cand_at > MAX_AGE or mag_at > cand_at
    ):
        cand_usage, cand_at, cand_source = mag_usage, mag_at, "magpie"

    need_wham = (
        not cand_usage
        or now - cand_at > MAX_AGE
        or _reset_passed(cand_usage, cand_at, now)
    )
    if need_wham and now >= _wham_state["next_try"]:
        wham_usage, status = fetch_wham_usage(auth_path=auth_path)
        _wham_state["next_try"] = now + (
            WHAM_AUTH_BACKOFF if status in (401, 403) else WHAM_INTERVAL
        )
        if wham_usage:
            return wham_usage, now, "wham"

    if not cand_usage or now - cand_at > MAX_AGE:
        return None, 0.0, None
    return cand_usage, cand_at, cand_source
