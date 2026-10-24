#!/usr/bin/env python3
"""相册页的数据：「照片」App 自己识别出的最近几次旅行，每次取它精选的那几十张。

不认识锁、不认识 HTTP，只暴露 read() 和 image()。
- 只读打开图库数据库（Photos.sqlite，mode=ro），不改图库、不碰 iCloud，「照片」开着也不冲突
- 旅行 = ZPHOTOSHIGHLIGHT 里 ZKIND=3 的那层（「照片」App 里「旅行」回忆的来源，
  标题是「Japan」「Shaoxing & Jiaxing」这种地名）。照片挂在它的 day-group 关系上，
  ZDAYGROUPHIGHLIGHTBEINGSUMMARYASSETS 是「照片」App 精选出来的那一批，
  没精选就按美学评分 ZOVERALLAESTHETICSCORE 取前几十张
- 出图优先用图库里现成的 1024 渲染件（resources/derivatives/…_1_105_c.jpeg，已经套好编辑和方向），
  没有才退到 2048 那份或原图。HEIC 也交给 sips，它能解
- 横图裁成 800×480 整屏；竖图裁成 400×480，页面两张并排放——整屏塞一张竖图会糊或者两侧大黑边

服务端只给列表里有的照片出图：/photos/<id>.jpg 的 id 必须在最近一次 read() 的结果里，
不能拿它去翻整个图库。谁能访问由 access.py 把关，和别的页面一样。
"""
import math
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote

LIBRARY = Path(os.environ.get(
    "LX04_PHOTOS_LIBRARY", Path.home() / "Pictures" / "Photos Library.photoslibrary"))
TRIPS = int(os.environ.get("LX04_PHOTO_TRIPS", "4"))   # 最近几次旅行
PER_TRIP = 40          # 每次旅行最多几张：精选通常 30 来张，够轮一圈
REFRESH_SECONDS = 600  # 图库十分钟内新增的旅行，晚一点出现没关系；查询本身也不便宜（库有 2GB+）
CACHE_DIR = Path.home() / "Library" / "Caches" / "lx04-status" / "photos"
SCREEN = (800, 480)
HALF = (400, 480)      # 竖图：两张并排正好一屏
QUALITY = "82"
TIMEOUT = 30
EPOCH = 978307200      # Core Data 的时间从 2001-01-01 算起
UUID = re.compile(r"^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$")

_TRIPS_SQL = """
select Z_PK, ZSUBTITLE, ZSTARTDATE, ZENDDATE from ZPHOTOSHIGHLIGHT
where ZKIND = 3 and ZSTARTDATE is not null
order by ZSTARTDATE desc limit ?
"""
# 隐藏、废纸篓、截屏、视频一律不要
_ASSET_FILTER = ("ZKIND = 0 and ZHIDDEN = 0 and ZTRASHEDSTATE = 0 "
                 "and coalesce(ZISDETECTEDSCREENSHOT, 0) = 0")
_PICKED_SQL = f"""
select ZUUID, ZDIRECTORY, ZFILENAME, ZWIDTH, ZHEIGHT, ZDATECREATED from ZASSET
where ZDAYGROUPHIGHLIGHTBEINGSUMMARYASSETS = ? and {_ASSET_FILTER}
order by ZDATECREATED limit ?
"""
_BEST_SQL = f"""
select * from (
  select ZUUID, ZDIRECTORY, ZFILENAME, ZWIDTH, ZHEIGHT, ZDATECREATED from ZASSET
  where ZDAYGROUPHIGHLIGHTBEINGASSETS = ? and {_ASSET_FILTER}
  order by coalesce(ZOVERALLAESTHETICSCORE, 0) desc limit ?
) order by ZDATECREATED
"""


def query(db_path, trips=TRIPS, per_trip=PER_TRIP):
    """图库数据库 -> {"trips": [...], "photos": [...]}，旅行新的在前，同一次旅行内按拍摄时间。

    photos 的每项：id（UUID）、trip（trips 下标）、portrait、at（unix 秒），
    外加 _src 给本模块出图用，不发给页面。
    """
    uri = "file:" + quote(str(db_path)) + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        out_trips, photos = [], []
        for pk, place, start, end in conn.execute(_TRIPS_SQL, (trips,)).fetchall():
            rows = conn.execute(_PICKED_SQL, (pk, per_trip)).fetchall() \
                or conn.execute(_BEST_SQL, (pk, per_trip)).fetchall()
            if not rows:
                continue
            index = len(out_trips)
            out_trips.append({"place": place or "", "start": int(start + EPOCH),
                              "end": int((end or start) + EPOCH)})
            for uuid, directory, filename, width, height, created in rows:
                if not uuid or not UUID.match(uuid):
                    continue
                photos.append({
                    "id": uuid, "trip": index,
                    # ZWIDTH/ZHEIGHT 是摆正之后的尺寸
                    "portrait": bool(width and height and height > width),
                    "at": int(created + EPOCH) if created else None,
                    "_src": (directory or "", filename or ""),
                })
        return {"trips": out_trips, "photos": photos}
    finally:
        conn.close()


