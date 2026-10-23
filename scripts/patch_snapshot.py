#!/usr/bin/env python3
"""补丁快照与回滚：改坏 cordis.patch.yml 时一条命令回到上一版。

用法：
  python patch_snapshot.py --config config.yaml --snapshot
  python patch_snapshot.py --config config.yaml --list
  python patch_snapshot.py --config config.yaml --restore latest
  python patch_snapshot.py --config config.yaml --diff latest

快照存放：<dsh_home>/backup/patch/cordis.patch-YYYYMMDD-HHMMSS.yml
回滚前会先给当前文件拍一张，再校验快照 YAML 合法才写入。
"""
import argparse, datetime, os, shutil, sys

from tg_common import config_base, load_config, resolve_dsh_home, utf8_stdio

# Windows 控制台默认 GBK：固定 UTF-8，避免中文报告/JSON 在无 PYTHONIOENCODING 的环境里乱码或崩。
utf8_stdio()

STORE = ("backup", "patch")


def load_cfg(path):
    """配置交给 tg_common：坏 YAML / 编码坏 / 顶层不是映射都报错，不静默换 home 去拍错补丁。"""
    cfg, _base = load_config(path)
    return cfg


def paths(cfg, home_arg, profile, config_path="config.yaml"):
    home = resolve_dsh_home(home_arg, cfg=cfg, base=config_base(config_path))
    patch = os.path.join(home, "profiles", profile, "cordis.patch.yml")
    store = os.path.join(home, *STORE)
    return patch, store


def snapshots(store):
    if not os.path.isdir(store):
        return []
    return sorted(f for f in os.listdir(store) if f.endswith(".yml"))


def take(patch, store, label=""):
    if not os.path.isfile(patch):
        sys.exit(f"找不到补丁文件: {patch}")
    os.makedirs(store, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"cordis.patch-{ts}{('-' + label) if label else ''}.yml"
    dst = os.path.join(store, name)
    shutil.copy2(patch, dst)
    return name, dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--home", default="")
    ap.add_argument("--profile", default="desktop")
    ap.add_argument("--snapshot", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--restore", default="", help="快照名或 latest")
    ap.add_argument("--diff", default="", help="与当前文件对比：快照名或 latest")
    a = ap.parse_args()

    cfg = load_cfg(a.config)
    patch, store = paths(cfg, a.home, a.profile, a.config)

    if a.snapshot:
        name, dst = take(patch, store)
        print(f"已拍快照: {dst}")
        return

    if a.list:
        items = snapshots(store)
        if not items:
            print(f"还没有快照：{store}")
            return
        print(f"{'快照':<44}{'大小':>8}")
        for f in items:
            p = os.path.join(store, f)
            print(f"{f:<44}{os.path.getsize(p)/1024:>7.1f}k")
        print(f"\n共 {len(items)} 个；回滚用 --restore latest 或指定文件名。")
        return

    if a.restore:
        items = snapshots(store)
        if not items:
            sys.exit(f"没有可回滚的快照：{store}")
        name = items[-1] if a.restore == "latest" else a.restore
        src = os.path.join(store, name)
        if not os.path.isfile(src):
            sys.exit(f"快照不存在: {name}")
        try:
            import yaml
            yaml.safe_load(open(src, encoding="utf-8"))
        except ImportError:
            pass
        except Exception as e:
            sys.exit(f"快照不是合法 YAML，拒绝回滚: {str(e).splitlines()[0]}")
        backup, dst = take(patch, store, "before-restore")
        shutil.copy2(src, patch)
        print(f"已回滚到 {name}\n  回滚前状态已备份: {dst}\n  重启 DSH 后生效。")
        return

    if a.diff:
        import difflib
        items = snapshots(store)
        if not items:
            sys.exit(f"没有快照：{store}")
        name = items[-1] if a.diff == "latest" else a.diff
        src = os.path.join(store, name)
        if not os.path.isfile(src):
            sys.exit(f"快照不存在: {name}")
        d = difflib.unified_diff(open(src, encoding="utf-8").read().splitlines(),
                                 open(patch, encoding="utf-8").read().splitlines(),
                                 fromfile=f"snapshot:{name}", tofile="当前 patch", lineterm="")
        out = "\n".join(d)
        print(out if out.strip() else "（快照与当前文件一致）")
        return

    print("用法：--snapshot / --list / --restore <名|latest> / --diff <名|latest>")


if __name__ == "__main__":
    main()
