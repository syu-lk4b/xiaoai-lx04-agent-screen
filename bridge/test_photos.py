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
P = "000E78F3-5D1F-4524-9628-EC2939E00A%02d"


NOW = 900000000 + photos.EPOCH  # 测试里的「现在」：Core Data 时间 900000000
YEAR = 365.25 * 86400


def make_library(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
    create table ZMOMENT (Z_PK integer primary key, ZTITLE text, ZSTARTDATE real, ZENDDATE real);
    create table ZASSET (Z_PK integer primary key, ZUUID text, ZDIRECTORY text, ZFILENAME text,
                         ZWIDTH int, ZHEIGHT int, ZDATECREATED real, ZKIND int, ZHIDDEN int,
                         ZTRASHEDSTATE int, ZISDETECTEDSCREENSHOT int, ZMOMENT int,
                         ZOVERALLAESTHETICSCORE real default .5);
    create table ZPERSON (Z_PK integer primary key, ZPERSONUUID text, ZFULLNAME text, ZDISPLAYNAME text,
                          ZTYPE int default 0, ZFACECOUNT int default 10, ZDETECTIONTYPE int default 1,
                          ZMERGETARGETPERSON int, ZKEYFACE int);
    create table ZFACECROP (Z_PK integer primary key, ZPERSON int, ZFACE int, ZTYPE int default 1, ZRESOURCEDATA blob);
    create table ZDETECTEDFACE (Z_PK integer primary key, ZASSETFORFACE int, ZSIZE real, ZHIDDEN int,
                                ZDETECTIONTYPE int default 1, ZCENTERX real default .5,
                                ZCENTERY real default .5, ZPERSONFORFACE int);
    """)
    conn.executemany("insert into ZPERSON (Z_PK, ZPERSONUUID, ZFULLNAME, ZTYPE, ZFACECOUNT) values (?,?,?,?,?)", [
        (1, P % 1, "家人甲", 1, 500),
        (2, P % 2, "家人乙", 0, 200),
        (3, P % 3, None, 0, 50),
    ])
    conn.execute("insert into ZFACECROP (Z_PK, ZPERSON, ZRESOURCEDATA) values (1, 1, ?)", (b"\xff\xd8\xff\xe0fakejpeg",))
    conn.executemany("insert into ZMOMENT values (?,?,?,?)", [
        (1, "Los Angeles", 899000000, 899100000),
        (2, None, 880000000, 880000500),             # 没标题的时刻
    ])
    rows = [
        # pk, uuid, w, h, created, kind, hidden, trashed, screenshot, moment
        (1, U % 1, 4000, 3000, 899000100, 0, 0, 0, 0, 1),
        (2, U % 2, 3000, 4000, 899000200, 0, 0, 0, 0, 1),   # 竖图
        (3, U % 3, 3000, 4000, 880000100, 0, 0, 0, 0, 2),   # 竖图，另一个时刻
        (4, U % 4, 4000, 3000, 899000300, 0, 1, 0, 0, 1),   # 隐藏
        (5, U % 5, 4000, 3000, 899000400, 1, 0, 0, 0, 1),   # 视频
        (6, U % 6, 4000, 3000, 899000500, 0, 0, 0, 1, 1),   # 截屏
        (7, U % 7, 4000, 3000, 899000600, 0, 0, 1, 0, 1),   # 废纸篓
        (8, U % 8, 4000, 3000, 899000700, 0, 0, 0, 0, 1),   # 只有背景里的小脸
        (9, U % 9, 4000, 3000, 899000800, 0, 0, 0, 0, 1),   # 脸被用户隐藏
        (10, U % 10, 4000, 3000, 899000900, 0, 0, 0, 0, 1),  # 没有脸的风景
        (11, U % 11, 4000, 3000, 900000000 - 3 * YEAR, 0, 0, 0, 0, None),  # 三年前
        (12, "not-a-uuid", 4000, 3000, 899001000, 0, 0, 0, 0, 1),
        (13, U % 13, 4000, 3000, 899001100, 0, 0, 0, 0, 1),  # 狗
        (14, U % 14, 4000, 3000, 899001200, 0, 0, 0, 0, 1),  # 证件翻拍：评分低
        (15, U % 15, 4000, 3000, 899001300, 0, 0, 0, 0, 1),  # 还没评分
    ]
    conn.executemany(
        "insert into ZASSET (Z_PK, ZUUID, ZDIRECTORY, ZFILENAME, ZWIDTH, ZHEIGHT, ZDATECREATED, "
        "ZKIND, ZHIDDEN, ZTRASHEDSTATE, ZISDETECTEDSCREENSHOT, ZMOMENT) "
        "values (?,?,'A','x.heic',?,?,?,?,?,?,?,?)", rows)
    conn.execute("update ZASSET set ZOVERALLAESTHETICSCORE = .15 where Z_PK = 14")
    conn.execute("update ZASSET set ZOVERALLAESTHETICSCORE = null where Z_PK = 15")
    faces = [(pk, .2, 0) for pk in (1, 2, 3, 4, 5, 6, 7, 11, 12, 14, 15)]
    faces += [(8, .01, 0), (9, .3, 1)]
    conn.executemany("insert into ZDETECTEDFACE (ZASSETFORFACE, ZSIZE, ZHIDDEN) values (?,?,?)", faces)
    # 资产 1 属于家人甲（person 1），资产 2 属于家人乙（person 2），资产 3 属于未命名路人（person 3）
    conn.execute("update ZDETECTEDFACE set ZPERSONFORFACE = 1 where ZASSETFORFACE = 1")
    conn.execute("update ZDETECTEDFACE set ZPERSONFORFACE = 2 where ZASSETFORFACE = 2")
    conn.execute("update ZDETECTEDFACE set ZPERSONFORFACE = 3 where ZASSETFORFACE = 3")
    conn.executemany(
        "insert into ZDETECTEDFACE (ZASSETFORFACE, ZSIZE, ZHIDDEN, ZDETECTIONTYPE, ZCENTERX, ZCENTERY, ZPERSONFORFACE) "
        "values (?,?,?,?,?,?,?)", [
            (1, .01, 0, 1, .9, .1, 1),    # 背景小脸：不参与对焦
            (1, .2, 0, 1, .1, .9, 2),     # 同一张照片里的家人乙：不选家人时两张脸一起对焦，只选家人甲时只对焦家人甲
            (13, .3, 0, 3, .5, .5, None),
        ])
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

    def test_only_recent_photos_with_a_real_face(self):
        r = photos.query(self.db, now=NOW)
        # 隐藏、视频、截屏、废纸篓、只有小脸、脸被隐藏、没脸、宠物、证件翻拍、没评分、三年前、坏 id 都剔掉
        self.assertEqual({p["id"] for p in r["photos"]}, {U % 1, U % 2, U % 3})
        r = photos.query(self.db, years=4, now=NOW)
        self.assertIn(U % 11, {p["id"] for p in r["photos"]})

    def test_focus_is_size_weighted_center_of_real_faces_from_top_left(self):
        focus = {p["id"]: p["_focus"] for p in photos.query(self.db, now=NOW)["photos"]}
        # U%1：两张 0.2 的脸在 (.5,.5) 和 (.1,.9)，背景小脸不算；y 从左下原点翻成左上
        self.assertAlmostEqual(focus[U % 1][0], .3)
        self.assertAlmostEqual(focus[U % 1][1], .3)
        self.assertEqual(focus[U % 2], (.5, .5))

    def test_filter_by_selected_family_people_and_focus_only_on_them(self):
        # 只选家人甲（P%1）：只返回 U%1，且对焦点只对准家人甲 (.5, .5)，忽略旁边的其他人 (.1, .9)
        r = photos.query(self.db, people=[P % 1], now=NOW)
        self.assertEqual([p["id"] for p in r["photos"]], [U % 1])
        self.assertEqual(r["photos"][0]["_focus"], (.5, .5))

        # 选家人甲 + 家人乙：返回 U%1 和 U%2，排除路人 U%3
        r2 = photos.query(self.db, people=[P % 1, P % 2], now=NOW)
        self.assertEqual({p["id"] for p in r2["photos"]}, {U % 1, U % 2})

    def test_list_people_and_avatar(self):
        people = photos.list_people(self.db)
        self.assertEqual([p["id"] for p in people], [P % 1, P % 2, P % 3])
        self.assertEqual(people[0]["name"], "家人甲")
        self.assertTrue(people[0]["favorite"])
        # 插入一条 ZTYPE=2（已被用户标记「不是此人」的反例头像），验证不会误取它
        conn = sqlite3.connect(self.db)
        conn.execute("insert into ZFACECROP (Z_PK, ZPERSON, ZFACE, ZTYPE, ZRESOURCEDATA) values (2, 1, 2, 2, ?)", (b"\xff\xd8\xff\xe0wrongface",))
        conn.execute("update ZFACECROP set ZFACE = 1, ZTYPE = 1 where Z_PK = 1")
        conn.commit()
        conn.close()
        self.assertEqual(photos.person_avatar(P % 1, self.db), b"\xff\xd8\xff\xe0fakejpeg")
        self.assertIsNone(photos.person_avatar("invalid-uuid", self.db))

    def test_moment_gives_place_and_dates(self):
        r = photos.query(self.db, now=NOW)
        by_id = {p["id"]: r["moments"][p["moment"]] for p in r["photos"]}
        self.assertEqual(by_id[U % 1], {"place": "Los Angeles", "start": 899000000 + photos.EPOCH,
                                        "end": 899100000 + photos.EPOCH})
        self.assertEqual(by_id[U % 3]["place"], "")
        self.assertIs(by_id[U % 1], by_id[U % 2])  # 同一时刻只出一份

    def test_photo_without_moment_uses_its_own_date(self):
        r = photos.query(self.db, years=4, now=NOW)
        lone = next(p for p in r["photos"] if p["id"] == U % 11)
        self.assertEqual(r["moments"][lone["moment"]]["start"], lone["at"])

    def test_count_limit(self):
        # 坏 id 是抽完才剔的，会让数量随机少一张；这里只测上限
        conn = sqlite3.connect(self.db)
        conn.execute("delete from ZASSET where ZUUID = 'not-a-uuid'")
        conn.commit()
        conn.close()
        self.assertEqual(len(photos.query(self.db, count=2, now=NOW)["photos"]), 2)

    def test_portraits_end_up_adjacent(self):
        r = photos.query(self.db, now=NOW)
        ids = [p["id"] for p in r["photos"]]
        self.assertEqual(abs(ids.index(U % 2) - ids.index(U % 3)), 1)

    def test_pair_portraits(self):
        P = lambda n, portrait: {"id": n, "portrait": portrait}
        out = photos.pair_portraits([P(1, True), P(2, False), P(3, False), P(4, True), P(5, True)])
        self.assertEqual([p["id"] for p in out], [1, 4, 2, 3, 5])

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


class OffsetTests(unittest.TestCase):
    def test_window_follows_faces(self):
        # 960×480 的横图裁 800 宽：脸在最左边就贴左，在最右就贴右，居中就居中
        self.assertEqual(photos.crop_offset((960, 480), photos.SCREEN, (0, .5)), (0, 0))
        self.assertEqual(photos.crop_offset((960, 480), photos.SCREEN, (1, .5)), (160, 0))
        self.assertEqual(photos.crop_offset((960, 480), photos.SCREEN, (.5, .5)), (80, 0))

    def test_faces_sit_in_upper_part_of_window(self):
        # 800×600 裁 480 高：脸在 y=300，放到窗口 40% 处 -> 窗口从 300-192=108 开始
        self.assertEqual(photos.crop_offset((800, 600), photos.SCREEN, (.5, .5)), (0, 108))
        # 脸贴顶就不再往上取（不越界）
        self.assertEqual(photos.crop_offset((800, 600), photos.SCREEN, (.5, .05)), (0, 0))
        self.assertEqual(photos.crop_offset((800, 600), photos.SCREEN, (.5, 1)), (0, 120))


class AlbumTests(unittest.TestCase):
    def test_photos_without_local_file_are_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            library = Path(d)
            (library / "database").mkdir()
            make_library(library / "database" / "Photos.sqlite")
            # 只有 U%1 有本地渲染件，U%2、U%3 只在 iCloud
            derivatives = library / "resources" / "derivatives" / "A"
            derivatives.mkdir(parents=True)
            (derivatives / f"{U % 1}_1_105_c.jpeg").write_bytes(b"x")
            with unittest.mock.patch.object(photos, "LIBRARY", library), \
                    unittest.mock.patch.object(photos.time, "time", return_value=NOW):
                r = photos.Album().read()
            self.assertEqual([p["id"] for p in r["photos"]], [U % 1])
            self.assertNotIn("_src", r["photos"][0])  # 本地路径不发给页面


class ImageTests(unittest.TestCase):
    def test_only_listed_ids_are_served(self):
        album = photos.Album()
        album._value = {"moments": [], "photos": []}
        album._index = {}
        self.assertIsNone(album.image(U % 1))
        self.assertIsNone(album.image("../../etc/passwd"))


if __name__ == "__main__":
    unittest.main()
