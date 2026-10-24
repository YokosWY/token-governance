#!/usr/bin/env python3
"""环境自检：换一台机器后先跑这一条，确认依赖与数据目录是否齐。

为什么单列：脚本的依赖是分层的（Python 标准库 / pyyaml / 解压器 / node），
不同脚本需要的层级不同；缺了某一层时，有的脚本能降级、有的必须停。
一条命令把「缺什么、会影响哪些脚本、怎么装」列清楚，比逐个脚本试错快。

用法：
  python scripts/env_check.py                  # 人读
  python scripts/env_check.py --json           # 给 CI / 安装脚本
  python scripts/env_check.py --config my.yaml # 换配置

退出码：必需项缺失 → 1；只缺可选项 → 0（仍会打印警告）。

分级：
  required  缺了主流程就跑不了：Python ≥ 3.9；pyyaml（读 config）；解压器二选一
  optional  只影响单项功能：node（守卫自检 verify_guard.mjs）
  info      只报告不判定：dsh_home 里有没有 sessions / ledger
"""
import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys

from tg_common import (add_common_args, config_base, emit_json, load_config,
                       resolve_dsh_home, sessions_root, utf8_stdio)

utf8_stdio()

MIN_PY = (3, 9)
FIX = {
    "python": "装 Python 3.9+（本项目在 3.9/3.14 上验过；低于 3.9 不保证）",
    "pyyaml": "pip install pyyaml",
    "zstd": "装 zstd 命令（Windows: Git for Windows 自带；macOS: brew install zstd；"
            "Debian/Ubuntu: apt install zstd）或 pip install zstandard",
    "node": "装 Node 18+（只影响 verify_guard.mjs 的守卫自检，不影响看账）",
}


def _ver(t):
    return ".".join(str(x) for x in t)


def check_python():
    ok = sys.version_info[:2] >= MIN_PY
    return {
        "key": "python", "level": "required", "ok": ok,
        "detail": f"Python {_ver(sys.version_info[:3])}（要求 ≥ {_ver(MIN_PY)}）",
        "fix": "" if ok else FIX["python"],
    }


def _importable(name):
    """真 import 一次：find_spec 只看得到文件，模块自己抛 ImportError 时会被误判为可用。"""
    try:
        __import__(name)
        return True
    except Exception:
        return False


def check_pyyaml():
    ok = _importable("yaml")
    return {
        "key": "pyyaml", "level": "required", "ok": ok,
        "detail": "pyyaml 可用" if ok else "pyyaml 缺失：所有读 config.yaml 的脚本会直接拒绝运行",
        "fix": "" if ok else FIX["pyyaml"],
    }


def check_unzip():
    exe = shutil.which("zstd")
    mod = _importable("zstandard")
    ok = bool(exe) or mod
    got = []
    if exe:
        got.append(f"zstd 命令 {exe}")
    if mod:
        got.append("Python zstandard 模块")
    return {
        "key": "unzip", "level": "required", "ok": ok,
        "detail": "、".join(got) if ok else "解压器缺失：会话日志（.jsonl.zstd）读不了",
        "fix": "" if ok else FIX["zstd"],
    }


def check_node():
    exe = shutil.which("node")
    ver = ""
    if exe:
        try:
            r = subprocess.run([exe, "--version"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=10)
            ver = (r.stdout or "").strip()
        except Exception:
            ver = ""
    ok = bool(exe)
    return {
        "key": "node", "level": "optional", "ok": ok,
        "detail": f"node {ver}".strip() if ok else "node 缺失：只影响守卫自检，不影响看账",
        "fix": "" if ok else FIX["node"],
    }


def check_dsh_home(cfg, base, home_arg):
    home = resolve_dsh_home(home_arg, cfg=cfg, base=base)
    root = sessions_root(home)
    has_sessions = os.path.isdir(root)
    ledger = os.path.join(home, "storages", "cost-meter", "ledger.json")
    has_ledger = os.path.isfile(ledger)
    writable = False
    if os.path.isdir(home):
        # os.access(W_OK) 在 Windows 上不靠谱（永远 True），用真实建临时文件探一次
        try:
            import tempfile
            fd, probe = tempfile.mkstemp(prefix=".tg-write-probe-", dir=home)
            os.close(fd)
            os.remove(probe)
            writable = True
        except Exception:
            writable = False
    return {
        "key": "dsh_home", "level": "info", "ok": has_sessions or has_ledger,
        "detail": f"{home}；sessions={'有' if has_sessions else '无'} "
                  f"ledger={'有' if has_ledger else '无'} 可写={'是' if writable else '否'}",
        "fix": "" if (has_sessions or has_ledger)
        else "home 里既没有 sessions 也没有 ledger：确认 --dsh-home 或 config.yaml 的 dsh_home 指对了",
    }


def main():
    ap = argparse.ArgumentParser(description="TG 环境自检：依赖 + 数据目录")
    add_common_args(ap, json_flag=True)
    a = ap.parse_args()

    cfg, base = load_config(a.config, strict=False)  # 自检本身不能因为配置坏而没法报告
    rows = [check_python(), check_pyyaml(), check_unzip(), check_node(),
            check_dsh_home(cfg, base, a.dsh_home)]

    missing_req = [r for r in rows if r["level"] == "required" and not r["ok"]]
    out = {
        "ok": not missing_req,
        "python": _ver(sys.version_info[:3]),
        "platform": sys.platform,
        "config": os.path.abspath(a.config) if os.path.isfile(a.config) else "",
        "checks": [{k: r[k] for k in ("key", "level", "ok", "detail", "fix")} for r in rows],
        "missing_required": [r["key"] for r in missing_req],
    }
    if a.json:
        emit_json(out)
        return 1 if missing_req else 0

    print(f"TG 环境自检 · Python {out['python']} · {sys.platform}")
    print(f"config: {out['config'] or '（没有 config.yaml，按默认值检查）'}")
    print()
    icon = {True: "OK  ", False: "MISS"}
    for r in rows:
        tag = {"required": "必需", "optional": "可选", "info": "信息"}[r["level"]]
        print(f"  {icon[r['ok']]} [{tag}] {r['key']:9} {r['detail']}")
        if r["fix"]:
            print(f"         修复：{r['fix']}")
    print()
    if missing_req:
        print(f"缺必需项 {len(missing_req)} 个：{', '.join(r['key'] for r in missing_req)}"
              "；装完再跑一次本命令。")
    else:
        print("必需项齐全；可选项缺失只影响对应功能，不影响看账。")
    return 1 if missing_req else 0


if __name__ == "__main__":
    sys.exit(main())
