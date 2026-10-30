#!/usr/bin/env python3
"""TG 预检：只读探测冲突，不改任何文件。有冲突时退出码 1。

用法: python preflight.py [--config config.yaml]
"""
import argparse, json, os, shutil, sys

from tg_common import add_common_args, config_base, memory_root_from, resolve_dsh_home, utf8_stdio

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 输出含中文与 JSON：Windows 控制台默认 GBK，固定 UTF-8，否则 --json 会被上游按 UTF-8 解成乱码。
utf8_stdio()

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
            data = y.safe_load(f)
    except UnicodeDecodeError as e:
        print(f"拒绝：{path} 不是 UTF-8（{e}）。\n"
              "Windows 记事本默认可能存成 GBK/ANSI：另存为 UTF-8 再重跑。", file=sys.stderr)
        sys.exit(2)
    except y.YAMLError as e:
        first = str(e).splitlines()[0]
        print(f"拒绝：{path} 不是合法 YAML（{first}）。\n"
              "Windows 路径请用正斜杠 C:/Users/... 或单引号 'C:\\Users\\...'；"
              "双引号里的反斜杠会被当成转义。", file=sys.stderr)
        sys.exit(2)
    except OSError as e:
        print(f"拒绝：{path} 读不了（{e}）。", file=sys.stderr)
        sys.exit(2)
    if data is None:
        return {}
    if not isinstance(data, dict):
        print(f"拒绝：{path} 顶层不是映射（读到 {type(data).__name__}），配置应当是 key: value 形式。",
              file=sys.stderr)
        sys.exit(2)
    return data


def expand(p, base=None):
    s = os.path.expanduser(os.path.expandvars(str(p)))
    if base and not os.path.isabs(s):
        s = os.path.join(base, s)
    return os.path.normpath(s)


def same_installed(name, installed_dir):
    """安装目录里的 SKILL.md 与本包同名技能逐字节一致 → 就是「已经装好了」，不算冲突。

    为什么要比内容：旧版预检把「目录存在」一律当冲突，于是照 README 先拷技能再预检的
    新用户会得到 10 条「同名技能」，看起来像装坏了（2026-10-30 复现）。
    """
    try:
        with open(os.path.join(installed_dir, "SKILL.md"), "rb") as f:
            got = f.read()
        with open(os.path.join(REPO, "skills", name, "SKILL.md"), "rb") as f:
            want = f.read()
        return got == want
    except OSError:
        return False


