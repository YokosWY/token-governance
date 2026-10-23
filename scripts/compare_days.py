#!/usr/bin/env python3
"""按天对比成本口径：单价、上下文、缓存未命中、输出。

用法：
  python compare_days.py --config config.yaml --days 7
  python compare_days.py --ledger <path> --days 7
"""
import argparse, json, os, sys

from tg_common import add_common_args, ledger_path, load_config, resolve_dsh_home, utf8_stdio

utf8_stdio()


def load(cfg_path, home_arg=""):
    cfg, base = load_config(cfg_path)
    return ledger_path(resolve_dsh_home(home_arg, cfg=cfg, base=base))


def main():
    ap = argparse.ArgumentParser()
    add_common_args(ap, json_flag=False)
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--days", type=int, default=7)
    a = ap.parse_args()

    path = a.ledger or load(a.config, a.dsh_home)
    if not os.path.isfile(path):
        sys.exit(f"找不到账本: {path}")
    led = json.load(open(path, encoding="utf-8"))
    days = led.get("days") or {}
    keys = sorted(days)[-a.days:]

    rows = []
    for d in keys:
        v = days[d]
        c = v.get("calls") or 0
        if not c:
            continue
        rows.append((d, c, v.get("costCny", 0) / c,
                     (v.get("input", 0) + v.get("cacheRead", 0)) / c,
                     v.get("input", 0) / c,
                     v.get("output", 0) / c))

    if len(rows) >= 2:
        import statistics
        prev = rows[:-1]
        last = rows[-1]
        print()
        print(f"以之前 {len(prev)} 天的中位数为基线，对比 {last[0]}：")
        for i, name in enumerate(("单次成本", "平均上下文", "未命中 input", "输出 token")):
            base = statistics.median([r[2 + i] for r in prev])
            now = last[2 + i]
            if base:
                fmt = ">10.4f" if i == 0 else ">10.0f"
                print(f"  {name:<12} {base:{fmt}} → {now:{fmt}}   {(now/base-1)*100:+.1f}%")
        print()
        print("注意：改配置当天缓存会失效、成本偏高；用了高价模型的日子也会拉高基线。")


if __name__ == "__main__":
    main()
