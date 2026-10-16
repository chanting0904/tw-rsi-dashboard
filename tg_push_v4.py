from version import RELEASE_VERSION
# -*- coding: utf-8 -*-
"""Publish the canonical simulation plan; brokerage holdings are not tracked."""
import datetime
import html
import json
import os
from pathlib import Path
import ssl
import urllib.request
import urllib.error
from zoneinfo import ZoneInfo

from publish_gate import evaluate, fingerprint, load_holidays, last_trading_day_of_week
from publication_check import verify_public_index
from delivery_state import deliver_parts, save_state, RejectedDelivery

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
    lines = ["<b>🌩️ V4 本週模擬調倉計畫</b>",
             f"訊號基準：{escape(signal.get('sig_date'))}｜收盤調倉日：{escape(signal.get('rebalance_date'))}",
             f"版本：{escape(signal.get('release_version', RELEASE_VERSION))}｜{escape(signal.get('commit_sha', '—'))}",
             f"模擬配置：股票 {exposure:.1%}｜現金 {signal.get('buffer', 0):.1%}"]
    rules = signal.get('selection_rules', {})
    if rules.get('a_rank_days'):
        lines.append(f"A通道：{rules['a_rank_days']}日平均RSI排名；舊A持股符合資格且排名前{rules['a_retention_rank']}可保留，最多{rules['a_entry_rank']}檔。")
    if signal.get('receivables', 0):
        lines.append(f"應收股利／權益：{signal['receivables']:,.0f} 元，尚未列入可用現金。")
    if signal.get("signal_mode") == "PRE_CLOSE_PLAN":
        lines.append("股數以基準日收盤價試算；當日收盤成交價會影響最終模擬結果。")
    else:
        lines.append("本期已收盤，以下為新版收盤模擬目標；請對照券商實際持股，未成交差額於下週第一個交易日處理。")
    for action, label in LABELS.items():
        rows = [a for a in signal.get("actions", []) if a["action"] == action
                and (action == 'SELL' or a.get('allocation_eligible', True))]
        lines.append(f"\n<b>{label}（{len(rows)}）</b>")
        for row in sorted(rows, key=lambda a: (a.get("rank") or 99, str(a["stock_code"]))):
            lines.append(f"• <code>{escape(row['stock_code'])}</code> {escape(row['stock_name'])}"
                         f"（{escape(row['channel'])}）｜模擬目標 {row['target_weight']:.2%}"
                         f"｜調倉前模擬 {row['current_weight']:.2%}")
            if row.get("reason"):
                lines.append("  原因：" + escape(row["reason"])[:500])
            if row.get('recommendation_note'):
                lines.append('  ' + escape(row['recommendation_note']))
        if not rows:
            lines.append("（無）")
    restricted = [a for a in signal.get('actions', []) if a['action'] != 'SELL' and not a.get('allocation_eligible', True)]
    for asset in signal.get('restricted_assets', []):
        if any(row['stock_code']==asset['code'] for row in restricted):
            continue
        restricted.append({'stock_code':asset['code'],'stock_name':asset.get('name',asset['code']),
            'recommendation_note':f"歷史股數{asset['shares']}股；保守估值{asset['book_value']:,.0f}元，不列入下單或可用現金。"})
    if restricted:
        lines.append('\n<b>歷史受限資產（不列入下單）</b>')
        for row in restricted:
            lines.append(f"• {escape(row['stock_code'])} {escape(row['stock_name'])}｜{escape(row.get('recommendation_note', '不可交易'))}")
    if signal.get('pending_assets'):
        lines.append(f"\n<b>模擬待領配股：{len(signal['pending_assets'])}筆，目前不用另外下單</b>")
        lines.append('原有股票可照策略賣出，應領配股不會取消。配股日期及股數可在網頁展開查看。')
    for warning in signal.get("warnings", []):
        lines.append("⚠️ " + escape(warning)[:1000])
    lines.extend(["\n收盤調倉；未成交部分於下週第一個交易日用市價單補調倉。",
                  "網頁與本訊息使用同一份策略結果。請在網頁輸入資金，查看整股後的試算股數與權重；不同本金會有取整差異。",
                  "若同週收到修正版，請以版本較新、與網頁一致的計畫為準。",
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
    try:
        with urllib.request.urlopen(request, timeout=30, context=ssl.create_default_context()) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        if 400 <= error.code < 500:
            raise RejectedDelivery(f"Telegram 拒絕訊息（HTTP {error.code}）") from None
        raise RuntimeError("Telegram 回應不明，停止自動重發") from None
    except (OSError, ValueError):
        raise RuntimeError("Telegram 送達狀態不明，停止自動重發") from None
    if not result.get("ok"):
        raise RejectedDelivery("Telegram 未接受訊息")
    message_id = result.get("result", {}).get("message_id")
    if not message_id:
        raise RuntimeError("Telegram 缺少送達編號，停止自動重發")
    return message_id


def delivery_due(today, last_trading_day, *, force=False, event_name=None):
    # A version release may land on a weekend. The publication gate still
    # requires this week's verified signal, and delivery receipts deduplicate.
    return force or today == last_trading_day or event_name == 'push'


def main():
    today = datetime.datetime.now(TW).date()
    if not delivery_due(today, last_trading_day_of_week(today, load_holidays()),
                        force=FORCE, event_name=os.environ.get('GITHUB_EVENT_NAME')):
        print("非本週最後交易日，跳過推播")
        return
    gate = evaluate(force=FORCE, notify=not DRY_RUN)
    if not gate["ok"]:
        raise RuntimeError("發布檢查未通過：" + gate["reason"])
    signal = json.loads(SIG_FILE.read_text(encoding="utf-8"))
    state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
    key = fingerprint(signal)
    if state.get("last_pushed_key") == key:
        print("相同訊號已推播，跳過")
        return
    chunks = list(message_chunks(build_message(signal)))
    chunks = [f"V4 通知 {index}/{len(chunks)}\n" + chunk for index, chunk in enumerate(chunks, 1)]
    if DRY_RUN:
        for chunk in chunks:
            tg_send(chunk)
        return
    verify_public_index(attempts=3)
    deliver_parts(chunks, key, state, send=tg_send,
                  save=lambda value: save_state(value, STATE_FILE))


if __name__ == "__main__":
    main()
