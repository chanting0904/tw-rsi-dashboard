import datetime as dt
import sqlite3
import unittest

import monthly_revenue as mr

PAGE = ("<table><tr><th class=tt>公司<br>代號</th><th>公司名稱</th></tr>"
        "<tr align=right><td align=center>1101</td><td align=left>台泥</td><td nowrap>  16,357,176</td>"
        "<td nowrap> 14,321,556</td><td nowrap> 13,882,248</td><td nowrap>14.21</td><Td nowrap>17.82</td></tr>"
        "<tr align=right><td align=center>2330</td><td>台積電</td><td>不適用</td><td>1</td><td>不適用</td></tr>"
        "<tr><td>合計</td><td>合計</td><td>1</td><td>1</td><td>1</td></tr></table>")


def memory():
    con = sqlite3.connect(":memory:")
    con.execute(mr.SCHEMA)
    return con


class MonthlyRevenueTest(unittest.TestCase):
    def test_parse_skips_headers_and_totals(self):
        self.assertEqual(mr.parse(PAGE), [("1101", "台泥", 16357176, 13882248), ("2330", "台積電", None, None)])

    def test_backfill_uses_deadline_and_live_keeps_first_seen(self):
        con = memory()
        mr.store(con, "sii", 2025, 12, {"1101": ("台泥", 10, 9)}, "backfill", dt.date(2026, 11, 23))
        self.assertEqual(con.execute("SELECT available FROM revenue").fetchone()[0], "2026-01-11")
        mr.store(con, "sii", 2026, 10, {"1101": ("台泥", 10, 9)}, "live", dt.date(2026, 11, 5))
        added, changed = mr.store(con, "sii", 2026, 10, {"1101": ("台泥", 11, 9)}, "live", dt.date(2026, 11, 20))
        self.assertEqual((added, changed), (0, 1))
        row = con.execute("SELECT revenue, available, updated FROM revenue WHERE ym='2026-10'").fetchone()
        self.assertEqual(row, (11, "2026-11-05", "2026-11-20"))

    def test_check_waits_for_deadline_then_requires_full_month(self):
        con = memory()
        for n, market in enumerate(mr.MARKETS):
            full = {f"{n + 1}{i:03d}": ("x", 1, 1) for i in range(900)}
            mr.store(con, market, 2026, 9, full, "backfill", dt.date(2026, 11, 1))
        mr.check(con, dt.date(2026, 11, 11))          # 期限隔天前只檢查 9 月
        with self.assertRaises(SystemExit):
            mr.check(con, dt.date(2026, 11, 12))      # 10 月仍空 → 告警


if __name__ == "__main__":
    unittest.main()
