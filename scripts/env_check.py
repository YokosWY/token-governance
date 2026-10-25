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

from tg_common import (add_common_args, config_base, emit_json, ledger_paths, load_config,
                       resolve_dsh_home, sessions_root, unzip_channels, utf8_stdio)

utf8_stdio()

MIN_PY = (3, 9)
FIX = {
    "python": "装 Python 3.9+（本项目在 3.9/3.14 上验过；低于 3.9 不保证）",
    "pyyaml": "pip install pyyaml（Linux/macOS 上若 python 不在 PATH，用 python3 -m pip）",
    "zstd": "Python ≥ 3.14 自带 compression.zstd，无需安装；更老的 Python 二选一："
            "pip install zstandard（首选，跨平台），或装系统包：macOS brew install zstd、"
            "Debian/Ubuntu apt install zstd。Windows 不保证自带 zstd.exe",
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


def _probe_module(name, *attrs):
    """真 import 一次并检查关键 API：find_spec 只看得到文件，同名假模块会被误判为可用。"""
    try:
        mod = __import__(name, fromlist=["*"])
    except Exception:
        return False, ""
    for a in attrs:
        if not hasattr(mod, a):
            return False, f"{name} 能导入但缺少 {a}（同名假模块？）"
    return True, ""


def check_pyyaml():
    ok, why = _probe_module("yaml", "safe_load")
    return {
        "key": "pyyaml", "level": "required", "ok": ok,
        "detail": "pyyaml 可用" if ok else (why or "pyyaml 缺失：所有读 config.yaml 的脚本会直接拒绝运行"),
        "fix": "" if ok else FIX["pyyaml"],
    }


def check_config(path):
    """config 本身也是必需项：文件在但读不出来时，后面所有检查都会落到错误的 home 上。"""
    if not path or not os.path.isfile(path):
        return {"key": "config", "level": "required", "ok": True,
                "detail": "没有 config.yaml（按默认值检查）", "fix": ""}
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except ImportError:
        return {"key": "config", "level": "required", "ok": False,
                "detail": f"{path} 读不了：没装 pyyaml", "fix": FIX["pyyaml"]}
    except UnicodeDecodeError as e:
        return {"key": "config", "level": "required", "ok": False,
                "detail": f"{path} 不是 UTF-8（{e}）", "fix": "另存为 UTF-8 再跑"}
    except Exception as e:
        return {"key": "config", "level": "required", "ok": False,
                "detail": f"{path} 不是合法 YAML：{str(e).splitlines()[0]}",
                "fix": "改好配置（常见：双引号里写 Windows 反斜杠 / GBK 存盘 / 顶层不是映射）"}
    if not isinstance(data, dict):
        return {"key": "config", "level": "required", "ok": False,
                "detail": f"{path} 顶层不是映射（读到 {type(data).__name__}）",
                "fix": "配置应是 key: value 形式"}
    return {"key": "config", "level": "required", "ok": True,
            "detail": f"{path} 可读", "fix": ""}


def check_unzip():
    """解压器判定：直接问 tg_common 的通道表，不在这里另立一套。

    历史教训：本函数曾只认 `zstd` 命令与 `zstandard` 模块，漏了 Python ≥ 3.14 的内建
    `compression.zstd`；于是「3.14 + 没装 zstandard + 没有 zstd 命令」的机器上自检报
    「必需项缺失、退出码 1」，而所有脚本都能正常解压。用户被指去装一个用不到的包，
    公司机/无网环境直接卡死。判据只能有一份。
    """
    chans = unzip_channels()
    ok = any(c["ok"] for c in chans)
    got = [c["detail"] for c in chans if c["ok"]]
    if not ok:
        got = ["解压器缺失：会话日志（.jsonl.zstd）读不了（" +
               "；".join(c["detail"] for c in chans) + "）"]
    return {
        "key": "unzip", "level": "required", "ok": ok,
        "detail": "、".join(got),
        "fix": "" if ok else FIX["zstd"],
    }


def check_node():
    exe = shutil.which("node")
    ver, major = "", 0
    if exe:
        try:
            r = subprocess.run([exe, "--version"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=10)
            ver = (r.stdout or "").strip()
            major = int(ver.lstrip("v").split(".")[0]) if ver.lstrip("v")[:1].isdigit() else 0
        except Exception:
            ver = ""
    ok = bool(exe) and major >= 18
    if not exe:
        detail = "node 缺失：只影响守卫自检，不影响看账"
    elif major and major < 18:
        detail = f"node {ver} 低于 18：守卫自检（verify_guard.mjs）可能跑不了"
    else:
        detail = f"node {ver}".strip()
    return {
        "key": "node", "level": "optional", "ok": ok,
        "detail": detail, "fix": "" if ok else FIX["node"],
    }


def check_pricing(cfg, base):
    """计价来源：别人换模型 / 换币种时，默认价表会把钱算错，而报告看起来完全正常。

    只报「钱是用哪套价、哪个汇率、按哪个时区切日」，不判对错——真实单价只有用户知道。
    没有价表时 ledger.py 会用内置 deepseek 价，这一条是给「我在用 Claude/GPT」的人看的。
    """
    cfg = cfg or {}
    led = cfg.get("ledger") or {}
    pr = led.get("prices") or {}
    legacy = cfg.get("prices_usd_per_mtok") or {}
    models = sorted((pr.get("models") or {}).keys()) or sorted(legacy.keys())
    fx = (pr.get("fx") or {}).get("usdToCny")
    asof = (pr.get("fx") or {}).get("asOf") or ""
    cur = led.get("currency") or "CNY"
    tz = led.get("timezone") or "系统时区"
    if pr:
        source = "config 的 ledger.prices"
    elif legacy:
        source = "退回 prices_usd_per_mtok"
    else:
        source = "没有价表（ledger.py 会用内置 deepseek 价）"
    detail = f"{source}；模型 {len(models)} 个"
    if fx:
        detail += f"；1 USD = {fx} {cur}" + (f"（{asof}）" if asof else "（无日期，需自核）")
    detail += f"；日按 {tz} 切"
    return {
        "key": "pricing", "level": "info", "ok": bool(models),
        "detail": detail,
        "fix": "" if models else "没有价表：换模型时钱会算错。把实际单价填进 config.yaml 的 "
                                  "ledger.prices.models（含 cacheMiss/cacheHit/output）",
    }


def check_dsh_home(cfg, base, home_arg, note=""):
    home = resolve_dsh_home(home_arg, cfg=cfg, base=base)
    root = sessions_root(home)
    has_sessions = os.path.isdir(root)
    # 账本可能已迁到 config 的 ledger.path（ledger.py 派生），也可能还是旧插件路径
    paths = ledger_paths(cfg, base, [home])
    hit = next((p for p in paths if os.path.isfile(p)), "")
    has_ledger = bool(hit)
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
        "detail": (note + f"{home}；sessions={'有' if has_sessions else '无'} "
                   f"ledger={'有' if has_ledger else '无'}"
                   + (f"（{hit}）" if hit else "") + f" 可写={'是' if writable else '否'}"),
        "fix": "" if (has_sessions or has_ledger)
        else "home 里既没有 sessions 也没有 ledger：先跑 python scripts/detect_home.py 自动找，"
             "或用 --dsh-home / config.yaml 的 dsh_home 指定",
    }


def main():
    ap = argparse.ArgumentParser(description="TG 环境自检：依赖 + 数据目录")
    add_common_args(ap, json_flag=True)
    a = ap.parse_args()

    cfg_row = check_config(a.config)
    cfg, base = load_config(a.config, strict=False)  # 自检本身不能因为配置坏而没法报告
    if not cfg_row["ok"]:
        # 配置坏了，后面的 home 解析会退回默认值：把这件事写在行里，别让「齐备」误导人
        cfg = {}
    rows = [check_python(), check_pyyaml(), check_unzip(), check_node(), cfg_row,
            check_pricing(cfg, base),
            check_dsh_home(cfg, base, a.dsh_home,
                           note="" if cfg_row["ok"] else "（config 解析失败，以下按默认 home 检查）")]

    missing_req = [r for r in rows if r["level"] == "required" and not r["ok"]]
    out = {
        "ok": not missing_req,
        "python": _ver(sys.version_info[:3]),
        "platform": sys.platform,
        "config": os.path.abspath(a.config) if os.path.isfile(a.config) else "",
        "config_error": "" if cfg_row["ok"] else cfg_row["detail"],
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
