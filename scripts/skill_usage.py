#!/usr/bin/env python3
"""技能使用埋点：哪些技能真被调用过，哪些一直待机。

用法：
  python skill_usage.py --config config.yaml --days 7
  python skill_usage.py --dsh-home ~/.dsh --days 30 --json

口径：
  - 记 `skill` 工具的调用参数里的技能名（name / skill / 参数键任一命中）。
  - 记用户消息以 `/技能名` 形式的直接调用。
  - 「已装」按 skills_dir 下的目录名；「常驻」看 SKILL.md 是否有 disable-model-invocation。
"""
import argparse, json, os, re, subprocess, sys, time

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
    d = zstd.ZstdDecompressor()
    out = []
    while data:
        obj = d.decompressobj()
        out.append(obj.decompress(data))
        if not obj.eof:
            break
        data = obj.unused_data
    return b"".join(out)


def text_of(msg):
    if isinstance(msg, str):
        return msg
    return "".join(c.get("text") or "" for c in (msg.get("content") or []) if isinstance(c, dict))


def skill_name_from_args(args):
    try:
        j = json.loads(args or "{}")
    except Exception:
        return ""
    if not isinstance(j, dict):
        return ""
    for k in ("name", "skill", "skill_name", "id"):
        v = j.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def installed_skills(skills_dir):
    out = {}
    if not os.path.isdir(skills_dir):
        return out
    for d in sorted(os.listdir(skills_dir)):
        p = os.path.join(skills_dir, d, "SKILL.md")
        if not os.path.isfile(p):
            continue
        try:
            head = open(p, encoding="utf-8", errors="ignore").read(4000)
        except Exception:
            head = ""
        standby = "disable-model-invocation: true" in head
        out[d] = "待机" if standby else "常驻"
    return out


def scan(root, days):
    cut = time.time() - days * 86400
    files = []
    for dp, _, fs in os.walk(root):
        for f in fs:
            if f.endswith(".zstd"):
                p = os.path.join(dp, f)
                try:
                    if os.path.getmtime(p) >= cut:
                        files.append(p)
                except OSError:
                    pass
    calls, slash = {}, {}
    sessions = set()
    for p in files:
        sid = os.path.basename(os.path.dirname(p)).replace("session-", "")[:8]
        try:
            raw = decompress(p)
        except SystemExit:
            raise
        except Exception:
            continue
        for line in raw.split(b"\n"):
            if not line.strip():
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            t = o.get("type")
            d = o.get("data") or {}
            if t == "tool/call" and (d.get("name") == "skill"):
                name = skill_name_from_args(d.get("arguments") or "")
                if name:
                    calls[name] = calls.get(name, 0) + 1
                    sessions.add(sid)
            elif t == "user/message":
                txt = text_of(d.get("message") or d).strip()
                m = re.match(r"^/([a-zA-Z0-9_\-]+)", txt)
                if m:
                    slash[m.group(1)] = slash.get(m.group(1), 0) + 1
                    sessions.add(sid)
    return files, calls, slash, sessions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--dsh-home", default="")
    ap.add_argument("--skills-dir", default="")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    home_arg = a.dsh_home
    skills_dir = a.skills_dir
    cfg, base = load_config(a.config)
    skills_dir = skills_dir or cfg.get("skills_dir") or ""
    home = resolve_dsh_home(home_arg, cfg=cfg, base=base)
    skills_dir = os.path.expanduser(os.path.expandvars(skills_dir or os.path.join(home, "skills")))
    root = sessions_root(home)
    if not os.path.isdir(root):
        sys.exit(f"找不到会话目录: {root}")

    files, calls, slash, sessions = scan(root, a.days)
    installed = installed_skills(skills_dir)
    used = set(calls) | set(slash)
    tg_installed = {k: v for k, v in installed.items() if k.startswith("tg-")}
    payload = {
        "window_days": a.days, "sessions": len(files), "skills_dir": skills_dir,
        "installed": len(installed), "tg_installed": tg_installed,
        "skill_tool_calls": calls, "slash_calls": slash,
        "never_used": sorted(k for k in installed if k not in used),
    }
    if a.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print(f"扫描 {len(files)} 个会话（近 {a.days} 天）；已装技能 {len(installed)} 个，其中 tg-* {len(tg_installed)} 个\n")
    rows = sorted({**{k: v for k, v in calls.items()}, **{k: v for k, v in slash.items()}}.items(),
                  key=lambda kv: -kv[1])
    if rows:
        print(f"{'技能':<24}{'skill 调用':>10}{'/名字':>8}")
        for name, n in rows:
            print(f"{name:<24}{calls.get(name, 0):>10}{slash.get(name, 0):>8}")
    else:
        print("窗口内没有任何技能调用。")
    never = [k for k in tg_installed if k not in used]
    print(f"\ntg-* 从未被调用: {', '.join(never) if never else '无'}")
    print("说明：待机技能模型侧不可见，只能 read 或 /名字；命中 0 不代表没用，先看是否需要常驻。")


if __name__ == "__main__":
    main()
