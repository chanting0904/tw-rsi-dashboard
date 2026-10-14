# -*- coding: utf-8 -*-
"""每天 06:00 觸發：僅「本週最後交易日」才執行完整選股
（週五休市 → 提前週四，與回測 w5_close 一致）。
FORCE_FULL=1 可手動強制（workflow_dispatch full 用）。"""
import datetime, json, os, subprocess, sys
import shutil
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo
from publish_gate import load_holidays, last_trading_day_of_week

ROOT = os.path.dirname(os.path.abspath(__file__))
FORCE = os.environ.get("FORCE_FULL") == "1"


HOLS = load_holidays()


def trading(d):
    return d.weekday() < 5 and d.strftime("%Y-%m-%d") not in HOLS


def main():
    today = datetime.datetime.now(ZoneInfo("Asia/Taipei")).date()
    # 本週最後交易日：今天是交易日且明天非交易日（週五休市 → 提前週四）
    is_last_td = today == last_trading_day_of_week(today, HOLS)
    if FORCE or is_last_td:
        print(f"[full] {today} 為本週最後交易日，執行完整選股")
        # [B3] GitHub Actions 唯一來源：先由正式引擎產生 canonical（選股邏輯/參數不變）。
        #      validation 失敗 → b3_canonical 自身 exit(1)，這裡接住並中止、不發布。
        from publish_gate import evaluate
        with tempfile.TemporaryDirectory(prefix="b3-stage-", dir=ROOT) as temp:
            staged = Path(temp) / "output"
            staged.mkdir()
            for name in ("baseline.json", "regression_history.json"):
                source = Path(ROOT) / "output" / name
                if source.exists():
                    shutil.copy2(source, staged / name)
            env = dict(os.environ, B3_OUTPUT_DIR=str(staged))
            r0 = subprocess.run([sys.executable, os.path.join(ROOT, "b3_canonical.py"),
                                 "--live-rebalance", today.isoformat()], env=env)
            if r0.returncode != 0:
                sys.exit(3)
            gate = evaluate(force=FORCE, output_dir=str(staged))
            if not gate["ok"]:
                print("[gate] FAIL：", gate["reason"])
                sys.exit(2)
            published = Path(ROOT) / "output"
            shutil.copytree(staged, published, dirs_exist_ok=True)
            # Drop old partial-week previews from the generated history only.
            names = {path.name for path in (staged / "history").glob("*.json")}
            for path in (published / "history").glob("*.json"):
                if path.name not in names:
                    path.unlink()
        print(f"[gate] PASS rebalance={gate['rebalance_date']} hash={gate['canonical_hash']}")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "update_data.py")])
        sys.exit(r.returncode)
    else:
        print(f"[skip] {today} 非本週最後交易日（{today.strftime('%A')}），本日不選股")


if __name__ == "__main__":
    main()
