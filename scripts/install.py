#!/usr/bin/env python3
r"""一条命令装好 TG：找 DSH home → 写 config.yaml → 拷技能 → 自检。

为什么单列：手动安装是 6 步，而新用户最常卡的三处都不在脚本里：
  1) 桌面版 DSH 的 home 由安装位置决定，`~/.dsh` 往往不存在；detect_home 只给 dsh_home，
     `skills_dir` 还留着默认值，预检于是「拒绝：技能目录不可用」；
  2) PowerShell 的 `Copy-Item -Recurse skills\tg-* "$dshHome\skills\"` 在目标 skills 目录
     不存在时直接失败（无法将容器复制到现有叶项），照 README 抄就会卡住；
  3) 先拷技能再预检，预检会把刚装的 10 个技能报成「同名技能」冲突，用户以为装坏了。

安装器把「探测 → 写配置 → 拷贝 → 预检」串成一条命令，并在最后给出重启与下一步。
默认幂等：重复跑只会更新技能文件，不覆盖你已配好的 memory_root / 价格表。

用法：
  python scripts/install.py                    # 自动探测并安装（会问对话夹放哪）
  python scripts/install.py --dry-run          # 只打印计划，不写任何文件
  python scripts/install.py --dsh-home D:/x/dsh --yes
  python scripts/install.py --skills-dir D:/x/dsh/skills
  python scripts/install.py --with-guard       # 顺带装循环守卫（改 package.json 前备份）
  python scripts/install.py --with-account     # 顺带装账目胶囊（输入框花费/交接卡，改 package.json 前备份）
  python scripts/install.py --with-guard --with-account   # 两个插件一起装
  python scripts/install.py --check            # 只检查安装结果，不写

退出码：0 成功 / 1 失败 / 2 定位不到 DSH home（或非交互却需要选择）
"""
import argparse
import os
import shutil
import subprocess
import sys
import time

from tg_common import (DEFAULT_CONFIG, MemoryRootUnset, default_memory_root,
                       ensure_memory_layout, ensure_memory_root, expand, home_candidates,
                       is_generated, is_interactive, load_config, mark_generated,
                       sessions_root, utf8_stdio, write_memory_root)

utf8_stdio()

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_SKILLS = os.path.join(REPO, "skills")
EXAMPLE = os.path.join(REPO, "config.example.yaml")


def repo_skills():
    """本包里的技能目录名（tg-*），排序后返回。"""
    if not os.path.isdir(SRC_SKILLS):
        return []
    return sorted(d for d in os.listdir(SRC_SKILLS)
                  if d.startswith("tg-") and os.path.isdir(os.path.join(SRC_SKILLS, d)))


def looks_like_home(p):
    """结构证据：sessions / profiles / AGENTS.md / storages 任一存在即认。"""
    return any(os.path.exists(os.path.join(p, name))
               for name in ("sessions", "profiles", "storages", "AGENTS.md"))


def detect_best(cfg, base):
    """调 detect_home 的打分逻辑（不复制一份探测规则）。失败返回 ""。"""
    try:
        import detect_home
        cands, _how = detect_home.detect(cfg, base)
        return cands[0]["path"] if cands else ""
    except Exception:
        return ""


def pick_home(a, cfg, base):
    """按 tg_common 的口径选 home：显式 > 配置 > $DSH_HOME > 探测。

    不直接读 $DSH_HOME / 不自己拼 home 路径：口径只允许在 tg_common 一处实现。

    两种候选区别对待（2026-10-30 评测定）：
      - `$DSH_HOME` 是用户/启动器显式设的，意图明确：即使目录还没生成也不换成自动探测到的
        另一套 home（那会把技能装进另一个真实安装），由 main 直接失败并报出来；
      - config.yaml 里的路径可能是照抄示例留下的空壳（`~/.dsh`）：不存在或没有结构证据就跳过，
        继续往下找，否则桌面版一上来就写错盘。
    """
    if a.dsh_home:
        return expand(a.dsh_home), "命令行 --dsh-home"
    for p, src in home_candidates(cfg, base):
        p = expand(p)
        if src == "$DSH_HOME":
            return p, src
        if not os.path.isdir(p):
            continue
        if not looks_like_home(p):
            print("  [warn] 跳过不像 DSH home 的候选：%s（%s）" % (p, src))
            continue
        return p, src
    best = detect_best(cfg, base)
    if best:
        return best, "自动探测（detect_home）"
    return "", ""


