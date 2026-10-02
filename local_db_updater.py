# -*- coding: utf-8 -*-
"""
本地資料庫每日增量更新器（local_db_updater.py）
============================================================
- 優先 TWSE + TPEx 官方免費源（台灣本機最穩、無流量限制）
- 抓「當日」收盤價 / 成交金額 / 成交股數 → 增量 append 進 local_db/*.feather
- FinLab 只當 fallback（官方源失敗或缺檔時）
- 目的：日常運行完全不吃 FinLab 流量（單點依賴備案）
============================================================
用法：
    python local_db_updater.py          # 每日盤後更新（13:30 後）
    python local_db_updater.py --force  # 強制重新抓取
"""
import os, re, sys, json, ssl, shutil, urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "local_db")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
HDR = {"User-Agent": "Mozilla/5.0"}


def _get(url):
    req = urllib.request.Request(url, headers=HDR)
    return urllib.request.urlopen(req, timeout=25, context=CTX).read().decode("utf-8", "replace")


def twse_all():
    """TWSE 上市全體：代號→(收盤價, 成交金額, 成交股數)"""
    j = json.loads(_get("https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&type=ALLBUT0999"))
    if j.get("stat") != "OK":
        raise RuntimeError("TWSE stat=" + str(j.get("stat")))
    out = {}
    for t in j.get("tables", []):
        f = t.get("fields", [])
        if "證券代號" not in f or "收盤價" not in f:
            continue
        ci, pi = f.index("證券代號"), f.index("收盤價")
        amt_i = f.index("成交金額") if "成交金額" in f else None
        vol_i = f.index("成交股數") if "成交股數" in f else None
        for row in t.get("data", []):
            try:
                c = str(row[ci]).strip()
                pv = str(row[pi]).replace(",", "").strip()
                if not c or not pv or pv == "--":
                    continue
                rec = {"px": float(pv)}
                if amt_i is not None:
                    av = str(row[amt_i]).replace(",", "").strip()
                    rec["amt"] = float(av) if av and av != "--" else None
                if vol_i is not None:
                    vv = str(row[vol_i]).replace(",", "").strip()
                    rec["vol"] = float(vv) if vv and vv != "--" else None
                out[c] = rec
            except Exception:
                pass
    if not out:
        raise RuntimeError("TWSE 無資料")
    d = str(j.get("date", ""))
    if d and len(d) == 8:
        d = "%s-%s-%s" % (d[:4], d[4:6], d[6:8])
    return out, d


