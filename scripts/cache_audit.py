#!/usr/bin/env python3
"""cache_audit：缓存命中体检。只读、零写入、纯标准库，换机器不用装依赖。

三种数据源，自动降级：
  1) DSH 账本   --config config.yaml（读 dsh_home / dsh_homes）
  2) 任意 jsonl --jsonl path（每行一个 JSON，只要能找到 usage 里的两个 token 数）
  3) 都没有     打印采集模板，按 10 次响应手工填

核心只算三个数：命中率、未命中/步、未命中成本占比（按价差 k = P_in / P_cache）。

用法:
  python cache_audit.py --config config.yaml --days 7
  python cache_audit.py --jsonl session.jsonl --price-in 0.15 --price-cache 0.003
  python cache_audit.py --template          # 打印手工采集模板
"""
import argparse
import json
import os
import sys

from tg_common import expand, load_config, resolve_dsh_homes, utf8_stdio

DEFAULT_IN = 0.15      # 未命中输入价（USD / M tok），换 provider 自己改
DEFAULT_CACHE = 0.003  # 命中价
FIELD_ALIASES = {
    "input": ("input", "input_tokens", "prompt_tokens", "promptTokenCount", "uncached_input_tokens"),
    "cacheRead": ("cacheRead", "cache_read", "cache_read_input_tokens", "cache_read_tokens",
                  "cached_tokens", "prompt_cache_hit_tokens", "cachedContentTokenCount"),
    "cacheWrite": ("cacheWrite", "cache_write", "cache_creation_input_tokens", "prompt_cache_miss_tokens"),
    "output": ("output", "output_tokens", "completion_tokens", "candidatesTokenCount"),
}


def _safe_stdout():
    utf8_stdio()


def pick(d, names):
    for n in names:
        v = d.get(n)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return int(v)
    return None


def find_usage(obj, depth=0):
    """在任意嵌套结构里找 usage 字典。"""
    if depth > 4 or not isinstance(obj, dict):
        return None
    for key in ("usage", "token_usage", "tokenUsage", "usageMetadata"):
        u = obj.get(key)
        if isinstance(u, dict):
            return u
    for v in obj.values():
        if isinstance(v, dict):
            u = find_usage(v, depth + 1)
            if u:
                return u
    return None


def read_ledger(path):
    """读一份 DSH ledger，返回 {date: {input, cacheRead, cacheWrite, output, calls, cost}}。"""
    try:
        with open(path, encoding="utf-8") as f:
            led = json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"[skip] 读不出来（可能正在写）: {path} ({e.__class__.__name__})", file=sys.stderr)
        return None
    if not isinstance(led, dict):
        return None
    out = {}
    for date, day in (led.get("days") or {}).items():
        if not isinstance(day, dict):
            continue
        out[date] = {
            "input": int(day.get("input") or 0),
            "cacheRead": int(day.get("cacheRead") or 0),
            "cacheWrite": int(day.get("cacheWrite") or 0),
            "output": int(day.get("output") or 0),
            "calls": int(day.get("calls") or 0),
            "cost": float(day.get("costCny") or day.get("cost") or 0.0),
        }
    return out


def collect_ledgers(args):
    """按 --ledger > --home > config 的 dsh_homes/dsh_home > $DSH_HOME > ~/.dsh 找账本，按真实路径去重。"""
    candidates = []
    # 显式指定的路径不存在时直接报错：静默回退到别的 home 会给出错误结论
    if args.ledger and not os.path.isfile(expand(args.ledger)):
        sys.exit(f"--ledger 不存在: {expand(args.ledger)}")
    if args.home and not os.path.isdir(expand(args.home)):
        sys.exit(f"--home 不存在: {expand(args.home)}")
    cfg, base = load_config(args.config) if args.config else ({}, os.getcwd())
    if args.home:
        homes = [expand(args.home)]
    else:
        homes = resolve_dsh_homes("", cfg=cfg, base=base)
    if args.ledger:
        candidates.append(expand(args.ledger))
    candidates += [os.path.join(h, "storages", "cost-meter", "ledger.json") for h in homes]
    if not candidates:
        candidates = [os.path.join(expand("~/.dsh"), "storages", "cost-meter", "ledger.json")]

    merged, found, seen = {}, [], set()
    for p in candidates:
        if not os.path.isfile(p):
            continue
        real = os.path.realpath(p)
        if real in seen:
            print(f"[skip] 同一份账本已计过: {real}", file=sys.stderr)
            continue
        days = read_ledger(p)
        if not days:
            continue
        seen.add(real)
        found.append(p)
        for date, d in days.items():
            tgt = merged.setdefault(date, {k: 0 for k in ("input", "cacheRead", "cacheWrite", "output", "calls")})
            tgt.setdefault("cost", 0.0)
            for k in ("input", "cacheRead", "cacheWrite", "output", "calls"):
                tgt[k] += d[k]
            tgt["cost"] += d["cost"]
    return merged, found


