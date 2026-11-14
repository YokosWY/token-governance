#!/usr/bin/env python3
"""TG 包自检：结构 / frontmatter / 交叉引用 / 陈旧词 / 可移植性。

用法：
  python audit_pack.py                    # 只查本包（脚本所在项目）
  python audit_pack.py --also <另一项目>
"""
import argparse, difflib, fnmatch, json, os, re, subprocess, sys

from tg_common import utf8_stdio

# 输出含中文，Windows 控制台默认 GBK：固定成 UTF-8，避免打包到没有 PYTHONIOENCODING 的环境就崩。
utf8_stdio()

STALE = ["Metron", "Tessera", "metron", "tessera", "tg-guard", "metron-", "90% 规则"]


def read(p):
    try:
        return open(p, encoding="utf-8").read()
    except Exception:
        return ""


SKIP_DIRS = ("_history", "_archive", "node_modules", ".git", "__pycache__")
LOCAL_IDS = []  # 由 main() 按 --local-id / 环境变量填充

# .gitignore 里声明「不发布」的本机文件：它们本来就该含本机路径，扫了只会淹没有效告警。
# 目录前缀 / 文件前缀匹配（config.yaml、data/ledger.json*、_local/*）。
LOCAL_ONLY = ("config.yaml", "data/ledger.json", "_local/", "_forge/", "tg-baseline.json")


def _is_backup(name):
    """备份文件：xxx.bak / xxx.bak-20261022-194451 / xxx.bak_key（不误伤 bakery.py）。"""
    return name.endswith(".bak") or ".bak-" in name or ".bak_" in name


def walk(root):
    out = []
    for dp, dirs, fs in os.walk(root):
        # 按目录名比较：用子串判断会把 .github 当成 .git 整棵跳过（ci.yml 从没被扫过）
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in fs:
            if f.endswith((".pyc", ".pyo")) or _is_backup(f):
                continue
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
        # 扫描面必须覆盖真正会带路径的文件类型：.mjs/.js 里同样会出现本机绝对路径，
        # 原先只扫 .md/.py/.yaml/.yml，`scripts/verify_guard.mjs` 的 `D:/AI/...` 就是这样逃过门禁的。
        if not f.endswith((".md", ".py", ".yaml", ".yml", ".mjs", ".js",
                           ".cmd", ".sh", ".ps1", ".json")):
            continue
        base = os.path.basename(f)
        rel = os.path.relpath(f, root)
        rel_c = rel.replace(os.sep, "/")
        if any(rel_c == p or rel_c.startswith(p) for p in LOCAL_ONLY):
            continue  # .gitignore 里声明不发布的本机文件：本来就含本机路径
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
        # 本机标识泄露检查：只认「路径形状」出现（/home/<id>/、/Users/<id>/、Users\<id>\），
        # 不做裸子串——USER=user/test/dev 这类常见词会让 Linux/macOS 上一片「高」误报。
        # --local-id 可显式指定要查的标识（默认取 USERNAME/USER/COMPUTERNAME）。
        for lid in LOCAL_IDS:
            pat = re.compile(
                r"(?:/home/|/Users/|[A-Za-z]:[\\/]+Users[\\/]+)" + re.escape(lid) + r"(?=[/\\\s\"'`)]|$)",
                re.IGNORECASE)
            if pat.search(s):
                issues.append((label, "高", f"路径里含本机用户名/主机名: {rel}"))
        # 绝对路径：正反斜杠都要认。旧写法 `D:\\?AI` 只匹配反斜杠形式，
        # 于是 `D:/AI/DSH-NEXT/local-plugins/...`（正斜杠，JS 里最常见）永远抓不到。
        # 只盯本机专有目录名，不抓通用盘符路径（`C:/Users/...`、`C:/dsh-next` 是合理示例）。
        if (label == "TG" and base != "CHANGELOG.md"
                and re.search(r"[A-Za-z]:[\\/]+(?:AI|DSH-NEXT|tg-memory)\b", s, re.I)
                and "portable-vs-local" not in f):
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


# 与断言数绑定的表述：README 的目录树与上手命令、CONTRIBUTING 的提交前自查、
# CI 的回归步骤名。这些 N 必须等于 data/counts.json 的 tests。
ASSERTION_COUNT_RE = re.compile(
    r"(\d+)\s*(?:项断言|项回归|assertions?)|回归测试[（(]\s*(\d+)\s*项\s*[）)]")
ASSERTION_COUNT_FILES = (
    "README.md", "README_EN.md", "CONTRIBUTING.md", ".github/workflows/ci.yml",
)


