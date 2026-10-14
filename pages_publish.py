"""Request a branch-based Pages build and confirm the intended commit is live."""
import json
import os
import subprocess
import time
import urllib.request


def request_json(url, token, method="GET"):
    request = urllib.request.Request(url, method=method,
        headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
                 "User-Agent": "B3-Pages-publisher"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def publish(token, repository, expected_commit, attempts=30, pause=10):
    base = f"https://api.github.com/repos/{repository}/pages/builds"
    request_json(base, token, method="POST")
    for _ in range(attempts):
        build = request_json(base + "/latest", token)
        if build.get("commit") == expected_commit:
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


if __name__ == "__main__":
    main()
