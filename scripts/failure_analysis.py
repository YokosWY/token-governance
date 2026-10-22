#!/usr/bin/env python3
"""失败重试分析：哪里在报错、报什么、花了多少钱。

用法：
  python failure_analysis.py --config config.yaml --days 3
  python failure_analysis.py --dsh-home ~/.dsh --days 7 --json --top 12

口径：
  - 失败判据与 retry_patterns 一致（非零退出码 / Error / Traceback / ENOENT / EACCES）。
  - 「受影响轮次」= 至少一次失败调用的用户轮次；钱按轮次汇总（价格读 config）。
  - 报文取首行，数字归一成 N，只用于分类，不做内容展示。
"""
import argparse, json, os, re, statistics, subprocess, sys, time

FAIL_EXIT = re.compile(r"\[exit code: ([1-9]\d*)\]")
PROTOCOL = (
    "file has not been read",
    "file changed since it was read",
    "old_string and new_string must differ",
    "unknown tool",
    "cannot modify",
)


def is_protocol(sig):
    return any(k in sig for k in PROTOCOL)
DIGITS = re.compile(r"\d+")


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
    d = zstd.ZstdDecompressor()
    out = []
    while data:
        obj = d.decompressobj()
        out.append(obj.decompress(data))
        if not obj.eof:
            break
        data = obj.unused_data
    return b"".join(out)


def text_of(msg):
    if isinstance(msg, str):
        return msg
    return "".join(c.get("text") or "" for c in (msg.get("content") or []) if isinstance(c, dict))


def is_failure(text):
    t = (text or "").lstrip()
    return bool(FAIL_EXIT.search(t)) or t.startswith(("Error", "ERROR", "Traceback (most recent call last)", "ENOENT", "EACCES"))


SKIP = {"[stderr]", "(no output)", "[exit code: N]"}
EXIT_ONLY = re.compile(r"^\[exit code: \d+\]$")


def signature(text):
    lines = [x.strip() for x in (text or "").splitlines() if x.strip()]
    pick = ""
    for line in lines:
        norm = DIGITS.sub("N", line)
        if norm in SKIP or EXIT_ONLY.match(norm):
            continue
        pick = norm
        break
    if not pick:
        pick = DIGITS.sub("N", lines[0]) if lines else ""
    pick = re.sub(r"\s+", " ", pick)
    pick = re.sub(r"[A-Za-z]:[\\/][^\s\"]+", "<path>", pick)
    return pick[:100]