def _read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _read_text_nl(path):
    """原样读并识别换行风格，返回 (归一化成 \\n 的文本, 原换行符)。

    为什么不用 text 模式读：Windows 上 text 模式读进来是 `\\n`，写回去也成 `\\n`，
    于是用户原本 CRLF 的 config.yaml 会被整篇重写（git diff 全红），看起来像安装器改了他一堆配置。
    """
    with open(path, "rb") as f:
        raw = f.read()
    nl = "\r\n" if b"\r\n" in raw else "\n"
    return raw.decode("utf-8").replace("\r\n", "\n"), nl


def _atomic_write(path, data):
    tmp = path + ".tmp"
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(tmp, mode) as f:
        f.write(data)
    os.replace(tmp, path)


def _is_pristine(path):
    """配置文件是否还等于 config.example.yaml（刚生成、用户没改过）→ 不值得建备份。"""
    try:
        with open(path, "rb") as f:
            cur = f.read()
        with open(EXAMPLE, "rb") as f:
            return cur == f.read()
    except OSError:
        return False


def set_config_value(path, key, value):
    """按行改一个顶层键：保留注释、其它项与换行风格；内容没变就不写、不备份。

    返回 (路径, 备份路径或 "")。只改第一处未注释的 key 行，找不到就追加。
    """
    if not os.path.isfile(path):
        d = os.path.dirname(os.path.abspath(path))
        os.makedirs(d, exist_ok=True)
        if os.path.isfile(EXAMPLE):
            shutil.copyfile(EXAMPLE, path)
            mark_generated(path)   # 同一次运行里后续几次改写都不必再为它留备份
    old, nl = _read_text_nl(path) if os.path.isfile(path) else ("", "\n")
    lines = old.splitlines() if old else []
    line = '%s: "%s"' % (key, str(value).replace("\\", "/"))
    hit = False
    for i, s in enumerate(lines):
        if s.lstrip().startswith(key + ":") and not s.lstrip().startswith("#"):
            lines[i] = line
            hit = True
            break
    if not hit:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(line)
    new = nl.join(lines)
    if not new.endswith(nl):
        new += nl
    data = new.encode("utf-8")
    with open(path, "rb") as f:
        if f.read() == data:
            return path, ""      # 已经是目标值：不写、不留备份
    bak = ""
    if not is_generated(path) and not _is_pristine(path):   # 刚生成的模板没有「旧内容」可回滚
        bak = path + ".bak-install-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copyfile(path, bak)
    _atomic_write(path, data)
    return path, bak


def copy_skills(src_root, skills_dir, names, dry_run=False):
    """逐技能复制（目录已存在则合并覆盖）。返回 (写入文件数, 更新技能数, 失败技能列表)。

    失败要报出来：某个技能目录里被同名普通文件占住时，copytree 会中途失败，
    旧版只打一条 [warn] 就继续，最后仍打印「安装完成」rc=0（2026-10-30 评测定）。
    """
    written = updated = 0
    failed = []
    for name in names:
        src = os.path.join(src_root, name)
        dst = os.path.join(skills_dir, name)
        if not os.path.isdir(src):
            continue
        if dry_run:
            updated += 1
            continue
        existed = os.path.isdir(dst)
        if os.path.realpath(src) == os.path.realpath(dst):
            continue  # 目标就是仓库自己：别把文件复制到自身
        try:
            os.makedirs(skills_dir, exist_ok=True)
            shutil.copytree(src, dst, dirs_exist_ok=True)
        except OSError as e:
            print("  [warn] 写不进 %s：%s（目录不可写，或被同名文件占了）" % (dst, e),
                  file=sys.stderr)
            failed.append(name)
            continue
        for _dp, _dn, fs in os.walk(src):
            written += len(fs)
        if existed:
            updated += 1
    return written, updated, failed


