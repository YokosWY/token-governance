#!/usr/bin/env python3
"""Token-Governance 体检：只读 ledger，输出 N 天的花费与结构。

用法:
  python cost_summary.py --config config.yaml [--days 7] [--json]
配置里至少要有 dsh_home（或 dsh_homes 列表）。
"""
import argparse, json, os, sys, datetime

from tg_common import (add_common_args, emit_json, expand, ledger_paths, load_config,
                       resolve_dsh_homes, utf8_stdio)

# Windows 控制台默认 GBK：固定 UTF-8，避免中文报告/JSON 在无 PYTHONIOENCODING 的环境里乱码或崩。
utf8_stdio()


def read_ledger(path):
    if not os.path.isfile(path):
        return None, path
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), path
    except (json.JSONDecodeError, UnicodeDecodeError):
        print(f"[skip] ledger 读不出来（可能正在写，稍后重跑）: {path}", file=sys.stderr)
        return None, path

def summarize(days, n):
    keys = sorted(days)[-n:]
    rows, tok = [], {"input": 0, "cacheRead": 0, "cacheWrite": 0, "output": 0, "calls": 0, "cny": 0.0}
    models = {}
    for k in keys:
        d = days[k]
        cny = float(d.get("costCny") or 0.0)
        rows.append({
            "date": k,
            "cny": round(cny, 2),
            "calls": int(d.get("calls") or 0),
            "input": int(d.get("input") or 0),
            "cacheRead": int(d.get("cacheRead") or 0),
            "output": int(d.get("output") or 0),
        })
        for f in ("input", "cacheRead", "cacheWrite", "output", "calls"):
            tok[f] += int(d.get(f) or 0)
        tok["cny"] += cny
        for m, v in (d.get("byProviderModel") or {}).items():
            slot = models.setdefault(m, {"calls": 0, "tokens": 0})
            slot["calls"] += int(v.get("calls") or 0)
            slot["tokens"] += sum(int(v.get(f) or 0) for f in ("input", "cacheRead", "cacheWrite", "output"))
    return rows, tok, models

def main():
    ap = argparse.ArgumentParser()
    add_common_args(ap, json_flag=True)
    ap.add_argument("--days", type=int, default=7)
    a = ap.parse_args()

    # strict：YAML 写坏要当场报出来，不能静默退回默认 home 再给一份错账
    cfg, base = load_config(a.config, strict=True)
    homes = resolve_dsh_homes(a.dsh_home, cfg=cfg, base=base)
    merged, found, seen = {}, [], set()
    estimated = []          # 价表没覆盖到、被按 default 折算的模型（钱数只能当量级看）
    paths = [p for p in dict.fromkeys(ledger_paths(cfg, base, homes)) if os.path.isfile(p)]
    # 新账本（ledger.py 派生）与旧插件账本可能同时存在：只认新账本，避免同一笔钱算两遍
    new_schema = []
    for p in paths:
        led, _ = read_ledger(p)
        if led and str(led.get("schema", "")).startswith("tg-ledger"):
            new_schema.append(p)
    for p in (new_schema or paths):
        led, _ = read_ledger(p)
        if led is None:
            print(f"[skip] 没有 ledger: {p}", file=sys.stderr)
            continue
        real = os.path.realpath(p)
        if real in seen:
            print(f"[skip] 同一份 ledger 已计过（按真实路径去重）: {real}", file=sys.stderr)
            continue
        seen.add(real)
        found.append(p)
        for u in ((led.get("pricing") or {}).get("unmatched") or []):
            tag = f"{u.get('provider') or '?'}:{u.get('model') or '?'}（{u.get('calls') or 0} 次）"
            if tag not in estimated:
                estimated.append(tag)
        for k, v in (led.get("days") or {}).items():
            tgt = merged.setdefault(k, {"date": k, "input": 0, "cacheRead": 0, "cacheWrite": 0,
                                        "output": 0, "calls": 0, "costCny": 0.0, "byProviderModel": {}})
            for f in ("input", "cacheRead", "cacheWrite", "output", "calls"):
                tgt[f] += int(v.get(f) or 0)
            tgt["costCny"] += float(v.get("costCny") or 0.0)
            for m, mv in (v.get("byProviderModel") or {}).items():
                dst = tgt["byProviderModel"].setdefault(m, {"calls": 0, "input": 0, "cacheRead": 0, "cacheWrite": 0, "output": 0})
                for f in ("calls", "input", "cacheRead", "cacheWrite", "output"):
                    dst[f] += int(mv.get(f) or 0)
    if not merged:
        tried = "\n".join("  " + p for p in ledger_paths(cfg, base, homes))
        sys.exit("没有可读的 ledger，查过：\n" + tried +
                 "\n2026-10-24 起账本由会话日志派生：python scripts/ledger.py ingest"
                 "\n长期没有就改 config 里的 dsh_home。")

    rows, tok, models = summarize(merged, a.days)
    total_ctx = tok["input"] + tok["cacheRead"]
    out = {
        "ledgers": found,
        "days": rows,
        "totals": {
            "cny": round(tok["cny"], 2),
            "calls": tok["calls"],
            "context_tokens": total_ctx,
            "cache_read_share": round(tok["cacheRead"] / total_ctx * 100, 2) if total_ctx else 0,
            "avg_context_per_call": int(total_ctx / tok["calls"]) if tok["calls"] else 0,
            "cny_per_call": round(tok["cny"] / tok["calls"], 4) if tok["calls"] else 0,
        },
        "models": sorted(models.items(), key=lambda kv: -kv[1]["tokens"])[:10],
        "estimated": estimated,
    }
    if a.json:
        emit_json(out); return

    print(f"数据源: {len(found)} 个 ledger")
    print(f"窗口: 最近 {len(rows)} 天 | 合计 CNY {out['totals']['cny']} | {out['totals']['calls']} 次调用")
    print(f"上下文 {total_ctx/1e9:.3f} B | 缓存命中 {out['totals']['cache_read_share']}% | "
          f"均值 {out['totals']['avg_context_per_call']/1000:.0f} k/次 | CNY {out['totals']['cny_per_call']}/次")
    print("\n日期        CNY     调用     平均上下文")
    for r in rows:
        avg = (r["input"] + r["cacheRead"]) / r["calls"] if r["calls"] else 0
        print(f"{r['date']}  {r['cny']:7.2f}  {r['calls']:6d}  {avg/1000:9.0f} k")
    print("\n通道（按 token）")
    for m, v in out["models"][:5]:
        print(f"  {m:45s} {v['calls']:6d} 次  {v['tokens']/1e6:9.0f} M")
    if estimated:
        more = "…" if len(estimated) > 3 else ""
        print(f"\n! 含估算：{len(estimated)} 个模型不在价表里，按 default 价折算"
              f"（{'、'.join(estimated[:3])}{more}）")
        print("  这些次数算出来的钱只能当量级看；把真实单价填进 config.yaml 的 "
              "ledger.prices.models，再跑 ledger.py ingest --rebuild")

if __name__ == "__main__":
    main()
