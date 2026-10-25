#!/usr/bin/env python3
"""安装向导：建「对话夹母文件夹」，并问宿主放在哪。

为什么单列：以前没有配置时脚本会静默落到 `~/tg-memory`，把「路径配错 / 忘配」伪装成
「用户没配置」——2026-10-25 就是这样把交接卡写进了 C:\\Users\\<user>\\tg-memory。现在
未配置一律要求显式选一个位置，选中后写回 config.yaml，之后所有脚本共用同一份口径。

用法：
    python scripts/setup.py                      # 交互：问放在哪（默认 ~/Documents/tg-memory）
    python scripts/setup.py --yes                # 不问，直接用默认路径（适合脚本化安装）
    python scripts/setup.py --memory-root D:/x   # 指定路径（CI / 无人值守）
    python scripts/setup.py --show               # 只打印现状，不建目录、不改配置

退出码：0 成功 / 1 出错 / 2 需要交互但当前不是终端（非交互场景请给 --memory-root 或 --yes）
"""
import argparse
import os
import sys

from tg_common import (DEFAULT_CONFIG, MemoryRootUnset, default_memory_root,
                       ensure_memory_root, is_interactive, load_config,
                       memory_root_from, utf8_stdio, write_memory_root)

utf8_stdio()


def main():
    ap = argparse.ArgumentParser(description="建对话夹母文件夹并写进 config.yaml")
    ap.add_argument("--config", default=DEFAULT_CONFIG,
                    help=f"配置文件，默认 {DEFAULT_CONFIG}（不存在时按 config.example.yaml 生成）")
    ap.add_argument("--memory-root", default="", help="直接指定母文件夹路径（不交互）")
    ap.add_argument("--yes", action="store_true", help="不问，直接用默认路径")
    ap.add_argument("--show", action="store_true", help="只打印当前解析结果")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args()

    cfg, base = load_config(a.config)          # 配置写坏直接退出（与其它脚本同口径）

    if a.show:
        cur = memory_root_from(cfg, base)
        env = os.environ.get("TG_MEMORY_ROOT") or ""
        if a.json:
            import json
            print(json.dumps({"config": a.config, "memory_root": cur,
                              "exists": bool(cur) and os.path.isdir(cur),
                              "env_TG_MEMORY_ROOT": env,
                              "default": default_memory_root()},
                             ensure_ascii=False, indent=2))
            return 0
        print(f"config      : {a.config}")
        print(f"memory_root : {cur or '(未配置)'}")
        print(f"存在        : {os.path.isdir(cur) if cur else False}")
        print(f"env         : {env or '(未设置)'}")
        print(f"默认建议    : {default_memory_root()}")
        return 0

    cfg2 = dict(cfg) if isinstance(cfg, dict) else {}
    if a.memory_root:
        cfg2["memory_root"] = a.memory_root
    elif a.yes and not memory_root_from(cfg, base):
        # --yes 只表示「别问我」：已经配过就沿用，别把用户配的路径覆盖成默认值
        cfg2["memory_root"] = default_memory_root()
    elif not memory_root_from(cfg, base) and not is_interactive():
        print("需要选一个位置，但当前不是可交互终端。给一个参数再跑：", file=sys.stderr)
        print('  python scripts/setup.py --memory-root "<路径>"', file=sys.stderr)
        print("  python scripts/setup.py --yes            # 用默认 "
              + default_memory_root(), file=sys.stderr)
        return 2

    try:
        root, info = ensure_memory_root(cfg2, base, config_path=a.config)
    except MemoryRootUnset as e:
        print(str(e), file=sys.stderr)
        return 2
    except OSError as e:
        print(f"建不了目录（{e.strerror or e}）；换个路径或检查盘符/权限。", file=sys.stderr)
        return 1

    # 值来自命令行 / --yes 时也要落盘：否则下次跑还是「未配置」，等于白选
    explicit = bool(a.memory_root) or (a.yes and not memory_root_from(cfg, base))
    if explicit:
        try:
            write_memory_root(a.config, root)
            info["persisted"] = True
        except OSError as e:
            print(f"[warn] 写不进 {a.config}：{e}（本次仍用 {root}）", file=sys.stderr)

    if a.json:
        import json
        print(json.dumps({"memory_root": root, "source": info["source"],
                          "created": info["created"], "config": a.config},
                         ensure_ascii=False, indent=2))
        return 0

    print()
    print(f"母文件夹：{root}")
    print(f"  {'新建' if info['created'] else '已存在'}"
          f"（来源：{'config.yaml' if info['source'] == 'config' else '环境变量' if info['source'] == 'env' else '本次选择'}）")
    print(f"  结构文件：{os.path.join(root, 'README.md')}、{os.path.join(root, '_template')}")
    if info.get("persisted") or info["source"] in ("prompt", "env"):
        print(f"  已写入：{os.path.abspath(a.config)}")
    print()
    print("下一步：")
    print("  python scripts/preflight.py --config config.yaml     # 只读预检")
    print('  python scripts/new_session.py <workspace> <sid> "标题"  # 手建一个对话夹')
    return 0


if __name__ == "__main__":
    sys.exit(main())
