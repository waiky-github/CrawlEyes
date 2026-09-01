#!/usr/bin/env python3
"""
GitHub REST API 完整推送 —— 绕开 git push 的 TLS -110 静默失败。

用法:
    python scripts/rest_push.py CrawlEyes main

原理:
    用 GitHub git data API 完整重建: 遍历本地 tree 递归上传所有 blob/tree，
    创建 commit，更新 ref。彻底绕开 HTTPS smart protocol（GnuTLS -110）。
"""
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request

OWNER = "waiky-github"
CRED_FILE = "/home/agentuser/.git-credentials"


def get_token() -> str:
    with open(CRED_FILE) as f:
        c = f.read()
    for line in c.splitlines():
        m = re.match(r"https://waiky-github:([^@]+)@github\.com", line)
        if m:
            return m.group(1)
    raise RuntimeError("github token not found in " + CRED_FILE)


def gh(method: str, url: str, body=None, tok: str = "") -> tuple[int, dict]:
    req = urllib.request.Request(url, method=method,
                                 headers={"Authorization": f"token {tok}",
                                          "Accept": "application/vnd.github+json"})
    if body is not None:
        req.add_header("Content-Type", "application/json")
        data = json.dumps(body).encode()
    else:
        data = None
    try:
        with urllib.request.urlopen(req, data=data, timeout=60) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode())
        except Exception:
            err = {"message": str(e)}
        return e.code, err


def git(cwd: str, *args) -> str:
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True).stdout.strip()


def upload_tree(cwd: str, base: str, tok: str, tree_sha: str, prefix: str = "") -> str:
    """递归上传 tree 及其所有 blob/子树，返回新的 tree sha（GitHub 会用新 sha 返回）。"""
    # 列出 tree 条目
    out = git(cwd, "ls-tree", tree_sha)
    entries = []
    for line in out.splitlines():
        parts = line.split("\t")
        meta, path = parts[0], parts[1]
        mode, typ, sha = meta.split()
        full = f"{prefix}{path}"
        if typ == "blob":
            if sha in BLOB_CACHE:
                entries.append({"path": path, "mode": mode, "type": "blob", "sha": sha})
                continue
            # 读 blob 内容，base64 上传（cat-file 二进制读取，兼容图片等）
            data = subprocess.run(["git", "-C", cwd, "cat-file", "blob", sha],
                                  capture_output=True).stdout
            import base64
            enc = base64.b64encode(data).decode()
            st, resp = gh("POST", f"{base}/git/blobs",
                          {"content": enc, "encoding": "base64"}, tok=tok)
            if st != 201:
                raise RuntimeError(f"blob upload failed {path}: {resp}")
            BLOB_CACHE.add(sha)
            entries.append({"path": path, "mode": mode, "type": "blob", "sha": sha})
        elif typ == "tree":
            sub_sha = upload_tree(cwd, base, tok, sha, prefix=f"{full}/")
            entries.append({"path": path, "mode": mode, "type": "tree", "sha": sub_sha})
    st, resp = gh("POST", f"{base}/git/trees", {"tree": entries}, tok=tok)
    if st != 201:
        raise RuntimeError(f"tree upload failed {tree_sha}: {resp}")
    return resp["sha"]


BLOB_CACHE = set()


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    repo, branch = sys.argv[1], sys.argv[2]
    cwd = "/home/agentuser/crawl-public" if repo == "CrawlEyes" else f"/home/agentuser/{repo}"
    tok = get_token()
    base = f"https://api.github.com/repos/{OWNER}/{repo}"

    st, ref = gh("GET", f"{base}/git/ref/heads/{branch}", tok=tok)
    parent_sha = ref["object"]["sha"]
    print(f"远端 {branch}: {parent_sha[:7]}")

    head_sha = git(cwd, "rev-parse", "HEAD")
    head_msg = git(cwd, "log", "-1", "--pretty=%B")
    head_tree = git(cwd, "rev-parse", "HEAD^{tree}")
    print(f"本地 HEAD: {head_sha[:7]} parent: {parent_sha[:7]}")

    # 已存在则跳过
    st, _ = gh("GET", f"{base}/git/commits/{head_sha}", tok=tok)
    if st == 200:
        print("本地 HEAD 已在远端，无需推送")
        return

    print("上传 tree 对象...")
    new_tree = upload_tree(cwd, base, tok, head_tree)
    print(f"tree 上传完成: {new_tree[:7]}")

    st, commit = gh("POST", f"{base}/git/commits",
                    {"message": head_msg, "tree": new_tree, "parents": [parent_sha]}, tok=tok)
    if st != 201:
        raise RuntimeError(f"commit create failed: {commit}")
    print(f"commit 创建: {commit['sha'][:7]}")

    st, upd = gh("PATCH", f"{base}/git/refs/heads/{branch}",
                 {"sha": commit["sha"], "force": False}, tok=tok)
    if st != 200:
        raise RuntimeError(f"ref update failed: {upd}")
    print(f"✅ {branch} 已更新到 {commit['sha'][:7]}")


if __name__ == "__main__":
    main()
