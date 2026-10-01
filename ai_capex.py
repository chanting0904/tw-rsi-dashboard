# -*- coding: utf-8 -*-
"""
ai_capex.py — AI 紅利「capex」自動更新（每季財報後執行）
- 台廠（AI/CoWoS 供應鏈）：FinLab financial_statement:取得不動產_廠房及設備（deadline 對齊，公告日後才生效）
  → 失敗時 FinMind TaiwanStockCashFlowStatement 備援
- 美系 Hyperscaler：SEC EDGAR 官方 API（免費免 key）PaymentsToAcquirePropertyPlantAndEquipment
- 輸出：更新 ai_signals.json 的 capex 狀態 + 台美數字明細（保留 monet/cowos 手動值）
GitHub Actions 每月 1 號自動執行（財報季 1/4/7/10 月中財報後下個月 1 號生效）。
"""
import os, io, json, sys, ssl, datetime, urllib.request, urllib.parse
import pandas as pd

AI_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_signals.json")

# AI / CoWoS 供應鏈 watchlist（台廠）
TW_WATCH = ["2330", "2317", "2454", "3008", "3661", "3037", "3189", "3711", "6239", "3443"]
TW_NAMES = {"2330": "台積電", "2317": "鴻海", "2454": "聯發科", "3008": "大立光",
            "3661": "世芯-KY", "3037": "欣興", "3189": "景碩", "3711": "日月光",
            "6239": "力成", "3443": "創意"}

# 美系 Hyperscaler（ticker + CIK 備援）
US_WATCH = {"MSFT": "0000789019", "GOOGL": "0001652044", "META": "0001326801", "AMZN": "0001018724"}
US_NAMES = {"MSFT": "微軟", "GOOGL": "Google", "META": "Meta", "AMZN": "亞馬遜"}
US_TICKERS = {"MSFT": "MSFT", "GOOGL": "GOOGL", "META": "META", "AMZN": "AMZN"}

CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE


