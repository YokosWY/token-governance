#!/usr/bin/env python3
r"""DSH 数据目录探测：换机器后「配错 home → 报没有数据」是最高频的困惑，先把它自动化。

为什么需要：桌面版 DSH 的 home 由安装时选的位置决定，可以是任意盘任意目录
（安装目录、`安装目录/home`、`%APPDATA%/<产品名>/home`…），`~/.dsh` 只是其中之一。
默认猜 `~/.dsh` 的脚本在别人的机器上会安静地指向一个空目录，
然后报告「没有会话 / 没有账本」——看起来像工具坏了，其实是路径没对上。

探测顺序（可信度从高到低）：
  1. --dsh-home 显式参数（只验证，不再猜）
  2. $DSH_HOME
  3. config.yaml 的 dsh_homes / dsh_home
  4. 桌面版的指针文件：<app>/home/desktop-next-location.json 里的 "home" 字段
  5. 标准位置：~/.dsh、~/.dsh-next、~/.config/dsh、~/Library/Application Support/dsh、
     %APPDATA%\<产品>\home、%LOCALAPPDATA%\dsh
  6. 名称含 dsh 的目录（~ 与各盘符根下，限深 3、限量扫描）及其 <dir>/home 子目录

打分只看结构，不看名字：有 sessions/ 且里面真有会话文件得分最高。

用法：
  python scripts/detect_home.py                 # 人读：候选 + 可直接粘贴的 config 片段
  python scripts/detect_home.py --json          # 给安装脚本 / env_check 读
  python scripts/detect_home.py --emit-config   # 只打印 YAML 片段

退出码：0 = 找到至少一个可用候选；1 = 没找到（附手工指定办法）。

只读：本脚本不创建、不修改任何文件。
"""
import argparse
import glob
import json
import os
import sys

from tg_common import (add_common_args, config_base, emit_json, expand, home_candidates,
                       load_config, sessions_root, utf8_stdio)

utf8_stdio()

MAX_PER_ROOT = 400          # 每个根下的最大扫描条目（防止用户在超大目录上等）
SKIP_DIRS = {"node_modules", ".git", "__pycache__", "site-packages", "cache", "tmp",
             "Temp", "Windows", "ProgramData", "AppData", "System Volume Information",
             "$Recycle.Bin", "dist", "build", "vendor"}


def _is_dir(p):
    try:
        return os.path.isdir(p)
    except OSError:
        return False


def score_home(p):
    """给候选目录打分：只认结构证据（sessions / storages / AGENTS.md / profiles）。

    为什么不用「名字像 DSH」做主要依据：用户的目录名五花八门，而 sessions 里
    有 session*.jsonl* 这件事骗不了人。
    """
    if not _is_dir(p):
        return -1, {}
    ev = {"sessions": False, "session_files": 0, "storages": False,
          "agents": False, "profiles": False, "ledger": False}
    sc = 0
    sp = sessions_root(p)
    if _is_dir(sp):
        ev["sessions"] = True
        sc += 1
        n = 0
        try:
            groups = os.listdir(sp)[:300]
        except OSError:
            groups = []
        for name in groups:
            d = os.path.join(sp, name)
            if not _is_dir(d):
                continue
            # 会话文件在 <sessions>/<分组>/<会话 id>/session*.jsonl[.zstd]：找两层就够
            for root, dirs, files in os.walk(d):
                if root[len(d):].count(os.sep) >= 2:
                    dirs[:] = []      # 到底了，别往附件/缓存里翻
                if any(f.startswith("session") and ".jsonl" in f for f in files):
                    n += 1
                    break
            if n >= 3:
                break
        ev["session_files"] = n
        if n:
            sc += 2
    if _is_dir(os.path.join(p, "storages")):
        ev["storages"] = True
        sc += 1
    if os.path.isfile(os.path.join(p, "AGENTS.md")):
        ev["agents"] = True
        sc += 1
    if _is_dir(os.path.join(p, "profiles")):
        ev["profiles"] = True
        sc += 1
    for lp in (os.path.join(p, "data", "ledger.json"),
               os.path.join(p, "storages", "cost-meter", "ledger.json")):
        if os.path.isfile(lp):
            ev["ledger"] = True
            sc += 1
            break
    return sc, ev


