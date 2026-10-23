# -*- coding: utf-8 -*-
r"""install_guard.py — 把 TG 里的循环守卫装进指定 DSH home（幂等，带备份）。

做三件事：
  1. 复制 plugins\dsh-loop-guard\ 到 <home>\local-plugins\dsh-loop-guard\（内容相同则跳过）
  2. 在 profiles\<profile>\package.json 里补 dependency（link:）与 dsh.profile.bundles 条目
  3. 打印重启提示（插件在下次启动 DSH 时生效）

用法：
  python scripts\install_guard.py                    # 只看会改什么（dry-run，默认）
  python scripts\install_guard.py --apply            # 真正写入
  python scripts\install_guard.py --status           # 查当前状态
  python scripts\install_guard.py --uninstall --apply
  python scripts\install_guard.py --home <DSH_HOME> --profile desktop
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

from tg_common import config_base, load_config, resolve_dsh_home, utf8_stdio

utf8_stdio()

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "plugins" / "dsh-loop-guard"
PLUGIN_NAME = "@local/dsh-loop-guard"
DIR_NAME = "dsh-loop-guard"


def pick_home(explicit: str | None) -> Path:
    """口径同 tg_common.resolve_dsh_home：显式 > config 的 dsh_home > $DSH_HOME > ~/.dsh。"""
    cfg, base = load_config(str(REPO / "config.yaml"))
    return Path(resolve_dsh_home(explicit or "", cfg=cfg, base=base))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict) -> None:
    backup = path.with_suffix(path.suffix + dt.datetime.now().strftime(".bak-guard-%Y%m%d-%H%M%S"))
    shutil.copyfile(path, backup)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  已备份：{backup.name}")


def status(home: Path, profile: str) -> int:
    target = home / "local-plugins" / DIR_NAME
    pkg = home / "profiles" / profile / "package.json"
    print(f"home    : {home}")
    print(f"plugin  : {target} -> {'已存在' if target.exists() else '不存在'}")
    if target.exists() and (target / 'index.js').exists():
        print(f"index.js: sha256 {sha(target / 'index.js')[:12]}")
    print(f"profile : {pkg} -> {'存在' if pkg.exists() else '不存在'}")
    if pkg.exists():
        data = read_json(pkg)
        dep = (data.get("dependencies") or {}).get(PLUGIN_NAME)
        bundles = ((data.get("dsh") or {}).get("profile") or {}).get("bundles") or []
        print(f"dependency: {dep or '未登记'}")
        print(f"bundles   : {'已登记' if PLUGIN_NAME in bundles else '未登记'}")
    return 0


def install(home: Path, profile: str, apply: bool) -> int:
    target = home / "local-plugins" / DIR_NAME
    pkg = home / "profiles" / profile / "package.json"
    files = ("index.js", "package.json", "cordis.patch.yml", "README.md")
    if not SRC.exists():
        print(f"[FAIL] 源目录不存在：{SRC}")
        return 1
    if not pkg.exists():
        print(f"[FAIL] profile 不存在：{pkg}")
        return 1

    print(f"home={home} profile={profile}")
    print(f"1) 复制 {SRC} -> {target}")
    # 逐文件比对：只比 index.js 会让 README / patch 的改动永远同步不过去
    same = target.exists() and all(
        (target / f).exists() and sha(target / f) == sha(SRC / f) for f in files)
    if same:
        print("   内容相同，跳过")
    elif not apply:
        print("   （dry-run）会写入 index.js / package.json / cordis.patch.yml / README.md")
    else:
        target.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copyfile(SRC / f, target / f)
        print(f"   已复制 {len(files)} 个文件")

    data = read_json(pkg)
    deps = data.setdefault("dependencies", {})
    link = f"link:{str(target).replace(os.sep, '/')}"
    need_dep = deps.get(PLUGIN_NAME) != link
    bundles = (((data.get("dsh") or {}).get("profile") or {}).get("bundles"))
    if bundles is None:
        print("[FAIL] package.json 里没有 dsh.profile.bundles，跳过登记")
        return 1
    need_bundle = PLUGIN_NAME not in bundles

    print(f"2) 登记 dependency：{PLUGIN_NAME} -> {link}（{'需写' if need_dep else '已存在'}）")
    print(f"3) 登记 bundles：{'需写' if need_bundle else '已存在'}")

    if apply and (need_dep or need_bundle):
        deps[PLUGIN_NAME] = link
        if need_bundle:
            bundles.insert(0, PLUGIN_NAME)
        write_json(pkg, data)
        print("   已写入 package.json")
        print("   提示：如仓库用 pnpm 管理，执行 pnpm install 让 link 生效（本机已有 node_modules 软链时通常可跳过）")
    elif not apply:
        print("   （dry-run，未写入）")

    print("\n完成。重启 DSH 后生效；验证：cscript //nologo <home>\\... 或看会话里是否出现【循环守卫】提醒。")
    return 0


def uninstall(home: Path, profile: str, apply: bool) -> int:
    pkg = home / "profiles" / profile / "package.json"
    if not pkg.exists():
        print(f"[FAIL] profile 不存在：{pkg}")
        return 1
    data = read_json(pkg)
    deps = data.get("dependencies") or {}
    bundles = (((data.get("dsh") or {}).get("profile") or {}).get("bundles")) or []
    has_dep = PLUGIN_NAME in deps
    has_bundle = PLUGIN_NAME in bundles
    print(f"摘除 dependency={has_dep} bundle={has_bundle}（插件目录保留）")
    if apply and (has_dep or has_bundle):
        deps.pop(PLUGIN_NAME, None)
        data["dsh"]["profile"]["bundles"] = [b for b in bundles if b != PLUGIN_NAME]
        write_json(pkg, data)
        print("   已写入 package.json")
    elif not apply:
        print("   （dry-run，未写入）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home")
    ap.add_argument("--profile", default="desktop")
    ap.add_argument("--apply", action="store_true", help="真正写入（默认 dry-run）")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    args = ap.parse_args()

    home = pick_home(args.home)
    if args.status:
        return status(home, args.profile)
    if args.uninstall:
        return uninstall(home, args.profile, args.apply)
    return install(home, args.profile, args.apply)


if __name__ == "__main__":
    sys.exit(main())
