#!/usr/bin/env python3
"""新建对话夹：python new_session.py <workspace> <session-id> [标题] [--root DIR] [--config config.yaml]

不给 --root 时先读 config.yaml 的 memory_root，再读 $TG_MEMORY_ROOT；都没配就问宿主放在哪
（非交互终端则报错并给命令）——不再静默落到 ~/tg-memory。
"""
import argparse, datetime, os, sys

from tg_common import (MemoryRootUnset, ensure_memory_root, expand, load_config,
                       utf8_stdio)

utf8_stdio()


FILES = {
    "00-card.md": "# 会话卡 · {title}\n\n- 会话：{sid}\n- 工作区：{ws}\n- 开始：{date}\n- 状态：进行中\n\n## 目标\n- \n\n## 当前进度\n- [ ]\n\n## 下一步\n- [ ]\n\n## 关键路径\n- 产出：\n- 日志：30-logs/\n",
    "10-outline.md": "# 大纲 · {title}\n\n1. \n",
    "20-decisions.md": "# 决策与结论 · {title}\n\n| 日期 | 决策 | 理由 | 影响 |\n|---|---|---|---|\n",
    "40-context.md": "# 压缩摘要 · {title}\n\n## 已确立的事实\n- \n\n## 指针\n- 日志：30-logs/\n",
    "90-links.md": "# 对外链接 · {title}\n\n- 产出：\n",
    "30-logs/README.md": "原始输出放这里。正文只保留路径。\n",
}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workspace"); ap.add_argument("session_id"); ap.add_argument("title", nargs="?", default=None)
    ap.add_argument("--root", default=None, help="对话夹根；缺省读 config.yaml 的 memory_root")
    ap.add_argument("--config", default="config.yaml")
    a = ap.parse_args()
    title = a.title or a.session_id
    root = a.root
    if not root:
        cfg, base = load_config(a.config)
        try:
            root, _info = ensure_memory_root(cfg, base, config_path=a.config)
        except MemoryRootUnset as e:
            sys.exit(str(e))
    root_x = os.path.expanduser(os.path.expandvars(str(root)))
    if root_x.startswith("~"):
        # expanduser 展不开（HOME/USERPROFILE 都没设）时它会原样返回 ~，继续跑会在 cwd 建字面 ~ 目录
        sys.exit(f"无法展开 {root}：HOME / USERPROFILE 都没设置。\n"
                 "用 --root 指定绝对路径，或设好 HOME 再跑。")
    dst = os.path.join(root_x, a.workspace, a.session_id)
    if os.path.exists(dst):
        print("已存在:", dst); return
    # 先确认根能建目录：根是文件、盘不存在、无权限时给一句话，不留半截目录
    if os.path.exists(root_x) and not os.path.isdir(root_x):
        sys.exit(f"--root / memory_root 指向的是文件而不是目录: {root_x}")
    parent = os.path.dirname(dst) or "."
    try:
        os.makedirs(os.path.join(dst, "30-logs"), exist_ok=True)
    except OSError as e:
        sys.exit(f"建不了目录 {dst}（{e.strerror or e}）。\n"
                 f"检查：{parent} 是否可写、盘符是否存在、路径是否过长（Windows 260 字符限制）。")
    for rel, tpl in FILES.items():
        p = os.path.join(dst, rel)
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(tpl.format(title=title, sid=a.session_id,
                                   ws=a.workspace, date=datetime.date.today().isoformat()))
        except OSError as e:
            sys.exit(f"写不了 {p}（{e.strerror or e}）。")
    print("created:", dst)

if __name__ == "__main__":
    main()