def _is_python_test_count(line):
    """这一行写的是不是 Python 回归套件的断言数。

    两类「数字不等于 tests 却正确」的表述要排除：
      1. 版本表行（`| v1.6.0 | ...`）：写的是当次发布的真值，属历史，不追改；
      2. 账目胶囊的 155 项 Node 断言：另一套真值，由插件自己的测试守。
    """
    if re.match(r"\s*\|\s*v\d", line):
        return False
    low = line.lower()
    return not re.search(r"\bnode\b", low) and "dsh-tg-account" not in low


def check_assertion_counts(root, counts, issues):
    """文档与 CI 里的断言数必须等于 counts["tests"]，漂了按「高」报。

    与徽章检查同级：徽章在第一屏，正文与 CI step name 会被照着判断回归规模；
    「中」不阻断 CI，等于没门禁。
    """
    try:
        want = int(counts["tests"])
    except (KeyError, TypeError, ValueError):
        return
    for rel in ASSERTION_COUNT_FILES:
        p = os.path.join(root, *rel.split("/"))
        if not os.path.isfile(p):
            continue
        for lineno, line in enumerate(read(p).splitlines(), 1):
            if not _is_python_test_count(line):
                continue
            for m in ASSERTION_COUNT_RE.finditer(line):
                got = m.group(1) or m.group(2)
                if int(got) != want:
                    issues.append(("TG", "高",
                                   f"{rel}:{lineno} 断言数 {got} 与 data/counts.json tests={want} 不一致"))


# CI job 的显示名与门禁数量也被文档反复引用：文档写错一个显示名，读者会照着它
# 去 CI 页面找不存在的 job。扫描面与断言数检查对齐（README / 英文 README /
# CONTRIBUTING / docs/*.md），候选只认反引号包裹、形如 `jobname (…)` 的名字。
CI_JOB_CANDIDATE_RE = re.compile(r"`([a-z][a-z0-9-]* \([^`)]*\))`")
CI_JOB_COUNT_RE = re.compile(r"([0-9]+|[一二两三四五六七八九十]+)\s*个\s*(?:job|门禁)",
                             re.IGNORECASE)
CI_EXPR_RE = re.compile(r"\$\{\{[^}]*\}\}")
CI_JOB_FILES = ("README.md", "README_EN.md", "CONTRIBUTING.md")

_CN_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_number(s):
    """把「五」「十二」「二十五」这类计数词转成整数；阿拉伯数字原样转。"""
    if s.isdigit():
        return int(s)
    if "十" in s:
        head, _, tail = s.partition("十")
        tens = _CN_DIGITS.get(head, 1) if head else 1
        ones = _CN_DIGITS.get(tail, 0) if tail else 0
        return tens * 10 + ones
    return _CN_DIGITS.get(s, 0)


def _yaml_scalar(v):
    """取 `name:` 的值：去掉引号与行尾注释（name 里不会出现 ` #`）。"""
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return re.sub(r"\s+#.*$", "", v).strip()


def parse_ci_jobs(root):
    """从 .github/workflows/*.yml 读出 {job id: 显示名}。

    只认顶层 `jobs:` 下两空格缩进的 job id、块内四空格缩进的 `name:`。
    不展开 matrix：`${{ … }}` 原样保留，由 check_ci_jobs 归一成通配符。
    刻意不依赖 pyyaml——缺 pyyaml 的机器上 audit_pack 也必须能跑完（测试会注入
    ImportError）；仓库的 workflow 就这几种写法，行解析足够且行为可预测。
    """
    jobs = {}
    wf_dir = os.path.join(root, ".github", "workflows")
    if not os.path.isdir(wf_dir):
        return jobs
    for name in sorted(os.listdir(wf_dir)):
        if not name.endswith((".yml", ".yaml")):
            continue
        in_jobs, cur = False, None
        for line in read(os.path.join(wf_dir, name)).splitlines():
            if re.match(r"^jobs:\s*(?:#.*)?$", line):
                in_jobs, cur = True, None
                continue
            if not in_jobs:
                continue
            if line and not line[0].isspace():
                break  # 回到顶层键，jobs 块结束
            m = re.match(r"^  ([A-Za-z0-9_-]+):\s*(?:#.*)?$", line)
            if m:
                cur = m.group(1)
                jobs[cur] = ""
                continue
            m = re.match(r"^    name:\s*(.+)$", line)
            if m and cur:
                jobs[cur] = _yaml_scalar(m.group(1))
    return jobs


def _job_template(name):
    """`matrix (py${{ matrix.python }} · ${{ matrix.os }})` → `matrix (py* · *)`。"""
    return CI_EXPR_RE.sub("*", name)


