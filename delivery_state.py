"""Durable per-part receipts; never automatically resend an uncertain request."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parent


def save_state(state, path):
    path = Path(path)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    if os.environ.get("TG_PROGRESS_GIT") != "1":
        return
    if path.resolve() != (ROOT / "state.json").resolve():
        raise ValueError("只允許保存專案的通知狀態")
    subprocess.run(["git", "add", "--", "state.json"], cwd=ROOT, check=True)
    changed = subprocess.run(["git", "diff", "--cached", "--quiet", "--", "state.json"], cwd=ROOT)
    if changed.returncode not in (0, 1):
        raise RuntimeError("無法檢查 Telegram 狀態變更")
    if changed.returncode == 1:
        subprocess.run(["git", "commit", "--only", "-m", "record telegram part [skip ci]", "--", "state.json"], cwd=ROOT, check=True)
    for attempt in range(3):
        pushed = subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=ROOT)
        if pushed.returncode == 0:
            return
        # Keep our local receipt if another remote commit arrived. A real merge
        # conflict must stop delivery; never discard either side's receipts.
        pulled = subprocess.run(["git", "pull", "--rebase", "--autostash", "origin", "main"], cwd=ROOT)
        if pulled.returncode:
            raise RuntimeError("通知進度保存衝突，停止後續分段發送")
        if attempt < 2:
            time.sleep(5)
    raise RuntimeError("通知進度無法保存至 GitHub，停止後續分段發送")


def deliver_parts(chunks, canonical_key, state, *, send, save):
    hashes = [hashlib.sha256(chunk.encode("utf-8")).hexdigest() for chunk in chunks]
    delivery_key = hashlib.sha256((canonical_key + ":" + ":".join(hashes)).encode()).hexdigest()
    receipt = state.get("telegram_delivery", {})
    if receipt.get("delivery_key") != delivery_key:
        if any(part.get("status") == "sending" for part in receipt.get("parts", [])):
            raise RuntimeError("先前通知有送達狀態不明的分段，停止自動重發，請確認 TG 訊息")
        receipt = {"delivery_key": delivery_key, "canonical_key": canonical_key,
                   "parts": [{"sha256": digest, "status": "pending"} for digest in hashes]}
        state["telegram_delivery"] = receipt
    parts = receipt.get("parts", [])
    if len(parts) != len(chunks) or any(part.get("sha256") != digest for part, digest in zip(parts, hashes)):
        raise RuntimeError("通知進度內容不一致，停止發送")
    for index, (chunk, part) in enumerate(zip(chunks, parts), 1):
        if part.get("status") == "confirmed":
            continue
        if part.get("status") == "sending":
            raise RuntimeError(f"TG 第 {index} 段送達狀態不明，停止自動重發，請確認 TG 訊息")
        part.update(status="sending", started_at=dt.datetime.now(dt.timezone.utc).isoformat())
        save(state)  # Persist intention before the external send, including across runner cancellation.
        try:
            message_id = send(chunk)
        except RejectedDelivery:
            part["status"] = "pending"  # Telegram explicitly confirmed rejection; safe to retry later.
            save(state)
            raise
        part.update(status="confirmed", message_id=message_id,
                    confirmed_at=dt.datetime.now(dt.timezone.utc).isoformat())
        save(state)
    state["last_pushed_key"] = canonical_key
    save(state)


class RejectedDelivery(RuntimeError):
    """A server response confirmed that Telegram did not accept this request."""
