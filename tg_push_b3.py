# -*- coding: utf-8 -*-
"""Publish the canonical simulation plan; brokerage holdings are not tracked."""
import datetime
import html
import json
import os
from pathlib import Path
import ssl
import urllib.request
from zoneinfo import ZoneInfo

from publish_gate import evaluate, fingerprint, load_holidays, last_trading_day_of_week

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state.json"
SIG_FILE = ROOT / "output/latest_signals.json"
DRY_RUN = os.environ.get("DRY_RUN") == "1"
FORCE = os.environ.get("FORCE_FULL") == "1" or os.environ.get("FORCE") == "1"
TW = ZoneInfo("Asia/Taipei")
LABELS = {"SELL": "🔴 賣出", "REBALANCE_SELL": "🟡 減碼", "BUY": "🟢 買進",
          "REBALANCE_BUY": "🟡 加碼", "HOLD": "⚪ 持有"}


def build_message(signal):
    escape = lambda value: html.escape(str(value))
    exposure = signal.get("stock_exposure", 1 - signal.get("buffer", 0))
    lines = ["<b>🌩️ B3 本週模擬調倉計畫</b>",
             f"訊號基準：{escape(signal.get('sig_date'))}｜收盤調倉日：{escape(signal.get('rebalance_date'))}",
             f"版本：{escape(signal.get('commit_sha', '—'))}",
             f"模擬配置：股票 {exposure:.1%}｜現金 {signal.get('buffer', 0):.1%}"]
    if signal.get("signal_mode") == "PRE_CLOSE_PLAN":
        lines.append("股數以基準日收盤價試算；當日收盤成交價會影響最終模擬結果。")
    for action, label in LABELS.items():
        rows = [a for a in signal.get("actions", []) if a["action"] == action]
        lines.append(f"\n<b>{label}（{len(rows)}）</b>")
        for row in sorted(rows, key=lambda a: (a.get("rank") or 99, str(a["stock_code"]))):
            lines.append(f"• <code>{escape(row['stock_code'])}</code> {escape(row['stock_name'])}"
                         f"（{escape(row['channel'])}）｜策略目標 {row['target_weight']:.2%}"
                         f"｜調倉前模擬 {row['current_weight']:.2%}")
            if row.get("reason"):
                lines.append("  原因：" + escape(row["reason"])[:500])
        if not rows:
            lines.append("（無）")
    for warning in signal.get("warnings", []):
        lines.append("⚠️ " + escape(warning)[:1000])
    lines.extend(["\n收盤調倉；未成交部分於下週第一個交易日用市價單補調倉。",
                  "網頁與本訊息使用同一份策略結果。請在網頁輸入資金，查看試算股數。",
                  "系統持倉為策略模擬；實際下單請對照券商持股。",
                  "https://chanting0904.github.io/tw-rsi-dashboard/"])
    return "\n".join(lines)


def message_chunks(text, limit=3500):
    chunk = ""
    for line in text.splitlines():
        if len(line) > limit:
            raise ValueError("單行 Telegram 訊息過長")
        if len(chunk) + len(line) + 1 > limit:
            yield chunk.rstrip()
            chunk = ""
        chunk += line + "\n"
    if chunk:
        yield chunk.rstrip()


def tg_send(text):
    if DRY_RUN:
        print(text)
        return
    token, chat = os.environ.get("TG_TOKEN"), os.environ.get("TG_CHAT_ID")
    if not token or not chat:
        raise RuntimeError("缺少 TG_TOKEN / TG_CHAT_ID")
    payload = {"chat_id": str(chat), "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": True}
    request = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                     data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30, context=ssl.create_default_context()) as response:
        if not json.load(response).get("ok"):
            raise RuntimeError("Telegram 未確認訊息發送成功")


def main():
    today = datetime.datetime.now(TW).date()
    if not FORCE and today != last_trading_day_of_week(today, load_holidays()):
        print("非本週最後交易日，跳過推播")
        return
    gate = evaluate(force=FORCE, notify=not DRY_RUN)
    if not gate["ok"]:
        raise RuntimeError("發布檢查未通過：" + gate["reason"])
    signal = json.loads(SIG_FILE.read_text(encoding="utf-8"))
    state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
    key = fingerprint(signal)
    if not FORCE and state.get("last_pushed_key") == key:
        print("相同訊號已推播，跳過")
        return
    for chunk in message_chunks(build_message(signal)):
        tg_send(chunk)
    if not DRY_RUN:
        state["last_pushed_key"] = key
        temp = STATE_FILE.with_suffix(".json.tmp")
        temp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        temp.replace(STATE_FILE)


if __name__ == "__main__":
    main()
