#!/usr/bin/env python3
"""TG 预检：只读探测冲突，不改任何文件。有冲突时退出码 1。

用法: python preflight.py [--config config.yaml]
"""
import argparse, json, os, shutil, sys

SKILLS = ["tg-router", "tg-cost", "tg-context", "tg-memory", "tg-memlib",
          "tg-orchestrate", "tg-gui", "tg-isolate", "tg-ops", "tg-loop"]


def load_yaml(path):
    try:
        import yaml
    except ImportError:
        print("拒绝：缺少 pyyaml。先 pip install pyyaml 再重跑——"
              "否则会退回默认 ~/.dsh/skills，查的不是你配置的目录。", file=sys.stderr)
        sys.exit(2)
    if not os.path.isfile(path):
        return {}
    import yaml as y
    try:
        with open(path, encoding="utf-8") as f:
            return y.safe_load(f) or {}
    except y.YAMLError as e:
        first = str(e).splitlines()[0]
        print(f"拒绝：{path} 不是合法 YAML（{first}）。\n"
              "Windows 路径请用正斜杠 C:/Users/... 或单引号 'C:\\Users\\...'；"
              "双引号里的反斜杠会被当成转义。", file=sys.stderr)
        sys.exit(2)


def expand(p, base=None):
    s = os.path.expanduser(os.path.expandvars(str(p)))
    if base and not os.path.isabs(s):
        s = os.path.join(base, s)
    return os.path.normpath(s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    a = ap.parse_args()

    cfg = load_yaml(a.config)
    base = os.path.dirname(os.path.abspath(a.config)) if os.path.isfile(a.config) else os.getcwd()
    skills_dir = expand(cfg.get("skills_dir") or "~/.dsh/skills", base)
    memory_root = expand(cfg.get("memory_root") or "~/tg-memory", base)
    dsh_home = expand(cfg.get("dsh_home") or "~/.dsh", base)

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
    for cand in ("skill-router", "router"):
        if os.path.exists(os.path.join(skills_dir, cand)):
            add("已有路由", os.path.join(skills_dir, cand), "问：并存 / 接管 / 替换")
            break
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
            if ".bak" in prof or prof.endswith("-backup") or prof.startswith("backup"):
                continue  # 备份目录，不是生效配置
            pp = os.path.join(dsh_home, "profiles", prof, "cordis.patch.yml")
            if not os.path.isfile(pp):
                continue
            txt = open(pp, encoding="utf-8", errors="ignore").read()
            for tag in ("spill-policy", "compaction-basic", "tool-result-pruner",
                        "preset-standard", "preset-gui", "preset-ptc", "preset-cordis"):
                if f"id: {tag}" in txt:
                    hits.append(f"{prof}:{tag}")
    if hits:
        add("补丁条目已存在", ", ".join(sorted(set(hits))[:6]), "问：合并 / 冲突项交给你手改")

    # 6. 覆盖块漂移：patch 里覆盖的 preset 与组合文件里实际的 preset 对不上
    if os.path.isdir(dsh_home):
        prof_dir = os.path.join(dsh_home, "profiles")
        for prof in (os.listdir(prof_dir) if os.path.isdir(prof_dir) else []):
            if ".bak" in prof or prof.endswith("-backup") or prof.startswith("backup"):
                continue  # 备份目录，不是生效配置
            pp = os.path.join(dsh_home, "profiles", prof, "cordis.patch.yml")
            cp = os.path.join(dsh_home, "profiles", prof, "cordis.yml")
            if not (os.path.isfile(pp) and os.path.isfile(cp)):
                continue
            import re
            covered = set(re.findall(r"^\s*-\s*id:\s*(preset-\S+)", open(pp, encoding="utf-8", errors="ignore").read(), re.M))
            actual = set(re.findall(r"^\s*-\s*id:\s*(preset-\S+)", open(cp, encoding="utf-8", errors="ignore").read(), re.M))
            gone = sorted(covered - actual)
            extra = sorted(actual - covered) if covered else []
            if gone:
                add("覆盖块漂移", f"{prof}: 覆盖了已不存在的 {', '.join(gone)}", "升级后重跑 gen_preset_override.py")
            if extra:
                add("未覆盖的 preset", f"{prof}: {', '.join(extra)} 没有整段覆盖", "问：要不要一并覆盖")

            # 7. 插件名漂移：patch 引用的包不在组合文件里 → 升级后很可能启动失败
            pkg_patch = set(re.findall(r"^\s*name:\s*['\"]?(@[^'\"\s]+)", open(pp, encoding="utf-8", errors="ignore").read(), re.M))
            pkg_comp = set(re.findall(r"^\s*name:\s*['\"]?(@[^'\"\s]+)", open(cp, encoding="utf-8", errors="ignore").read(), re.M))
            lost = sorted(pkg_patch - pkg_comp)
            if lost:
                add("插件名漂移", f"{prof}: {', '.join(lost[:4])} 不在组合文件里", "升级可能删/改名了插件，先看启动日志再改 patch")

    # 8. 缓存体检（只读，不计入冲突、不改退出码）
    cache_line = None
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from types import SimpleNamespace
        from cache_audit import collect_ledgers
        prices = cfg.get("prices_usd_per_mtok") or {}
        p_in = float(prices.get("input") or 0.15)
        p_cache = float(prices.get("cached") or 0.003)
        merged, _found = collect_ledgers(SimpleNamespace(config=a.config, home=None, ledger=None))
        if merged:
            keys = sorted(merged)[-7:]
            tin = sum(merged[k]["input"] for k in keys)
            tc = sum(merged[k]["cacheRead"] for k in keys)
            calls = sum(merged[k]["calls"] for k in keys)
            ctx = tin + tc
            hit = tc / ctx if ctx else 0.0
            miss = tin / calls if calls else 0.0
            k = p_in / p_cache if p_cache else 0.0
            mshare = (tin * p_in) / (tin * p_in + tc * p_cache) if ctx else 0.0
            if k >= 8 and miss > 800:
                verdict = "值得做 → 先停自动改写历史（关 pruner），再用 cache_audit 看 3 天"
            elif k >= 3:
                verdict = "边际 → 先缩前缀与步数（tg-context），别追命中率"
            else:
                verdict = "不值得做 → 价差太小，命中率再高也省不到 3%"
            cache_line = (f"命中率 {hit * 100:.1f}% | 未命中 {miss:.0f}/步 | "
                          f"每步上下文 {ctx / calls / 1000:.0f}k | k={k:.1f} | "
                          f"未命中占成本 {mshare * 100:.0f}% → {verdict}")
        else:
            cache_line = "无账本：正常用一天再看；或 python scripts/cache_audit.py --jsonl <会话日志>"
    except Exception as e:  # 体检失败不影响预检
        cache_line = f"跳过（{e.__class__.__name__}: {e}）"

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
        if cache_line:
            print(f"\n缓存体检（只读，不阻断）：\n  {cache_line}")
        return
    w1 = max(len(r[0]) for r in rows) + 2
    w2 = min(60, max(len(r[1]) for r in rows) + 2)
    for k, d, act in rows:
        print(f"  {k:<{w1}} {d[:w2]:<{w2}} → {act}")
    if conflicts:
        print(f"判定：待确认（退出码 1）。{conflicts} 项冲突，逐项裁决后再动手。")
        if cache_line:
            print(f"\n缓存体检（只读，不阻断）：\n  {cache_line}")
        sys.exit(1)
    if cache_line:
        print(f"\n缓存体检（只读，不阻断）：\n  {cache_line}")
    print("\n判定：放行（退出码 0），按提示补齐即可。")


if __name__ == "__main__":
    main()