def tpex_all():
    """TPEx 上櫃全體：代號→(收盤價, 成交金額, 成交股數)"""
    rows = json.loads(_get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"))
    out, d = {}, None
    for r in rows:
        try:
            c = str(r.get("SecuritiesCompanyCode", "")).strip()
            pv = str(r.get("Close", "")).replace(",", "").strip()
            if not c or not pv or pv == "--":
                continue
            rec = {"px": float(pv)}
            av = str(r.get("Amount", "")).replace(",", "").strip()
            rec["amt"] = float(av) if av and av != "--" else None
            vv = str(r.get("Volume", "")).replace(",", "").strip()
            rec["vol"] = float(vv) if vv and vv != "--" else None
            out[c] = rec
            dd = str(r.get("Date", ""))
            if dd:
                dd = "%04d%s" % (int(dd[:3]) + 1911, dd[3:])
                d = "%s-%s-%s" % (dd[:4], dd[4:6], dd[6:8])
        except Exception:
            pass
    if not out:
        raise RuntimeError("TPEx 無資料")
    return out, d


def finlab_all(token):
    """FinLab fallback：抓全市場當日收盤價（資料日=最新交易日）"""
    import finlab
    from finlab import data
    finlab.login(api_token=token)
    data.set_storage(data.FileStorage(os.path.join(HERE, "finlab_db")))
    raw = data.get("price:收盤價")
    last = raw.dropna(how="all").index[-1]
    row = raw.loc[last]
    out = {str(c): {"px": float(v), "amt": None, "vol": None}
           for c, v in row.items() if v == v and float(v) > 0}
    d = last.strftime("%Y-%m-%d")
    return out, d


def merge_day(db_file, day, recs):
    """把當日資料寫進 feather（date 欄格式），回傳是否新增"""
    df = pd.read_feather(db_file)
    if "date" not in df.columns:
        raise ValueError(f"{db_file} 無 date 欄")
    # 若該日已存在 → 直接更新該列（避免重複）
    if day in df["date"].astype(str).values:
        return False, df
    new_row = {"date": pd.Timestamp(day)}
    for c in df.columns[1:]:
        v = recs.get(str(c))
        new_row[c] = v["px"] if v else None
    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return True, df


def merge_amt(db_file, day, recs, field="amt"):
    df = pd.read_feather(db_file)
    if "date" not in df.columns:
        raise ValueError(f"{db_file} 無 date 欄")
    if day in df["date"].astype(str).values:
        return False, df
    new_row = {"date": pd.Timestamp(day)}
    for c in df.columns[1:]:
        v = recs.get(str(c))
        new_row[c] = v[field] if v else None
    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return True, df


def main():
    import pandas as pd
    force = "--force" in sys.argv
    if not os.path.isdir(DB):
        print("[本地庫] local_db 不存在（GitHub Actions 端），跳過本地更新")
        return
    # 1. 先看本地庫最新日期
    close_f = os.path.join(DB, "close.feather")
    last_local = None
    if os.path.exists(close_f):
        df = pd.read_feather(close_f)
        last_local = pd.to_datetime(df["date"]).max()
    today = datetime.now().strftime("%Y-%m-%d")
    if last_local and not force:
        last_s = str(last_local.date())
        if last_s >= today:
            # 本地已是今天或更新 → 不需更新
            print(f"本地庫已是最新（{last_s}），跳過")
            return

    # 2. 官方源（TWSE + TPEx）
    px, dstr = {}, None
    for name, fn in (("TWSE", twse_all), ("TPEx", tpex_all)):
        try:
            p, d = fn()
            px.update(p)
            if d:
                dstr = max(dstr, d) if dstr else d
            print(f"[OK] {name}: {len(p)} 檔")
        except Exception as e:
            print(f"[FAIL] {name}: {e}")

    # 3. FinLab fallback（官方源完全失敗或缺當日）
    if not px or (dstr and dstr != today and last_local and str(last_local.date()) == today):
        pass  # 官方源失敗才用
    if not px:
        token = (os.environ.get("FINLAB_TOKEN") or "").strip()
        if not token:
            try:
                token = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8")).get("FINLAB_TOKEN", "")
            except Exception:
                token = ""
        if token:
            print("-> 官方源失敗，FinLab fallback…")
            try:
                px, dstr = finlab_all(token)
                print(f"[OK] FinLab: {len(px)} 檔，資料日 {dstr}")
            except Exception as e:
                print(f"[FAIL] FinLab: {e}")
    if not px:
        print("[FAIL] 所有來源皆失敗，本地庫未更新")
        sys.exit(1)

    # 4. 寫入本地庫（close / amount / volume）
    day = dstr or today
    for fname, field in (("close.feather", "px"), ("amount.feather", "amt"), ("volume.feather", "vol")):
        fp = os.path.join(DB, fname)
        if not os.path.exists(fp):
            continue
        try:
            added, df = merge_amt(fp, day, px, field)
            if added:
                tmp = fp + ".tmp"
                df.to_feather(tmp)
                os.replace(tmp, fp)
                print(f"[OK] {fname} +{day} ({len(px)} 檔)")
            else:
                print(f"[SKIP] {fname} {day} 已存在")
        except Exception as e:
            print(f"[ERR] {fname}: {e}")

    print("✅ 本地庫更新完成，資料日", day)


if __name__ == "__main__":
    main()
