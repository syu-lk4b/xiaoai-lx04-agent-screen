#!/usr/bin/env python3
"""相册页的数据：过去两年里有人物的照片，随机抽一批轮播。

不认识锁、不认识 HTTP，只暴露 read() 和 image()。
- 只读打开图库数据库（Photos.sqlite，mode=ro），不改图库、不碰 iCloud，「照片」开着也不冲突
- 「有人物」= ZDETECTEDFACE 里有一张占画面 MIN_FACE 以上的人脸（ZSIZE 是脸宽占画面的比例）。
  不设下限的话一半是风景照里远处的路人：近两年带脸的 1300 多张里，过了 0.05 的约 400 张。
  ZDETECTIONTYPE 1 是人，3/4 是狗/猫——不加这条，宠物照也会混进来
- 证件照（护照、身份证、驾照的翻拍）上的脸也算人脸，不能上屏。它们的美学评分
  ZOVERALLAESTHETICSCORE 都在 0.2 以下，正常合影在 0.35 以上，按 MIN_AESTHETIC 一刀切开；
  没评过分（还没分析）的照片也不放，宁可少一张
- 每次刷新列表都重新 order by random()，一天里看到的不是同一批
- 地点和日期取照片所在的「时刻」（ZMOMENT，「照片」App 按时间地点自动分的组），
  标题是「Los Angeles」「Izu & Izunokuni」这种；不少时刻没有标题，那就只显示日期
- 出图优先用图库里现成的 1024 渲染件（resources/derivatives/…_1_105_c.jpeg，已经套好编辑和方向），
  没有才退到 2048 那份或原图。HEIC 也交给 sips，它能解
- 横图裁成 800×480 整屏；竖图裁成 400×480，页面两张并排放——整屏塞一张竖图会糊或者两侧大黑边。
  裁切窗口对准人脸（按脸大小加权的中心），居中裁的话合影站一排时人头常被切掉。
  ZCENTERX/ZCENTERY 是摆正后画面里的 0–1 坐标，原点在左下（Vision 的约定），y 要翻过来用

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
YEARS = float(os.environ.get("LX04_PHOTO_YEARS", "2"))    # 只取最近几年
COUNT = int(os.environ.get("LX04_PHOTO_COUNT", "300"))    # 每轮抽几张：12 秒一张，一小时轮不完
MIN_FACE = 0.05        # 脸宽至少占画面 5%，挡掉背景里的路人
HUMAN = 1              # ZDETECTIONTYPE：1 人，3 狗，4 猫
MIN_AESTHETIC = 0.3    # 挡证件翻拍和随手拍，见模块说明
FACE_AT = 0.4          # 人脸放在裁切窗口上方 40% 处，头顶留白、身体也带进来
REFRESH_SECONDS = 600  # 十分钟重新抽一次；查询带索引，几十毫秒
CACHE_DIR = Path.home() / "Library" / "Caches" / "lx04-status" / "photos"
SCREEN = (800, 480)
HALF = (400, 480)      # 竖图：两张并排正好一屏
QUALITY = "82"
TIMEOUT = 30
EPOCH = 978307200      # Core Data 的时间从 2001-01-01 算起
UUID = re.compile(r"^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$")

# 隐藏、废纸篓、截屏、视频一律不要；人脸被用户在「照片」里隐藏的也不算
_FACES = """from ZDETECTEDFACE f where f.ZASSETFORFACE = a.Z_PK and f.ZDETECTIONTYPE = :human
            and coalesce(f.ZHIDDEN, 0) = 0 and f.ZSIZE >= :min_face"""
_PEOPLE_SQL = f"""
select a.ZUUID, a.ZDIRECTORY, a.ZFILENAME, a.ZWIDTH, a.ZHEIGHT, a.ZDATECREATED,
       a.ZMOMENT, m.ZTITLE, m.ZSTARTDATE, m.ZENDDATE,
       (select sum(f.ZCENTERX * f.ZSIZE) / sum(f.ZSIZE) {_FACES}),
       (select sum(f.ZCENTERY * f.ZSIZE) / sum(f.ZSIZE) {_FACES})
from ZASSET a left join ZMOMENT m on m.Z_PK = a.ZMOMENT
where a.ZKIND = 0 and a.ZHIDDEN = 0 and a.ZTRASHEDSTATE = 0
  and coalesce(a.ZISDETECTEDSCREENSHOT, 0) = 0
  and coalesce(a.ZOVERALLAESTHETICSCORE, 0) >= :min_aesthetic
  and a.ZDATECREATED >= :since
  and exists (select 1 {_FACES})
