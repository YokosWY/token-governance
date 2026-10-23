#!/usr/bin/env python3
"""按 step 重放一个会话，把上下文拆成模块并折成钱；可做配置反事实对比。

用法：
  python measure_modules.py --session <id|path> [--config config.yaml]
  python measure_modules.py --session <id> --cf-schema 284000 --cf-catalog 42500
  python measure_modules.py --session <id> --json

只认 DSH v4 会话日志（session.v4.jsonl.zstd）。
价格默认 input $0.15 / cacheRead $0.003 / output $0.6（每百万 token）、汇率 7.1；
--config 可写 prices_usd_per_mtok 与 rate 覆盖，未覆盖时输出标「默认 flash 价（估算）」。
"""
import argparse, json, os, subprocess, sys

from tg_common import config_base, resolve_dsh_home, sessions_root, utf8_stdio

utf8_stdio()

DEFAULT_RATE = 7.1
DEFAULT_PRICES = {"input": 0.15, "cached": 0.003, "output": 0.6}

PRICES = {}          # 由 set_prices() 填充，单位 CNY / 百万 token
PRICE_SOURCE = "默认 flash 价（估算）"


def set_prices(prices, rate, source="默认 flash 价（估算）"):
    global PRICES, PRICE_SOURCE
    PRICES = {k: float(v) * rate / 1e6 for k, v in prices.items()}
    PRICE_SOURCE = source


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


def decompress(path):
    """先试 zstd 命令，没有再用 Python zstandard；两者都没有才退出。"""
    try:
        r = subprocess.run(["zstd", "-dc", path], capture_output=True)
        if r.returncode == 0 and r.stdout:
            return r.stdout
    except FileNotFoundError:
        pass
    try:
        import zstandard as zstd
    except ImportError:
        sys.exit("解压需要二选一：`zstd` 命令（Windows 用 Git for Windows 自带的，macOS `brew install zstd`）"
                 "或 `pip install zstandard`。")
    with open(path, "rb") as f:
        data = f.read()
    dctx = zstd.ZstdDecompressor()
    out = []
    while data:
        dobj = dctx.decompressobj()
        try:
            out.append(dobj.decompress(data))
        except Exception:
            sys.exit(f"zstd 解压失败（文件损坏或不是 zstd 流）: {path}")
        if not dobj.eof:
            break
        data = dobj.unused_data
    return b"".join(out)