def analyze(path):
    """返回 (失败调用列表, 每轮成本, 每轮是否有失败)。"""
    events = []
    for line in decompress(path).split(b"\n"):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except Exception:
            pass

    calls, results = {}, {}
    for o in events:
        t = o.get("type")
        d = o.get("data") or {}
        if t == "tool/call":
            calls[d.get("callId")] = d.get("name")
        elif t == "tool/result":
            msg = d.get("message") or {}
            cid = d.get("toolCallId") or (msg.get("source") or {}).get("callId")
            if cid:
                results[cid] = text_of(msg)

    fails, turn = [], 0
    turn_cost_input, turn_failed = [], []
    cur = {"inp": 0, "read": 0, "out": 0, "fail": 0}
    for o in events:
        t = o.get("type")
        d = o.get("data") or {}
        if t == "user/message":
            if turn:
                turn_cost_input.append(cur)
                turn_failed.append(cur["fail"] > 0)
            turn += 1
            cur = {"inp": 0, "read": 0, "out": 0, "fail": 0}
            continue
        if t == "assistant/message":
            u = d.get("usage") or {}
            cur["inp"] += u.get("inputTokens", 0) or 0
            cur["read"] += u.get("cacheReadTokens", 0) or 0
            cur["out"] += u.get("outputTokens", 0) or 0
        elif t == "tool/result" and not t == "tool/call":
            msg = d.get("message") or {}
            cid = d.get("toolCallId") or (msg.get("source") or {}).get("callId")
            if cid and is_failure(results.get(cid, "")):
                fails.append({"tool": calls.get(cid) or "?", "sig": signature(results.get(cid, "")), "turn": turn})
                cur["fail"] += 1
    if turn:
        turn_cost_input.append(cur)
        turn_failed.append(cur["fail"] > 0)
    return fails, turn_cost_input, turn_failed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--dsh-home", default="")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--protocol-only", action="store_true", help="只看工具协议错误（没读就改/文件被改/未知工具）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    prices, rate, src = {"input": 0.15, "cached": 0.003, "output": 0.6}, 7.1, "默认 flash 价（估算）"
    home = a.dsh_home
    if os.path.isfile(a.config):
        try:
            import yaml
            cfg = yaml.safe_load(open(a.config, encoding="utf-8")) or {}
        except Exception as e:
            sys.exit(f"{a.config} 读不了：{str(e).splitlines()[0]}")
        p = cfg.get("prices_usd_per_mtok") or {}
        if p:
            prices.update({k: float(v) for k, v in p.items() if v is not None})
            src = f"config: {a.config}"
        if cfg.get("rate") is not None:
            rate = float(cfg["rate"])
        home = home or cfg.get("dsh_home") or ""
    cin, cread, cout = prices["input"] * rate / 1e6, prices["cached"] * rate / 1e6, prices["output"] * rate / 1e6

    root = os.path.join(os.path.expanduser(os.path.expandvars(home or "~/.dsh")), "sessions")
    if not os.path.isdir(root):
        sys.exit(f"找不到会话目录: {root}")
    cut = time.time() - a.days * 86400
    files = []
    for dp, _, fs in os.walk(root):
        for f in fs:
            if f.endswith(".zstd"):
                p = os.path.join(dp, f)
                try:
                    if os.path.getmtime(p) >= cut:
                        files.append(p)
                except OSError:
                    pass
    if not files:
        sys.exit(f"近 {a.days} 天没有会话")

    all_fails = []
    by_tool, by_sig = {}, {}
    fail_cost, total_cost, fail_turns, all_turns = 0.0, 0.0, 0, 0
    sessions_with_fail = set()
    for p in files:
        sid = os.path.basename(os.path.dirname(p)).replace("session-", "")[:8]
        try:
            fails, tc, tf = analyze(p)
        except SystemExit:
            raise
        except Exception:
            continue
        for c in tc:
            total_cost += c["inp"] * cin + c["read"] * cread + c["out"] * cout
        for c, bad in zip(tc, tf):
            all_turns += 1
            if bad:
                fail_turns += 1
                fail_cost += c["inp"] * cin + c["read"] * cread + c["out"] * cout
        for f in fails:
            all_fails.append(f)
            sessions_with_fail.add(sid)

    proto = [f for f in all_fails if is_protocol(f["sig"])]
    shown = proto if a.protocol_only else all_fails
    for f in shown:
        by_tool[f["tool"]] = by_tool.get(f["tool"], 0) + 1
        by_sig[(f["tool"], f["sig"])] = by_sig.get((f["tool"], f["sig"]), 0) + 1

    payload = {
        "window_days": a.days, "sessions": len(files), "sessions_with_failure": len(sessions_with_fail),
        "turns": all_turns, "turns_with_failure": fail_turns,
        "failed_calls": len(all_fails),
        "protocol_failed_calls": len(proto),
        "protocol_share": round(len(proto) / len(all_fails) * 100, 1) if all_fails else 0,
        "failed_call_cost": round(fail_cost, 2), "total_cost": round(total_cost, 2),
        "cost_share": round(fail_cost / total_cost * 100, 1) if total_cost else 0,
        "by_tool": dict(sorted(by_tool.items(), key=lambda kv: -kv[1])),
        "top_signatures": [{"tool": k[0], "sig": k[1], "n": v} for k, v in
                           sorted(by_sig.items(), key=lambda kv: -kv[1])[:a.top]],
        "price_source": src,
    }
    if a.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print(f"失败重试分析（近 {a.days} 天，{len(files)} 个会话；价格 {src}）\n")
    print(f"失败调用 {len(all_fails)} 次，落在 {fail_turns} 轮 / {len(sessions_with_fail)} 个会话（总 {all_turns} 轮）")
    print(f"其中工具协议错误 {len(proto)} 次（占失败调用 {payload['protocol_share']}%）"
          + ("——本次只列这一类" if a.protocol_only else ""))
    print(f"这些轮次花了 ¥{fail_cost:.2f}，占窗口总花费 ¥{total_cost:.2f} 的 {payload['cost_share']}%\n")
    print(f"{'工具':<18}{'失败次数':>8}")
    for k, v in list(payload["by_tool"].items())[:8]:
        print(f"{k:<18}{v:>8}")
    print(f"\n{'工具':<16}{'次数':>6}  报文首行（数字已归一）")
    for row in payload["top_signatures"]:
        print(f"{row['tool']:<16}{row['n']:>6}  {row['sig']}")
    print("\n处置建议按报文归类：路径不存在 → 先 ls 确认；text not found → 换关键词；"
          "命令不存在 → 查 PATH；超时 → 拆小批。数字只用于分类，不回显内容。")


if __name__ == "__main__":
    main()
