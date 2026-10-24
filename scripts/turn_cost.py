#!/usr/bin/env python3
"""任务成本：按「一条用户指令 = 一个任务」算钱，而不是按步。

用法：
  python turn_cost.py --config config.yaml --days 3
  python turn_cost.py --config config.yaml --session <id>
  python turn_cost.py --dsh-home ~/.dsh --days 7 --json

输出：每轮（任务）的步数、上下文、CNY、失败调用数；再给中位/均值/P90。
价格读 config 的 prices_usd_per_mtok + rate，缺省用 flash 价（估算）。
"""
import argparse, json, os, statistics, subprocess, sys, time

from tg_common import config_base, load_config, resolve_dsh_home, sessions_root, utf8_stdio, decompress_bytes

# Windows 控制台默认 GBK：固定 UTF-8，避免中文报告/JSON 在无 PYTHONIOENCODING 的环境里乱码或崩。
utf8_stdio()


def decompress(path):
    """解压 .zstd：口径统一在 tg_common（zstd 命令 → zstandard 模块 → 一句可执行提示）。"""
    return decompress_bytes(path)


FAIL_EXIT = __import__("re").compile(r"\[exit code: ([1-9]\d*)\]")


def is_failure(text):
    t = (text or "").lstrip()
    return bool(FAIL_EXIT.search(t)) or t.startswith(("Error", "ERROR", "Traceback (most recent call last)", "ENOENT", "EACCES"))


def text_of(msg):
    return "".join(c.get("text") or "" for c in (msg.get("content") or []) if isinstance(c, dict))


def turns_of(path):
    """切轮：遇到 user/message 收一段。返回每轮的用量与失败数。"""
    cur = None
    out = []
    failed_ids = {}
    events = []
    raw = decompress(path)
    for line in raw.split(b"\n"):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except Exception:
            pass
    for o in events:
        t = o.get("type")
        if t == "tool/result":
            d = o.get("data") or {}
            msg = d.get("message") or {}
            cid = d.get("toolCallId") or (msg.get("source") or {}).get("callId")
            if cid:
                failed_ids[cid] = is_failure(text_of(msg))
    for o in events:
        t = o.get("type")
        if t == "user/message":
            if cur:
                out.append(cur)
            cur = {"inp": 0, "read": 0, "out": 0, "steps": 0, "tools": 0, "fails": 0}
            continue
        if not cur:
            continue
        if t == "assistant/message":
            u = (o.get("data") or {}).get("usage") or {}
            cur["inp"] += u.get("inputTokens", 0) or 0
            cur["read"] += u.get("cacheReadTokens", 0) or 0
            cur["out"] += u.get("outputTokens", 0) or 0
            cur["steps"] += 1
        elif t == "tool/call":
            cur["tools"] += 1
            cid = (o.get("data") or {}).get("callId")
            if failed_ids.get(cid):
                cur["fails"] += 1
    if cur:
        out.append(cur)
    return [c for c in out if c["steps"] or c["tools"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--dsh-home", default="")
    ap.add_argument("--session", default="")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--top", type=int, default=10, help="明细最多打印几轮")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    prices, rate, src = {"input": 0.15, "cached": 0.003, "output": 0.6}, 7.1, "默认 flash 价（估算）"
    cfg, _base = load_config(a.config)
    p = cfg.get("prices_usd_per_mtok") or {}
    if p:
        prices.update({k: float(v) for k, v in p.items() if v is not None})
        src = f"config: {a.config}"
    if cfg.get("rate") is not None:
        rate = float(cfg["rate"])
    cin, cread, cout = (prices["input"] * rate / 1e6, prices["cached"] * rate / 1e6,
                        prices["output"] * rate / 1e6)
    root = sessions_root(resolve_dsh_home(a.dsh_home, cfg=cfg, base=config_base(a.config)))
    if not os.path.isdir(root):
        sys.exit(f"找不到会话目录: {root}（检查 --dsh-home / config 的 dsh_home）")

    files = []
    if a.session:
        for dp, _, fs in os.walk(root):
            if a.session in dp:
                files = [os.path.join(dp, f) for f in fs if f.endswith(".zstd")]
                break
        if not files:
            sys.exit(f"找不到会话: {a.session}")
    else:
        cut = time.time() - a.days * 86400
        for dp, _, fs in os.walk(root):
            for f in fs:
                if f.endswith(".zstd"):
                    p = os.path.join(dp, f)
                    if os.path.getmtime(p) >= cut:
                        files.append(p)

    rows = []
    for p in files:
        sid = os.path.basename(os.path.dirname(p)).replace("session-", "")[:8]
        for i, t in enumerate(turns_of(p), 1):
            ctx = t["inp"] + t["read"]
            rows.append({
                "session": sid, "turn": i, "steps": t["steps"],
                "ctx_per_step": int(ctx / t["steps"]) if t["steps"] else 0,
                "cost": t["inp"] * cin + t["read"] * cread + t["out"] * cout,
                "tools": t["tools"], "fails": t["fails"],
            })
    if not rows:
        sys.exit("窗口内没有可用轮次（要含 user/message 与 assistant/message）。")

    costs = sorted(r["cost"] for r in rows)
    steps = sorted(r["steps"] for r in rows)
    p90 = costs[min(len(costs) - 1, int(len(costs) * 0.9))]
    summary = {
        "turns": len(rows), "sessions": len(files), "price_source": src,
        "cost_median": round(statistics.median(costs), 4),
        "cost_mean": round(sum(costs) / len(costs), 4),
        "cost_p90": round(p90, 4),
        "cost_total": round(sum(costs), 4),
        "steps_median": statistics.median(steps),
        "fail_turns": sum(1 for r in rows if r["fails"]),
    }
    if a.json:
        print(json.dumps({"summary": summary, "turns": rows[:200]}, ensure_ascii=False, indent=2))
        return

    print(f"任务成本（{len(files)} 个会话 / {len(rows)} 轮；价格 {src}）\n")
    print(f"{'每轮 CNY':>10}{'中位':>10}{'均值':>10}{'P90':>10}")
    print(f"{'':>10}{summary['cost_median']:>10}{summary['cost_mean']:>10}{summary['cost_p90']:>10}")
    print(f"\n总花费 ¥{summary['cost_total']:.2f}；每轮步数中位 {summary['steps_median']}；"
          f"有失败调用的轮次 {summary['fail_turns']}/{len(rows)}\n")
    print(f"{'会话':10}{'轮':>4}{'步':>5}{'上下文/步':>11}{'CNY':>9}{'工具':>6}{'失败':>6}")
    for r in sorted(rows, key=lambda x: -x["cost"])[:a.top]:
        print(f"{r['session']:10}{r['turn']:>4}{r['steps']:>5}{r['ctx_per_step']/1000:>10.0f}k"
              f"{r['cost']:>9.3f}{r['tools']:>6}{r['fails']:>6}")
    print("\n口径：一条用户指令 = 一轮任务。省钱要落在这里，不是单步。")


if __name__ == "__main__":
    main()
