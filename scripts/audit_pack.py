#!/usr/bin/env python3
"""TG 包自检：结构 / frontmatter / 交叉引用 / 陈旧词 / 可移植性。

用法：
  python audit_pack.py                    # 只查本包（脚本所在项目）
  python audit_pack.py --also <另一项目>
"""
import argparse, os, re, subprocess, sys

STALE = ["Metron", "Tessera", "metron", "tessera", "tg-guard", "metron-", "90% 规则"]


def read(p):
    try:
        return open(p, encoding="utf-8").read()
    except Exception:
        return ""


def walk(root):
    out = []
    for dp, _, fs in os.walk(root):
        if any(x in dp for x in ("_history", "_archive", "node_modules", ".git")):
            continue
        for f in fs:
            out.append(os.path.join(dp, f))
    return out


def check(label, root, issues):
    if not os.path.isdir(root):
        issues.append((label, "高", f"项目目录不存在: {root}"))
        return 0
    files = walk(root)

    for f in files:
        if os.path.basename(f) != "SKILL.md":
            continue
        s = read(f)
        m = re.match(r"^---\n(.*?)\n---", s, re.S)
        rel = os.path.relpath(f, root)
        if not m:
            issues.append((label, "高", f"frontmatter 缺失/不闭合: {rel}"))
            continue
        fm = m.group(1)
        name = re.search(r"^name:\s*(\S+)", fm, re.M)
        desc = re.search(r"^description:\s*(.+)$", fm, re.M)
        dirname = os.path.basename(os.path.dirname(f))
        if not name:
            issues.append((label, "高", f"缺 name: {rel}"))
        elif name.group(1).strip('"') != dirname:
            issues.append((label, "中", f"name({name.group(1)}) ≠ 目录名({dirname})"))
        if not desc:
            issues.append((label, "高", f"缺 description: {rel}"))
        else:
            d = desc.group(1).strip().strip('"')
            if len(d) > 200:
                issues.append((label, "中", f"description 超 200 字符（{len(d)}）: {rel}"))

    for f in files:
        if not f.endswith((".md", ".py", ".yaml", ".yml")):
            continue
        base = os.path.basename(f)
        rel = os.path.relpath(f, root)
        if base == "audit_pack.py":
            continue  # 检查器自身包含关键词表，跳过
        s = read(f)
        if base not in ("CHANGELOG.md", "principles.md"):
            # 版本表行（| v0.3 | ...）允许提及旧名，属历史沿革
            lines = [ln for ln in s.splitlines() if not re.match(r"\|\s*v\d", ln)]
            body = chr(10).join(lines)
            for w in STALE:
                if w in body:
                    issues.append((label, "中", f"陈旧词 '{w}' 出现在 {rel}"))
        # CHANGELOG 是历史：条目里提旧文件名属当时事实，不追改（合并见 v0.10.16）
        if base != "CHANGELOG.md":
            for ref in re.findall(r"`(docs/[A-Za-z0-9_\-./]+|scripts/[A-Za-z0-9_\-./]+)`", s):
                here = os.path.dirname(f)
                if not (os.path.exists(os.path.join(here, ref)) or os.path.exists(os.path.join(root, ref))):
                    issues.append((label, "中", f"{rel} 引用不存在的 {ref}"))
        local_ids = [v for v in (os.environ.get("USERNAME"), os.environ.get("USER"),
                                 os.environ.get("COMPUTERNAME")) if v and len(v) > 2]
        if local_ids and any(v in s for v in local_ids):
            issues.append((label, "高", f"含本机用户名/主机名: {rel}"))
        if (label == "TG" and base != "CHANGELOG.md"
                and re.search(r"D:\\?AI", s) and "portable-vs-local" not in f):
            issues.append((label, "中", f"TG 出现绝对路径: {rel}"))

    # README 技能清单 vs 实际目录
    sk = os.path.join(root, "skills")
    if os.path.isdir(sk):
        actual = {d for d in os.listdir(sk) if os.path.isdir(os.path.join(sk, d)) and d != "__pycache__"}
        rd = read(os.path.join(root, "README.md"))
        listed = set(re.findall(r"`([a-z][a-z0-9\-]+)`", rd)) & actual
        missing = actual - listed
        if missing:
            issues.append((label, "中", f"README 未列出技能: {sorted(missing)}"))
    return len(files)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--also", action="append", default=[], help="顺带检查另一个项目目录")
    a = ap.parse_args()

    issues = []
    n = check("TG", a.root, issues)
    print(f"TG: {a.root}  （{n} 个文件）")
    for extra in a.also:
        n2 = check(os.path.basename(extra.rstrip("\\/")).upper(), extra, issues)
        print(f"{os.path.basename(extra)}: {extra}  （{n2} 个文件）")

    # 数字单一真相：文档表格必须与 data/metrics.json 一致
    gm = os.path.join(a.root, "scripts", "gen_metrics.py")
    if os.path.isfile(gm):
        r = subprocess.run([sys.executable, gm, "--check", "--root", a.root],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            issues.append(("TG", "中", "文档数字与 data/metrics.json 不一致（跑 gen_metrics.py --write）"))

    print()
    if not issues:
        print("OK 未发现问题")
        return
    for label, sev, msg in issues:
        print(f"[{label}] {sev}  {msg}")
    high = sum(1 for i in issues if i[1] == "高")
    print(f"\n合计 {len(issues)} 条：高 {high} / 中 {len(issues) - high}")
    sys.exit(1 if high else 0)


if __name__ == "__main__":
    main()
