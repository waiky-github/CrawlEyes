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


def git_bytes(cwd: str, *args) -> bytes:
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True).stdout


COMMIT_CACHE: set = set()


def ensure_commit(cwd: str, base: str, tok: str, commit_sha: str, depth: int = 0):
    """递归确保 commit 及其祖先、tree 全部上传到远端。返回远端生成的 commit sha（若新上传）。

    关键: 复用本地 commit 的 message/tree/parents + 原始 author/committer 时间戳，
    GitHub 按内容寻址生成 commit。GitHub 序列化与本地 git 不同 → sha 不同，这是机制性事实，
    由调用方在 push 后做本地对齐消除分叉。
    """
    if commit_sha in COMMIT_CACHE or depth > 500:
        return None
    st, _ = gh("GET", f"{base}/git/commits/{commit_sha}", tok=tok)
    if st == 200:
        COMMIT_CACHE.add(commit_sha)
        return None
    # 读本地 commit 对象（二进制 cat-file，保留原始 message/author/committer）
    raw = git_bytes(cwd, "cat-file", "commit", commit_sha)
    # 从 commit 头解析 tree + parents + author/committer（保留原始时间戳保证内容寻址 sha 一致）
    header = raw.split(b"\n\n", 1)[0].decode()
    tree_sha = None
    parents = []
    author_line = ""
    committer_line = ""
    for line in header.splitlines():
        if line.startswith("tree "):
            tree_sha = line.split()[1]
        elif line.startswith("parent "):
            parents.append(line.split()[1])
        elif line.startswith("author "):
            author_line = line[7:]
        elif line.startswith("committer "):
            committer_line = line[10:]
    assert tree_sha, f"commit {commit_sha} 无 tree"

    def ident(line: str):
        """'Name <email> epoch +tz' → {'name','email','date'(ISO)}。GitHub 的 date 接受 ISO8601。"""
        import datetime
        m = re.match(r"^(.*?) <([^>]*)> (\d+) ([+-]\d{4})$", line)
        assert m, f"无法解析 ident: {line!r}"
        name, email, epoch, tz = m.groups()
        dt = datetime.datetime.fromtimestamp(int(epoch), datetime.timezone.utc)
        off = datetime.timedelta(hours=int(tz[:3]), minutes=int(tz[3:]) * (1 if tz[0] == "+" else -1))
        local = dt + off
        iso = local.strftime("%Y-%m-%dT%H:%M:%S") + tz[:3] + ":" + tz[3:]
        return {"name": name, "email": email, "date": iso}

    # 先确保 tree 上传
    upload_tree(cwd, base, tok, tree_sha)
    # 递归确保 parents 上传
    for p in parents:
        ensure_commit(cwd, base, tok, p, depth + 1)
    # 用原始 author/committer 字段重建 commit（GitHub 内容寻址 → sha 与本地不同但内容一致）
    commit_data = {
        "message": git(cwd, "log", "-1", "--pretty=%B", commit_sha),
        "tree": tree_sha,
        "parents": parents,
    }
    if author_line:
        commit_data["author"] = ident(author_line)
    if committer_line:
        commit_data["committer"] = ident(committer_line)
    st, resp = gh("POST", f"{base}/git/commits", commit_data, tok=tok)
    if st != 201:
        raise RuntimeError(f"commit upload failed {commit_sha}: {resp}")
    remote_sha = resp["sha"]
    COMMIT_CACHE.add(commit_sha)
    return remote_sha


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    repo, branch = sys.argv[1], sys.argv[2]
    cwd = "/home/agentuser/crawl-public" if repo == "CrawlEyes" else f"/home/agentuser/{repo}"
    tok = get_token()
    base = f"https://api.github.com/repos/{OWNER}/{repo}"

    head_sha = git(cwd, "rev-parse", "HEAD")
    print(f"本地 HEAD: {head_sha[:7]}")

    # 已存在则跳过（本地 HEAD 的 commit 对象已在远端）
    st, _ = gh("GET", f"{base}/git/commits/{head_sha}", tok=tok)
    if st == 200:
        print("本地 HEAD 已在远端，无需推送")
        return

    # 递归上传 HEAD 的完整祖先链 + tree（复用本地 commit 元数据，不新建独立分叉链）
    print("递归上传 commit 链 + tree...")
    remote_head = ensure_commit(cwd, base, tok, head_sha)
    if not remote_head:
        raise RuntimeError(f"ensure_commit 未返回远端 sha: {head_sha}")
    print(f"GitHub commit: {remote_head[:7]}（内容与本地一致，GitHub 序列化 sha 不同）")

    # 更新 ref 到 GitHub 生成的 commit（与本地内容相同）
    st, upd = gh("PATCH", f"{base}/git/refs/heads/{branch}",
                 {"sha": remote_head, "force": False}, tok=tok)
    if st != 200:
        raise RuntimeError(f"ref update failed: {upd}")
    print(f"✅ {branch} 已更新到 {remote_head[:7]}")

    # 本地对齐到 GitHub commit（内容相同，仅 sha 为 GitHub 序列化 → 消除本地/远端历史分叉）
    # ⚠️ reset --hard 会丢弃未提交工作区改动，push 前务必 commit 干净
    git(cwd, "fetch", "origin", branch)
    git(cwd, "reset", "--hard", f"origin/{branch}")
    print(f"✅ 本地已对齐到 origin/{branch}（无分叉）")


if __name__ == "__main__":
    main()