def pointer_homes():
    """桌面版指针文件：<app>/home/desktop-next-location.json 或 <app>/*location*.json。"""
    out = []
    app_roots = []
    home = os.path.expanduser("~")
    for cand in (os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA"),
                 os.path.join(home, "Library", "Application Support"),
                 os.path.join(home, ".config")):
        if cand and _is_dir(cand):
            app_roots.append(cand)
    for root in app_roots:
        for pat in ("*/home/*location*.json", "*/*location*.json"):
            try:
                paths = glob.glob(os.path.join(root, pat))[:50]
            except OSError:
                continue
            for pf in paths:
                try:
                    with open(pf, encoding="utf-8") as f:
                        data = json.load(f)
                except (OSError, ValueError):
                    continue
                v = data.get("home") if isinstance(data, dict) else None
                if isinstance(v, str) and v.strip():
                    out.append((expand(v), f"指针文件 {pf}"))
    return out


def standard_homes():
    home = os.path.expanduser("~")
    cands = [
        os.path.join(home, ".dsh"),
        os.path.join(home, ".dsh-next"),
        os.path.join(home, ".dsh-desktop"),
        os.path.join(home, ".config", "dsh"),
        os.path.join(home, "Library", "Application Support", "dsh"),
        os.path.join(home, "AppData", "Roaming", "dsh"),
        os.path.join(home, "AppData", "Local", "dsh"),
    ]
    for env in ("APPDATA", "LOCALAPPDATA"):
        root = os.environ.get(env)
        if root:
            cands += [os.path.join(root, "dsh"), os.path.join(root, "DSH"),
                      os.path.join(root, "dsh-desktop")]
            try:
                for name in os.listdir(root):
                    lp = os.path.join(root, name)
                    if "dsh" in name.lower() and _is_dir(lp):
                        cands.append(lp)
                        if _is_dir(os.path.join(lp, "home")):
                            cands.append(os.path.join(lp, "home"))
            except OSError:
                pass
    return [(c, "标准位置") for c in cands]


def scan_roots():
    """名称含 dsh 的目录：~ 与各盘符根下，限深 3。"""
    roots = [os.path.expanduser("~")]
    if os.name == "nt":
        import string
        for ch in string.ascii_uppercase:
            r = ch + ":\\"
            if _is_dir(r):
                roots.append(r)
    else:
        for r in ("/opt", "/mnt", "/media", "/usr/local"):
            if _is_dir(r):
                roots.append(r)
    out = []
    for root in roots:
        n = 0
        for depth in (1, 2):
            pat = os.path.join(root, *(["*"] * depth))
            try:
                listing = glob.glob(pat)[:MAX_PER_ROOT]
            except OSError:
                continue
            for p in listing:
                n += 1
                if n > MAX_PER_ROOT:
                    break
                base = os.path.basename(p.rstrip("\\/"))
                if base in SKIP_DIRS:
                    continue
                if "dsh" not in base.lower():
                    continue
                out.append((p, "目录名含 dsh"))
                if _is_dir(os.path.join(p, "home")):
                    out.append((os.path.join(p, "home"), "目录名含 dsh（/home 子目录）"))
            if n > MAX_PER_ROOT:
                break
    return out


