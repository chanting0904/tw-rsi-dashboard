"""Read the public HTML back before delivering its matching Telegram plan."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import time
import urllib.request

from publish_gate import fingerprint
from version import RELEASE_VERSION

ROOT = Path(__file__).resolve().parent
PUBLIC_URL = "https://chanting0904.github.io/tw-rsi-dashboard/"


def embedded_data(document):
    marker = "const DATA = "
    if document.count(marker) != 1:
        raise ValueError("公開網頁缺少唯一的訊號資料")
    return json.JSONDecoder().raw_decode(document.split(marker, 1)[1])[0]


def expected_publication(root=ROOT):
    root = Path(root)
    body = (root / "index.html").read_bytes()
    signal = json.loads((root / "output/latest_signals.json").read_text(encoding="utf-8"))
    data = embedded_data(body.decode("utf-8"))
    expected = {"release_version": RELEASE_VERSION, "canonical_hash": fingerprint(signal),
                "rebal_date": signal.get("rebalance_date"), "sig_date": signal.get("sig_date"),
                "price_basis_date": signal.get("price_basis_date") or signal.get("rebalance_date"),
                "sig_validated": True}
    if signal.get("release_version") != RELEASE_VERSION:
        raise ValueError("本地訊號版本與程式版本不同")
    if any(data.get(key) != value for key, value in expected.items()):
        raise ValueError("本地網頁與訊號版本、日期或驗證狀態不一致")
    return expected, hashlib.sha256(body).hexdigest()


def verify_public_index(*, root=ROOT, attempts=12, pause=10, write_receipt=True):
    expected, expected_sha = expected_publication(root)
    last_error = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(PUBLIC_URL + "?v4_verify=" + str(time.time_ns()),
                headers={"Cache-Control": "no-cache", "User-Agent": "V4-publication-check"})
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
            data = embedded_data(body.decode("utf-8"))
            if hashlib.sha256(body).hexdigest() != expected_sha:
                raise ValueError("公開網站內容尚未與本次發布完全一致")
            if any(data.get(key) != value for key, value in expected.items()):
                raise ValueError("公開網站訊號版本、日期或內容不一致")
            result = {**expected, "html_sha256": expected_sha,
                      "verified_at": dt.datetime.now(dt.timezone.utc).isoformat(), "url": PUBLIC_URL}
            if write_receipt:
                destination = Path(root) / "output/publication_verified.json"
                temporary = destination.with_suffix(".json.tmp")
                temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                temporary.replace(destination)
            print("公開網頁已核對：", expected["release_version"], expected["rebal_date"], expected["canonical_hash"])
            return result
        except (OSError, ValueError) as error:
            last_error = error
            if attempt + 1 < attempts:
                time.sleep(pause)
    raise RuntimeError("公開網頁核對失敗，不發送一般調倉通知：" + str(last_error))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempts", type=int, default=12)
    args = parser.parse_args()
    verify_public_index(attempts=args.attempts)