def collect_jsonl(path):
    """逐行读，容忍脏行；返回 (rows, skipped)。"""
    rows, skipped = [], 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line[0] not in "{[":
                skipped += 1
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            u = find_usage(obj)
            if not u:
                skipped += 1
                continue
            row = {}
            for k, names in FIELD_ALIASES.items():
                v = pick(u, names)
                row[k] = v
            if row["input"] is None and row["cacheRead"] is None:
                skipped += 1
                continue
            row["input"] = row["input"] or 0
            row["cacheRead"] = row["cacheRead"] or 0
            row["cacheWrite"] = row["cacheWrite"] or 0
            row["output"] = row["output"] or 0
            rows.append(row)
    return rows, skipped


def pct(x):
    return f"{x * 100:.2f}%"


def diagnose(t, k, window):
    """返回诊断建议列表。全部基于阈值，不猜。"""
    tips = []
    total_ctx = t["input"] + t["cacheRead"]
    if t["calls"] == 0:
        return ["没有可统计的调用：数据源选错了，或账本刚建还没记账。"]
    miss_per_call = t["input"] / t["calls"]
    hit = t["cacheRead"] / total_ctx if total_ctx else 0.0
    per_call = total_ctx / t["calls"]

    if t["cacheRead"] == 0 and t["input"] > 0:
        tips.append("cacheRead 为 0：这个 provider/端点可能不支持前缀缓存，或走的是不返回缓存字段的代理。先确认第 0 步再动手。")
    if k < 3:
        tips.append(f"价差 k={k:.1f}：即使把未命中打到 0，省的也不到 {min(90, int(100 / k))}% 的上下文成本 → 优先级最低。")
    if miss_per_call > 3000:
        tips.append(f"未命中 {miss_per_call:.0f}/步，偏高：怀疑有自动裁剪或压缩在改写历史（见 docs/benchmark.md 第十节）。")
    elif miss_per_call > 1500:
        tips.append(f"未命中 {miss_per_call:.0f}/步：先查最近有没有改配置/换模型/切通道，再看 3 天趋势。")
    if hit < 0.95 and total_ctx:
        tips.append(f"命中率 {pct(hit)}：低于 95%，常见原因是频繁换会话/换模型/空闲超 TTL。")
    if window and per_call > window * 0.6:
        tips.append(f"每步上下文 {per_call / 1000:.0f} k ≈ 窗口 {window / 1000:.0f} k 的 {per_call / window * 100:.0f}%：压缩会开始触发，"
                    "先缩静态前缀（工具清单/技能目录），别先调低压缩水位。")
    if not tips:
        tips.append("各项指标在正常区间；继续只看 3 天趋势，别为 0.3 个百分点改配置。")
    return tips


