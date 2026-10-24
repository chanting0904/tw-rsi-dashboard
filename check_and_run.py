# -*- coding: utf-8 -*-
"""最後交易日前一交易日21:45產生計畫，最後交易日早上僅補做未完成部分。
（週五休市 → 週三晚間產生週四計畫，與回測 w5_close 一致）。
FORCE_FULL=1 可手動強制（workflow_dispatch full 用）。"""
import datetime, os, subprocess, sys
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
    from publish_gate import prev_trading_day_str
    execution_day = last_trading_day_of_week(today, HOLS)
    if execution_day is None:
        print("[skip] 本週完全休市，不建立調倉計畫")
        return
    basis_day = datetime.date.fromisoformat(prev_trading_day_str(execution_day, HOLS))
    if FORCE or today in (basis_day, execution_day):
        print(f"[full] {today} 產生 {execution_day} 調倉計畫，資料基準 {basis_day}")
        # [V5] GitHub Actions 唯一來源：先由正式引擎產生 canonical（選股邏輯/參數不變）。
        #      validation 失敗 → v5_canonical 自身 exit(1)，這裡接住並中止、不發布。
        from publish_gate import evaluate
        with tempfile.TemporaryDirectory(prefix="v5-stage-", dir=ROOT) as temp:
            staged = Path(temp) / "output"
            staged.mkdir()
            for name in ("baseline.json", "regression_history.json"):
                source = Path(ROOT) / "output" / name
                if source.exists():
                    shutil.copy2(source, staged / name)
            if (Path(ROOT)/'output/history').exists():
                shutil.copytree(Path(ROOT)/'output/history',staged/'history',dirs_exist_ok=True)
            env = dict(os.environ, V5_OUTPUT_DIR=str(staged))
            r0 = subprocess.run([sys.executable, os.path.join(ROOT, "v5_canonical.py"),
                                 "--live-rebalance", execution_day.isoformat()], env=env)
            if r0.returncode != 0:
                sys.exit(3)
            gate = evaluate(force=FORCE, output_dir=str(staged), notify=False)
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
        from weekly_status import write_status
        write_status('ready')
        r = subprocess.run([sys.executable, os.path.join(ROOT, "update_data.py")])
        sys.exit(r.returncode)
    else:
        print(f"[skip] {today} 非本週訊號日或最後交易日（{today.strftime('%A')}），本日不選股")


if __name__ == "__main__":
    main()