def load_cfg():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if os.path.exists(p):
        try:
            return json.load(io.open(p, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def q_of(ts):
    ts = pd.Timestamp(ts)
    return f"{ts.year}-Q{(ts.month - 1) // 3 + 1}"


def get_finlab_capex(token):
    """FinLab：watchlist 最新公告季 + 前季 取得不動產_廠房及設備（仟元）。回傳 {code: (季, 值, 前季值)}"""
    import finlab
    from finlab import data
    finlab.login(api_token=token)
    data.set_storage(data.FileStorage(os.path.join(os.path.dirname(os.path.abspath(__file__)), "finlab_db")))
    close = data.get("price:收盤價")
    cap_q = data.get("financial_statement:取得不動產_廠房及設備").apply(pd.to_numeric, errors="coerce")
    # 用季頻原索引（財報期），不要 reindex 成日頻（會產生每天重複值）
    out = {}
    for c in TW_WATCH:
        try:
            s = cap_q[c].dropna()
            if len(s) < 2:
                out[c] = (None, None, None)
                continue
            cur = abs(float(s.iloc[-1]))
            # 前年同季（往前 4 個財報期）；資料不足時退回上一期
            prev = abs(float(s.iloc[-5])) if len(s) >= 5 else abs(float(s.iloc[-2]))
            out[c] = (q_of(s.index[-1]), cur, prev)
        except Exception:
            out[c] = (None, None, None)
    return out


def get_finmind_capex(token, code):
    """FinMind 備援：TaiwanStockCashFlowStatement 抓最新『取得不動產、廠房及設備』（仟元）"""
    q = urllib.parse.urlencode({
        "dataset": "TaiwanStockCashFlowStatement", "data_id": code,
        "start_date": "2024-01-01", "token": token})
    url = f"https://api.finmindtrade.com/api/v4/data?{q}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
        arr = json.loads(r.read()).get("data") or []
    if not arr:
        return None, None
    col = next((k for k in arr[-1] if "不動產" in k and "廠房" in k and "取得" in k), None)
    if not col:
        return None, None
    cur = arr[-1]; prev = arr[-2] if len(arr) > 1 else {}
    return float(cur.get(col) or 0), float(prev.get(col) or 0)


def get_yf_capex(ticker):
    """yfinance：優先單季 Capital Expenditure（與最新季報對齊），失敗退年度。
    回傳 (財季, 最新單季值, 前年同期值)。"""
    import yfinance as yf
    t = yf.Ticker(ticker)
    for df in (t.quarterly_cashflow, t.cashflow):
        if df is None or len(df) == 0:
            continue
        idx = [i for i in df.index if "Capital Expenditure" in str(i)]
        if not idx:
            continue
        row = df.loc[idx[0]].dropna()
        if len(row) == 0:
            continue
        cur = abs(float(row.iloc[0]))
        prev = abs(float(row.iloc[1])) if len(row) > 1 else None
        return q_of(row.index[0]), cur, prev
    return None, None, None


def get_sec_capex(cik, tag="PaymentsToAcquirePropertyPlantAndEquipment"):
    """SEC EDGAR：最新一期 + 前年同期（同月同日）YoY（美元）。tag 可換（部分公司用不同科目名）"""
    url = f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{tag}.json"
    req = urllib.request.Request(url, headers={"User-Agent": "Doubao Quant AdminContact@doubao.local"})
    with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
        j = json.loads(r.read())
    rows = [x for x in j.get("units", {}).get("USD", []) if x.get("form") in ("10-Q", "10-K") and x.get("val")]
    if not rows:
        return None, None, None
    rows.sort(key=lambda x: x["end"])
    cur_end = rows[-1]["end"]
    # 資料過舊（近 2 年內無 filing）→ 視為抓不到，換 tag 重試
    if int(cur_end[:4]) < int(datetime.datetime.now().year) - 2:
        return None, None, None
    cur_val = max(x["val"] for x in rows if x["end"] == cur_end)  # 同 end 多筆修正版→取最大
    # 前年同期配對：同月同日（財年結束日相同）
    cands = [x for x in rows if x["end"][5:] == cur_end[5:] and int(x["end"][:4]) == int(cur_end[:4]) - 1]
    prev_val = max(x["val"] for x in cands) if cands else None
    return q_of(cur_end), float(cur_val), (float(prev_val) if prev_val else None)


def judge(capex_chg):
    """合併台美後判斷狀態"""
    if capex_chg is None:
        return "待更新"
    if capex_chg >= 0.10:
        return "正成長"
    if capex_chg >= 0:
        return "持平成長"
    if capex_chg >= -0.10:
        return "減速"
    return "轉負"


def main():
    cfg = load_cfg()
    token = os.environ.get("FINLAB_TOKEN") or cfg.get("FINLAB_TOKEN") or ""
    fm_token = os.environ.get("FINMIND_TOKEN") or cfg.get("FINMIND_TOKEN") or ""

    tw = {}
    if token:
        try:
            tw = get_finlab_capex(token)
        except Exception as e:
            print("FinLab capex 失敗:", str(e)[:120])
    # FinMind 補缺（FinLab 失敗或 NaN）
    for c in TW_WATCH:
        if tw.get(c, (None, None, None))[1] is None and fm_token:
            try:
                cur, prev = get_finmind_capex(fm_token, c)
                tw[c] = (q_of(pd.Timestamp.now()), cur, prev)
            except Exception:
                pass

    us = {}
    for name, cik in US_WATCH.items():
        q_, cur, prev = None, None, None
        # 主：yfinance 年度 capex（GitHub Actions 已裝）
        try:
            q_, cur, prev = get_yf_capex(US_TICKERS[name])
        except Exception as e:
            print(f"YF {name} 失敗:", str(e)[:100])
        # 備援：SEC EDGAR（兩種 tag）
        if cur is None:
            for tag in ("PaymentsToAcquirePropertyPlantAndEquipment", "AcquisitionOfPropertyAndEquipment"):
                try:
                    q_, cur, prev = get_sec_capex(cik, tag)
                except Exception as e:
                    print(f"SEC {name} {tag} 失敗:", str(e)[:100])
                if cur is not None:
                    break
        us[name] = {"q": q_, "v": cur, "prev": prev}

    # 合併判斷：台美各自 YoY 中位數
    chgs = []
    for c, (q_, cur, prev) in tw.items():
        if cur and prev and prev > 0:
            chgs.append((cur - prev) / prev)
    for u in us.values():
        if u.get("v") and u.get("prev") and u["prev"] > 0:
            chgs.append((u["v"] - u["prev"]) / u["prev"])
    capex_chg = None
    if chgs:
        chgs.sort()
        capex_chg = chgs[len(chgs) // 2]  # 中位數
    status = judge(capex_chg)

    # 更新 ai_signals.json（保留 monet/cowos/updated 手動欄位）
    ai = {}
    if os.path.exists(AI_FILE):
        try:
            ai = json.load(io.open(AI_FILE, encoding="utf-8"))
        except Exception:
            ai = {}
    now = datetime.datetime.now()
    ai["capex"] = status
    ai["capex_chg"] = round(capex_chg * 100, 1) if capex_chg is not None else None
    ai["capex_tw"] = {c: {"q": tw.get(c, (None, None, None))[0], "v": tw.get(c, (None, None, None))[1],
                          "prev": tw.get(c, (None, None, None))[2]} for c in TW_WATCH}
    ai["capex_us"] = us
    if ai.get("updated") in (None, "", "—") or (isinstance(ai.get("updated"), str) and "Q" not in str(ai.get("updated"))):
        ai["updated"] = f"{now.year}-Q{(now.month - 1) // 3 + 1}"
    ai["note"] = "capex 由 ai_capex.py 每季自動更新（FinLab 台廠 + SEC 美系）；monet/cowos 每季法說會後手動更新"
    json.dump(ai, io.open(AI_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print(f"capex 狀態: {status} (chg {capex_chg*100:.1f}%)")
    print("台廠:", {c: tw[c][1] for c in TW_WATCH})
    print("台廠prev:", {c: tw[c][2] for c in TW_WATCH})
    print("美系:", {k: v["v"] for k, v in us.items()})
    print("美系prev:", {k: v["prev"] for k, v in us.items()})
    print("chgs:", chgs)


if __name__ == "__main__":
    main()
