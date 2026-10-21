#!/usr/bin/env python3
"""TG 预检：只读探测冲突，不改任何文件。有冲突时退出码 1。

用法: python preflight.py [--config config.yaml]
"""
import argparse, json, os, shutil, sys

SKILLS = ["tg-router", "tg-cost", "tg-context", "tg-memory",
          "tg-orchestrate", "tg-gui", "tg-ops"]


def load_yaml(path):
    try:
        import yaml
    except ImportError:
        print("! 需要 pyyaml：pip install pyyaml", file=sys.stderr)
        return {}
    if not os.path.isfile(path):
        return {}
    import yaml as y
    with open(path, encoding="utf-8") as f:
        return y.safe_load(f) or {}


def expand(p):
    return os.path.expanduser(os.path.expandvars(str(p)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    a = ap.parse_args()

    cfg = load_yaml(a.config)
    skills_dir = expand(cfg.get("skills_dir") or "~/.dsh/skills")
    memory_root = expand(cfg.get("memory_root") or "~/tg-memory")
    dsh_home = expand(cfg.get("dsh_home") or "~/.dsh")

    rows, conflicts = [], 0

    def add(kind, detail, action):
        nonlocal conflicts
        conflicts += 1
        rows.append((kind, detail, action))

    # 1. 同名技能
    for s in SKILLS:
        p = os.path.join(skills_dir, s)
        if os.path.exists(p):
            add("同名技能", p, "问：覆盖 / 改名 / 跳过")

    # 2. 已有 router
    for cand in ("skill-router", "router", "skillq"):
        if os.path.exists(os.path.join(skills_dir, cand)):
            add("已有路由", os.path.join(skills_dir, cand), "问：并存 / 接管 / 替换")
            break
    if shutil.which("skillq"):
        rows.append(("已有路由工具", "PATH 中有 skillq", "可并存：tg-router 会优先用它"))

    # 3. 记忆根
    if os.path.isdir(memory_root) and os.listdir(memory_root):
        add("记忆根非空", f"{memory_root}（{len(os.listdir(memory_root))} 项）",
            "问：合并 / 另起目录")

    # 4. 配置
    if os.path.isfile(a.config):
        rows.append(("已有配置", a.config, "已在使用；改动前先备份"))
    elif os.path.isfile("config.example.yaml"):
        rows.append(("配置", "未生成 config.yaml", "复制 config.example.yaml 再改两行"))

    # 5. 补丁条目
    hits = []
    if os.path.isdir(dsh_home):
        prof_dir = os.path.join(dsh_home, "profiles")
        for prof in (os.listdir(prof_dir) if os.path.isdir(prof_dir) else []):
            pp = os.path.join(dsh_home, "profiles", prof, "cordis.patch.yml")
            if not os.path.isfile(pp):
                continue
            txt = open(pp, encoding="utf-8", errors="ignore").read()
            for tag in ("spill-policy", "compaction-basic", "tool-result-pruner",
                        "preset-standard", "preset-gui"):
                if f"id: {tag}" in txt:
                    hits.append(f"{prof}:{tag}")
    if hits:
        add("补丁条目已存在", ", ".join(sorted(set(hits))[:6]), "问：合并 / 冲突项交给你手改")

    # 准入判定：放行 0 / 待确认 1 / 拒绝 2
    blocked = not os.path.isdir(skills_dir)
    # 输出
    print("TG 准入预检（只读，未修改任何文件）\n")
    if blocked:
        print(f"拒绝：技能目录不可用 {skills_dir}")
        print("判定：拒绝（退出码 2）。不绕路、不提权，先修好环境。")
        sys.exit(2)
    if not rows:
        print("判定：放行（退出码 0）。")
        return
    w1 = max(len(r[0]) for r in rows) + 2
    w2 = min(60, max(len(r[1]) for r in rows) + 2)
    for k, d, act in rows:
        print(f"  {k:<{w1}} {d[:w2]:<{w2}} → {act}")
    if conflicts:
        print(f"判定：待确认（退出码 1）。{conflicts} 项冲突，逐项裁决后再动手。")
        sys.exit(1)
    print("\n判定：放行（退出码 0），按提示补齐即可。")


if __name__ == "__main__":
    main()
