#!/usr/bin/env python3
"""新建对话夹：python new_session.py <workspace> <session-id> [标题] [--root DIR]"""
import argparse, datetime, os, shutil

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
    ap.add_argument("--root", default=os.path.expanduser("~/tg-memory"))
    a = ap.parse_args()
    title = a.title or a.session_id
    dst = os.path.join(os.path.expanduser(a.root), a.workspace, a.session_id)
    if os.path.exists(dst):
        print("已存在:", dst); return
    os.makedirs(os.path.join(dst, "30-logs"), exist_ok=True)
    for rel, tpl in FILES.items():
        p = os.path.join(dst, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w", encoding="utf-8").write(tpl.format(title=title, sid=a.session_id,
                                                        ws=a.workspace, date=datetime.date.today().isoformat()))
    print("created:", dst)

if __name__ == "__main__":
    main()
