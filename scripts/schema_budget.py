#!/usr/bin/env python3
"""TG 工具 schema 预算与归因：每步重发的工具清单，到底谁占的。

为什么单列：schema 每步都在重发，是「每步成本」里最大的固定块。
工具一多就容易反弹（新装一个 MCP 回来几十 KB），需要一条能定期跑、能卡预算的命令。

用法：
  python scripts/schema_budget.py                      # 最近会话，按服务/族归因
  python scripts/schema_budget.py --days 7 --top 12    # 窗口与条数
  python scripts/schema_budget.py --session <id>       # 指定会话（--list 看 id）
  python scripts/schema_budget.py --json               # 给别的脚本用
  python scripts/schema_budget.py --check              # 超预算退出码 1（preflight / CI 用）

口径：
  - 字节 = 该工具在 `request/header.tools` 里的 JSON 序列化长度（ensure_ascii=False）。
  - token 换算用 0.4 token/字节的粗估，只用于量级判断，不是计费口径。
  - 预算默认 160 KB，可用 config.yaml 的 schema_budget_kb 覆盖；0 = 关闭检查。
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import measure_modules as mm  # noqa: E402  复用会话定位与解压
from tg_common import load_config, load_yaml, resolve_dsh_home, sessions_root, utf8_stdio  # noqa: E402

DEFAULT_BUDGET_KB = 160
TOK_PER_BYTE = 0.4


def safe_stdout():
    utf8_stdio()


def group_of(name):
    """把工具名归到「谁装上来的」。前缀规则，不求完美，只求能定位。"""
    if name.startswith("mcp__"):
        parts = name.split("__")
        if len(parts) >= 3 and parts[1]:
            return "mcp:" + parts[1]
    if name.startswith("mcp_connector_"):
        return "mcp:connector"
    exact = {
        "read": "本地文件", "write": "本地文件", "edit": "本地文件",
        "glob": "本地文件", "grep": "本地文件",
    }
    if name in exact:
        return exact[name]
    prefixes = [
        ("agnes_", "AI4Scholar 图像视频"),
        ("sci_draw", "AI4Scholar 绘图"),
        ("auto_cite", "AI4Scholar 引文"),
        ("search_", "AI4Scholar 检索"),
        ("download_", "AI4Scholar PDF"),
        ("read_", "AI4Scholar 全文"),
        ("get_pubmed", "PubMed"),
        ("get_semantic", "Semantic Scholar"),
        ("get_ai4scholar", "AI4Scholar 账户"),
        ("subagent", "编排"),
        ("workflow", "编排"),
        ("schedule_", "编排"),
        ("ops_", "本地执行"),
        ("py", "本地执行"),
        ("terminal_", "终端"),
        ("job_", "终端"),
        ("session_", "会话与资源"),
        ("list_", "会话与资源"),
        ("read_mcp", "会话与资源"),
        ("save_token", "会话与资源"),
    ]
    for p, label in prefixes:
        if name.startswith(p):
            return label
    return "其他"


def load_budget(config_path):
    """返回 (预算KB, 来源, 价格表USD, 汇率)。预算优先级：config.schema_budget_kb > 默认 160。

    配置读取走 tg_common.load_yaml：读不出来会 stderr 警告，不静默换成另一套默认值。
    """
    prices = dict(mm.DEFAULT_PRICES)
    rate = mm.DEFAULT_RATE
    if not config_path or not os.path.isfile(config_path):
        return DEFAULT_BUDGET_KB, "默认", prices, rate
    cfg = load_yaml(config_path, strict=False)
    if not cfg:
        return DEFAULT_BUDGET_KB, f"默认（{config_path} 没读到配置）", prices, rate
    p = cfg.get("prices_usd_per_mtok") or {}
    for k in list(prices):
        if p.get(k) is not None:
            prices[k] = float(p[k])
    if cfg.get("rate") is not None:
        rate = float(cfg["rate"])
    v = cfg.get("schema_budget_kb")
    if v is None:
        return DEFAULT_BUDGET_KB, "默认", prices, rate
    try:
        return int(v), f"config: {config_path}", prices, rate
    except (TypeError, ValueError):
        return DEFAULT_BUDGET_KB, "默认（值不是整数）", prices, rate


def load_dsh_home(config_path, arg=""):
    """--dsh-home 没显式给时，跟 config.yaml 的 dsh_home 走（多套客户端时别读错账）。

    口径统一在 tg_common.resolve_dsh_home：显式 > config > $DSH_HOME > ~/.dsh。
    """
    cfg, base = load_config(config_path)
    return resolve_dsh_home(arg, cfg=cfg, base=base)


def pick_session(dsh_home, session, days, limit=8):
    """返回 [(路径, 会话 id)]。session 指定时只回一个；否则按时间倒序多取几个。

    多取几个的原因：最新那个会话可能刚开始写、还没有 request/header，
    调用方要能往下顺延，而不是把「没有 header」当成错误。
    """
    if session:
        p = mm.find_session(dsh_home, session)
        if not p:
            sys.exit(f"找不到会话: {session}")
        return [(p, os.path.basename(os.path.dirname(p)).replace("session-", "")[:8])]
    root = sessions_root(dsh_home)
    if not os.path.isdir(root):
        sys.exit(f"找不到会话目录: {root}（检查 --dsh-home）")
    import time
    cutoff = time.time() - days * 86400
    found = []
    for dp, _, fs in os.walk(root):
        for f in fs:
            if not f.endswith(".zstd"):
                continue
            p = os.path.join(dp, f)
            try:
                m = os.path.getmtime(p)
            except OSError:
                continue
            if m >= cutoff:
                found.append((m, p))
    if not found:
        sys.exit(f"近 {days} 天没有会话文件；加 --days 放宽，或用 --session 指定。")
    found.sort(reverse=True)
    return [(p, os.path.basename(os.path.dirname(p)).replace("session-", "")[:8])
            for _, p in found[:limit]]


def collect(path):
    """读会话里第一条 request/header，返回 [(name, bytes)] 与步数；没有 header 返回 None。"""
    events = mm.load_events(path)
    if not events:
        return None
    tools, steps = None, 0
    for o in events:
        t = o.get("type")
        if t == "request/header" and tools is None:
            tools = o["data"]["header"].get("tools") or []
        elif t == "assistant/message":
            steps += 1
    if tools is None:
        return None
    rows = [(x.get("name") or "?", len(json.dumps(x, ensure_ascii=False))) for x in tools]
    return rows, steps


def main():
    safe_stdout()
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default="")
    ap.add_argument("--days", type=int, default=7, help="找最近会话的窗口（天）")
    ap.add_argument("--top", type=int, default=12, help="归因表显示几行")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--dsh-home", default="", help="不给则跟 config.yaml 的 dsh_home")
    ap.add_argument("--budget-kb", type=int, default=-1, help="覆盖 config 里的预算")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", action="store_true", help="超预算时退出码 1")
    a = ap.parse_args()

    dsh_home = load_dsh_home(a.config, a.dsh_home)

    rows, steps, sid, skipped = None, 0, "", 0
    for path, cand in pick_session(dsh_home, a.session, a.days):
        got = collect(path)
        if got:
            rows, steps, sid = got[0], got[1], cand
            break
        skipped += 1
    if rows is None:
        sys.exit("窗口内的会话都没有 request/header：只认 DSH v4 会话日志，"
                 "其它客户端（Claude Code / Codex / Cursor）没有同构结构。")
    total = sum(b for _, b in rows)

    budget, budget_src, prices, rate = load_budget(a.config)
    if a.budget_kb >= 0:
        budget, budget_src = a.budget_kb, "--budget-kb"
    over = max(total - budget * 1024, 0) if budget else 0

    groups = {}
    for name, b in rows:
        g = group_of(name)
        cur = groups.setdefault(g, {"tools": 0, "bytes": 0})
        cur["tools"] += 1
        cur["bytes"] += b
    glist = sorted(({"group": g, **v} for g, v in groups.items()), key=lambda x: -x["bytes"])
    singles = sorted(({"tool": n, "bytes": b, "group": group_of(n)} for n, b in rows), key=lambda x: -x["bytes"])

    # 估算：调小 schema 省的是「每步重发量」，与命中率无关（前缀稳定时也会按比例变小）
    saved_tokens_step = int(over * TOK_PER_BYTE)
    saved_cny_step = saved_tokens_step / 1e6 * prices["input"] * rate

    out = {
        "session": sid, "steps": steps, "tools": len(rows),
        "skipped_sessions": skipped,
        "schema_bytes": total, "schema_kb": round(total / 1024, 1),
        "budget_kb": budget, "budget_source": budget_src,
        "over_budget_kb": round(over / 1024, 1),
        "groups": glist, "top_tools": singles[:a.top],
        "estimate": {
            "saved_tokens_per_step": saved_tokens_step,
            "saved_cny_per_step": round(saved_cny_step, 5),
            "tok_per_byte": TOK_PER_BYTE,
            "price_basis": f"input ${prices['input']}/M × {rate}（粗估）",
        },
    }

    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        if a.check and over:
            sys.exit(1)
        return

    print(f"会话 {sid}  步={steps}  工具={len(rows)}  schema={total/1024:.1f} KB  预算={budget} KB（{budget_src}）")
    if over:
        print(f"超预算 {over/1024:.1f} KB → 每步约省 {saved_tokens_step} tokens ≈ ¥{saved_cny_step:.4f}/步（粗估）")
    else:
        print(f"余量 {budget - total/1024:.1f} KB")
    print(f"\n{'组':<22}{'工具':>5}{'KB':>8}{'占%':>7}")
    for g in glist[:a.top]:
        print(f"{g['group']:<22}{g['tools']:>5}{g['bytes']/1024:>8.1f}{g['bytes']/total*100:>6.1f}%")
    if len(glist) > a.top:
        rest = sum(g["bytes"] for g in glist[a.top:])
        print(f"{'（其余 %d 组）' % (len(glist)-a.top):<22}{'':>5}{rest/1024:>8.1f}{rest/total*100:>6.1f}%")
    print(f"\n{'单工具 Top':<30}{'KB':>8}{'占%':>7}")
    for t in singles[:8]:
        print(f"{t['tool']:<30}{t['bytes']/1024:>8.1f}{t['bytes']/total*100:>6.1f}%")
    print("\n口径：字节取自 request/header.tools；token 按 0.4/字节粗估，只作量级；"
          "省的是每步重发量（与命中率分账）。")

    if a.check and over:
        sys.exit(1)


if __name__ == "__main__":
    main()