def main():
    _safe_stdout()
    ap = argparse.ArgumentParser(description="缓存命中体检（只读）")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--home", help="DSH 数据目录，覆盖 config")
    ap.add_argument("--ledger", help="直接指定 ledger.json")
    ap.add_argument("--jsonl", help="任意会话日志（每行一个 JSON）")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--price-in", type=float, default=None, help="未命中输入价（USD/M tok）")
    ap.add_argument("--price-cache", type=float, default=None, help="命中价（USD/M tok）")
    ap.add_argument("--window", type=int, default=None, help="上下文窗口 token 数")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--template", action="store_true", help="打印手工采集模板")
    a = ap.parse_args()

    if a.template:
        print(json.dumps({"usage": {"input_tokens": 0, "cache_read_input_tokens": 0,
                                    "output_tokens": 0}}, ensure_ascii=False))
        print("# 每次响应存一行，两个 token 数照抄；攒 10 行后 --jsonl 跑一次。")
        return

    cfg, _base = load_config(a.config)
    prices = cfg.get("prices_usd_per_mtok") or {}
    p_in = a.price_in if a.price_in is not None else float(prices.get("input") or DEFAULT_IN)
    p_cache = a.price_cache if a.price_cache is not None else float(prices.get("cached") or DEFAULT_CACHE)
    k = (p_in / p_cache) if p_cache else 0.0

    out = {"source": None, "days": [], "totals": {}, "diagnosis": [], "k": round(k, 2)}

    if a.jsonl:
        rows, skipped = collect_jsonl(a.jsonl)
        if not rows:
            sys.exit(f"没有解析出 usage：{a.jsonl}（跳过 {skipped} 行）。先用 --template 看格式。")
        t = {kk: sum(r[kk] or 0 for r in rows) for kk in ("input", "cacheRead", "cacheWrite", "output")}
        t["calls"] = len(rows)
        t["cost"] = 0.0
        out["source"] = a.jsonl
        # 前缀重建事件，两种判据：
        #   a) 该步几乎全量重算（input/total > 0.5）——压缩、裁剪、换会话后首步都长这样
        #   b) 总上下文单步突然变大 >1.5 倍——插入/恢复历史
        events, prev = [], None
        for i, r in enumerate(rows):
            total = (r["input"] or 0) + (r["cacheRead"] or 0)
            if total <= 0:
                continue
            ratio = (r["input"] or 0) / total
            kind = None
            if ratio > 0.5 and total > 5000:
                kind = "全量重算"
            elif prev and total > prev * 1.5 and total > 5000:
                kind = "前缀突变"
            if kind and (not events or events[-1]["step"] != i):
                events.append({"step": i, "kind": kind, "from": prev or 0, "to": total,
                               "miss_ratio": round(ratio, 3)})
            prev = total
        out["rewrite_events"] = events[:10]
        out["skipped_lines"] = skipped
    else:
        merged, found = collect_ledgers(a)
        if not merged:
            print("没有可读的账本。可选：", file=sys.stderr)
            print("  --ledger <path>   --home <dsh_home>   --jsonl <session.jsonl>", file=sys.stderr)
            print("  或改 config.yaml 里的 dsh_home / dsh_homes", file=sys.stderr)
            sys.exit(2)
        keys = sorted(merged)[-a.days:]
        rows = [dict(date=d, **{kk: merged[d][kk] for kk in ("input", "cacheRead", "cacheWrite", "output", "calls", "cost")})
                for d in keys]
        t = {kk: sum(r[kk] for r in rows) for kk in ("input", "cacheRead", "cacheWrite", "output", "calls")}
        t["cost"] = sum(r["cost"] for r in rows)
        out["source"] = found
        out["ledgers"] = found

    total_ctx = t["input"] + t["cacheRead"]
    miss_cost = t["input"] / 1e6 * p_in
    hit_cost = t["cacheRead"] / 1e6 * p_cache
    share = miss_cost / (miss_cost + hit_cost) if (miss_cost + hit_cost) else 0.0
    out["totals"] = {
        "calls": t["calls"],
        "context_tokens": total_ctx,
        "cache_read_share": round(t["cacheRead"] / total_ctx, 4) if total_ctx else 0.0,
        "miss_per_call": int(t["input"] / t["calls"]) if t["calls"] else 0,
        "context_per_call": int(total_ctx / t["calls"]) if t["calls"] else 0,
        "output_per_call": int(t["output"] / t["calls"]) if t["calls"] else 0,
        "miss_cost_share": round(share, 4),
        "prices": {"in": p_in, "cache": p_cache, "k": round(k, 2)},
    }
    out["days"] = rows[-14:]
    out["diagnosis"] = diagnose(t, k, a.window or cfg.get("context_window"))

    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    src = out["source"]
    print(f"数据源: {src if isinstance(src, str) else ' + '.join(os.path.basename(p) for p in src)}")
    print(f"样本: {t['calls']} 次调用 | 上下文 {total_ctx / 1e6:.2f} M tok")
    print(f"命中率 {pct(out['totals']['cache_read_share'])} | 未命中 {out['totals']['miss_per_call']}/步 | "
          f"每步上下文 {out['totals']['context_per_call'] / 1000:.0f} k | 价差 k={k:.1f}")
    print(f"未命中成本占比 {pct(share)}（按 P_in={p_in} / P_cache={p_cache} USD/M tok）")
    if "rewrite_events" in out:
        print(f"\n前缀重建事件 {len(out['rewrite_events'])} 次（全量重算 = 该步 input 占上下文 >50%；前缀突变 = 总上下文单步 >1.5 倍）")
        for e in out["rewrite_events"]:
            print(f"  step {e['step']:>5}  {e['kind']:<6} {e['from'] / 1000:>7.1f}k → {e['to'] / 1000:>7.1f}k  "
                  f"未命中占比 {e['miss_ratio'] * 100:.0f}%")
    print("\n诊断")
    for tip in out["diagnosis"]:
        print(f"  - {tip}")


if __name__ == "__main__":
    main()
