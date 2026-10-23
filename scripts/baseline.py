#!/usr/bin/env python3
"""首日基线：先记一次前缀指标，过一天再比，解决「新装当天没有账本」。

用法：
  python baseline.py --config config.yaml --write      # 记基线
  python baseline.py --config config.yaml --compare    # 与基线比

记的是与任务无关的静态量（都不需要 ledger）：
  起始上下文 / 工具 schema 字节 / 技能目录单条字节 / 步轮比
口径：取最近 N 个有 usage 的会话（默认 5 个、每个 ≥5 步）取中位数。
"""
import argparse, json, os, statistics, subprocess, sys, time, datetime

from tg_common import config_base, load_config, resolve_dsh_home, sessions_root, utf8_stdio

# Windows 控制台默认 GBK：固定 UTF-8，避免中文报告/JSON 在无 PYTHONIOENCODING 的环境里乱码或崩。
utf8_stdio()


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
    dctx = zstd.ZstdDecompressor()
    out = []
    while data:
        dobj = dctx.decompressobj()
        out.append(dobj.decompress(data))
        if not dobj.eof:
            break
        data = dobj.unused_data
    return b"".join(out)


def text_of(o):
    d = o.get("data") or {}
    m = d.get("message") or d
    c = m.get("content") if isinstance(m, dict) else None
    if isinstance(c, list):
        return "".join(x.get("text") or "" for x in c if isinstance(x, dict))
    return c if isinstance(c, str) else ""


def scan(path):
    raw = decompress(path)
    tools_bytes = start_ctx = None
    cat_sizes, steps, turns = [], 0, 0
    for line in raw.split(b"\n"):
        if not line.strip():
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        t = o.get("type")
        if t == "request/header":
            if tools_bytes is None:
                try:
                    tools_bytes = len(json.dumps(o["data"]["header"].get("tools", []), ensure_ascii=False))
                except Exception:
                    pass
            continue
        if t == "assistant/message":
            u = o["data"].get("usage") or {}
            i = u.get("inputTokens", 0) or 0
            c = u.get("cacheReadTokens", 0) or 0
            steps += 1
            if start_ctx is None:
                start_ctx = i + c
            continue
        if t in ("user/message", "user/delta"):
            turns += 1
            if b"<available_skills>" in line:
                txt = text_of(o)
                if "<available_skills>" in txt:
                    cat_sizes.append(len(txt))
    if steps < 5:
        return None
    return {
        "steps": steps,
        "turns": max(turns, 1),
        "steps_per_turn": steps / max(turns, 1),
        "start_ctx": start_ctx or 0,
        "tools_bytes": tools_bytes or 0,
        "cat_bytes": max(cat_sizes) if cat_sizes else 0,
    }


def collect(root, samples):
    files = []
    for dp, _, fs in os.walk(root):
        for f in fs:
            if f.endswith(".zstd"):
                p = os.path.join(dp, f)
                try:
                    files.append((os.path.getmtime(p), p))
                except OSError:
                    pass
    files.sort(reverse=True)
    rows = []
    for _, p in files:
        r = scan(p)
        if r:
            rows.append(r)
        if len(rows) >= samples:
            break
    return rows


def median(rows, key):
    return statistics.median([r[key] for r in rows]) if rows else 0


def metrics(root, samples):
    rows = collect(root, samples)
    if not rows:
        sys.exit("还没有可用会话（要 ≥5 步且含 usage）。先正常用一会儿，再记基线。")
    return {
        "samples": len(rows),
        "date": datetime.date.today().isoformat(),
        "start_ctx": int(median(rows, "start_ctx")),
        "tools_bytes": int(median(rows, "tools_bytes")),
        "cat_bytes": int(median(rows, "cat_bytes")),
        "steps_per_turn": round(median(rows, "steps_per_turn"), 2),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--dsh-home", default="")
    ap.add_argument("--file", default="", help="基线文件，默认 config 同目录 tg-baseline.json")
    ap.add_argument("--samples", type=int, default=5)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--compare", action="store_true")
    a = ap.parse_args()

    base = config_base(a.config)
    cfg, _ = load_config(a.config)
    root = sessions_root(resolve_dsh_home(a.dsh_home, cfg=cfg, base=base))
    if not os.path.isdir(root):
        sys.exit(f"找不到会话目录: {root}（检查 config 的 dsh_home）")

    path = a.file or os.path.join(base, "tg-baseline.json")
    now = metrics(root, a.samples)

    if not (a.write or a.compare):
        print(json.dumps(now, ensure_ascii=False, indent=2))
        print("\n加 --write 记基线，或 --compare 与基线比。")
        return

    if a.write:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(now, f, ensure_ascii=False, indent=2)
        print(f"基线已记：{path}")
        print(f"  样本 {now['samples']} 个会话；起始上下文 {now['start_ctx']/1000:.0f} k，"
              f"schema {now['tools_bytes']/1000:.0f} kB，目录 {now['cat_bytes']/1000:.1f} kB")
        print("改完配置后隔一天再跑 --compare；当天比一定更贵（缓存前缀失效）。")
        return

    if not os.path.isfile(path):
        sys.exit(f"没有基线文件：{path}。先跑 --write。")
    old = json.load(open(path, encoding="utf-8"))
    print(f"基线 {old.get('date')}（{old.get('samples')} 个会话） → 现在 {now['date']}（{now['samples']} 个）\n")
    print(f"{'指标':<16}{'基线':>12}{'现在':>12}{'变化':>10}")
    for key, name, fmt in (("start_ctx", "起始上下文", "{:.0f} k"), ("tools_bytes", "工具 schema", "{:.0f} kB"),
                           ("cat_bytes", "技能目录单条", "{:.1f} kB"), ("steps_per_turn", "步/轮", "{:.2f}")):
        o, n = float(old.get(key) or 0), float(now[key])
        if key in ("start_ctx", "tools_bytes"):
            o, n = o / 1000, n / 1000
        elif key == "cat_bytes":
            o, n = o / 1000, n / 1000
        delta = (n / o - 1) * 100 if o else 0
        print(f"{name:<16}{fmt.format(o):>12}{fmt.format(n):>12}{delta:>9.1f}%")


if __name__ == "__main__":
    main()
