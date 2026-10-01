# -*- coding: utf-8 -*-
"""每日盤後更新網頁收盤價。資料源優先序：
TWSE+TPEx 官方免費源（台灣/本機最穩）-> FinLab（國外 runner 兜底，token）-> yfinance（最後）。
所有源皆失敗 -> exit(1) 讓 workflow 紅叉，絕不假成功。"""
import os, re, sys, json, ssl, urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "RSI選股器.html")
IDX = os.path.join(HERE, "index.html")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
HDR = {"User-Agent": "Mozilla/5.0"}


def _get(url):
    req = urllib.request.Request(url, headers=HDR)
    return urllib.request.urlopen(req, timeout=20, context=CTX).read().decode("utf-8", "replace")


def twse_prices():
    j = json.loads(_get("https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&type=ALLBUT0999"))
    if j.get("stat") != "OK":
        raise RuntimeError("TWSE stat=" + str(j.get("stat")))
    px = {}
    for t in j.get("tables", []):
        f = t.get("fields", [])
        if "證券代號" in f and "收盤價" in f:
            ci, pi = f.index("證券代號"), f.index("收盤價")
            for row in t.get("data", []):
                try:
                    v = str(row[pi]).replace(",", "").strip()
                    if v and v != "--":
                        px[str(row[ci]).strip()] = float(v)
                except Exception:
                    pass
    if not px:
        raise RuntimeError("TWSE 無價格")
    return px, str(j.get("date"))


def tpex_prices():
    rows = json.loads(_get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"))
    px, dstr = {}, None
    for r in rows:
        try:
            c = str(r.get("SecuritiesCompanyCode", "")).strip()
            v = str(r.get("Close", "")).replace(",", "").strip()
            if c and v and v != "--":
                px[c] = float(v)
                dstr = str(r.get("Date"))
        except Exception:
            pass
    if not px:
        raise RuntimeError("TPEx 無價格")
    if dstr:
        dstr = "%04d%s" % (int(dstr[:3]) + 1911, dstr[3:])
    return px, dstr


def finlab_prices(token):
    import finlab
    from finlab import data
    finlab.login(api_token=token)
    raw = data.get("price:收盤價")
    last = raw.dropna(how="all").index[-1]
    row = raw.loc[last]
    px = {str(c): float(v) for c, v in row.items() if v == v and float(v) > 0}
    return px, last.strftime("%Y%m%d")


def yf_prices(codes):
    import yfinance as yf
    out = {}
    for c in codes:
        for suf in (".TW", ".TWO"):
            try:
                h = yf.Ticker(c + suf).history(period="5d")
                if len(h):
                    out[c] = float(h["Close"].iloc[-1])
                    break
            except Exception:
                pass
    return out


def read_data(html):
    m = re.search(r"const DATA = (\{.*?\});", html, re.S)
    return m, json.loads(m.group(1))


def codes_of(d):
    out = []
    for k in ("holdings_a", "holdings_b"):
        out += [str(h.get("code")) for h in d.get(k, [])]
    return out


def update_file(path, px, sig):
    html = open(path, encoding="utf-8").read()
    m, d = read_data(html)
    for k in ("holdings_a", "holdings_b"):
        for h in d.get(k, []):
            c = str(h.get("code"))
            if c in px:
                h["px"] = px[c]
    d["px_date"] = sig
    d.setdefault("rebal_date", d.get("sig_date"))
    d["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    new = json.dumps(d, ensure_ascii=False)
    final_html = html[:m.start(1)] + new + html[m.end(1):]
    tmp = path + ".tmp"
    open(tmp, "w", encoding="utf-8").write(final_html)
    os.replace(tmp, path)   # 原子替換，防寫一半斷電毀檔


def main():
    token = os.environ.get("FINLAB_TOKEN")
    px, dstr = {}, None

    try:
        p1, d1 = twse_prices()
        px.update(p1)
        dstr = d1
        print("[OK] TWSE OK", len(p1))
    except Exception as e:
        print("[FAIL] TWSE 失敗:", e)
    try:
        p2, d2 = tpex_prices()
        px.update(p2)
        # 日期取較新者：TPEx 盤後更新可能慢半天，不能覆蓋 TWSE 的正確日期
        if d2:
            dstr = max(dstr, d2) if dstr else d2
        print("[OK] TPEx OK", len(p2))
    except Exception as e:
        print("[FAIL] TPEx 失敗:", e)

    codes = []
    for p in (SRC, IDX):
        if os.path.exists(p):
            try:
                _, d = read_data(open(p, encoding="utf-8").read())
                codes = codes_of(d)
                break
            except Exception:
                pass

    miss = [c for c in codes if c not in px]
    if (not px or miss) and token:
        print("-> 官方源不足，改用 FinLab 兜底（缺", len(miss) if px else "全部", "）")
        try:
            pf, df = finlab_prices(token)
            for c in (miss if px else codes):
                if c in pf:
                    px[c] = pf[c]
            if not dstr:
                dstr = df
            print("[OK] FinLab OK", len(pf), "| 資料日", df)
        except Exception as e:
            print("[FAIL] FinLab 失敗:", e)

    miss = [c for c in codes if c not in px]
    if miss:
        print("-> yfinance 補:", miss)
        px.update(yf_prices(miss))

    miss = [c for c in codes if c not in px]
    if not px or (codes and miss):
        print("[FAIL] 致命：以下持股所有資料源皆失敗:", miss if codes else "（全市場無資料）")
        sys.exit(1)

    if not dstr:
        dstr = datetime.now().strftime("%Y%m%d")
    sig = "%s-%s-%s" % (dstr[:4], dstr[4:6], dstr[6:8])
    for p in (SRC, IDX):
        if os.path.exists(p):
            update_file(p, px, sig)
            print("[OK] 已更新", os.path.basename(p), "->", sig)


if __name__ == "__main__":
    main()
