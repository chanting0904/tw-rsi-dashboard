# -*- coding: utf-8 -*-
"""
AI 紅利三指標 — 每月半自動提醒（GitHub Actions 每月 1 號 09:00 台北執行）
- 財報季月份（1/4/7/10）：發詳細查詢清單 + 更新指引
- 其他月份：輕量確認（值若仍有效可忽略）
- 若 ai_signals.json 的 updated 已過季：附加警示
"""
import os, json, datetime
import urllib.request

TG_TOKEN = os.environ["TG_TOKEN"]
TG_CHAT_ID = os.environ["TG_CHAT_ID"]
AI_FILE = "ai_signals.json"

QUARTER_MONTHS = {1, 4, 7, 10}


def tg_send(text: str):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": True}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["ok"]


def emoji(v):
    v = str(v)
    if any(x in v for x in ["正", "跟上", "滿載", "成長"]):
        return "🟢"
    if any(x in v for x in ["轉負", "落後", "鬆動", "下降", "減速"]):
        return "🔴"
    if any(x in v for x in ["持平", "觀察", "減速"]):
        return "🟡"
    return "⚪"


def load_ai():
    default = {"capex": "待更新", "monet": "待更新", "cowos": "待更新", "updated": "—"}
    if not os.path.exists(AI_FILE):
        return default
    try:
        s = json.load(open(AI_FILE, encoding="utf-8"))
        for k in default:
            s.setdefault(k, default[k])
        return s
    except Exception:
        return default


def main():
    now = datetime.datetime.now()
    ai = load_ai()
    updated = str(ai.get("updated", "—"))
    cur_q = f"{now.year}-Q{(now.month - 1) // 3 + 1}"
    stale = updated != cur_q

    lines = [f"<b>🤖 AI 紅利三指標 {'財報季' if now.month in QUARTER_MONTHS else '月度'}提醒</b>",
             f"📅 今天：{now:%Y-%m-%d}｜目前設定：<b>{updated}</b>{'（已過季！）' if stale else ''}\n",
             f"▫️ Hyperscaler capex：{emoji(ai.get('capex'))} {ai.get('capex')}",
             f"▫️ AI 變現率：{emoji(ai.get('monet'))} {ai.get('monet')}",
             f"▫️ CoWoS 產能：{emoji(ai.get('cowos'))} {ai.get('cowos')}"]

    if now.month in QUARTER_MONTHS:
        lines += ["",
                  "<b>📋 本季查詢清單（財報季）：</b>",
                  "① Hyperscaler capex → 微軟 / Google / Meta / 亞馬遜最新季報（SEC EDGAR 或財經新聞）",
                  "② AI 變現率 → 對比各廠 AI 營收成長 vs capex 成長（法說會逐字稿）",
                  "③ CoWoS 產能 → 台積電法說會（本月中）CoWoS 產能與交期說法",
                  "",
                  "<b>✏️ 更新方式：</b>編輯 repo 根目錄 <code>ai_signals.json</code> 後 commit，"
                  "或直接把新值傳給豆包請它幫你改（本機與 GitHub 兩邊都要同步）。"]
    else:
        lines += ["",
                  "ℹ️ 若上季值仍有效可忽略本提醒；",
                  "若有重大突發（台積電法說、財報爆雷）請即時更新 <code>ai_signals.json</code>。"]

    if stale:
        lines += ["",
                  "⚠️ <b>目前值已過季（{}）</b>，建議於本月財報季內更新！".format(updated)]

    tg_send("\n".join(lines))
    print(f"AI reminder sent ({updated} vs {cur_q})")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("AI monitor failed:", e)
        raise
