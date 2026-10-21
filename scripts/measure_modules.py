#!/usr/bin/env python3
"""按 step 重放一个会话，把上下文拆成模块并折成钱；可做配置反事实对比。

用法：
  python measure_modules.py --session <id|path>
  python measure_modules.py --session <id> --cf-schema 284000 --cf-catalog 42500
  python measure_modules.py --session <id> --json

价格默认 input $0.15 / cacheRead $0.003 / output $0.6（每百万 token），汇率 7.1。
"""
import argparse, json, os, subprocess, sys

RATE = 7.1
C_IN = 0.15 * RATE / 1e6
C_READ = 0.003 * RATE / 1e6
C_OUT = 0.6 * RATE / 1e6


def find_session(dsh_home, key):
    if os.path.isfile(key):
        return key
    root = os.path.join(os.path.expanduser(dsh_home), "sessions")
    for dp, _, fs in os.walk(root):
        if key in dp:
            for f in fs:
                if f.endswith(".zstd"):
                    return os.path.join(dp, f)
    return None


def load_events(path):
    raw = subprocess.run(["zstd", "-dc", path], capture_output=True).stdout
    out = []
    for line in raw.split(b"\n"):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def text_of(o):
    d = o.get("data") or {}
    m = d.get("message") or d
    c = m.get("content") if isinstance(m, dict) else None
    if isinstance(c, list):
        return "".join(x.get("text") or "" for x in c if isinstance(x, dict))
    return c if isinstance(c, str) else ""


def analyze(events):
    tools_bytes, cat_sizes = None, []
    ctx, acc, step_rows, turns = [], {}, [], 0
    for o in events:
        t = o.get("type")
        if t == "request/header":
            if tools_bytes is None:
                tools_bytes = len(json.dumps(o["data"]["header"].get("tools", []), ensure_ascii=False))
            continue
        if t == "assistant/message":
            u = o["data"].get("usage") or {}
            for mod, ch in list(ctx) + [("工具 schema", tools_bytes or 0)]:
                acc[mod] = acc.get(mod, 0) + ch
            step_rows.append((
                (u.get("inputTokens", 0) or 0) + (u.get("cacheReadTokens", 0) or 0),
                u.get("outputTokens", 0) or 0,
                u.get("inputTokens", 0) or 0,
                u.get("cacheReadTokens", 0) or 0))
            continue
        if t in ("user/message", "user/delta"):
            turns += 1
        d = o.get("data") or {}
        if t in ("tool/result", "tool-result"):
            mod = "工具结果"
        elif t == "assistant/message":
            mod = "助手输出"
        elif t in ("user/message", "user/delta"):
            mod = "用户输入"
        else:
            continue
        txt = text_of(o)
        if "<available_skills>" in txt:
            mod = "技能目录"
            cat_sizes.append(len(txt))
        elif "system-reminder" in txt or "runtime context" in txt:
            mod = "系统提示/提醒"
        if txt:
            ctx.append((mod, len(txt)))
    if not step_rows:
        return None

    total_ctx = sum(r[0] for r in step_rows)
    tok_per_char = total_ctx / (sum(acc.values()) or 1)
    ctx_in = sum(r[2] for r in step_rows)
    ctx_read = sum(r[3] for r in step_rows)
    blended = (ctx_in * C_IN + ctx_read * C_READ) / max(ctx_in + ctx_read, 1)
    out_tok = sum(r[1] for r in step_rows)

    cost = {m: ch * tok_per_char * blended for m, ch in acc.items()}
    cost["助手输出"] = out_tok * C_OUT
    return dict(steps=len(step_rows), turns=max(turns, 1), cost=cost,
                tokens={m: int(ch * tok_per_char) for m, ch in acc.items()},
                tools_bytes=tools_bytes,
                cat_copy=(sum(cat_sizes) / len(cat_sizes)) if cat_sizes else 0,
                out_tok=out_tok, total_ctx=total_ctx)


def counterfactual(r, cf_schema, cf_catalog):
    f_s = cf_schema / r["tools_bytes"] if (cf_schema and r["tools_bytes"]) else 1
    f_c = cf_catalog / r["cat_copy"] if (cf_catalog and r["cat_copy"]) else 1
    c = dict(r["cost"])
    for m, f in (("工具 schema", f_s), ("技能目录", f_c)):
        if m in c:
            c[m] *= f
    return sum(c.values()), f_s, f_c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--dsh-home", default="~/.dsh")
    ap.add_argument("--cf-schema", type=int, default=0, help="对照的工具 schema 字节数")
    ap.add_argument("--cf-catalog", type=int, default=0, help="对照的技能目录单条字节数")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    path = find_session(a.dsh_home, a.session)
    if not path:
        sys.exit(f"找不到会话: {a.session}")
    r = analyze(load_events(path))
    if not r:
        sys.exit("该会话没有 usage 记录")
    total = sum(r["cost"].values())

    if a.json:
        print(json.dumps({k: v for k, v in r.items() if k != "cost"} | {"total": total, "cost": r["cost"]},
                         ensure_ascii=False, indent=2, default=int))
        return

    print(f"会话 {os.path.basename(os.path.dirname(path))}")
    print(f"步={r['steps']} 轮={r['turns']} 总¥={total:.3f} 每轮¥={total/r['turns']:.4f} "
          f"上下文={r['total_ctx']/1e6:.1f}M 输出={r['out_tok']/1e3:.0f}k")
    print(f"schema={r['tools_bytes']}B 目录单条={r['cat_copy']:.0f}B")
    if a.cf_schema or a.cf_catalog:
        cf, fs, fc = counterfactual(r, a.cf_schema, a.cf_catalog)
        lo, hi = min(total, cf), max(total, cf)
        print(f"本会话 ¥{total:.3f}（¥{total/r['turns']:.4f}/轮）"
              f" | 对照 ¥{cf:.3f}（¥{cf/r['turns']:.4f}/轮）"
              f" | 差 {abs(total-cf)/hi*100:.1f}%（schema×{fs:.2f} 目录×{fc:.2f}）")
    print(f"\n{'模块':<14}{'tokens':>12}{'¥':>10}{'占%':>8}{'¥/轮':>10}")
    for m, c in sorted(r["cost"].items(), key=lambda x: -x[1]):
        print(f"{m:<14}{r['tokens'].get(m,0)/1e6:>10.2f}M{c:>10.4f}{c/total*100:>7.1f}%{c/r['turns']:>10.4f}")


if __name__ == "__main__":
    main()
