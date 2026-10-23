#!/usr/bin/env python3
"""换参重试观测：同一工具连续调用 ≥N 次但参数各不相同，只统计不拦截。

用法：
  python retry_patterns.py --dsh-home ~/.dsh --days 7
  python retry_patterns.py --json

口径：
  - 会话内按 user/message 切段，段内连续同工具调用算一串。
  - 「换参重试」= 串长 ≥ --min-run 且相邻参数都不相同（description 也算参数）。
  - 「字面重复」= 相邻参数完全相同，用于和循环守卫的触发面对照。
价格与归因不参与本脚本，只出次数。
"""
import argparse, json, os, re, subprocess, sys, time
from collections import Counter, defaultdict

from tg_common import load_config, resolve_dsh_home, sessions_root, utf8_stdio

utf8_stdio()

FAIL_EXIT = re.compile(r"\[exit code: ([1-9]\d*)\]")
FAIL_HEAD = ("Error", "ERROR", "Traceback (most recent call last)", "ENOENT", "EACCES")


def is_failure(text):
    """只看硬信号：非零退出码、或以报错字样开头。命中列表里的 error 不算。"""
    if FAIL_EXIT.search(text or ""):
        return True
    return (text or "").lstrip().startswith(FAIL_HEAD)


def decompress(path):
    try:
        r = subprocess.run(["zstd", "-dc", path], capture_output=True)
        if r.returncode == 0 and r.stdout:
            return r.stdout
    except FileNotFoundError:
        pass
    try:
        import zstandard as zstd
    except ImportError:
        sys.exit("解压需要二选一：`zstd` 命令或 `pip install zstandard`。")
    with open(path, "rb") as f:
        data = f.read()
    dctx = zstd.ZstdDecompressor()
    out = []
    while data:
        dobj = dctx.decompressobj()
        out.append(dobj.decompress(data))
        if not dobj.eof:
            break
        data = dobj.unused_data
    return b"".join(out)


def events(path):
    raw = decompress(path)
    for line in raw.split(b"\n"):
        if not line.strip():
            continue
        try:
            yield json.loads(line)
        except Exception:
            pass


def analyze(path, min_run):
    runs = []
    cur = None
    failed = {}
    for o in events(path):
        t = o.get("type")
        if t == "user/message":
            if cur:
                runs.append(cur)
            cur = None
            continue
        if t == "tool/result":
            d = o.get("data") or {}
            msg = d.get("message") or {}
            cid = d.get("toolCallId") or (msg.get("source") or {}).get("callId")
            if cid:
                text = "".join(c.get("text") or "" for c in (msg.get("content") or []) if isinstance(c, dict))
                failed[cid] = is_failure(text)
            continue
        if t != "tool/call":
            continue
        d = o.get("data") or {}
        name, args, cid = d.get("name"), d.get("arguments") or "", d.get("callId")
        if cur and cur["name"] == name:
            cur["args"].append(args)
            cur["ids"].append(cid)
        else:
            if cur:
                runs.append(cur)
            cur = {"name": name, "args": [args], "ids": [cid]}
    if cur:
        runs.append(cur)

    retry, literal = [], []
    for r in runs:
        n = len(r["args"])
        if n < 2:
            continue
        distinct = len(set(r["args"])) == n
        adjacent_same = any(a == b for a, b in zip(r["args"], r["args"][1:]))
        r["fails"] = sum(1 for cid in r["ids"] if failed.get(cid))
        if n >= min_run and distinct:
            retry.append(r)
        if adjacent_same:
            literal.append(r)
    return retry, literal


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsh-home", default="", help="不给则读 config 的 dsh_home，再退回 $DSH_HOME / ~/.dsh")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--days", type=int, default=7, help="只看最近 N 天修改过的会话")
    ap.add_argument("--min-run", type=int, default=3, help="串长下限，默认 3")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    cfg, base = load_config(a.config)
    root = sessions_root(resolve_dsh_home(a.dsh_home, cfg=cfg, base=base))
    if not os.path.isdir(root):
        sys.exit(f"找不到会话目录: {root}（检查 --dsh-home）")
    cutoff = time.time() - a.days * 86400

    files = []
    for dp, _, fs in os.walk(root):
        for f in fs:
            if not f.endswith(".zstd"):
                continue
            p = os.path.join(dp, f)
            try:
                if os.path.getmtime(p) >= cutoff:
                    files.append(p)
            except OSError:
                pass
    if not files:
        sys.exit(f"近 {a.days} 天没有会话文件：{root}")

    retry_by_tool, literal_by_tool = Counter(), Counter()
    fail_by_tool = Counter()
    retry_sessions, literal_sessions, fail_sessions = set(), set(), set()
    calls = 0
    for p in files:
        try:
            retry, literal = analyze(p, a.min_run)
        except SystemExit:
            raise
        except Exception:
            continue
        sid = os.path.basename(os.path.dirname(p))[:20]
        for r in retry:
            retry_by_tool[r["name"]] += 1
            retry_sessions.add(sid)
            if r.get("fails"):
                fail_by_tool[r["name"]] += 1
                fail_sessions.add(sid)
        for r in literal:
            literal_by_tool[r["name"]] += 1
            literal_sessions.add(sid)
        calls += 1

    retry_total = sum(retry_by_tool.values())
    literal_total = sum(literal_by_tool.values())
    fail_total = sum(fail_by_tool.values())
    payload = {
        "window_days": a.days,
        "min_run": a.min_run,
        "sessions": len(files),
        "retry_runs": retry_total,
        "retry_sessions": len(retry_sessions),
        "retry_by_tool": dict(retry_by_tool.most_common()),
        "retry_after_failure_runs": fail_total,
        "retry_after_failure_sessions": len(fail_sessions),
        "retry_after_failure_by_tool": dict(fail_by_tool.most_common()),
        "literal_runs": literal_total,
        "literal_sessions": len(literal_sessions),
        "literal_by_tool": dict(literal_by_tool.most_common()),
    }
    if a.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print(f"扫描 {len(files)} 个会话（近 {a.days} 天）\n")
    print(f"换参重试（同工具连续 ≥{a.min_run} 次、参数各不相同）: {retry_total} 处 / {len(retry_sessions)} 个会话")
    for name, n in retry_by_tool.most_common(5):
        print(f"  {name:<28}{n:>4} 处")
    print(f"  其中串内至少一次报错（失败重试）: {fail_total} 处 / {len(fail_sessions)} 个会话")
    for name, n in fail_by_tool.most_common(5):
        print(f"  {name:<28}{n:>4} 处")
    print(f"\n字面重复（相邻参数完全相同，守卫的触发面）: {literal_total} 处 / {len(literal_sessions)} 个会话")
    for name, n in literal_by_tool.most_common(5):
        print(f"  {name:<28}{n:>4} 处")
    print("\n说明：两个数都只是观测，不做拦截；换参重试的处置走 tg-loop 的两次法则。")


if __name__ == "__main__":
    main()
