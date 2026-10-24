"""相册页的检查：用和「照片」图库同结构的小库，不碰真图库。

    vendor/mibe/.venv/bin/python -m unittest discover -s bridge -p 'test_*.py' -v
"""
import sqlite3
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import photos

U = "AC6664E7-D9F2-43CA-89A7-DC39EACCB2%02d"


def make_library(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
    create table ZPHOTOSHIGHLIGHT (Z_PK integer primary key, ZKIND int, ZSUBTITLE text,
                                   ZSTARTDATE real, ZENDDATE real);
    create table ZASSET (Z_PK integer primary key, ZUUID text, ZDIRECTORY text, ZFILENAME text,
                         ZWIDTH int, ZHEIGHT int, ZDATECREATED real, ZKIND int, ZHIDDEN int,
                         ZTRASHEDSTATE int, ZISDETECTEDSCREENSHOT int, ZOVERALLAESTHETICSCORE real,
                         ZDAYGROUPHIGHLIGHTBEINGASSETS int, ZDAYGROUPHIGHLIGHTBEINGSUMMARYASSETS int);
    """)
    conn.executemany("insert into ZPHOTOSHIGHLIGHT values (?,?,?,?,?)", [
        (1, 3, "Japan", 800000000, 800500000),        # 旅行，有精选
        (2, 3, "Beijing", 700000000, 700100000),      # 更早的旅行，没精选
        (3, 0, "Shanghai", 900000000, 900000000),     # 普通的一天，不是旅行
        (4, 3, "Empty", 600000000, 600000000),        # 一张能用的都没有
    ])
    rows = [
        # uuid, w, h, created, kind, hidden, trashed, screenshot, score, day-group, summary
        (U % 1, 4000, 3000, 800000300, 0, 0, 0, 0, .5, 1, 1),
        (U % 2, 3000, 4000, 800000100, 0, 0, 0, 0, .4, 1, 1),   # 竖图，拍得早
        (U % 3, 4000, 3000, 800000200, 0, 0, 0, 0, .9, 1, None),  # 没进精选
        (U % 4, 4000, 3000, 800000400, 0, 1, 0, 0, .9, 1, 1),   # 隐藏
        (U % 5, 4000, 3000, 800000500, 1, 0, 0, 0, .9, 1, 1),   # 视频
        (U % 6, 4000, 3000, 800000600, 0, 0, 0, 1, .9, 1, 1),   # 截屏
        (U % 7, 4000, 3000, 700000100, 0, 0, 0, 0, .2, 2, None),
        (U % 8, 4000, 3000, 700000200, 0, 0, 0, 0, .8, 2, None),
        (U % 9, 4000, 3000, 700000300, 0, 0, 0, 0, .5, 2, None),
        (U % 10, 4000, 3000, 900000100, 0, 0, 0, 0, .9, 3, 3),
        (U % 11, 4000, 3000, 600000100, 0, 0, 1, 0, .9, 4, 4),  # 废纸篓
        ("not-a-uuid", 4000, 3000, 800000700, 0, 0, 0, 0, .9, 1, 1),
    ]
    conn.executemany(
        "insert into ZASSET (ZUUID, ZDIRECTORY, ZFILENAME, ZWIDTH, ZHEIGHT, ZDATECREATED, ZKIND, "
        "ZHIDDEN, ZTRASHEDSTATE, ZISDETECTEDSCREENSHOT, ZOVERALLAESTHETICSCORE, "
        "ZDAYGROUPHIGHLIGHTBEINGASSETS, ZDAYGROUPHIGHLIGHTBEINGSUMMARYASSETS) "
        "values (?,'A','x.heic',?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = Path(self.dir.name) / "Photos Library" / "Photos.sqlite"  # 路径带空格，图库就是这样
        self.db.parent.mkdir()
        make_library(self.db)

    def tearDown(self):
        self.dir.cleanup()

    def test_trips_newest_first_with_curated_photos_in_time_order(self):
        r = photos.query(self.db)
        self.assertEqual([t["place"] for t in r["trips"]], ["Japan", "Beijing"])
        self.assertEqual(r["trips"][0]["start"], 800000000 + photos.EPOCH)
        japan = [p["id"] for p in r["photos"] if p["trip"] == 0]
        # 只要精选里的，按拍摄时间；隐藏、视频、截屏、坏 id 都剔掉
        self.assertEqual(japan, [U % 2, U % 1])
        self.assertTrue(r["photos"][0]["portrait"])
        self.assertFalse(r["photos"][1]["portrait"])

    def test_trip_without_curation_falls_back_to_best_scored(self):
        r = photos.query(self.db, per_trip=2)
        beijing = [p["id"] for p in r["photos"] if p["trip"] == 1]
        self.assertEqual(beijing, [U % 8, U % 9])  # 分最高的两张，再按时间排

    def test_trip_limit(self):
        r = photos.query(self.db, trips=1)
        self.assertEqual(len(r["trips"]), 1)
        self.assertEqual({p["trip"] for p in r["photos"]}, {0})

    def test_database_is_opened_read_only(self):
        uri = "file:" + photos.quote(str(self.db)) + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("delete from ZASSET")
        conn.close()


class CropTests(unittest.TestCase):
    def test_landscape_fills_screen(self):
        self.assertEqual(photos.crop_plan(1024, 768, photos.SCREEN), (800, 600))
        self.assertEqual(photos.crop_plan(2048, 1024, photos.SCREEN), (960, 480))

    def test_portrait_fills_half(self):
        self.assertEqual(photos.crop_plan(768, 1024, photos.HALF), (400, 534))
        self.assertEqual(photos.crop_plan(480, 2000, photos.HALF), (400, 1667))

    def test_never_short_by_a_pixel(self):
        for w, h in ((1023, 767), (1000, 601), (3, 2)):
            cw, ch = photos.crop_plan(w, h, photos.SCREEN)
            self.assertGreaterEqual(cw, 800)
            self.assertGreaterEqual(ch, 480)


class AlbumTests(unittest.TestCase):
    def test_photos_without_local_file_are_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            library = Path(d)
            (library / "database").mkdir()
            make_library(library / "database" / "Photos.sqlite")
            # 只有 U%1 有本地渲染件，U%2 只在 iCloud
            derivatives = library / "resources" / "derivatives" / "A"
            derivatives.mkdir(parents=True)
            (derivatives / f"{U % 1}_1_105_c.jpeg").write_bytes(b"x")
            with unittest.mock.patch.object(photos, "LIBRARY", library):
                r = photos.Album().read()
            self.assertEqual([p["id"] for p in r["photos"]], [U % 1])
            self.assertNotIn("_src", r["photos"][0])  # 本地路径不发给页面


class ImageTests(unittest.TestCase):
    def test_only_listed_ids_are_served(self):
        album = photos.Album()
        album._value = {"trips": [], "photos": []}
        album._index = {}
        self.assertIsNone(album.image(U % 1))
        self.assertIsNone(album.image("../../etc/passwd"))


if __name__ == "__main__":
    unittest.main()
