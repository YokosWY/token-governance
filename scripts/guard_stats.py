#!/usr/bin/env python3
"""循环守卫命中统计：读 loop-guard 写出的 events.jsonl，出「提醒了多少次、谁在重复」。

为什么要有这张表：守卫只提醒不拦截，如果没人统计，装了也不知道有没有用。
events.jsonl 每条 = 一次真正发出的提醒，只有工具名、第几次、参数哈希与长度，没有参数内容。

用法：
  python scripts/guard_stats.py                    # 近 7 天，全部
  python scripts/guard_stats.py --days 3 --top 8
  python scripts/guard_stats.py --file <path>      # 指定 events.jsonl
  python scripts/guard_stats.py --json

日志默认在 <DSH_HOME 或 ~/.dsh>/storages/loop-guard/events.jsonl（守卫的 logFile 配置可改）。
"""
import argparse, datetime, json, os, sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULTS = {
    "file": "",
    "days": 7,
    "top": 10,
    "dsh_home": "",
    "json": False,
}


def default_path(dsh_home):
    home = os.path.expanduser(dsh_home or os.environ.get("DSH_HOME") or "~/.dsh")
    return os.path.join(home, "storages", "loop-guard", "events.jsonl")


def load(path):
    """返回 (事件列表, 坏行数)。坏行跳过，不让半截文件打断统计。"""
    events, bad = [], 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            if isinstance(o, dict) and o.get("tool"):
                events.append(o)
            else:
                bad += 1
    return events, bad


def summarize(events, days, top):
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    kept, by_tool, by_day, by_count = [], {}, {}, {}
    for o in events:
        ts = o.get("ts") or ""
        try:
            t = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            t = None
        if t and t < cutoff:
            continue
        kept.append(o)
        tool = str(o.get("tool"))
        by_tool[tool] = by_tool.get(tool, 0) + 1
        day = (ts[:10] if len(ts) >= 10 else "未知")
        by_day[day] = by_day.get(day, 0) + 1
        c = o.get("count")
        key = str(c) if isinstance(c, int) else "?"
        by_count[key] = by_count.get(key, 0) + 1

    top_tools = sorted(by_tool.items(), key=lambda kv: -kv[1])[:top]
    return {
        "window_days": days,
        "events": len(kept),
        "events_total": len(events),
        "tools": len(by_tool),
        "top_tools": [{"tool": k, "hits": v} for k, v in top_tools],
        "by_day": dict(sorted(by_day.items())),
        "by_count": dict(sorted(by_count.items(), key=lambda kv: (len(kv[0]), kv[0]))),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="", help="events.jsonl 路径；默认 <DSH_HOME>/storages/loop-guard/events.jsonl")
    ap.add_argument("--dsh-home", default="", help="不给则用 DSH_HOME 或 ~/.dsh")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    path = os.path.expanduser(a.file or default_path(a.dsh_home))
    if not os.path.isfile(path):
        msg = (f"没有守卫日志：{path}\n"
               "原因通常是：① 装的是旧版守卫（v3 之前不写日志）；② 近几天没触发过提醒。\n"
               "确认：python scripts/install_guard.py --status；触发一次重复调用再看。")
        if a.json:
            print(json.dumps({"error": "no-log", "path": path, "hint": msg}, ensure_ascii=False, indent=2))
        else:
            print(msg)
        return 2

    events, bad = load(path)
    s = summarize(events, a.days, a.top)
    s["file"] = path
    s["bad_lines"] = bad

    if a.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return 0

    print(f"守卫命中 · 近 {s['window_days']} 天（{path}）")
    print(f"  提醒 {s['events']} 次，涉及 {s['tools']} 个工具（文件内共 {s['events_total']} 条）")
    if not s["events"]:
        print("  窗口内没有提醒。要么没触发，要么守卫没装最新版。")
        return 0
    print(f"\n{'工具':<28}{'次数':>6}")
    for t in s["top_tools"]:
        print(f"{t['tool']:<28}{t['hits']:>6}")
    print(f"\n{'日期':<12}{'次数':>6}")
    for d, n in s["by_day"].items():
        print(f"{d:<12}{n:>6}")
    print("\n口径：一条 = 一次真正追加到上下文的提醒（同一链只在 2/4/6 次各记一次）；"
          "参数只记哈希与长度，不记内容。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
