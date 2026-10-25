#!/usr/bin/env python3
"""ledger.py —— TG 账本 v2：从 DSH 会话日志重建「日 / 模型 / 会话」三级成本账。

为什么重写：旧账本由 dsh-cost-meter 插件写入（`<dsh_home>/storages/cost-meter/ledger.json`），
2026-10-24 插件卸载后没有写方。新账本改为**从会话日志派生**（`sessions/**/session.v4.jsonl.zstd`
里的 `assistant/message.data.usage`），因此：
  - 没有插件也能记，换机器/换 home 只要日志在就能重算；
  - 口径可复算：token → 价格表 → 成本，价格改了一次 `--rebuild` 就全量重算；
  - 幂等：同一批日志反复 ingest 结果完全一致（按文件 size+mtime 缓存解析结果）。

口径（必须写清，否则数字会打架）：
  inputTokens      → cacheMiss 价（新输入）
  cacheWriteTokens → cacheMiss 价（写入缓存，按未命中计，偏保守）
  cacheReadTokens  → cacheHit 价
  outputTokens     → output 价
  价格单位 USD / 百万 token；成本按 `ledger.prices.fx.usdToCny` 折人民币。
  峰谷：`ledger.prices.peak.windows` 是**本地时区**的小时区间（半开），命中且非节假日走 peak 价。

用法：
  python scripts/ledger.py ingest                 # 增量重建（默认写 config 的 ledger.path）
  python scripts/ledger.py ingest --rebuild       # 忽略缓存，全量重扫
  python scripts/ledger.py report --days 7        # 按天汇总
  python scripts/ledger.py report --days 7 --by model
  python scripts/ledger.py day 2026-10-24         # 单日明细（模型 + 会话）
  python scripts/ledger.py verify                 # 校验账本内部自洽（CI 用）
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

from tg_common import (DEFAULT_CONFIG, add_common_args, decompress_bytes, emit_json,
                       expand, load_config, resolve_dsh_homes, sessions_root, utf8_stdio)

LEDGER_SCHEMA = "tg-ledger/2"
CACHE_VERSION = 2
SESSION_GLOB_SUFFIXES = (".zstd", ".jsonl")


# ---------------------------------------------------------------- 时间与配置

def local_zone(cfg):
    """账本的「日」按本地时区切；zoneinfo 缺失时退回系统时区（只在 Windows 老 Python 上发生）。"""
    name = ((cfg.get("ledger") or {}).get("timezone") or "").strip()
    if name:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name), name
        except Exception:
            pass
        # Windows 上 Python 常没带 tzdata：无夏令时的时区用固定偏移兜底，别让整条流水线停在这
        fixed = {"Asia/Shanghai": 8, "Asia/Chongqing": 8, "Asia/Hong_Kong": 8,
                 "Asia/Taipei": 8, "UTC": 0}.get(name)
        if fixed is not None:
            from datetime import timedelta
            return timezone(timedelta(hours=fixed)), f"{name}(fixed)"
        print(f"[warn] 时区 {name} 不可用，退回系统本地时区", file=sys.stderr)
    return datetime.now().astimezone().tzinfo, "system"


def ledger_file(cfg, base):
    """账本落盘路径：config 的 ledger.path（相对 config 目录），默认 data/ledger.json。"""
    raw = ((cfg.get("ledger") or {}).get("path") or "").strip() or "data/ledger.json"
    return expand(raw, base)


def cache_file(ledger_path):
    return ledger_path + ".cache.json"


# ---------------------------------------------------------------- 价格表

def price_table(cfg):
    """把 config 的 ledger.prices 归一成一个查表函数；缺项时退回旧的 prices_usd_per_mtok。

    返回 (prices, fx, peak_cfg)，prices = {模型名: {cacheMiss, cacheHit, output, peak:{...}}}。
    """
    led = cfg.get("ledger") or {}
    pr = led.get("prices") or {}
    legacy = cfg.get("prices_usd_per_mtok") or {}
    base = {
        "cacheMiss": float(legacy.get("input", 0.15)),
        "cacheHit": float(legacy.get("cached", 0.003)),
        "output": float(legacy.get("output", 0.6)),
    }
    models = {}
    for name, spec in (pr.get("models") or {}).items():
        if not isinstance(spec, dict):
            continue
        row = dict(base)
        for k in ("cacheMiss", "cacheHit", "output"):
            if spec.get(k) is not None:
                row[k] = float(spec[k])
        peak = {k: row[k] for k in ("cacheMiss", "cacheHit", "output")}
        if isinstance(spec.get("peak"), dict):
            for k in ("cacheMiss", "cacheHit", "output"):
                if spec["peak"].get(k) is not None:
                    peak[k] = float(spec["peak"][k])
        else:
            # 没写 peak 就按 2 倍估（旧插件的口径），并在报告里标注
            peak = {k: row[k] * 2 for k in row}
        row["peak"] = peak
        models[name] = row
    if isinstance(pr.get("default"), dict):
        for k in ("cacheMiss", "cacheHit", "output"):
            if pr["default"].get(k) is not None:
                base[k] = float(pr["default"][k])
    base_peak = {k: base[k] * 2 for k in base}
    if isinstance(pr.get("default", {}).get("peak"), dict):
        for k in ("cacheMiss", "cacheHit", "output"):
            if pr["default"]["peak"].get(k) is not None:
                base_peak[k] = float(pr["default"]["peak"][k])
    base["peak"] = base_peak

    fx = led.get("prices", {}).get("fx") or {}
    rate = float(fx.get("usdToCny") or cfg.get("rate") or 7.1)
    peak_cfg = pr.get("peak") or {}
    windows = []
    for w in (peak_cfg.get("windows") or []):
        if isinstance(w, (list, tuple)) and len(w) == 2:
            windows.append((int(w[0]), int(w[1])))
    return {"models": models, "default": base}, rate, {
        "enabled": bool(peak_cfg.get("enabled", bool(windows))),
        "windows": windows,
        "holidays": set(peak_cfg.get("holidays") or []),
    }


def price_for(prices, model):
    """模型名匹配：先全名，再去掉 `provider:` 前缀，再按后缀包含，最后 default。

    返回 (价格行, 命中方式)。命中方式是为了暴露「静默按 default 折算」：别人换模型
    （Claude / GPT / 自建）时名字对不上价表就落到 default，算出来的钱看着正常，
    其实套的是 DeepSeek 的价——这是最容易被忽略的错数来源，所以必须能报出来。
    """
    if model in prices["models"]:
        return prices["models"][model], "exact"
    short = model.split(":", 1)[-1]
    if short in prices["models"]:
        return prices["models"][short], "short"
    for name, row in prices["models"].items():
        if name and (short.startswith(name) or name in short):
            return row, "contains"
    return prices["default"], "default"


def is_peak(peak_cfg, day, hour):
    if not peak_cfg["enabled"] or day in peak_cfg["holidays"]:
        return False
    return any(a <= hour < b for a, b in peak_cfg["windows"])


# ---------------------------------------------------------------- 日志解析

def iter_session_files(homes):
    for home in homes:
        root = sessions_root(home)
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                if fn.startswith("session.v4.jsonl"):
                    yield home, os.path.join(dirpath, fn)


def parse_session_file(path, want_meta_only=False):
    """解析一份会话日志 → (meta, records)。

    records 里每条是紧凑列表 [ts_ms, provider, model, input, cacheRead, cacheWrite, output, turn, step]；
    用列表不用 dict 是为了让缓存文件小一个数量级（8000 条 ≈ 0.7 MB）。
    """
    raw = (open(path, "rb").read() if path.endswith(".jsonl") else decompress_bytes(path))
    meta = {"id": "", "cwd": "", "parent": "", "origin": "", "preset": "", "title": "",
            "createdAt": 0, "lines": 0, "usageEvents": 0, "damaged": 0}
    records = []
    for line in raw.decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        meta["lines"] += 1
        try:
            ev = json.loads(line)
        except Exception:
            meta["damaged"] += 1
            continue
        etype = ev.get("type")
        if etype == "session":
            meta["id"] = ev.get("id") or meta["id"]
            meta["cwd"] = ev.get("cwd") or meta["cwd"]
            meta["parent"] = ev.get("parentSession") or meta["parent"]
            meta["origin"] = ev.get("origin") or meta["origin"]
            meta["preset"] = ev.get("agentPreset") or meta["preset"]
            meta["createdAt"] = ev.get("createdAt") or meta["createdAt"]
            continue
        if etype == "session/title":
            title = (ev.get("data") or {}).get("title")
            if title:
                meta["title"] = str(title)
            continue
        if '"usage"' not in line:
            continue
        data = ev.get("data")
        if not isinstance(data, dict):
            continue
        usage = data.get("usage")
        if not isinstance(usage, dict):
            continue
        src = ((data.get("message") or {}).get("source") or {}) if isinstance(data.get("message"), dict) else {}
        provider = src.get("provider") or "unknown"
        model = src.get("model") or "unknown"
        meta["usageEvents"] += 1
        records.append([
            int(ev.get("time") or 0), str(provider), str(model),
            int(usage.get("inputTokens") or 0), int(usage.get("cacheReadTokens") or 0),
            int(usage.get("cacheWriteTokens") or 0), int(usage.get("outputTokens") or 0),
            int(data.get("turn") or 0), int(data.get("step") or 0),
        ])
    if not meta["id"]:
        meta["id"] = os.path.basename(os.path.dirname(path))
    return meta, records


def load_cache(path):
    if not os.path.isfile(path):
        return {"v": CACHE_VERSION, "files": {}}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("v") != CACHE_VERSION or not isinstance(data.get("files"), dict):
            return {"v": CACHE_VERSION, "files": {}}
        return data
    except Exception:
        return {"v": CACHE_VERSION, "files": {}}


def save_json_atomic(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


# ---------------------------------------------------------------- 聚合

def build_ledger(homes, cfg, base, rebuild=False, quiet=False):
    tz, tz_name = local_zone(cfg)
    lpath = ledger_file(cfg, base)
    cpath = cache_file(lpath)
    cache = {"v": CACHE_VERSION, "files": {}} if rebuild else load_cache(cpath)
    files = cache["files"]
    prices, fx_rate, peak_cfg = price_table(cfg)

    seen_paths = set()
    stats = {"files": 0, "cached": 0, "events": 0, "damaged": 0, "startedAt": time.time()}
    days = {}
    # 价表没覆盖到的模型：名字对不上就按 default 折算，必须记账并报出来
    unmatched = {}
    matched_how = {}

    for home, path in iter_session_files(homes):
        real = os.path.realpath(path)
        if real in seen_paths:
            continue
        seen_paths.add(real)
        try:
            st = os.stat(real)
        except OSError:
            continue
        hit = files.get(real)
        if hit and not rebuild and hit.get("size") == st.st_size and abs(hit.get("mtime", 0) - st.st_mtime) < 1e-6:
            meta, records = hit["meta"], hit["records"]
            stats["cached"] += 1
        else:
            try:
                meta, records = parse_session_file(real)
            except SystemExit:
                raise
            except Exception as e:
                print(f"[warn] 解析失败，跳过：{real}（{e}）", file=sys.stderr)
                continue
            files[real] = {"size": st.st_size, "mtime": st.st_mtime, "meta": meta, "records": records}
        stats["files"] += 1
        stats["events"] += len(records)
        stats["damaged"] += meta.get("damaged", 0)

        for ts, provider, model, i, cr, cw, o, turn, step in records:
            if not ts:
                continue
            dt = datetime.fromtimestamp(ts / 1000.0, tz)
            day = dt.strftime("%Y-%m-%d")
            slot = days.setdefault(day, {
                "date": day, "calls": 0, "input": 0, "cacheRead": 0, "cacheWrite": 0,
                "output": 0, "costUsd": 0.0, "costCny": 0.0, "peakCostCny": 0.0,
                "byProviderModel": {}, "sessions": {},
            })
            key = f"{provider}:{model}"
            row, how = price_for(prices, model)
            matched_how[how] = matched_how.get(how, 0) + 1
            if how == "default":
                u = unmatched.setdefault(key, {"calls": 0, "provider": provider, "model": model})
                u["calls"] += 1
            peak = is_peak(peak_cfg, day, dt.hour)
            rate = row["peak"] if peak else row
            usd = (i * rate["cacheMiss"] + cw * rate["cacheMiss"]
                   + cr * rate["cacheHit"] + o * rate["output"]) / 1_000_000.0
            cny = usd * fx_rate
            slot["calls"] += 1
            slot["input"] += i
            slot["cacheRead"] += cr
            slot["cacheWrite"] += cw
            slot["output"] += o
            slot["costUsd"] += usd
            slot["costCny"] += cny
            if peak:
                slot["peakCostCny"] += cny
            pm = slot["byProviderModel"].setdefault(key, {
                "calls": 0, "input": 0, "cacheRead": 0, "cacheWrite": 0, "output": 0,
                "costUsd": 0.0, "costCny": 0.0})
            pm["calls"] += 1
            pm["input"] += i
            pm["cacheRead"] += cr
            pm["cacheWrite"] += cw
            pm["output"] += o
            pm["costUsd"] += usd
            pm["costCny"] += cny
            sid = meta.get("id") or "unknown"
            ss = slot["sessions"].setdefault(sid, {
                "origin": meta.get("origin") or "main", "parent": meta.get("parent") or "",
                "cwd": meta.get("cwd") or "", "title": meta.get("title") or "",
                "calls": 0, "costCny": 0.0, "firstAt": ts, "lastAt": ts})
            ss["calls"] += 1
            ss["costCny"] += cny
            ss["firstAt"] = min(ss["firstAt"], ts)
            ss["lastAt"] = max(ss["lastAt"], ts)
            if meta.get("title"):
                ss["title"] = meta["title"]

    # 只保留还有日志的文件的缓存条目，避免账本越用越大
    files = {k: v for k, v in files.items() if k in seen_paths}

    total = {"calls": 0, "input": 0, "cacheRead": 0, "cacheWrite": 0, "output": 0,
             "costUsd": 0.0, "costCny": 0.0, "peakCostCny": 0.0}
    for day in days.values():
        for k in ("calls", "input", "cacheRead", "cacheWrite", "output"):
            day[k] = int(day[k])
            total[k] += day[k]
        for k in ("costUsd", "costCny", "peakCostCny"):
            total[k] += day[k]
        day["tokens"] = {"total": day["input"] + day["cacheRead"] + day["cacheWrite"] + day["output"]}
        day["costCny"] = round(day["costCny"], 6)
        day["costUsd"] = round(day["costUsd"], 6)
        day["peakCostCny"] = round(day["peakCostCny"], 6)
        for pm in day["byProviderModel"].values():
            for k in ("costUsd", "costCny"):
                pm[k] = round(pm[k], 6)
        for ss in day["sessions"].values():
            ss["costCny"] = round(ss["costCny"], 6)

    ordered = {k: days[k] for k in sorted(days)}
    ordered_keys = list(ordered)
    sources = []
    for home in homes:
        n = sum(1 for p in files if os.path.realpath(p).startswith(os.path.realpath(sessions_root(home))))
        sources.append({"home": home, "files": n, "exists": os.path.isdir(sessions_root(home))})

    ledger = {
        "schema": LEDGER_SCHEMA,
        "generatedAt": datetime.now(tz).isoformat(timespec="seconds"),
        "generator": "token-governance scripts/ledger.py",
        "timezone": tz_name,
        "currency": "CNY",
        "pricing": {
            "currency": "USD",
            "fx": {"usdToCny": fx_rate},
            "peak": {"enabled": peak_cfg["enabled"], "windows": peak_cfg["windows"],
                     "holidays": sorted(peak_cfg["holidays"])},
            "models": sorted(prices["models"]),
            "source": "config.yaml ledger.prices（缺项退回 prices_usd_per_mtok；peak 缺省按 2 倍）",
            # 命中方式分布 + 兜底清单：下游据此判断「这份账本是不是含估算」
            "matched": dict(sorted(matched_how.items())),
            "unmatched": [unmatched[k] for k in sorted(unmatched)],
            "estimated": bool(unmatched),
        },
        "sources": sources,
        "days": ordered,
        "totals": {
            "days": len(ordered),
            "range": [ordered_keys[0] if ordered_keys else "", ordered_keys[-1] if ordered_keys else ""],
            "calls": total["calls"],
            "tokens": {"input": total["input"], "cacheRead": total["cacheRead"],
                       "cacheWrite": total["cacheWrite"], "output": total["output"],
                       "total": total["input"] + total["cacheRead"] + total["cacheWrite"] + total["output"]},
            "costCny": round(total["costCny"], 4),
            "costUsd": round(total["costUsd"], 4),
            "peakCostCny": round(total["peakCostCny"], 4),
        },
    }
    save_json_atomic(lpath, ledger)
    save_json_atomic(cpath, {"v": CACHE_VERSION, "files": files})
    if not quiet:
        print(f"账本 → {lpath}")
        print(f"  文件 {stats['files']} 份（缓存命中 {stats['cached']}）· usage 事件 {stats['events']} · "
              f"损坏行 {stats['damaged']}")
        print(f"  {ledger['totals']['range'][0]} → {ledger['totals']['range'][1]}："
              f"{len(ordered)} 天 · {total['calls']} 次调用 · ¥{ledger['totals']['costCny']:.2f}")
        if unmatched:
            print(f"  ! 计价兜底：{len(unmatched)} 个模型不在价表里，按 default 折算 ——"
                  f" 钱数只能当量级看。补 config.yaml 的 ledger.prices.models 后重跑 --rebuild")
            for k in sorted(unmatched):
                print(f"      {k}（{unmatched[k]['calls']} 次）")
        print(f"  耗时 {time.time() - stats['startedAt']:.1f}s")
    return ledger


# ---------------------------------------------------------------- 读取与展示

def load_ledger(path):
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def fmt_int(n):
    return f"{n:,}"


def pct(part, whole):
    return f"{part / whole * 100:.1f}%" if whole else "-"


def cmd_report(ledger, args):
    days = ledger.get("days") or {}
    keys = sorted(days)[-args.days:]
    if not keys:
        sys.exit("账本里没有数据：先跑 python scripts/ledger.py ingest")
    by = args.by
    if by == "model":
        agg = {}
        for k in keys:
            for name, v in (days[k].get("byProviderModel") or {}).items():
                slot = agg.setdefault(name, {"calls": 0, "tokens": 0, "costCny": 0.0})
                slot["calls"] += v["calls"]
                slot["tokens"] += v["input"] + v["cacheRead"] + v["cacheWrite"] + v["output"]
                slot["costCny"] += v["costCny"]
        rows = [{"key": n, **v} for n, v in sorted(agg.items(), key=lambda x: -x[1]["costCny"])]
    elif by == "session":
        agg = {}
        for k in keys:
            for sid, v in (days[k].get("sessions") or {}).items():
                slot = agg.setdefault(sid, {"calls": 0, "costCny": 0.0, "title": v.get("title") or "",
                                            "origin": v.get("origin") or "", "last": 0})
                slot["calls"] += v["calls"]
                slot["costCny"] += v["costCny"]
                slot["last"] = max(slot["last"], v["lastAt"])
        rows = [{"key": s, **v} for s, v in sorted(agg.items(), key=lambda x: -x[1]["costCny"])]
    else:
        rows = []
        for k in keys:
            d = days[k]
            tok = d["input"] + d["cacheRead"] + d["cacheWrite"] + d["output"]
            rows.append({"key": k, "calls": d["calls"], "tokens": tok, "costCny": d["costCny"],
                         "cacheHit": d["cacheRead"] / (d["input"] + d["cacheRead"]) if (d["input"] + d["cacheRead"]) else None})

    if args.json:
        emit_json({"by": by, "days": args.days, "rows": rows})
        return

    print(f"TG 账本 · 最近 {len(keys)} 天（{keys[0]} → {keys[-1]}）· 按{ {'day': '天', 'model': '模型', 'session': '会话'}[by] }\n")
    if by == "day":
        print(f"{'日期':<12}{'¥':>9}{'调用':>7}{'tokens':>16}{'缓存命中率':>11}")
        for r in rows:
            print(f"{r['key']:<12}{r['costCny']:>9.2f}{r['calls']:>7}{fmt_int(r['tokens']):>16}"
                  f"{(pct(r['cacheHit'], 1) if r['cacheHit'] is not None else '-'):>11}")
        tot = sum(r['costCny'] for r in rows)
        print(f"{'合计':<12}{tot:>9.2f}{sum(r['calls'] for r in rows):>7}"
              f"{fmt_int(sum(r['tokens'] for r in rows)):>16}")
    elif by == "model":
        print(f"{'provider:model':<44}{'¥':>9}{'调用':>8}{'tokens':>15}")
        for r in rows:
            print(f"{r['key']:<44}{r['costCny']:>9.2f}{r['calls']:>8}{fmt_int(r['tokens']):>15}")
    else:
        print(f"{'会话':<40}{'¥':>8}{'调用':>7}  标题")
        for r in rows[:args.top]:
            title = (r['title'] or '')[:36]
            mark = "↳" if r['origin'] == "subagent" else " "
            print(f"{r['key']:<40}{r['costCny']:>8.2f}{r['calls']:>7} {mark}{title}")
        if len(rows) > args.top:
            print(f"… 其余 {len(rows) - args.top} 个会话合计 ¥{sum(r['costCny'] for r in rows[args.top:]):.2f}")


def cmd_day(ledger, args):
    d = (ledger.get("days") or {}).get(args.date)
    if not d:
        sys.exit(f"账本里没有 {args.date}；可用日期：{', '.join(sorted(ledger.get('days') or {}))}")
    if args.json:
        emit_json(d)
        return
    tok = d["input"] + d["cacheRead"] + d["cacheWrite"] + d["output"]
    print(f"{args.date} · ¥{d['costCny']:.2f} · {d['calls']} 次调用 · {fmt_int(tok)} tokens"
          f" · 峰时占 ¥{d['peakCostCny']:.2f}\n")
    print(f"{'provider:model':<44}{'¥':>8}{'调用':>7}{'tokens':>13}")
    for name, v in sorted(d["byProviderModel"].items(), key=lambda x: -x[1]["costCny"]):
        print(f"{name:<44}{v['costCny']:>8.2f}{v['calls']:>7}"
              f"{fmt_int(v['input'] + v['cacheRead'] + v['cacheWrite'] + v['output']):>13}")
    print(f"\n{'会话':<40}{'¥':>8}{'调用':>7}")
    for sid, v in sorted(d["sessions"].items(), key=lambda x: -x[1]["costCny"])[:15]:
        print(f"{sid:<40}{v['costCny']:>8.2f}{v['calls']:>7}")


def cmd_verify(ledger, args):
    problems = []
    tot = ledger.get("totals") or {}
    days = ledger.get("days") or {}
    s_calls = sum(d["calls"] for d in days.values())
    s_tokens = sum(d["input"] + d["cacheRead"] + d["cacheWrite"] + d["output"] for d in days.values())
    s_cny = sum(d["costCny"] for d in days.values())
    if s_calls != tot.get("calls"):
        problems.append(f"totals.calls={tot.get('calls')} ≠ 逐日合计 {s_calls}")
    if s_tokens != (tot.get("tokens") or {}).get("total"):
        problems.append(f"totals.tokens.total={tot.get('tokens', {}).get('total')} ≠ 逐日合计 {s_tokens}")
    if abs(s_cny - float(tot.get("costCny") or 0)) > 0.01:
        problems.append(f"totals.costCny={tot.get('costCny')} ≠ 逐日合计 {s_cny:.4f}")
    for k, d in days.items():
        if d["input"] < 0 or d["cacheRead"] < 0 or d["output"] < 0:
            problems.append(f"{k}: 出现负 token")
        if abs(sum(v["costCny"] for v in d["byProviderModel"].values()) - d["costCny"]) > 0.01:
            problems.append(f"{k}: 模型合计 ≠ 当日成本")
        if abs(sum(v["costCny"] for v in d["sessions"].values()) - d["costCny"]) > 0.02:
            problems.append(f"{k}: 会话合计 ≠ 当日成本")
        if d["peakCostCny"] > d["costCny"] + 0.001:
            problems.append(f"{k}: 峰时成本超过总成本")
    out = {"ok": not problems, "days": len(days), "problems": problems,
           "totals": tot, "schema": ledger.get("schema"), "generatedAt": ledger.get("generatedAt")}
    if args.json:
        emit_json(out)
    else:
        print(("账本自洽 ✅  " if not problems else "账本不自洽 ❌  ") +
              f"{len(days)} 天 · {tot.get('calls')} 次调用 · ¥{tot.get('costCny')}")
        for p in problems:
            print("  - " + p)
    return 0 if not problems else 1


# ---------------------------------------------------------------- CLI

def main():
    utf8_stdio()
    ap = argparse.ArgumentParser(description="TG 账本 v2（从会话日志派生日/模型/会话成本）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest", help="重扫会话日志并写账本")
    add_common_args(ing, json_flag=True)
    ing.add_argument("--rebuild", action="store_true", help="忽略缓存全量重扫")
    ing.add_argument("--quiet", action="store_true", help="不打印过程")

    rep = sub.add_parser("report", help="按天/模型/会话汇总")
    add_common_args(rep, json_flag=True)
    rep.add_argument("--days", type=int, default=7)
    rep.add_argument("--by", choices=["day", "model", "session"], default="day")
    rep.add_argument("--top", type=int, default=15)

    one = sub.add_parser("day", help="单日明细")
    add_common_args(one, json_flag=True)
    one.add_argument("date", help="YYYY-MM-DD")

    ver = sub.add_parser("verify", help="校验账本自洽")
    add_common_args(ver, json_flag=True)

    a = ap.parse_args()
    cfg, base = load_config(a.config, strict=(a.cmd == "ingest"))
    homes = resolve_dsh_homes(a.dsh_home, cfg=cfg, base=base)
    lpath = ledger_file(cfg, base)

    if a.cmd == "ingest":
        led = build_ledger(homes, cfg, base, rebuild=a.rebuild, quiet=a.quiet)
        if a.json:
            emit_json({"path": lpath, "totals": led["totals"], "sources": led["sources"]})
        return 0

    led = load_ledger(lpath)
    if led is None:
        sys.exit(f"没有账本：{lpath}\n先跑 python scripts/ledger.py ingest")
    if led.get("schema") != LEDGER_SCHEMA:
        print(f"[warn] 账本 schema={led.get('schema')!r}，期望 {LEDGER_SCHEMA}；建议 --rebuild 重生成",
              file=sys.stderr)
    if a.cmd == "report":
        cmd_report(led, a)
        return 0
    if a.cmd == "day":
        cmd_day(led, a)
        return 0
    return cmd_verify(led, a)


if __name__ == "__main__":
    sys.exit(main())