def verify(home, skills_dir, names):
    """安装结果自检：技能文件树、frontmatter、内容是否与本包一致。返回 (问题, 提醒)。

    为什么比整棵树：只验「SKILL.md 存在 + name 对」时，技能里的 templates/ 没落地、
    或内容被换成占位，`--check` 照样报「检查通过」——那是假绿（2026-10-30 两轮评委会都实测到）。
    """
    problems, warns = [], []
    if not os.path.isdir(skills_dir):
        problems.append("技能目录不存在：%s" % skills_dir)
        return problems, warns
    for name in names:
        p = os.path.join(skills_dir, name, "SKILL.md")
        if not os.path.isfile(p):
            problems.append("缺 SKILL.md：%s" % p)
            continue
        head = _read_text(p)[:400]
        if not head.startswith("---"):
            problems.append("frontmatter 缺失：%s" % p)
            continue
        if ("name: %s" % name) not in head:
            problems.append("frontmatter 的 name 不是 %s：%s" % (name, p))
            continue
        # 整棵技能目录逐文件比对：缺文件、内容不同、或本该是目录却成了文件，都算没装好
        bad = []
        src_root = os.path.join(SRC_SKILLS, name)
        for dp, _dn, fs in os.walk(src_root):
            rel = os.path.relpath(dp, src_root)
            for f in fs:
                relf = f if rel == "." else os.path.join(rel, f)
                s = os.path.join(dp, f)
                d = os.path.join(skills_dir, name, relf)
                if not os.path.isfile(d):
                    bad.append(relf)
                    continue
                try:
                    with open(s, "rb") as fh:
                        want = fh.read()
                    with open(d, "rb") as fh:
                        got = fh.read()
                except OSError as e:
                    warns.append("读不了 %s：%s" % (d, e))
                    continue
                if got != want:
                    bad.append(relf)
        if bad:
            problems.append("%s：%d 个文件缺失或与本包不同（%s%s）；重跑 install.py 会覆盖"
                            % (name, len(bad), "、".join(bad[:3]), " …" if len(bad) > 3 else ""))
    if not os.path.isdir(sessions_root(home)):
        warns.append("home 里还没有 sessions/：%s（第一次启动 DSH 后才会生成）" % home)
    return problems, warns


