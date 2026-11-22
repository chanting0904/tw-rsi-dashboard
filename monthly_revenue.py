# -*- coding: utf-8 -*-
"""上市櫃月營收資料庫（研究用；選股與調倉不讀取這份資料）。

    python monthly_revenue.py update                 # 排程：抓最近兩個月（新公布與更正）
    python monthly_revenue.py backfill 2013-01       # 一次性補歷史（較慢，每頁間隔數秒）
    python monthly_revenue.py fill-delisted          # 一次性：已下市公司歷史改由 FinLab 補（觀測站不保留）
    python monthly_revenue.py check                  # 只做完整性檢查
    加上 --release：執行前從 GitHub Release 下載、完成後上傳 monthly_revenue.sqlite3

資料來源：公開資訊觀測站月報表（mopsov.twse.com.tw/nas/t21/{sii,otc}/t21sc03_民國年_月_{0,1}.html），
單位千元。available 欄為回測可用日：排程實際第一次抓到的日期；補歷史時無從得知，保守用次月 11 日
（法定最晚次月 10 日公布）。
"""
import argparse
import datetime as dt
import html
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
DB = ROOT / "local_db" / "monthly_revenue.sqlite3"
TAG = "local-db"
URL = "https://mopsov.twse.com.tw/nas/t21/{market}/t21sc03_{roc}_{month}_{kind}.html"
MARKETS = ("sii", "otc")
TPE = dt.timezone(dt.timedelta(hours=8))
# 單一市場整月公司數低於此值視為抓取不完整（2013 年起上市約 800+、上櫃約 600+ 家）
MIN_ROWS = {"sii": 700, "otc": 500}
ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
TAGS = re.compile(r"<[^>]+>")
CODE = re.compile(r"^[0-9A-Z]{4,6}$")

SCHEMA = """CREATE TABLE IF NOT EXISTS revenue(
    ym TEXT NOT NULL,            -- 營收所屬年月 YYYY-MM
    code TEXT NOT NULL,
    market TEXT NOT NULL,        -- sii 上市 / otc 上櫃
    name TEXT,
    revenue INTEGER,             -- 當月營收（千元）
    last_year INTEGER,           -- 去年同月營收（千元）
    available TEXT NOT NULL,     -- 回測可使用日 YYYY-MM-DD
    source TEXT NOT NULL,        -- live 排程抓到 / backfill 補歷史
    updated TEXT NOT NULL,       -- 最後一次數值變動日
    PRIMARY KEY (ym, code))"""


def today():
    return dt.datetime.now(TPE).date()


def months(start, end):
    y, m = start
    while (y, m) <= end:
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def prev_month(y, m, n=1):
    for _ in range(n):
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return y, m


def deadline(y, m):
    """法定公布期限次月 10 日 → 次月 11 日起可用。"""
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    return dt.date(ny, nm, 11)


def number(text):
    text = text.replace(",", "").strip()
    return int(text) if re.fullmatch(r"-?\d+", text) else None


def parse(page):
    """回傳 [(code, name, revenue, last_year)]；跳過表頭、小計與合計列。"""
    rows = []
    for row in ROW.findall(page):
        cells = [html.unescape(TAGS.sub("", c)).strip() for c in CELL.findall(row)]
        if len(cells) < 5 or not CODE.match(cells[0]):
            continue
        rows.append((cells[0], cells[1], number(cells[2]), number(cells[4])))
    return rows


def fetch(market, y, m, session, pause=0.0):
    """抓一個市場一個月份（本國與外國企業兩頁）。網頁尚未建立時回傳 None。"""
    out, seen = {}, False
    for kind in (0, 1):
        if pause:
            time.sleep(pause)
        url = URL.format(market=market, roc=y - 1911, month=m, kind=kind)
        for attempt in range(4):
            try:
                r = session.get(url, timeout=60)
                break
            except requests.RequestException:
                if attempt == 3:
                    raise
                time.sleep(10 * (attempt + 1))
        if r.status_code == 404:
            continue
        r.raise_for_status()
        seen = True
        for code, name, rev, ly in parse(r.content.decode("cp950", "replace")):
            out[code] = (name, rev, ly)
    return out if seen else None


def connect():
    DB.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB)
    con.execute(SCHEMA)
    return con


def store(con, market, y, m, rows, source, now):
    """新增或更新；已有資料只在數值變動時更新，available 與 source 保留第一次的紀錄。"""
    ym = f"{y:04d}-{m:02d}"
    first = (now if source == "live" else deadline(y, m)).isoformat()
    added = changed = 0
    for code, (name, rev, ly) in rows.items():
        old = con.execute("SELECT revenue, last_year FROM revenue WHERE ym=? AND code=?", (ym, code)).fetchone()
        if old is None:
            con.execute("INSERT INTO revenue VALUES (?,?,?,?,?,?,?,?,?)",
                        (ym, code, market, name, rev, ly, first, source, now.isoformat()))
            added += 1
        elif old != (rev, ly):
            con.execute("UPDATE revenue SET name=?, revenue=?, last_year=?, updated=? WHERE ym=? AND code=?",
                        (name, rev, ly, now.isoformat(), ym, code))
            changed += 1
    con.commit()
    return added, changed