order by random() limit :count
"""


def pair_portraits(photos):
    """随机顺序里把竖图两两挨着放：页面把相邻两张竖图拼成一屏，落单的才居中留黑边。"""
    out, lone = [], None
    for p in photos:
        if not p["portrait"]:
            out.append(p)
        elif lone is None:
            lone = p
            out.append(p)
        else:
            out.insert(out.index(lone) + 1, p)
            lone = None
    return out


def query(db_path, years=YEARS, count=COUNT, now=None):
    """图库数据库 -> {"moments": [...], "photos": [...]}，照片随机顺序、竖图成对相邻。

    photos 的每项：id（UUID）、moment（moments 下标）、portrait、at（unix 秒），
    外加 _src、_focus 给本模块出图用，不发给页面。moments 每项：place（可能为空）、start、end。
    """
    since = (time.time() if now is None else now) - years * 365.25 * 86400 - EPOCH
    uri = "file:" + quote(str(db_path)) + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        moments, index, photos = [], {}, []
        params = {"since": since, "min_face": MIN_FACE, "human": HUMAN,
                  "min_aesthetic": MIN_AESTHETIC, "count": count}
        for (uuid, directory, filename, width, height, created,
             moment, place, start, end, fx, fy) in conn.execute(_PEOPLE_SQL, params):
            if not uuid or not UUID.match(uuid):
                continue
            if moment not in index:
                index[moment] = len(moments)
                # 没挂时刻的照片（很少见）就用它自己的拍摄时间当起止
                start = start if start is not None else created
                moments.append({"place": place or "",
                                "start": int(start + EPOCH) if start is not None else None,
                                "end": int((end if end is not None else start) + EPOCH)
                                if start is not None else None})
            photos.append({
                "id": uuid, "moment": index[moment],
                # ZWIDTH/ZHEIGHT 是摆正之后的尺寸
                "portrait": bool(width and height and height > width),
                "at": int(created + EPOCH) if created else None,
                "_src": (directory or "", filename or ""),
                # 人脸中心，换成左上原点；坐标缺了就退回居中
                "_focus": (fx, 1 - fy) if fx is not None and fy is not None else (.5, .5),
            })
        return {"moments": moments, "photos": pair_portraits(photos)}
    finally:
        conn.close()


def crop_plan(width, height, target):
    """把 width×height 铺满 target（短边贴齐、居中裁掉多余）：返回 (缩放后宽, 高)。"""
    tw, th = target
    scale = max(tw / width, th / height)
    # 向上取整再裁，避免四舍五入后差一个像素铺不满；先 round 掉浮点误差，免得 800.0000001 变 801
    return math.ceil(round(width * scale, 6)), math.ceil(round(height * scale, 6))


def crop_offset(scaled, target, focus):
    """缩放后 scaled 尺寸的图里，target 窗口的左上角 (x, y)：对准 focus（0–1，左上原点），贴边不越界。"""
    (w, h), (tw, th), (fx, fy) = scaled, target, focus
    x = round(fx * w - tw / 2)
    y = round(fy * h - th * FACE_AT)
    return min(max(x, 0), w - tw), min(max(y, 0), h - th)


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
        self._params = None
        self._value = None
        self._index = {}

    def read(self, years=None, count=None):
        years = YEARS if years is None else years
        count = COUNT if count is None else count
        params = (years, count)
        with self._lock:
            now = time.monotonic()
            if self._value is None or self._params != params or now - self._at >= REFRESH_SECONDS:
                db = LIBRARY / "database" / "Photos.sqlite"
                try:
                    value = query(db, years=years, count=count)
                except sqlite3.Error as exc:
                    # 最常见的原因：后台服务没有读图库的权限（见 README「相册页」）
                    print(f"[photos] 读图库失败：{exc}", file=sys.stderr)
                    value = {"moments": [], "photos": [], "error": str(exc)}
                # 开了「优化 Mac 储存空间」的话，老照片可能只在 iCloud。没有本地文件的不进列表，
                # 页面就不会请求到一张出不了的图；这里只 stat 几百个文件，十分钟一次
                for p in value["photos"]:
                    p["_path"] = _source(p["id"], *p["_src"])
                value["photos"] = [p for p in value["photos"] if p["_path"]]
                self._index = {p["id"]: p for p in value["photos"]}
                self._value = {**value, "photos": [
                    {k: v for k, v in p.items() if not k.startswith("_")} for p in value["photos"]]}
                self._params = params
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
                    x, y = crop_offset((w, h), target, photo["_focus"])
                    CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    tmp = out.with_suffix(".tmp.jpg")
                    # 先等比缩放到铺满，再从 (x, y) 裁出目标尺寸。必须分两次跑：
                    # 和 -z 写在同一条命令里时 sips 会忽略 --cropOffset，照旧居中裁（sips-316 实测）
                    subprocess.run(
                        ["sips", "-s", "format", "jpeg", "-s", "formatOptions", "100",
                         "-z", str(h), str(w), str(src), "--out", str(tmp)],
                        capture_output=True, timeout=TIMEOUT, check=True,
                    )
                    subprocess.run(
                        ["sips", "-s", "format", "jpeg", "-s", "formatOptions", QUALITY,
                         "-c", str(target[1]), str(target[0]), "--cropOffset", str(y), str(x),
                         str(tmp)],
                        capture_output=True, timeout=TIMEOUT, check=True,
                    )
                    tmp.replace(out)
                return out.read_bytes()
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                print(f"[photos] 生成 {uuid} 失败：{exc}", file=sys.stderr)
                return None


_album = Album()


def read(years=None, count=None):
    return _album.read(years=years, count=count)


def image(uuid):
    return _album.image(uuid)
