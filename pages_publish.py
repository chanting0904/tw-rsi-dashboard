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


def publish(token, repository, expected_commit, attempts=30, pause=10):
    base = f"https://api.github.com/repos/{repository}/pages/builds"
    request_json(base, token, method="POST")
    checked = {}
    for _ in range(attempts):
        build = request_json(base + "/latest", token)
        commit = build.get("commit")
        if commit not in checked:
            checked[commit] = contains(expected_commit, commit)
        if checked[commit]:
            if build.get("status") == "built":
                return build
            if build.get("status") == "errored":
                raise RuntimeError("Pages 建置失敗：" + str(build.get("error", {}).get("message")))
        time.sleep(pause)
    raise RuntimeError("Pages 尚未完成本次版本建置，不發送調倉訊息")


def main():
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    result = publish(os.environ["GH_TOKEN"], os.environ["GITHUB_REPOSITORY"], commit)
    print("Pages 已上線：", result["commit"])
    verify_public_index()


if __name__ == "__main__":
    main()
