"""Request a branch-based Pages build and confirm the intended commit is live."""
import json
import os
import subprocess
import time
import urllib.request
from publication_check import verify_public_index


def request_json(url, token, method="GET"):
    request = urllib.request.Request(url, method=method,
        headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
                 "User-Agent": "V5-Pages-publisher"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def contains(expected_commit, built_commit):
    """較新的提交已推上 main 時，Pages 會建置那一版；只要它包含本次提交就算數（內容另由公開核對比對）。"""
    if not built_commit:
        return False
    if built_commit == expected_commit:
        return True
    subprocess.run(["git", "fetch", "-q", "origin", "main"], check=False)
    return subprocess.run(["git", "merge-base", "--is-ancestor", expected_commit, built_commit],
                          stderr=subprocess.DEVNULL).returncode == 0


def publish(token, repository, expected_commit, attempts=12, pause=10):
    """要求重建並短暫等待。舊版 builds API 的狀態常與實際部署不符（已上線仍顯示 building／errored），
    所以這裡不據此判定失敗；是否真的上線一律由 verify_public_index 讀回公開網頁比對。"""
    base = f"https://api.github.com/repos/{repository}/pages/builds"
    request_json(base, token, method="POST")
    checked = {}
    for _ in range(attempts):
        build = request_json(base + "/latest", token)
        commit = build.get("commit")
        if commit not in checked:
            checked[commit] = contains(expected_commit, commit)
        if checked[commit] and build.get("status") == "built":
            return build
        time.sleep(pause)
    return None


def main():
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    result = publish(os.environ["GH_TOKEN"], os.environ["GITHUB_REPOSITORY"], commit)
    print("Pages 建置回報：", result["commit"] if result else "未回報完成，改以公開網頁核對")
    verify_public_index(attempts=36)


if __name__ == "__main__":
    main()