def check_ci_jobs(root, issues):
    """文档里的 CI job 名与「N 个 job/门禁」必须与 workflow 定义一致，漂了按「高」报。

    「中」不阻断 CI，等于没门禁：文档说「四个 job」时读者会漏掉一条真实门禁。
    """
    jobs = parse_ci_jobs(root)
    if not jobs:
        return
    templates = [_job_template(v or k) for k, v in jobs.items()]
    docs = []
    for rel in CI_JOB_FILES:
        p = os.path.join(root, *rel.split("/"))
        if os.path.isfile(p):
            docs.append((rel, p))
    docs_dir = os.path.join(root, "docs")
    if os.path.isdir(docs_dir):
        for name in sorted(os.listdir(docs_dir)):
            p = os.path.join(docs_dir, name)
            if name.endswith(".md") and os.path.isfile(p):
                docs.append((f"docs/{name}", p))
    for rel, p in docs:
        for lineno, line in enumerate(read(p).splitlines(), 1):
            for m in CI_JOB_CANDIDATE_RE.finditer(line):
                cand = m.group(1)
                if any(fnmatch.fnmatchcase(cand, t) for t in templates):
                    continue
                close = difflib.get_close_matches(cand, templates, n=1, cutoff=0)
                hint = f"；最接近：'{close[0]}'" if close else ""
                issues.append(("TG", "高",
                               f"{rel}:{lineno} 文档写的 job 名 '{cand}' 匹配不上 workflow 真名{hint}"))
            for m in CI_JOB_COUNT_RE.finditer(line):
                got = _cn_number(m.group(1))
                if got != len(jobs):
                    issues.append(("TG", "高",
                                   f"{rel}:{lineno} 写的 {got} 个 job/门禁，"
                                   f"workflow 实际 {len(jobs)} 个 job"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--also", action="append", default=[], help="顺带检查另一个项目目录")
    ap.add_argument("--local-id", action="append", default=[],
                    help="额外要查的本机标识（默认 USERNAME/USER/COMPUTERNAME）")
    a = ap.parse_args()

    global LOCAL_IDS
    LOCAL_IDS = list(a.local_id) or [
        v for v in (os.environ.get("USERNAME"), os.environ.get("USER"),
                    os.environ.get("COMPUTERNAME"))
        if v and len(v) > 2]

    issues = []
    n = check("TG", a.root, issues)
    print(f"TG: {a.root}  （{n} 个文件）")
    for extra in a.also:
        n2 = check(os.path.basename(extra.rstrip("\\/")).upper(), extra, issues)
        print(f"{os.path.basename(extra)}: {extra}  （{n2} 个文件）")

    # README 徽章 vs data/counts.json vs 实际目录 vs CHANGELOG 最新版本；
    # 文档与 CI 里的断言数也必须与 counts["tests"] 一致。
    # 这些一律算「高」：徽章是第一屏，漂了用户最先看到；「中」不阻断 CI，等于没门禁。
    counts_p = os.path.join(a.root, "data", "counts.json")
    rd = read(os.path.join(a.root, "README.md"))
    if os.path.isfile(counts_p):
        try:
            counts = json.load(open(counts_p, encoding="utf-8"))
        except Exception as e:
            counts = {}
            issues.append(("TG", "高", f"data/counts.json 读不了：{e}"))
        chg = read(os.path.join(a.root, "CHANGELOG.md"))
        m = re.search(r"^## v([0-9][0-9.]+)", chg, re.M)
        latest = m.group(1) if m else ""
        if counts.get("version") and latest and counts["version"] != latest:
            issues.append(("TG", "高",
                           f"data/counts.json version={counts['version']} 与 CHANGELOG 最新 {latest} 不一致"))
        real = {
            "scripts": len([f for f in os.listdir(os.path.join(a.root, "scripts"))
                            if f.endswith((".py", ".mjs"))]),
            "skills": len([d for d in os.listdir(os.path.join(a.root, "skills"))
                           if os.path.isdir(os.path.join(a.root, "skills", d))]) if os.path.isdir(
                os.path.join(a.root, "skills")) else 0,
        }
        for key, n in real.items():
            if counts.get(key) is not None and int(counts[key]) != n:
                issues.append(("TG", "高", f"data/counts.json {key}={counts[key]}，实际 {n} 个"))
        badges = {
            "version": re.search(r"badge/version-v([0-9][0-9.]+)", rd),
            "scripts": re.search(r"badge/scripts-(\d+)", rd),
            "tests": re.search(r"badge/tests-(\d+)", rd),
            "skills": re.search(r"badge/skills-(\d+)", rd),
        }
        for key, got in badges.items():
            if not got:
                issues.append(("TG", "高", f"README 缺 {key} 徽章"))
                continue
            want = latest if key == "version" else str(counts.get(key, ""))
            if want and got.group(1) != want:
                issues.append(("TG", "高", f"README 徽章 {key}={got.group(1)} 与真值 {want} 不一致"))
        check_assertion_counts(a.root, counts, issues)

    # CI job 名与门禁清单：文档里反引号写的 job 名、以及「N 个 job/门禁」的计数，
    # 必须与 .github/workflows/*.yml 对得上；漂了按「高」报（中不阻断等于没门禁）。
    check_ci_jobs(a.root, issues)

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