def main():
    ap = argparse.ArgumentParser(description="一条命令装 TG")
    ap.add_argument("--dsh-home", default="", help="DSH 数据目录；不给则自动探测")
    ap.add_argument("--skills-dir", default="", help="技能目录；默认 <dsh_home>/skills")
    ap.add_argument("--memory-root", default="", help="对话夹母文件夹；默认 ~/Documents/tg-memory")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="配置文件，默认 config.yaml")
    ap.add_argument("--yes", action="store_true", help="不问，全部用默认值")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不写任何文件")
    ap.add_argument("--check", action="store_true", help="只检查安装结果")
    ap.add_argument("--with-guard", action="store_true", help="顺带安装循环守卫插件")
    ap.add_argument("--with-account", action="store_true", help="顺带安装账目胶囊插件")
    a = ap.parse_args()

    cfg, base = load_config(a.config, strict=True)
    names = repo_skills()
    if not names:
        print("[FAIL] 找不到 skills/tg-*：请在仓库根目录运行本脚本。", file=sys.stderr)
        return 1

    home, src = pick_home(a, cfg, base)
    if not home:
        print("找不到 DSH 数据目录。三条路：", file=sys.stderr)
        print("  1) 先启动一次 DSH，再重跑本命令（让它生成 sessions/）", file=sys.stderr)
        print('  2) python scripts/install.py --dsh-home "<DSH 数据目录>"', file=sys.stderr)
        print("  3) python scripts/detect_home.py 看它列出的候选", file=sys.stderr)
        return 2
    if not os.path.isdir(home):
        # 显式给的 home（--dsh-home / $DSH_HOME）不存在就硬失败：换成自动探测到的另一套，
        # 会把技能装进另一个真实安装，而用户以为自己装到了指定位置（2026-10-30 评委实测）。
        print("[FAIL] DSH home 不存在：%s（来源：%s）" % (home, src or "探测"), file=sys.stderr)
        print('  要么先启动一次 DSH 让它生成 sessions/，要么改用：'
              '--dsh-home "<真实的数据目录>"', file=sys.stderr)
        if src == "$DSH_HOME":
            print("  当前 $DSH_HOME 指向该路径；改环境变量或上面那行二选一。", file=sys.stderr)
        print("  不想到处猜：python scripts/detect_home.py 会列出本机候选", file=sys.stderr)
        return 2

    # 技能目录：显式 > 配置里已存在的 > <home>/skills。
    # 桌面版默认 ~/.dsh/skills 基本不存在，跟着写会让预检直接拒绝，所以以 home 为准。
    if a.skills_dir:
        skills_dir = expand(a.skills_dir)
    else:
        cfg_skills = cfg.get("skills_dir")
        cfg_skills = expand(cfg_skills, base) if isinstance(cfg_skills, str) and cfg_skills.strip() else ""
        if cfg_skills and os.path.isdir(cfg_skills):
            skills_dir = cfg_skills
        else:
            skills_dir = os.path.join(home, "skills")

    print("TG 安装器")
    print("  DSH home : %s（%s）" % (home, src))
    print("  技能目录 : %s" % skills_dir)
    print("  配置     : %s" % os.path.abspath(a.config))
    print("  技能数量 : %d（%s … %s）" % (len(names), names[0], names[-1]))
    print()

    if a.check:
        problems, warns = verify(home, skills_dir, names)
        for w in warns:
            print("  [warn] %s" % w)
        if problems:
            for p in problems:
                print("  [FAIL] %s" % p)
            print("\n检查未通过：按上面的路径补齐；重跑 python scripts/install.py 可自动修复。")
            return 1
        print("检查通过：%d 个技能在 %s；home 可读。" % (len(names), skills_dir))
        return 0

    if not looks_like_home(home):
        print("  [warn] 这个目录没有 sessions/ profiles/ AGENTS.md，可能不是 DSH 数据目录；"
              "仍按你给的路径安装。")
    listed = cfg.get("dsh_homes") or []
    if isinstance(listed, (list, tuple)) and listed:
        want = {os.path.realpath(expand(x, base)) for x in listed if isinstance(x, str) and x.strip()}
        if os.path.realpath(home) not in want:
            print("  [warn] config.yaml 用的是 dsh_homes 列表，安装器只写 dsh_home；"
                  "其它脚本仍优先读 dsh_homes，请把它也改成 %s" % home)

    if a.dry_run:
        print("（dry-run，不写文件）会做：")
        print("  1) 写 %s：dsh_home / skills_dir / memory_root" % a.config)
        print("  2) 复制 %d 个技能到 %s" % (len(names), skills_dir))
        print("  3) 自检并打印重启提示")
        return 0

    # 1) 配置：先保证 config.yaml 存在，再写两行必改项（写不进去要给人话，不能 traceback）
    try:
        set_config_value(a.config, "dsh_home", home)
        set_config_value(a.config, "skills_dir", skills_dir)
    except OSError as e:
        print("[FAIL] 写不进配置 %s：%s" % (os.path.abspath(a.config), e), file=sys.stderr)
        print('  换个可写的位置：--config "<目录>/config.yaml"', file=sys.stderr)
        return 1
    cfg, base = load_config(a.config, strict=True)

    # 2) 对话夹母文件夹：没配就问一次（--yes / 非交互时用默认值）
    if a.memory_root:
        try:
            set_config_value(a.config, "memory_root", expand(a.memory_root))
        except OSError as e:
            print("  [warn] 写不进 memory_root：%s" % e, file=sys.stderr)
        cfg, base = load_config(a.config, strict=True)
    try:
        root, info = ensure_memory_root(cfg, base, config_path=a.config,
                                        ask=(not a.yes) and is_interactive())
    except MemoryRootUnset:
        root = default_memory_root()
        info = {"source": "default", "created": ensure_memory_layout(root)}
        print("  对话夹母文件夹未配置：已用默认 %s（之后可用 setup.py 换）" % root)
    except OSError as e:
        print("[FAIL] 建不了对话夹母文件夹：%s" % e, file=sys.stderr)
        print('  换个可写的位置再跑：--memory-root "<目录>"', file=sys.stderr)
        return 1
    # 配置里已经写死的 memory_root 不会被上面建出来：这里补一次「真能建」的检查，
    # 否则路径落在文件底下时安装器会一路 rc=0，等用户跑 setup.py / 记笔记才炸。
    if not os.path.isdir(root):
        try:
            ensure_memory_layout(root)
        except OSError as e:
            print("[FAIL] 对话夹母文件夹建不了：%s（%s）" % (root, e), file=sys.stderr)
            print('  换个可写的位置再跑：--memory-root "<目录>"', file=sys.stderr)
            return 1
    if info.get("source") in ("prompt", "default", "env"):
        try:
            write_memory_root(a.config, root)
        except OSError as e:
            print("  [warn] 写不进 %s：%s" % (a.config, e), file=sys.stderr)

    # 3) 技能入目录
    written, updated, failed = copy_skills(SRC_SKILLS, skills_dir, names)
    print("  已复制 %d 个技能 / %d 个文件到 %s" % (len(names) - len(failed), written, skills_dir))
    for name in failed:
        print("  [FAIL] 复制失败：%s（见上面的 [warn]）" % name)

    # 4) 自检
    problems, warns = verify(home, skills_dir, names)
    for w in warns:
        print("  [warn] %s" % w)
    print()
    if problems or failed:
        for p in problems:
            print("  [FAIL] %s" % p)
        print("\n安装未完成：先修掉上面的项再跑 --check。")
        return 1
    print("安装完成：%d 个技能 → %s" % (len(names), skills_dir))
    cfg_path = os.path.abspath(a.config)
    if a.with_guard:
        if not os.path.isfile(os.path.join(home, "profiles", "desktop", "package.json")):
            print("\n  循环守卫：找不到 profiles/desktop/package.json，跳过"
                  "（用 scripts/install_guard.py --profile <名> 手动装）")
        else:
            r = subprocess.run([sys.executable, os.path.join(REPO, "scripts", "install_guard.py"),
                                "--home", home, "--apply"], text=True)
            if r.returncode != 0:
                print("\n  循环守卫安装失败（退出码 %d）：重启不会生效，先修它再重启。"
                      % r.returncode, file=sys.stderr)
                return 1
            print("\n  循环守卫已装好（重启后生效）")
    if a.with_account:
        if not os.path.isfile(os.path.join(home, "profiles", "desktop", "package.json")):
            print("\n  账目胶囊：找不到 profiles/desktop/package.json，跳过"
                  "（用 scripts/install_account.py --profile <名> 手动装）")
        else:
            cmd = [sys.executable, os.path.join(REPO, "scripts", "install_account.py"),
                   "--home", home]
            if not a.dry_run:
                cmd.append("--apply")
            r = subprocess.run(cmd, text=True)
            if r.returncode != 0:
                print("\n  账目胶囊安装失败（退出码 %d）：重启不会生效，先修它再重启。"
                      % r.returncode, file=sys.stderr)
                return 1
            print("\n  账目胶囊已装好（重启后生效）")
    print("下一步：")
    print("  1) 重启 DSH（技能在下次启动时加载）")
    # 命令要能**原样粘贴**进 cmd / PowerShell / bash：解释器只写命令名、其余路径加引号。
    # 旧版把解释器路径也加引号，PowerShell 里粘贴会 ParserError（2026-10-30 评测定）。
    exe = "python" if shutil.which("python") else (sys.executable or "python")
    pre = os.path.join(REPO, "scripts", "preflight.py")
    doc = os.path.join(REPO, "scripts", "doctor.py")
    print('  2) %s "%s" --config "%s"' % (exe, pre, cfg_path))
    print('  3) %s "%s" --config "%s" --days 7' % (exe, doc, cfg_path))
    if not shutil.which("python"):
        print('     （本机 PATH 里没有 python：把 python 换成 "%s"，PowerShell 要在前面加 &）' % exe)
    print("     （路径都是绝对的，在任意目录下都能直接照抄）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