def count(con, market, y, m):
    return con.execute("SELECT COUNT(*) FROM revenue WHERE market=? AND ym=?",
                       (market, f"{y:04d}-{m:02d}")).fetchone()[0]


def check(con, now=None):
    """法定期限過後 2 天，上個月每個市場的筆數須達下限且不少於前一個月的 95%。"""
    now = now or today()
    y, m = prev_month(now.year, now.month)
    if now < deadline(y, m) + dt.timedelta(days=1):
        y, m = prev_month(y, m)
    problems = []
    for market in MARKETS:
        n, before = count(con, market, y, m), count(con, market, *prev_month(y, m))
        if n < MIN_ROWS[market] or n < before * 0.95:
            problems.append(f"{market} {y}-{m:02d} 只有 {n} 筆（前月 {before} 筆）")
    if problems:
        raise SystemExit("月營收不完整：" + "；".join(problems))
    print(f"月營收完整性通過：{y}-{m:02d}", {k: count(con, k, y, m) for k in MARKETS})


def session():
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 (research data sync)"
    return s


def update(con, now=None):
    now = now or today()
    s = session()
    for y, m in (prev_month(now.year, now.month, 2), prev_month(now.year, now.month)):
        for market in MARKETS:
            rows = fetch(market, y, m, s, pause=2)
            if rows is None:
                print(f"{market} {y}-{m:02d} 尚未公布")
                continue
            added, changed = store(con, market, y, m, rows, "live", now)
            print(f"{market} {y}-{m:02d}：共 {len(rows)} 筆，新增 {added}、更正 {changed}")


def backfill(con, start):
    now = today()
    s = session()
    end = prev_month(now.year, now.month)
    for y, m in months(start, end):
        for market in MARKETS:
            if count(con, market, y, m) >= MIN_ROWS[market]:
                continue
            rows = fetch(market, y, m, s, pause=3)
            if not rows or len(rows) < MIN_ROWS[market]:
                raise SystemExit(f"{market} {y}-{m:02d} 只抓到 {len(rows or {})} 筆，停止補資料")
            added, _ = store(con, market, y, m, rows, "backfill", now)
            print(f"{market} {y}-{m:02d}：{len(rows)} 筆（新增 {added}）", flush=True)


def fill_delisted(con):
    """觀測站歷史月報表只列目前仍上市櫃的公司；已下市公司的歷史營收改由 FinLab 補（一次性、需 finlab）。
    只補最新月份已不在名單上的公司，available 用 FinLab 的實際公布日。"""
    import pandas as pd
    from finlab import data
    now = today().isoformat()
    latest = con.execute("SELECT MAX(ym) FROM revenue WHERE source != 'finlab'").fetchone()[0]
    alive = {c for (c,) in con.execute("SELECT code FROM revenue WHERE ym=?", (latest,))}
    rev, ly = (pd.DataFrame(data.get(k)) for k in ("monthly_revenue:當月營收", "monthly_revenue:去年當月營收"))
    added = 0
    for date, row in rev.iterrows():
        when = pd.Timestamp(date)
        ym = (when.to_period("M") - 1).strftime("%Y-%m")
        if ym < "2013-01":
            continue
        for code, value in row.dropna().items():
            if code in alive or not CODE.match(code):
                continue
            last = ly.at[date, code] if date in ly.index and code in ly.columns else None
            cur = con.execute("INSERT OR IGNORE INTO revenue VALUES (?,?,?,?,?,?,?,?,?)",
                              (ym, code, "delisted", None, int(value),
                               None if pd.isna(last) else int(last), when.date().isoformat(), "finlab", now))
            added += cur.rowcount
    con.commit()
    print("已下市公司補入", added, "筆")


def release(action):
    if action == "download":
        r = subprocess.run(["gh", "release", "download", TAG, "-p", DB.name, "-D", str(DB.parent), "--clobber"],
                           cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit("Release 找不到 " + DB.name + "（請先在本機 backfill 後上傳）：" + r.stderr.strip())
    else:
        subprocess.run(["gh", "release", "upload", TAG, str(DB), "--clobber"], cwd=ROOT, check=True)
        print("已上傳", DB.name)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["update", "backfill", "fill-delisted", "check"])
    p.add_argument("start", nargs="?", default="2013-01")
    p.add_argument("--release", action="store_true")
    a = p.parse_args(argv)
    if a.release:
        release("download")
    con = connect()
    if a.action == "update":
        update(con)
    elif a.action == "backfill":
        y, m = map(int, a.start.split("-"))
        backfill(con, (y, m))
    elif a.action == "fill-delisted":
        fill_delisted(con)
    con.commit()
    if a.release and a.action != "check":
        release("upload")   # 先上傳已抓到的部分，再檢查完整性（不完整只告警，下次排程補齊）
    check(con)
    con.close()


if __name__ == "__main__":
    sys.exit(main())
