"""Mac 状态页的解析检查，样本取自真机输出。

    vendor/mibe/.venv/bin/python -m unittest discover -s bridge -p 'test_*.py' -v
"""
import unittest
import unittest.mock

import system

VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                  1497412.
Pages wired down:                            1670444.
Pages purgeable:                              104471.
"Translation faults":                     1069310746.
Anonymous pages:                             1890803.
Pages occupied by compressor:                 605821.
"""

NETSTAT = """Name       Mtu   Network       Address            Ipkts Ierrs     Ibytes    Opkts Oerrs     Obytes  Coll
lo0        16384 <Link#1>                       9000     0     500000     9000     0     500000     0
en0        1500  <Link#16>   02:00:00:00:00:00  5337352     0 1359379884   290225     0  128123590     0
en0        1500  fe80::1     fe80:4::1        5337352     - 1359379884   290225     -  128123590     -
en1        1500  <Link#17>                            10     0       1000       10     0       2000     0
ipsec0     1400  <Link#30>                          8000     0  900000000     8000     0  100000000     0
"""

PS = """ 13.5 /System/Library/PrivateFrameworks/SkyLight.framework/Resources/WindowServer
  9.2 Y93TK974AT.com.bjango.istatmenus.status.mas
  4.7 /Applications/Ghostty.app/Contents/MacOS/ghostty
  3.0 ps
  2.8 /Users/me/.local/bin/herdr
"""


class ParseTests(unittest.TestCase):
    def test_memory_matches_activity_monitor(self):
        vm = system.parse_vm_stat(VM_STAT)
        self.assertEqual(vm["page_size"], 16384)
        # App（匿名 - 可清除）+ 联动 + 压缩，不含文件缓存
        self.assertEqual(system.memory_used(vm), (1890803 - 104471 + 1670444 + 605821) * 16384)

    def test_memory_missing_fields(self):
        self.assertIsNone(system.memory_used(system.parse_vm_stat("garbage")))

    def test_netstat_counts_physical_link_rows_once(self):
        # en0 的 IPv6 行、lo0、VPN 的 ipsec0 都不算；没有 MAC 列的 en1 也要对齐
        self.assertEqual(system.parse_netstat(NETSTAT), (1359379884 + 1000, 128123590 + 2000))

    def test_ps_top_names(self):
        self.assertEqual(system.parse_ps(PS), [
            {"name": "WindowServer", "cpu": 14},
            {"name": "Y93TK974AT.com.bjango.istatmenus.status.mas", "cpu": 9},
            {"name": "ghostty", "cpu": 5},
        ])

    def test_battery(self):
        text = "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t100%; charged; 0:00 remaining present: true"
        self.assertEqual(system.parse_battery(text), {"percent": 100, "charging": False, "ac": True})
        text = "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t57%; discharging; 3:10 remaining"
        self.assertEqual(system.parse_battery(text), {"percent": 57, "charging": False, "ac": False})
        text = "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t80%; charging; 0:40 remaining"
        self.assertTrue(system.parse_battery(text)["charging"])

    def test_gpu(self):
        text = ('"PerformanceStatistics" = {"Tiler Utilization %"=34,"Renderer Utilization %"=41,'
                '"Device Utilization %"=42,"In use system memory"=22789980160}')
        self.assertEqual(system.parse_gpu(text), {"percent": 42, "mem": 22789980160})
        self.assertIsNone(system.parse_gpu(""))

    def test_history_columns_and_gap_reset(self):
        sampler = system.Sampler()
        clock = [1000.0]
        with unittest.mock.patch.object(system.time, "monotonic", side_effect=lambda: clock[0]):
            for _ in range(3):
                sampler.read()
                clock[0] += system.CACHE_SECONDS
            h = sampler.read()["history"]
            self.assertEqual(len(h["cpu"]), 4)
            self.assertIsNone(h["cpu"][0])  # 第一次没有可比的上一轮
            self.assertEqual(set(h), {"cpu", "gpu", "mem", "rx", "tx"})
            clock[0] += system.HISTORY_GAP + 1  # 没人看屏一阵子
            self.assertEqual(len(sampler.read()["history"]["cpu"]), 1)

    def test_desktop_without_battery(self):
        self.assertIsNone(system.parse_battery("Now drawing from 'AC Power'\n"))

    def test_cpu_percent(self):
        self.assertEqual(system.cpu_percent((100, 50, 1000, 0), (209, 104, 2636, 0)), 9.1)
        self.assertIsNone(system.cpu_percent(None, (1, 1, 1, 0)))
        # 32 位 tick 回绕：差值为负，这一轮不报
        self.assertIsNone(system.cpu_percent((4294967000, 0, 0, 0), (10, 5, 5, 0)))

    def test_real_sample_shape(self):
        sampler = system.Sampler()
        first = sampler.read()
        self.assertIsNone(first["cpu"])  # 第一次没有可比的上一轮
        self.assertIs(sampler.read(), first)  # 缓存期内不重采
        self.assertGreater(first["mem"]["total"], 0)
        self.assertTrue(0 <= first["disk"]["percent"] <= 100)


if __name__ == "__main__":
    unittest.main()