def crop_plan(width, height, target):
    """把 width×height 铺满 target（短边贴齐、居中裁掉多余）：返回 (缩放后宽, 高)。"""
    tw, th = target
    scale = max(tw / width, th / height)
    # 向上取整再裁，避免四舍五入后差一个像素铺不满；先 round 掉浮点误差，免得 800.0000001 变 801
    return math.ceil(round(width * scale, 6)), math.ceil(round(height * scale, 6))


def _source(uuid, directory, filename):
    """图库里最合适的一份源图：1024 渲染件 > 2048 渲染件 > 原图。"""
    derivatives = LIBRARY / "resources" / "derivatives" / directory
    for suffix in ("_1_105_c.jpeg", "_1_102_o.jpeg"):
        p = derivatives / f"{uuid}{suffix}"
        if p.is_file():
            return p
    p = LIBRARY / "originals" / directory / filename
    return p if p.is_file() else None


def _size(path):
    r = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
                       capture_output=True, text=True, timeout=TIMEOUT, check=True)
    w = re.search(r"pixelWidth: (\d+)", r.stdout)
    h = re.search(r"pixelHeight: (\d+)", r.stdout)
    if not w or not h:
        raise ValueError(f"读不出尺寸：{path}")
    return int(w.group(1)), int(h.group(1))


class Album:
    """缓存照片列表，按需出图。线程安全：HTTP 线程可以同时调。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._render_lock = threading.Lock()
        self._at = 0.0
        self._value = None
        self._index = {}

    def read(self):
        with self._lock:
            now = time.monotonic()
            if self._value is None or now - self._at >= REFRESH_SECONDS:
                db = LIBRARY / "database" / "Photos.sqlite"
                try:
                    value = query(db)
                except sqlite3.Error as exc:
                    # 最常见的原因：后台服务没有读图库的权限（见 README「相册页」）
                    print(f"[photos] 读图库失败：{exc}", file=sys.stderr)
                    value = {"trips": [], "photos": [], "error": str(exc)}
                # 开了「优化 Mac 储存空间」的话，老照片可能只在 iCloud。没有本地文件的不进列表，
                # 页面就不会请求到一张出不了的图；这里只 stat 几百个文件，十分钟一次
                for p in value["photos"]:
                    p["_path"] = _source(p["id"], *p["_src"])
                value["photos"] = [p for p in value["photos"] if p["_path"]]
                self._index = {p["id"]: p for p in value["photos"]}
                self._value = {**value, "photos": [
                    {k: v for k, v in p.items() if not k.startswith("_")} for p in value["photos"]]}
                self._at = now
            return self._value

    def image(self, uuid):
        """id 对应的屏幕尺寸 JPEG 字节；不在列表里或转换失败返回 None。"""
        if self._value is None:
            self.read()
        photo = self._index.get(uuid)
        if photo is None:
            return None
        out = CACHE_DIR / f"{uuid}.jpg"
        with self._render_lock:  # 一张 sips 一两百毫秒，并发跑只会互相抢
            try:
                if not out.exists():
                    src = photo["_path"]
                    target = HALF if photo["portrait"] else SCREEN
                    w, h = crop_plan(*_size(src), target)
                    CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    tmp = out.with_suffix(".tmp.jpg")
                    # sips 按参数顺序执行：先等比缩放到铺满，再居中裁成目标尺寸
                    subprocess.run(
                        ["sips", "-s", "format", "jpeg", "-s", "formatOptions", QUALITY,
                         "-z", str(h), str(w), "-c", str(target[1]), str(target[0]),
                         str(src), "--out", str(tmp)],
                        capture_output=True, timeout=TIMEOUT, check=True,
                    )
                    tmp.replace(out)
                return out.read_bytes()
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                print(f"[photos] 生成 {uuid} 失败：{exc}", file=sys.stderr)
                return None


_album = Album()


def read():
    return _album.read()


def image(uuid):
    return _album.image(uuid)
