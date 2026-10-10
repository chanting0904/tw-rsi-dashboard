# -*- coding: utf-8 -*-
"""B3 管線一鍵 preflight（本機用；不改策略、不連網、不發 TG）。
依序檢查：
  1) py_compile：publish_gate.py / check_and_run.py / tg_push_b3.py / update_data.py
  2) 閘門乾跑：publish_gate.evaluate(force=True)（validation 結構，不驗新鮮度）
  3) TG 乾跑：DRY_RUN=1 組訊息（不發送、不 git push）
任一失敗即 exit(1)。用法：python preflight.py
"""
import os, sys, py_compile, json, subprocess, shutil

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
FAIL = []

# ---------- 1. 語法檢查 ----------
for f in ("publish_gate.py", "check_and_run.py", "tg_push_b3.py", "update_data.py"):
    try:
        py_compile.compile(os.path.join(ROOT, f), doraise=True)
        print(f"[OK]   py_compile {f}")
    except Exception as e:
        FAIL.append(f"py_compile {f}: {e}")
        print(f"[FAIL] py_compile {f}: {e}")

# ---------- 2. 閘門乾跑（FORCE 放寬新鮮度，驗 validation/結構） ----------
try:
    sys.path.insert(0, ROOT)
    import publish_gate
    r = publish_gate.evaluate(force=True)
    print(f"[{'OK' if r['ok'] else 'FAIL'}] gate(force) reason={r['reason']}")
    if not r["ok"]:
        FAIL.append(f"gate(force): {r['reason']}")
except Exception as e:
    FAIL.append(f"gate: {e}")
    print(f"[FAIL] gate: {e}")

# ---------- 3. TG 乾跑（DRY_RUN，組訊息不發送） ----------
try:
    bak = None
    if os.path.exists("state.json"):
        bak = "state.json"
        shutil.copy("state.json", "state.json.preflight.bak")
    env = dict(os.environ)
    env["DRY_RUN"] = "1"
    env["GITHUB_ACTIONS"] = "true"   # CI 模式：只寫檔不 git push
    env["FORCE_FULL"] = "1"
    env.setdefault("TG_TOKEN", "x")
    env.setdefault("TG_CHAT_ID", "1")
    p = subprocess.run([sys.executable, os.path.join(ROOT, "tg_push_b3.py")],
                       env=env, capture_output=True, text=True)
    out = (p.stdout or "") + (p.stderr or "")
    has_s5 = "S5" in out
    print(f"[{'OK' if (p.returncode == 0 and has_s5) else 'FAIL'}] tg DRY_RUN rc={p.returncode} 含S5={has_s5}")
    if p.returncode != 0 or not has_s5:
        FAIL.append(f"tg DRY_RUN rc={p.returncode} tail={out[-500:]}")
    if bak:
        shutil.copy("state.json.preflight.bak", "state.json")
        os.remove("state.json.preflight.bak")
except Exception as e:
    FAIL.append(f"tg dry: {e}")
    print(f"[FAIL] tg dry: {e}")

print("\n" + "=" * 50)
if FAIL:
    for f in FAIL:
        print("❌", f)
    print("PREFLIGHT FAIL")
    sys.exit(1)
print("✅ PREFLIGHT PASS（語法／閘門／TG 乾跑全過；未連網、未發送、未 push）")