def main():
    ap = argparse.ArgumentParser()
    add_common_args(ap, json_flag=True)
    a = ap.parse_args()

    cfg = load_yaml(a.config)
    base = config_base(a.config)
    skills_dir = expand(cfg.get("skills_dir") or "~/.dsh/skills", base)
    memory_root = memory_root_from(cfg, base)
    dsh_home = resolve_dsh_home(a.dsh_home, cfg=cfg, base=base)

    # 桌面版的 skills 就在 <home>/skills；配置还留着默认 ~/.dsh/skills 时自动改看它，
    # 否则新用户第一步就「拒绝：技能目录不可用」，而目录其实就在 home 下。
    # 只有该目录真含 tg-* 才回退：空目录/别的技能库不能把「拒绝」伪装成「放行」。
    home_skills = os.path.join(dsh_home, "skills")
    if not os.path.isdir(skills_dir) and any(
            os.path.isdir(os.path.join(home_skills, s)) for s in SKILLS):
        skills_dir = home_skills

    rows, conflicts = [], 0

    def add(kind, detail, action):
        nonlocal conflicts
        conflicts += 1
        rows.append((kind, detail, action))

    # 1. 同名技能：内容一致的是「已安装」，内容不同的才是冲突
    for s in SKILLS:
        p = os.path.join(skills_dir, s)
        if os.path.exists(p):
            if same_installed(s, p):
                rows.append(("已安装技能", p, "内容与本包一致，无需处理"))
            else:
                add("同名技能", p, "内容不同 → 问：覆盖 / 改名 / 跳过")

    # 1b. 缺技能：旧版只看「目录在不在」，于是 skills_dir 指到一个空目录也判「放行」，
    #     和 install.py --check 的 rc=1 自相矛盾（2026-10-30 评测定）。
    #     一个都没装 = 目录指错或还没装 → 待确认；只装了部分 = 提示补齐，不阻断。
    missing = [s for s in SKILLS if not os.path.isdir(os.path.join(skills_dir, s))]
    if len(missing) == len(SKILLS):
        add("缺技能", f"{skills_dir} 里一个 tg-* 都没有",
            "跑 python scripts/install.py（或把 skills_dir 指到已装技能的目录）")
    elif missing:
        rows.append(("技能不全", f"缺 {len(missing)} 个：{'、'.join(missing[:4])}"
                     + (" …" if len(missing) > 4 else ""),
                     "按需补：python scripts/install.py"))

    # 2. 已有 router
    for cand in ("skill-router", "router"):
        if os.path.exists(os.path.join(skills_dir, cand)):
            add("已有路由", os.path.join(skills_dir, cand), "问：并存 / 接管 / 替换")
            break
    # 3. 记忆根（未配置不再当成 ~/tg-memory，直接指路 setup.py）
    if not memory_root:
        rows.append(("记忆根未配置", "config.yaml 没有 memory_root，$TG_MEMORY_ROOT 也没设",
                     "跑 python scripts/setup.py 选一个目录（默认 ~/Documents/tg-memory）"))
    elif os.path.isdir(memory_root) and os.listdir(memory_root):
        entries = [e for e in os.listdir(memory_root) if e != ".DS_Store"]
        # setup.py 刚建的母文件夹只有 README.md + _template：那是安装产物，不是用户数据
        if set(entries) <= {"README.md", "_template"}:
            rows.append(("记忆根已建", memory_root, "只有安装脚手架，无需处理"))
        else:
            add("记忆根非空", f"{memory_root}（{len(entries)} 项）", "问：合并 / 另起目录")

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
    except (Exception, SystemExit) as e:  # 体检失败不影响预检；SystemExit 也要接住
        cache_line = f"跳过（{e.__class__.__name__}: {e}）"

    # 9. 工具 schema 预算（只读，不计入冲突、不改退出码）
    schema_line, schema_info = None, None
    try:
        import schema_budget as sb
        got = None
        for p, cand in sb.pick_session(dsh_home, "", 7, limit=5, strict=False):
            got = sb.collect(p)
            if got:
                break
        if got:
            trows, steps = got
            tot = sum(b for _, b in trows)
            budget, bsrc, prices, rate = sb.load_budget(a.config)
            groups = {}
            for name, b in trows:
                g = sb.group_of(name)
                cur = groups.setdefault(g, {"tools": 0, "bytes": 0})
                cur["tools"] += 1
                cur["bytes"] += b
            top = max(groups.items(), key=lambda kv: kv[1]["bytes"])
            over = max(tot - budget * 1024, 0) if budget else 0
            schema_info = {"schema_kb": round(tot / 1024, 1), "tools": len(trows),
                           "budget_kb": budget, "over_budget_kb": round(over / 1024, 1),
                           "top_group": top[0], "top_group_kb": round(top[1]["bytes"] / 1024, 1)}
            if over:
                schema_line = (f"{tot/1024:.0f} KB / {len(trows)} 个工具，超预算 {over/1024:.0f} KB"
                               f"；最大块 {top[0]} {top[1]['bytes']/1024:.0f} KB"
                               f" → python scripts/schema_budget.py 看归因")
            else:
                schema_line = (f"{tot/1024:.0f} KB / {len(trows)} 个工具，余量 {(budget-tot/1024):.0f} KB"
                               f"；最大块 {top[0]} {top[1]['bytes']/1024:.0f} KB")
        else:
            schema_line = "无可用会话日志：正常用一天再看；或 python scripts/schema_budget.py --session <id>"
    except (Exception, SystemExit) as e:  # 体检失败不影响预检；pick_session 会 sys.exit
        schema_line = f"跳过（{e.__class__.__name__}: {e}）"

    # 准入判定：放行 0 / 待确认 1 / 拒绝 2
    blocked = not os.path.isdir(skills_dir)
    verdict = "拒绝" if blocked else ("待确认" if conflicts else "放行")
    exit_code = 2 if blocked else (1 if conflicts else 0)

    if a.json:
        print(json.dumps({
            "verdict": verdict, "exit_code": exit_code, "conflicts": conflicts,
            "rows": [{"kind": k, "detail": d, "action": act} for k, d, act in rows],
            "cache": cache_line, "schema": schema_line, "schema_detail": schema_info,
            "skills_dir": skills_dir,
        }, ensure_ascii=False, indent=2))
        sys.exit(exit_code)

    def health():
        if cache_line:
            print(f"\n缓存体检（只读，不阻断）：\n  {cache_line}")
        if schema_line:
            print(f"\nschema 预算（只读，不阻断）：\n  {schema_line}")

    # 输出
    print("TG 准入预检（只读，未修改任何文件）\n")
    if blocked:
        print(f"拒绝：技能目录不可用 {skills_dir}")
        print("判定：拒绝（退出码 2）。不绕路、不提权，先修好环境。")
        sys.exit(2)
    if not rows:
        print("判定：放行（退出码 0）。")
        health()
        return
    w1 = max(len(r[0]) for r in rows) + 2
    w2 = min(60, max(len(r[1]) for r in rows) + 2)
    for k, d, act in rows:
        print(f"  {k:<{w1}} {d[:w2]:<{w2}} → {act}")
    if conflicts:
        print(f"判定：待确认（退出码 1）。{conflicts} 项待确认，逐项裁决后再动手。")
        health()
        sys.exit(1)
    health()
    print("\n判定：放行（退出码 0），按提示补齐即可。")


if __name__ == "__main__":
    main()