def detect(cfg=None, base=None, explicit="", scan=True):
    """返回候选列表，每项 {path, score, source, evidence}，按分数降序、按真实路径去重。"""
    seen = {}
    order = []

    def add(path, source):
        if not path:
            return
        p = expand(path, base)
        real = os.path.realpath(p)
        if real in seen:
            # 同一目录被多路发现：保留最高分来源，来源串起来
            if source not in seen[real]["source"]:
                seen[real]["source"] += " / " + source
            return
        sc, ev = score_home(p)
        if sc < 0:
            return
        seen[real] = {"path": p, "score": sc, "source": source, "evidence": ev}
        order.append(real)

    if explicit:
        add(explicit, "命令行 --dsh-home")
        return _sorted(seen, order), "只用 --dsh-home 验证（不再猜测）"
    # config / $DSH_HOME / 默认 ~/.dsh：顺序与来源都由 tg_common 定，这里不自己读环境变量
    for h, src in home_candidates(cfg, base):
        add(h, src)
    for p, why in pointer_homes():
        add(p, why)
    for p, why in standard_homes():
        add(p, why)
    if scan:
        for p, why in scan_roots():
            add(p, why)
    return _sorted(seen, order), ("按结构证据打分" if order else "没有任何候选")


def _sorted(seen, order):
    items = [seen[k] for k in order]
    items.sort(key=lambda x: (-x["score"], len(x["path"])))
    return items


def main():
    ap = argparse.ArgumentParser(description="探测 DSH 数据目录（home）")
    add_common_args(ap, json_flag=True)
    ap.add_argument("--no-scan", action="store_true", help="不扫盘，只查标准位置与指针文件")
    ap.add_argument("--emit-config", action="store_true", help="只打印可粘贴的 YAML 片段")
    a = ap.parse_args()

    cfg, base = load_config(a.config, strict=False)
    cands, how = detect(cfg, base, explicit=a.dsh_home, scan=not a.no_scan)
    best = cands[0] if cands else None
    out = {"ok": bool(best), "how": how, "candidates": cands[:5],
           "recommended": best["path"] if best else ""}
    if a.json:
        emit_json(out)
        return 0 if best else 1

    if a.emit_config:
        if best:
            p = best["path"].replace(os.sep, "/")
            print(f'dsh_home: "{p}"')
            print(f'skills_dir: "{p}/skills"')
        else:
            print("# 没有找到 DSH home：用 --dsh-home 手工指定，或先启动一次 DSH")
        return 0 if best else 1

    print(f"DSH home 探测 · {sys.platform}")
    print(f"config: {os.path.abspath(a.config) if os.path.isfile(a.config) else '（无）'}"
          f"  口径: {how}")
    print()
    if not cands:
        print("没有找到任何 DSH home 候选。")
        print("  1) 先启动一次 DSH 客户端，让它生成 sessions/ 与 storages/")
        print("  2) 或用 --dsh-home <路径> 直接指定")
        print("  3) 常见位置：~/.dsh、~/.config/dsh、%APPDATA%/<产品名>/home、安装目录/home")
        return 1
    icon = {True: "可用", False: "空目录"}
    for i, c in enumerate(cands[:5], 1):
        tag = "← 建议" if i == 1 else ""
        print(f"  {i}. [{c['score']} 分] {c['path']}  {tag}")
        ev = c["evidence"]
        bits = []
        if ev.get("sessions"):
            bits.append(f"sessions（会话文件 {ev.get('session_files', 0)} 个）")
        if ev.get("storages"):
            bits.append("storages")
        if ev.get("profiles"):
            bits.append("profiles")
        if ev.get("agents"):
            bits.append("AGENTS.md")
        if ev.get("ledger"):
            bits.append("ledger")
        print(f"     证据：{'、'.join(bits) or '只有目录'}   来源：{c['source']}")
    print()
    print("把它填进 config.yaml（复制 config.example.yaml 后改这两行）：")
    main_path = cands[0]["path"].replace(os.sep, "/")
    print(f'  dsh_home: "{main_path}"')
    print(f'  skills_dir: "{main_path}/skills"   # 桌面版的技能就在 home 下；默认 ~/.dsh/skills 多半不对')
    print()
    print("多条都要看（两套客户端并存）时用列表：")
    for c in cands[:3]:
        print(f'  - "{c["path"].replace(os.sep, "/")}"   # {c["source"]}')
    return 0


if __name__ == "__main__":
    sys.exit(main())