def load_events(path):
    raw = decompress(path)
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
    blended = (ctx_in * PRICES["input"] + ctx_read * PRICES["cached"]) / max(ctx_in + ctx_read, 1)
    out_tok = sum(r[1] for r in step_rows)

    cost = {m: ch * tok_per_char * blended for m, ch in acc.items()}
    cost["助手输出"] = out_tok * PRICES["output"]
    toks = {m: int(ch * tok_per_char) for m, ch in acc.items()}
    toks["助手输出"] = out_tok
    return dict(steps=len(step_rows), turns=max(turns, 1), cost=cost,
                tokens=toks,
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


def load_price_config(path):
    if not path or not os.path.isfile(path):
        return {}
    try:
        import yaml
    except ImportError:
        sys.exit("读 --config 需要 pyyaml：pip install pyyaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def list_sessions(root, days, limit):
    """最近会话速查：步数 / 轮数 / CNY / 首条用户消息。给 --session 选 id 用。"""
    import datetime, time
    cutoff = time.time() - days * 86400
    rows = []
    for dp, _, fs in os.walk(root):
        for f in fs:
            if not f.endswith(".zstd"):
                continue
            p = os.path.join(dp, f)
            try:
                if os.path.getmtime(p) < cutoff:
                    continue
            except OSError:
                continue
            try:
                ev = load_events(p)
            except SystemExit:
                raise
            except Exception:
                continue
            r = analyze(ev)
            if not r:
                continue
            preview = ""
            for o in ev:
                if o.get("type") in ("user/message", "user/delta"):
                    txt = text_of(o).strip().replace("\n", " ")
                    if txt:
                        preview = txt[:32]
                        break
            rows.append((os.path.getmtime(p), os.path.basename(dp).replace("session-", "")[:8],
                         datetime.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%m-%d %H:%M"),
                         r["steps"], r["turns"], sum(r["cost"].values()), r["tools_bytes"] or 0, preview))
    rows.sort(reverse=True)
    rows = rows[:limit]
    print(f"{'id':10}{'修改时间':>13}{'步':>6}{'轮':>5}{'CNY':>9}{'schema':>8}  首条消息")
    for _, sid, when, steps, turns, cost, tools, prev in rows:
        print(f"{sid:10}{when:>13}{steps:>6}{turns:>5}{cost:>9.3f}{tools/1000:>7.0f}k  {prev}")
    print(f"\n共 {len(rows)} 个（近 {days} 天，按修改时间倒序）；取 id 传给 --session。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default="")
    ap.add_argument("--list", action="store_true", help="列出最近会话，按修改时间倒序")
    ap.add_argument("--days", type=int, default=7, help="--list 的窗口天数")
    ap.add_argument("--limit", type=int, default=15, help="--list 最多几条")
    ap.add_argument("--dsh-home", default="", help="不给则读 config 的 dsh_home，再退回 $DSH_HOME / ~/.dsh")
    ap.add_argument("--config", default="", help="从 config.yaml 读 prices_usd_per_mtok 与 rate")
    ap.add_argument("--rate", type=float, default=0.0, help="USD→CNY 汇率，覆盖配置")
    ap.add_argument("--cf-schema", type=int, default=0, help="对照的工具 schema 字节数")
    ap.add_argument("--cf-catalog", type=int, default=0, help="对照的技能目录单条字节数")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    prices, rate, src = dict(DEFAULT_PRICES), DEFAULT_RATE, "默认 flash 价（估算）"
    cfg = load_price_config(a.config)
    if cfg:
        p = cfg.get("prices_usd_per_mtok") or {}
        found = False
        for k in list(prices):
            if p.get(k) is not None:
                prices[k] = float(p[k])
                found = True
        if cfg.get("rate") is not None:
            rate = float(cfg["rate"])
            found = True
        src = f"config: {a.config}" if found else f"config: {a.config} 没有价格表，用默认 flash 价（估算）"
    if a.rate:
        rate = a.rate
        src = f"{src}，汇率由 --rate 覆盖"
    set_prices(prices, rate, src)

    home = resolve_dsh_home(a.dsh_home, cfg=cfg, base=config_base(a.config))
    root = sessions_root(home)
    if a.list:
        if not os.path.isdir(root):
            sys.exit(f"找不到会话目录: {root}（检查 --dsh-home）")
        list_sessions(root, a.days, a.limit)
        return
    if not a.session:
        sys.exit("需要 --session <id|path>，或加 --list 先看有哪些会话。")

    path = find_session(home, a.session)
    if not path:
        sys.exit(f"找不到会话: {a.session}")
    events = load_events(path)
    if not events:
        sys.exit(f"会话文件里没有可解析事件（zstd 解压失败或文件被截断）: {path}")
    types = {o.get("type") for o in events if isinstance(o, dict)}
    if "assistant/message" not in types or "request/header" not in types:
        seen = ", ".join(sorted(t for t in types if t)[:10]) or "（空）"
        sys.exit("不支持的日志格式：只认 DSH v4 会话（session.v4.jsonl.zstd，"
                 "含 request/header 与 assistant/message 的 usage）。\n"
                 f"这个文件里的类型：{seen}\n"
                 "其它客户端（Claude Code / Codex / Cursor）没有同构日志；方法可照搬，脚本不适用。")
    r = analyze(events)
    if not r:
        sys.exit("该会话没有 usage 记录（刚开始的会话或没有 assistant 步）")
    total = sum(r["cost"].values())

    if a.json:
        print(json.dumps({k: v for k, v in r.items() if k != "cost"} |
                         {"total": total, "cost": r["cost"], "price_basis": PRICE_SOURCE,
                          "prices_usd_per_mtok": prices, "rate": rate},
                         ensure_ascii=False, indent=2, default=int))
        return

    print(f"会话 {os.path.basename(os.path.dirname(path))}")
    print(f"计价: input ${prices['input']}/M cacheRead ${prices['cached']}/M output ${prices['output']}/M"
          f" × {rate}（{PRICE_SOURCE}）")
    print(f"步={r['steps']} 轮={r['turns']} 总CNY={total:.3f} 每轮CNY={total/r['turns']:.4f} "
          f"上下文={r['total_ctx']/1e6:.1f}M 输出={r['out_tok']/1e3:.0f}k")
    print(f"schema={r['tools_bytes']}B 目录单条={r['cat_copy']:.0f}B")
    if a.cf_schema or a.cf_catalog:
        cf, fs, fc = counterfactual(r, a.cf_schema, a.cf_catalog)
        lo, hi = min(total, cf), max(total, cf)
        print(f"本会话 CNY {total:.3f}（CNY {total/r['turns']:.4f}/轮）"
              f" | 对照 CNY {cf:.3f}（CNY {cf/r['turns']:.4f}/轮）"
              f" | 差 {abs(total-cf)/hi*100:.1f}%（schema×{fs:.2f} 目录×{fc:.2f}）")
    print(f"\n{'模块':<14}{'tokens':>12}{'CNY':>10}{'占%':>8}{'CNY/轮':>10}")
    for m, c in sorted(r["cost"].items(), key=lambda x: -x[1]):
        print(f"{m:<14}{r['tokens'].get(m,0)/1e6:>10.2f}M{c:>10.4f}{c/total*100:>7.1f}%{c/r['turns']:>10.4f}")


if __name__ == "__main__":
    main()
