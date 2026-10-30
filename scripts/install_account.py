# -*- coding: utf-8 -*-
r"""install_account.py — 把 TG 里的账目胶囊装进指定 DSH home（幂等，带备份）。

做四件事：
  1. 复制 plugins\dsh-tg-account\ 的运行时文件与测试到 <home>\local-plugins\dsh-tg-account\（内容相同则跳过）
  2. 在 profiles\<profile>\package.json 里补 dependency（link:）与 dsh.profile.bundles 条目
  3. 写 <home>\_local\tg-account.json（root = 本仓库），胶囊据此找 config/prices/handoff.py
  4. 打印重启提示（客户端 bundle 在下次启动 DSH 时重建）

用法：
  python scripts\install_account.py                  # 只看会改什么（dry-run，默认）
  python scripts\install_account.py --apply          # 真正写入
  python scripts\install_account.py --status         # 查当前状态
  python scripts\install_account.py --uninstall --apply
  python scripts\install_account.py --home <DSH_HOME> --profile desktop
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from tg_common import load_config, resolve_dsh_home, utf8_stdio

utf8_stdio()

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "plugins" / "dsh-tg-account"
PLUGIN_NAME = "@local/dsh-tg-account"
DIR_NAME = "dsh-tg-account"
# 运行时必需 + 给人看的：lib 逐个复制，README/DECISIONS 方便在 home 里就地查
FLAT_FILES = ("package.json", "cordis.patch.yml", "README.md", "DECISIONS.md", "LICENSE")


def pick_home(explicit: str | None, config_path: str | None = None) -> Path:
    """口径同 tg_common.resolve_dsh_home：显式 > config 的 dsh_home > $DSH_HOME > ~/.dsh。"""
    cfg_path = config_path or str(REPO / "config.yaml")
    cfg, base = load_config(cfg_path)
    return Path(resolve_dsh_home(explicit or "", cfg=cfg, base=base))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict, tag: str) -> None:
    backup = path.with_suffix(path.suffix + dt.datetime.now().strftime(f".bak-{tag}-%Y%m%d-%H%M%S"))
    shutil.copyfile(path, backup)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  已备份：{backup.name}")


def src_files() -> list[Path]:
    """要装进 local-plugins 的文件：lib/*.js + tests/*.mjs + 顶层清单。

    tests 一起装：家目录里的副本要能自己 `node --test` 验一遍，
    否则代码更新了、旧断言（比如本机路径默认值）还留在那儿，下次跑就红。
    缺失直接报错，不装半套。
    """
    out = [SRC / name for name in FLAT_FILES]
    lib = sorted((SRC / "lib").glob("*.js")) if (SRC / "lib").is_dir() else []
    if not lib:
        raise SystemExit(f"[FAIL] 源目录缺 lib/*.js：{SRC}")
    tests = sorted((SRC / "tests").glob("*.test.mjs")) if (SRC / "tests").is_dir() else []
    return out + lib + tests


def same_tree(target: Path, files: list[Path]) -> bool:
    for f in files:
        rel = f.relative_to(SRC)
        t = target / rel
        if not t.exists() or sha(t) != sha(f):
            return False
    return True


def node_major() -> int:
    try:
        r = subprocess.run(["node", "-v"], capture_output=True, text=True, timeout=10)
        return int(r.stdout.strip().lstrip("v").split(".")[0])
    except Exception:
        return 0


def memory_root_state(home: Path) -> tuple[str, str]:
    """交接卡能不能用，取决于 memory_root。返回 (来源, 路径)；没有就 ("", "")。

    handoff.py 不会自己猜目录（旧版静默落 ~/tg-memory 的兜底已删），没配就每次都以
    「请先设置 memory_root」失败——胶囊面板上表现为「交接卡生成失败」，而且这个坑
    只有 handoff.py 的 stderr 里写着，装的时候不提，新用户会以为插件坏了。
    """
    env = os.environ.get("TG_MEMORY_ROOT", "")
    if env:
        return "env", env
    root = ""
    try:
        root = str(read_json(home / "_local" / "tg-account.json").get("root") or "")
    except Exception:
        root = ""
    if not root:
        return "", ""
    cfg = Path(root.replace("/", os.sep)) / "config.yaml"
    try:
        txt = cfg.read_text(encoding="utf-8")
    except Exception:
        return "", ""
    m = re.search(r"^\s*memory_root:\s*[\"']?([^\"'\r\n#]*)", txt, re.M)
    val = m.group(1).strip() if m else ""
    return ("config", val) if val else ("", "")


def status(home: Path, profile: str) -> int:
    target = home / "local-plugins" / DIR_NAME
    pkg = home / "profiles" / profile / "package.json"
    cfg_file = home / "_local" / "tg-account.json"
    print(f"home    : {home}")
    print(f"plugin  : {target} -> {'已存在' if target.exists() else '不存在'}")
    if target.exists() and (target / "package.json").exists():
        try:
            v = read_json(target / "package.json").get("version", "?")
        except Exception:
            v = "读不了"
        print(f"version : {v}")
    node_v = node_major()
    print(f"node    : {'未安装' if not node_v else 'v%d' % node_v}"
          f"{'（<22，胶囊要 node >= 22）' if node_v and node_v < 22 else ''}")
    print(f"profile : {pkg} -> {'存在' if pkg.exists() else '不存在'}")
    if pkg.exists():
        data = read_json(pkg)
        dep = (data.get("dependencies") or {}).get(PLUGIN_NAME)
        bundles = ((data.get("dsh") or {}).get("profile") or {}).get("bundles") or []
        print(f"dependency: {dep or '未登记'}")
        print(f"bundles   : {'已登记' if PLUGIN_NAME in bundles else '未登记'}")
    if cfg_file.exists():
        try:
            print(f"root    : {read_json(cfg_file).get('root') or '空'}")
        except Exception:
            print(f"root    : 读不了（{cfg_file}）")
    else:
        print("root    : 未配置（用内置价、交接卡关掉）")
    src, mr = memory_root_state(home)
    print(f"memory  : {('(' + src + ') ' + mr) if mr else '未配置（交接卡/切会话会生成失败）'}")
    return 0


def ensure_account_cfg(home: Path, apply: bool) -> bool:
    """写 _local/tg-account.json 的 root。用户已填了别的 root 就不覆盖。"""
    cfg_file = home / "_local" / "tg-account.json"
    want = str(REPO).replace(os.sep, "/")
    data: dict = {}
    if cfg_file.exists():
        try:
            data = read_json(cfg_file)
        except Exception:
            data = {}
    have = data.get("root") if isinstance(data, dict) else None
    if have and str(have).replace(os.sep, "/") != want:
        print(f"3) root 已配置为 {have}，保留（要改成 {want} 就手改这个文件）")
        return False
    if have:
        print("3) root 已指向本仓库，跳过")
        return False
    print(f"3) 写 {cfg_file}：root={want}")
    if not apply:
        print("   （dry-run，未写入）")
        return False
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    data = data if isinstance(data, dict) else {}
    data["root"] = want
    cfg_file.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def install(home: Path, profile: str, apply: bool) -> int:
    pkg = home / "profiles" / profile / "package.json"
    target = home / "local-plugins" / DIR_NAME
    if not SRC.exists():
        print(f"[FAIL] 源目录不存在：{SRC}")
        return 1
    if not pkg.exists():
        print(f"[FAIL] profile 不存在：{pkg}")
        print("       官方 desktop 默认 --profile desktop；目录名不同就显式传 --profile <名>")
        return 1

    files = src_files()
    print(f"home={home} profile={profile}")
    print(f"1) 复制 {SRC} -> {target}")
    if same_tree(target, files):
        print("   内容相同，跳过")
    elif not apply:
        print(f"   （dry-run）会写入 {len(files)} 个文件")
    else:
        for f in files:
            rel = f.relative_to(SRC)
            dst = target / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, dst)
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
    if apply and (need_dep or need_bundle):
        deps[PLUGIN_NAME] = link
        if need_bundle:
            bundles.insert(0, PLUGIN_NAME)
        write_json(pkg, data, "account")
        print("   已写入 package.json")
        print("   提示：如仓库用 pnpm 管理，执行 pnpm install 让 link 生效（本机已有 node_modules 软链时通常可跳过）")
    elif not apply:
        print("   （dry-run，未写入）")

    ensure_account_cfg(home, apply)

    node_v = node_major()
    if node_v and node_v < 22:
        print(f"\n[warn] node v{node_v} < 22：胶囊要 node >= 22，升级后再重启。")

    src, mr = memory_root_state(home)
    if not mr:
        print("\n[warn] 没找到 memory_root：交接卡 / 切会话 / 自动切换会「生成失败」，账目不受影响。")
        print('       补一次（二选一），然后重启 DSH：')
        print('         1) python scripts/setup.py --memory-root "<放对话夹的目录>"')
        print('         2) 在仓库 config.yaml 里写 memory_root: "<路径>"（或设环境变量 TG_MEMORY_ROOT）')
    else:
        print(f"\n交接卡记忆根：({src}) {mr}")

    print("\n完成。重启 DSH 后生效（客户端 bundle 要重建）；验证：输入框旁出现账面胶囊，"
          "面板底部不再报「价表用内置默认」。")
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
    print(f"摘除 dependency={has_dep} bundle={has_bundle}（插件目录与 _local/tg-account.json 保留）")
    if apply and (has_dep or has_bundle):
        deps.pop(PLUGIN_NAME, None)
        data["dsh"]["profile"]["bundles"] = [b for b in bundles if b != PLUGIN_NAME]
        write_json(pkg, data, "account")
        print("   已写入 package.json")
    elif not apply:
        print("   （dry-run，未写入）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home")
    ap.add_argument("--config", default="", help="配置文件；默认用仓库自带的 config.yaml")
    ap.add_argument("--profile", default="desktop")
    ap.add_argument("--apply", action="store_true", help="真正写入（默认 dry-run）")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    args = ap.parse_args()

    home = pick_home(args.home, args.config or None)
    if args.status:
        return status(home, args.profile)
    if args.uninstall:
        return uninstall(home, args.profile, args.apply)
    return install(home, args.profile, args.apply)


if __name__ == "__main__":
    sys.exit(main())
