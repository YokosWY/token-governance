#!/usr/bin/env python3
"""TG 回归测试：纯标准库，不需要 pytest。

用法：python tests/run_tests.py
覆盖：解压回退、计价、日志格式拒绝、换参重试口径、预检漂移、首日基线、数字单源。
"""
import json, os, re, shutil, subprocess, sys, tempfile

# Windows 默认控制台是 GBK；测试会打印带替换字符的 detail，不固定编码会以
# UnicodeEncodeError 崩掉（退出码 1，看起来像测试失败，其实是打印失败）。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
FIX = os.path.join(ROOT, "tests", "fixtures")
PY = sys.executable

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  {detail}" if detail and not cond else ""))


def run(script, *args):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"  # 子脚本也别在 GBK 控制台下崩
    return subprocess.run([PY, os.path.join(SCRIPTS, script), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env)


def compress(src, dst):
    """造 .zstd 夹具：与 tg_common 的通道顺序一致（命令 → 内建 → 模块）。

    测试自身也要能从「只有 3.14 内建压缩」的机器上跑起来，否则升级 Python 后
    夹具造不出来、大批用例静默 SKIP，看起来还是绿的。
    """
    try:
        r = subprocess.run(["zstd", "-q", "-f", src, "-o", dst], capture_output=True)
        if r.returncode == 0 and os.path.isfile(dst):
            return True
    except FileNotFoundError:
        pass
    with open(src, "rb") as f:
        data = f.read()
    try:
        from compression import zstd as czstd
        with open(dst, "wb") as f:
            f.write(czstd.compress(data))
        return True
    except ImportError:
        pass
    except Exception:
        return False
    try:
        import zstandard as zstd
    except ImportError:
        return False
    with open(dst, "wb") as f:
        f.write(zstd.ZstdCompressor().compress(data))
    return True


def put_session(home, sid, fixture, tmp):
    d = os.path.join(home, "sessions", sid)
    os.makedirs(d, exist_ok=True)
    src = os.path.join(FIX, fixture)
    dst = os.path.join(d, "session.v4.jsonl.zstd")
    ok = compress(src, dst)
    if ok:
        # 测试要的是「刚产生的会话」，但 `zstd -f src -o dst` 会继承源文件的 mtime。
        # 不归零的话，--days 1 的用例会随日历推移陆续变红（fixture 写于 2026-10-22）。
        try:
            os.utime(dst, None)
        except OSError:
            pass
    return ok


def write_cfg(path, home):
    """写 config.yaml：路径统一正斜杠（YAML 双引号里反斜杠会被当转义）。"""
    h = home.replace("\\", "/")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f'dsh_home: "{h}"\nskills_dir: "{h}/skills"\n')


def main():
    from datetime import datetime, timezone, timedelta  # 账本用例要构造确定的毫秒时间戳
    tmp = tempfile.mkdtemp(prefix="tg-test-")
    prices = os.path.join(tmp, "prices.yaml")
    with open(prices, "w", encoding="utf-8") as f:
        f.write("prices_usd_per_mtok:\n  input: 0.15\n  cached: 0.003\n  output: 0.6\nrate: 7.1\n")

    home = os.path.join(tmp, "home")
    os.makedirs(os.path.join(home, "skills"), exist_ok=True)
    # Python ≥3.14 自带 compression.zstd：这时「解压器缺失」类用例的前提不成立，必须按版本分流
    try:
        from compression import zstd as _builtin_zstd  # noqa: F401
        has_builtin_zstd = True
    except ImportError:
        has_builtin_zstd = False
    ok = put_session(home, "session-retry", "retry_session.jsonl", tmp)
    if not ok:
        # 没解压器就没法造夹具：这是环境缺件，不是失败（CI 里 continue-on-error 的 job 不该因此判红）
        print("SKIP 压缩工具不可用（zstd / python zstandard 都没有）：装 `pip install zstandard` 后重跑")
        return 0
    s_retry = os.path.join(home, "sessions", "session-retry", "session.v4.jsonl.zstd")
    put_session(home, "session-literal", "literal_session.jsonl", tmp)

    # 1. 解压：与源文件逐字节相同
    sys.path.insert(0, SCRIPTS)
    import measure_modules as mm
    with open(os.path.join(FIX, "retry_session.jsonl"), "rb") as f:
        raw = f.read()
    check("decompress 与源一致", mm.decompress(s_retry) == raw)

    # 2. 计价：总 CNY = (in*0.15 + cache*0.003 + out*0.6) * 7.1 / 1e6
    r = run("measure_modules.py", "--session", s_retry, "--config", prices)
    expect = (1500 * 0.15 + 18500 * 0.003 + 500 * 0.6) * 7.1 / 1e6
    got = 0.0
    for line in r.stdout.splitlines():
        if "总CNY=" in line:
            got = float(line.split("总CNY=")[1].split()[0])
    check("计价与手算一致", abs(got - expect) < 0.0005, f"got={got} expect={expect:.4f}")

    # 3. 非 DSH 日志必须拒绝
    foreign = os.path.join(tmp, "foreign.jsonl")
    with open(foreign, "w", encoding="utf-8") as f:
        f.write('{"type":"user/message","data":{}}\n{"type":"assistant/message","data":{}}\n')
    foreign_z = foreign + ".zstd"
    compress(foreign, foreign_z)
    r = run("measure_modules.py", "--session", foreign_z)
    check("外来日志被拒绝", r.returncode == 1 and "不支持的日志格式" in (r.stdout + r.stderr))

    # 4. 换参重试口径
    r = run("retry_patterns.py", "--dsh-home", home, "--days", "1", "--json")
    try:
        d = json.loads(r.stdout)
    except Exception:
        d = {}
    check("换参重试 1 处", d.get("retry_runs") == 1, str(d.get("retry_runs")))
    check("其中失败重试 1 处", d.get("retry_after_failure_runs") == 1, str(d.get("retry_after_failure_runs")))
    check("字面重复 1 处", d.get("literal_runs") == 1, str(d.get("literal_runs")))

    # 5. 预检：插件名漂移
    prof = os.path.join(home, "profiles", "test")
    os.makedirs(prof, exist_ok=True)
    with open(os.path.join(prof, "cordis.patch.yml"), "w", encoding="utf-8") as f:
        f.write("- insert:\n    - id: x\n      name: '@test/ghost'\n")
    with open(os.path.join(prof, "cordis.yml"), "w", encoding="utf-8") as f:
        f.write("- id: y\n  name: '@test/real'\n")
    cfg = os.path.join(tmp, "cfg.yaml")
    write_cfg(cfg, home)
    r = run("preflight.py", "--config", cfg)
    check("插件名漂移被报出", r.returncode == 1 and "插件名漂移" in r.stdout)

    # 5b. 空机器预检：没有会话时也要给判定（旧版会被 schema_budget 的 sys.exit 吞掉整段输出）
    empty_home = os.path.join(tmp, "emptyhome")
    os.makedirs(os.path.join(empty_home, "skills"), exist_ok=True)
    ecfg = os.path.join(tmp, "emptycfg.yaml")
    write_cfg(ecfg, empty_home)
    r = run("preflight.py", "--config", ecfg)
    # skills_dir 是空目录 = 一个 tg-* 都没装：必须是「待确认 + 缺技能」，
    # 不能像旧版那样判「放行」（那和 install.py --check 的 rc=1 自相矛盾）
    check("空 home 预检仍给判定且不冒充放行",
          r.returncode == 1 and "判定：待确认" in r.stdout and "缺技能" in r.stdout,
          (r.stdout + r.stderr)[:240])

    # 5c. 装完再预检：内容一致算「已安装」，不再报 10 条「同名技能」
    inst_home = os.path.join(tmp, "insthome")
    shutil.copytree(os.path.join(ROOT, "skills"), os.path.join(inst_home, "skills"))
    icfg = os.path.join(tmp, "instcfg.yaml")
    write_cfg(icfg, inst_home)
    r = run("preflight.py", "--config", icfg)
    check("已安装技能不判冲突",
          r.returncode == 0 and "已安装技能" in r.stdout and "同名技能" not in r.stdout,
          (r.stdout + r.stderr)[:240])

    # 5d. 安装器：dry-run 不写盘；真装可复跑；--check 通过
    i_home = os.path.join(tmp, "installer-home")
    os.makedirs(os.path.join(i_home, "sessions"), exist_ok=True)
    i_cfg = os.path.join(tmp, "installer.yaml")
    i_mem = os.path.join(tmp, "installer-memory")
    r = run("install.py", "--config", i_cfg, "--dsh-home", i_home, "--memory-root", i_mem,
            "--yes", "--dry-run")
    check("install.py dry-run 不写配置", r.returncode == 0 and not os.path.exists(i_cfg),
          (r.stdout + r.stderr)[:240])
    r = run("install.py", "--config", i_cfg, "--dsh-home", i_home, "--memory-root", i_mem, "--yes")
    skills_ok = all(os.path.isfile(os.path.join(i_home, "skills", s, "SKILL.md"))
                    for s in os.listdir(os.path.join(ROOT, "skills"))
                    if s.startswith("tg-"))
    check("install.py 装出配置与 10 个技能",
          r.returncode == 0 and os.path.isfile(i_cfg) and skills_ok,
          (r.stdout + r.stderr)[:240])
    r = run("install.py", "--config", i_cfg, "--dsh-home", i_home, "--check")
    check("install.py --check 通过", r.returncode == 0, (r.stdout + r.stderr)[:240])
    r = run("install.py", "--config", i_cfg, "--dsh-home", i_home, "--memory-root", i_mem, "--yes")
    check("install.py 可复跑", r.returncode == 0, (r.stdout + r.stderr)[:240])

    # 5d1. 账目胶囊安装器：dry-run 不写盘 → --apply 落插件/登记/root 配置 → 复跑幂等 → --uninstall 摘登记。
    #      胶囊是公开仓里唯一带 Node 前端的插件，装了不写 _local/tg-account.json 就会退成内置价，
    #      所以这里连「root 指向本仓库」一起断言。
    a_home = os.path.join(tmp, "account-home")
    os.makedirs(os.path.join(a_home, "sessions"), exist_ok=True)
    os.makedirs(os.path.join(a_home, "profiles", "desktop"), exist_ok=True)
    a_pkg = os.path.join(a_home, "profiles", "desktop", "package.json")
    a_root = os.path.join(a_home, "_local", "tg-account.json")
    with open(a_pkg, "w", encoding="utf-8") as f:
        json.dump({"dependencies": {}, "dsh": {"profile": {"bundles": ["@deepseek-ai/dsh-base"]}}},
                  f, indent=2)
    a_before = open(a_pkg, "rb").read()
    r = run("install_account.py", "--home", a_home, "--config", i_cfg)
    check("install_account dry-run 不写盘",
          r.returncode == 0 and open(a_pkg, "rb").read() == a_before and not os.path.exists(a_root)
          and not os.path.isdir(os.path.join(a_home, "local-plugins")),
          (r.stdout + r.stderr)[:240])
    r = run("install_account.py", "--home", a_home, "--config", i_cfg, "--apply")
    pkg_a = json.load(open(a_pkg, encoding="utf-8"))
    plug = os.path.join(a_home, "local-plugins", "dsh-tg-account")
    check("install_account 装出插件 + 登记 dependency/bundles + 写 root",
          r.returncode == 0
          and os.path.isfile(os.path.join(plug, "lib", "index.js"))
          and os.path.isfile(os.path.join(plug, "package.json"))
          and pkg_a["dependencies"].get("@local/dsh-tg-account", "").startswith("link:")
          and "@local/dsh-tg-account" in pkg_a["dsh"]["profile"]["bundles"]
          and json.load(open(a_root, encoding="utf-8")).get("root", "").endswith("token-governance"),
          (r.stdout + r.stderr)[:300])
    r = run("install_account.py", "--home", a_home, "--config", i_cfg, "--apply")
    check("install_account 可复跑（内容相同跳过、root 不重写）",
          r.returncode == 0 and "内容相同" in (r.stdout or ""),
          (r.stdout + r.stderr)[:240])
    r = run("install_account.py", "--home", a_home, "--config", i_cfg, "--status")
    check("install_account --status 认出已装",
          r.returncode == 0 and "已登记" in (r.stdout or "") and "0.10" in (r.stdout or ""),
          (r.stdout + r.stderr)[:240])
    r = run("install_account.py", "--home", a_home, "--config", i_cfg, "--uninstall", "--apply")
    pkg_b = json.load(open(a_pkg, encoding="utf-8"))
    check("install_account --uninstall 摘掉登记（插件目录保留）",
          r.returncode == 0 and "@local/dsh-tg-account" not in pkg_b.get("dependencies", {})
          and "@local/dsh-tg-account" not in pkg_b["dsh"]["profile"]["bundles"]
          and os.path.isdir(plug),
          (r.stdout + r.stderr)[:240])

    # 5d2. 一条命令装齐：install.py --with-account 在装技能的同一次跑里把胶囊也装上。
    #      dry-run 时插件侧同样只打印计划（不能出现「说好只看，结果写了 package.json」）。
    a_before2 = open(a_pkg, "rb").read()
    r = run("install.py", "--config", i_cfg, "--dsh-home", a_home, "--yes", "--with-account",
            "--dry-run")
    check("install.py --with-account dry-run 不写 profile",
          r.returncode == 0 and open(a_pkg, "rb").read() == a_before2
          and "dry-run" in (r.stdout or ""),
          (r.stdout + r.stderr)[:300])
    r = run("install.py", "--config", i_cfg, "--dsh-home", a_home, "--yes", "--with-account")
    pkg_c = json.load(open(a_pkg, encoding="utf-8"))
    check("install.py --with-account 一条命令装出胶囊",
          r.returncode == 0
          and "@local/dsh-tg-account" in pkg_c.get("dependencies", {})
          and "@local/dsh-tg-account" in pkg_c["dsh"]["profile"]["bundles"]
          and os.path.isfile(os.path.join(a_home, "_local", "tg-account.json")),
          (r.stdout + r.stderr)[:300])

    # 5d2. 真新用户路径：新 clone、没有 config.yaml、零参数、只靠环境变量定位 home。
    #      它不走 --config / --dsh-home / --memory-root，与上面几条都不是同一段代码路径：
    #      config.yaml 要自己按 example 生成，skills_dir 要自己落到 <home>/skills。
    clone = os.path.join(tmp, "newbie-clone")
    shutil.copytree(ROOT, clone, ignore=shutil.ignore_patterns(
        ".git", "__pycache__", "*.pyc", "config.yaml", "_local", "_history", "*bak*"))
    nb_home = os.path.join(tmp, "newbie-home")
    nb_mem = os.path.join(tmp, "newbie-memory")
    os.makedirs(os.path.join(nb_home, "sessions"), exist_ok=True)
    nb_env = {**os.environ, "PYTHONIOENCODING": "utf-8",
              "DSH_HOME": nb_home, "TG_MEMORY_ROOT": nb_mem}
    nb = subprocess.run([PY, os.path.join(clone, "scripts", "install.py")], cwd=clone,
                        capture_output=True, text=True, encoding="utf-8",
                        errors="replace", env=nb_env)
    nb_skills = all(os.path.isfile(os.path.join(nb_home, "skills", s, "SKILL.md"))
                    for s in os.listdir(os.path.join(ROOT, "skills")) if s.startswith("tg-"))
    check("install.py 零参数在新 clone 上装成（自建 config + 技能落 <home>/skills）",
          nb.returncode == 0 and os.path.isfile(os.path.join(clone, "config.yaml")) and nb_skills,
          (nb.stdout + nb.stderr)[:240])
    # 照抄它打印的「下一步」：只把解释器换成当前 PY、脚本换成副本里的同名脚本，参数原样喂回去。
    # 不用 shlex 拆整行：Windows 路径里的反斜杠会被 POSIX 规则当转义吃掉。
    step = [ln for ln in (nb.stdout or "").splitlines() if "preflight.py" in ln
            and "--config" in ln]
    ok_next, detail = False, "没打印带 --config 的 preflight 下一步"
    if step:
        m = re.search(r'--config\s+"([^"]+)"', step[0]) or re.search(r"--config\s+(\S+)", step[0])
        if m:
            rr = subprocess.run(
                [PY, os.path.join(clone, "scripts", "preflight.py"), "--config", m.group(1)],
                cwd=clone, capture_output=True, text=True, encoding="utf-8",
                errors="replace", env=nb_env)
            ok_next = rr.returncode == 0 and "判定：放行" in rr.stdout
            detail = (rr.stdout + rr.stderr)[:240]
        else:
            detail = "下一步里的 --config 取不出路径：%s" % step[0]
    check("install.py 打印的下一步可照抄（preflight 放行）", ok_next, detail)

    # 5d3. 独立评审实测出来的五处（2026-10-30）：都是「看起来装好了，其实没有」那类。
    def nb_run(*args, env=None, cwd=None):
        return subprocess.run([PY, os.path.join(clone, "scripts", "install.py"), *args],
                              cwd=cwd or clone, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env or nb_env)

    # (a) --check 必须比对内容：把一条技能截成占位，仍报通过就是假绿（旧版行为）
    victim = os.path.join(nb_home, "skills", "tg-cost", "SKILL.md")
    restore = open(victim, "rb").read()
    open(victim, "w", encoding="utf-8").write("---\nname: tg-cost\n---\n占位\n")
    r = nb_run("--check")
    check("install.py --check 抓到技能内容与本包不同", r.returncode == 1,
          (r.stdout + r.stderr)[-200:])
    open(victim, "wb").write(restore)
    # (b) 复跑：配置字节不变、不新增 .bak-install（旧版每次把 CRLF 重写成 LF、还留备份）
    cfg_clone = os.path.join(clone, "config.yaml")
    before_bytes = open(cfg_clone, "rb").read()
    before_baks = len([f for f in os.listdir(clone) if f.startswith("config.yaml.bak-install-")])
    r = nb_run()
    after_baks = len([f for f in os.listdir(clone) if f.startswith("config.yaml.bak-install-")])
    check("install.py 复跑不改配置字节、不留新备份",
          r.returncode == 0 and open(cfg_clone, "rb").read() == before_bytes
          and after_baks == before_baks,
          "rc=%s new_baks=%s" % (r.returncode, after_baks - before_baks))
    # (b2) 全新安装（配置由 example 生成）不该留下任何 .bak-*：那是没有旧内容可回滚的假备份
    fresh_baks = [f for f in os.listdir(clone) if ".bak-" in f]
    check("install.py 全新安装不留无效备份（.bak-install / .bak-memory-root）",
          not fresh_baks, fresh_baks[:4])
    # (b3) 反过来：用户改过的配置真被改时仍要留一份备份，别为了「不留备份」把回滚点砍了
    other_home = os.path.join(tmp, "other-home")
    os.makedirs(os.path.join(other_home, "sessions"), exist_ok=True)
    baks_before = len([f for f in os.listdir(clone) if f.startswith("config.yaml.bak-install-")])
    nb_run("--dsh-home", other_home)
    baks_after = len([f for f in os.listdir(clone) if f.startswith("config.yaml.bak-install-")])
    check("install.py 用户配置真被改时留回滚点", baks_after == baks_before + 1,
          "before=%s after=%s" % (baks_before, baks_after))
    # (c) 下一步里的脚本是绝对路径：换到别的 cwd 照抄也能跑（旧版相对路径必挂）
    #     先做这一条：(d)(e) 会故意把 config/skills_dir 弄坏，之后 preflight 就不会放行了。
    abs_ok = False
    if step:
        m = re.search(r'"([^"]*[\\/]scripts[\\/]preflight\.py)"', step[0])
        abs_ok = bool(m and os.path.isabs(m.group(1)) and os.path.isfile(m.group(1)))
        if abs_ok:
            mc = re.search(r'--config\s+"([^"]+)"', step[0])
            rr = subprocess.run([PY, m.group(1), "--config", mc.group(1)], cwd=tmp,
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", env=nb_env)
            abs_ok = rr.returncode == 0
    check("install.py 下一步用绝对路径（换 cwd 照抄仍放行）", abs_ok, (step or [""])[0][:160])
    # (d) 技能内嵌套文件被同名文件占住：不能打「安装完成」rc=0，--check 也要红。
    #     放在「弄坏 config」的用例之前：它依赖这份安装是好的。
    nested = os.path.join(nb_home, "skills", "tg-isolate", "templates")
    shutil.rmtree(nested, ignore_errors=True)
    open(nested, "w").write("占位")
    r = nb_run()
    check("install.py 技能内文件写不进时判失败（不再「安装完成」）",
          r.returncode == 1 and ("复制失败" in r.stdout + r.stderr
                                 or "缺失或与本包不同" in r.stdout + r.stderr),
          (r.stdout + r.stderr)[-240:])
    check("install.py --check 同样抓到技能内文件缺失", nb_run("--check").returncode == 1)
    os.remove(nested)
    nb_run()   # 复原，后面的用例还要用这份安装
    # (e) 打印的下一步要能**原样粘贴**：cmd / PowerShell 各执行一次（旧版给解释器加引号，
    #     PowerShell 会 ParserError；只拆参数调 subprocess 是测不到这一层的）
    fresh_out = nb_run().stdout or ""
    step2 = [ln for ln in fresh_out.splitlines() if "preflight.py" in ln]
    cmdline = step2[0].strip().lstrip("0123456789) ").strip() if step2 else ""
    shells_ok, detail = False, "没打印 preflight 命令"
    if cmdline:
        # cmd：shell=True 才会把整串原样交给 cmd，list 形式会把内层引号转义坏。
        # POSIX（CI 的 ubuntu/macos）没有 cmd 与 pwsh：换 bash -c 做同一件事，
        # 否则这条用例在 CI 上必红，而它测的「粘贴即用」在那边同样成立。
        if os.name == "nt":
            rc_cmd = subprocess.run(cmdline, shell=True, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", env=nb_env)
        else:
            rc_cmd = subprocess.run(["bash", "-c", cmdline], capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", env=nb_env)
        shells_ok = rc_cmd.returncode == 0
        detail = (rc_cmd.stdout + rc_cmd.stderr)[-200:]
        pwsh = shutil.which("pwsh") if os.name == "nt" else None
        if shells_ok and pwsh:
            ps1 = os.path.join(tmp, "paste.ps1")
            open(ps1, "w", encoding="utf-8").write(cmdline + "\nexit $LASTEXITCODE\n")
            rc_ps = subprocess.run([pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass",
                                    "-File", ps1], capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", env=nb_env)
            shells_ok = rc_ps.returncode == 0
            detail = (rc_ps.stdout + rc_ps.stderr)[-200:]
    check("install.py 下一步可原样粘贴（cmd / PowerShell 均通过）", shells_ok, detail)
    # (f) 写不进去（skills_dir 是已有文件）：rc=1 且说人话，不许 traceback
    #     这一条会把 clone 里的 skills_dir 改成那个文件，所以放在最后。
    afile = os.path.join(tmp, "blocker-file")
    open(afile, "w").write("x")
    r = nb_run("--skills-dir", afile)
    check("install.py 写不进技能目录时给 rc=1 且无 traceback",
          r.returncode == 1 and "Traceback" not in (r.stdout + r.stderr),
          (r.stdout + r.stderr)[-200:])
    # (g) 对话夹母文件夹落在文件底下：同样要人话 + rc=1（tg_common.ensure_memory_layout 会抛 OSError）
    r = nb_run("--memory-root", os.path.join(afile, "sub"))
    check("install.py 记忆根建不了时给 rc=1 且无 traceback",
          r.returncode == 1 and "Traceback" not in (r.stdout + r.stderr),
          (r.stdout + r.stderr)[-200:])
    # (h) 显式 $DSH_HOME 不存在：必须硬失败，不能悄悄装进自动探测到的另一套 home
    ghost = os.path.join(tmp, "ghost-home")
    bad_env = {**nb_env, "DSH_HOME": ghost}
    never_cfg = os.path.join(tmp, "never-written.yaml")
    r = nb_run("--config", never_cfg, env=bad_env)
    check("install.py 在 $DSH_HOME 不存在时失败而不是换一套 home",
          r.returncode == 2 and ghost not in (r.stdout or "")
          and not os.path.exists(never_cfg),
          (r.stdout + r.stderr)[-200:])
    check("install.py 下一步可原样粘贴（cmd / PowerShell 均通过）", shells_ok, detail)

    # 5e. skills_dir 回退只认含 tg-* 的 <home>/skills：空目录/别的技能库仍须拒绝
    odd_home = os.path.join(tmp, "oddhome")
    os.makedirs(os.path.join(odd_home, "skills", "other-skill"), exist_ok=True)
    ocfg = os.path.join(tmp, "oddcfg.yaml")
    with open(ocfg, "w", encoding="utf-8") as f:
        f.write('dsh_home: "%s"\n' % odd_home.replace("\\", "/"))
        f.write('skills_dir: "%s"\n' % os.path.join(tmp, "missing-skills").replace("\\", "/"))
    r = run("preflight.py", "--config", ocfg)
    check("skills_dir 缺失且 home/skills 无 tg-* 时拒绝",
          r.returncode == 2 and "拒绝" in r.stdout, (r.stdout + r.stderr)[:200])

    # 6. 首日基线
    basehome = os.path.join(tmp, "basehome")
    os.makedirs(os.path.join(basehome, "skills"), exist_ok=True)
    for i in range(5):
        put_session(basehome, f"session-b{i}", "baseline_session.jsonl", tmp)
    bcfg = os.path.join(tmp, "bcfg.yaml")
    write_cfg(bcfg, basehome)
    bfile = os.path.join(tmp, "tg-baseline.json")
    r1 = run("baseline.py", "--config", bcfg, "--file", bfile, "--write")
    r2 = run("baseline.py", "--config", bcfg, "--file", bfile, "--compare")
    check("基线可写", r1.returncode == 0 and os.path.isfile(bfile))
    check("基线可对比", r2.returncode == 0 and "起始上下文" in r2.stdout)

    # 7. 任务成本：一条用户指令 = 一轮
    r = run("turn_cost.py", "--config", prices, "--dsh-home", home, "--days", "1", "--json")
    try:
        tc = json.loads(r.stdout).get("summary", {})
    except Exception:
        tc = {}
    check("任务成本按轮聚合", tc.get("turns") == 2, str(tc.get("turns")))
    expect_total = (580.5 + 408.6) * 7.1 / 1e6
    check("任务成本合计与手算一致", abs(float(tc.get("cost_total", 0)) - expect_total) < 0.001,
          f"{tc.get('cost_total')} vs {expect_total:.4f}")

    # 8. 技能埋点
    skhome = os.path.join(tmp, "skhome")
    os.makedirs(os.path.join(skhome, "skills", "tg-cost"), exist_ok=True)
    os.makedirs(os.path.join(skhome, "skills", "tg-router"), exist_ok=True)
    with open(os.path.join(skhome, "skills", "tg-cost", "SKILL.md"), "w", encoding="utf-8") as f:
        f.write("---\nname: tg-cost\ndisable-model-invocation: true\n---\n")
    with open(os.path.join(skhome, "skills", "tg-router", "SKILL.md"), "w", encoding="utf-8") as f:
        f.write("---\nname: tg-router\n---\n")
    put_session(skhome, "session-sk", "skills_session.jsonl", tmp)
    r = run("skill_usage.py", "--dsh-home", skhome, "--skills-dir", os.path.join(skhome, "skills"),
            "--days", "1", "--json")
    try:
        su = json.loads(r.stdout)
    except Exception:
        su = {}
    check("技能调用被统计", su.get("skill_tool_calls", {}).get("tg-cost") == 1, str(su.get("skill_tool_calls")))
    check("/名字 调用被统计", su.get("slash_calls", {}).get("tg-removed") == 1, str(su.get("slash_calls")))
    check("待机状态被识别", su.get("tg_installed", {}).get("tg-cost") == "待机", str(su.get("tg_installed")))

    # 9. 补丁快照与回滚
    phome = os.path.join(tmp, "phome")
    pdir = os.path.join(phome, "profiles", "desktop")
    os.makedirs(pdir, exist_ok=True)
    ppatch = os.path.join(pdir, "cordis.patch.yml")
    with open(ppatch, "w", encoding="utf-8") as f:
        f.write("- id: a\n")
    r1 = run("patch_snapshot.py", "--home", phome, "--snapshot")
    with open(ppatch, "w", encoding="utf-8") as f:
        f.write("- id: broken\n")
    r2 = run("patch_snapshot.py", "--home", phome, "--restore", "latest")
    restored = open(ppatch, encoding="utf-8").read()
    check("快照可拍", r1.returncode == 0 and "已拍快照" in r1.stdout)
    check("回滚恢复内容", r2.returncode == 0 and restored.strip() == "- id: a")

    # 10. 失败重试分析
    r = run("failure_analysis.py", "--dsh-home", home, "--days", "1", "--json")
    try:
        fa = json.loads(r.stdout)
    except Exception:
        fa = {}
    check("失败调用被统计", fa.get("failed_calls") == 1, str(fa.get("failed_calls")))
    check("失败工具归类", (fa.get("by_tool") or {}).get("ops_bash") == 1, str(fa.get("by_tool")))
    check("失败轮次被统计", fa.get("turns_with_failure") == 1, str(fa.get("turns_with_failure")))

    # 11. 协议错误过滤
    put_session(home, "session-proto", "protocol_session.jsonl", tmp)
    r = run("failure_analysis.py", "--dsh-home", home, "--days", "1", "--protocol-only", "--json")
    try:
        fp = json.loads(r.stdout)
    except Exception:
        fp = {}
    check("协议错误被单列", fp.get("protocol_failed_calls", 0) >= 1, str(fp.get("protocol_failed_calls")))
    check("协议错误只含一类工具", list((fp.get("by_tool") or {}).keys()) == ["edit"], str(fp.get("by_tool")))

    # 12. schema 归因与预算
    schhome = os.path.join(tmp, "schhome")
    os.makedirs(os.path.join(schhome, "skills"), exist_ok=True)
    put_session(schhome, "session-sch", "schema_session.jsonl", tmp)
    scfg = os.path.join(tmp, "scfg.yaml")
    write_cfg(scfg, schhome)
    r = run("schema_budget.py", "--dsh-home", schhome, "--config", scfg, "--json")
    try:
        sb = json.loads(r.stdout)
    except json.JSONDecodeError:
        sb = {}
    foo = [g for g in (sb.get("groups") or []) if g.get("group") == "mcp:foo"]
    check("schema 按服务归因", bool(foo) and foo[0].get("tools") == 2, str(sb.get("groups"))[:120])
    check("schema 工具总数", sb.get("tools") == 4, str(sb.get("tools")))
    r2 = run("schema_budget.py", "--dsh-home", schhome, "--config", scfg, "--check", "--budget-kb", "1")
    r3 = run("schema_budget.py", "--dsh-home", schhome, "--config", scfg, "--check", "--budget-kb", "500")
    check("超预算退出码 1", r2.returncode == 1, str(r2.returncode))
    check("预算内退出码 0", r3.returncode == 0, str(r3.returncode))

    # 13. doctor 单入口：采集失败也要出动作清单，不能崩；空数据必须给 first-run 而不是空数组
    empty_home = os.path.join(tmp, "doctor-empty-home")
    os.makedirs(empty_home, exist_ok=True)
    # 必须配一份指向空 home 的 config：否则子脚本读仓库里的 config.yaml，
    # 会把本机真实账本/技能算进来，用例就测不到「空数据」了。
    dcfg = os.path.join(tmp, "doctor-empty.yaml")
    write_cfg(dcfg, empty_home)
    r = run("doctor.py", "--days", "1", "--json", "--dsh-home", empty_home, "--config", dcfg)
    try:
        doc = json.loads(r.stdout)
    except json.JSONDecodeError:
        doc = {}
    acts = doc.get("actions") or []
    check("doctor 空数据也给动作清单（first-run）",
          isinstance(acts, list) and any(a.get("id") == "first-run" for a in acts),
          f"actions={[a.get('id') for a in acts]}")
    check("doctor 缺数据不崩", r.returncode == 0, str(r.returncode))

    # 13b. 新装当天：有技能、没有会话时不能建议删技能，应给 first-run
    fresh_home = os.path.join(tmp, "doctor-fresh-home")
    shutil.copytree(os.path.join(ROOT, "skills"), os.path.join(fresh_home, "skills"))
    fcfg = os.path.join(tmp, "doctor-fresh.yaml")
    write_cfg(fcfg, fresh_home)
    r = run("doctor.py", "--days", "1", "--json", "--dsh-home", fresh_home, "--config", fcfg)
    try:
        doc2 = json.loads(r.stdout)
    except json.JSONDecodeError:
        doc2 = {}
    ids2 = [x.get("id") for x in (doc2.get("actions") or [])]
    check("新装无会话给 first-run 而不是删技能", ids2 == ["first-run"], str(ids2))

    # 14. 守卫命中统计（读 events.jsonl；坏行跳过）
    gdir = os.path.join(tmp, "guard")
    os.makedirs(gdir, exist_ok=True)
    gpath = os.path.join(gdir, "events.jsonl")
    with open(gpath, "w", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "2026-10-23T08:00:00+00:00", "tool": "read",
                            "count": 2, "hash": "abc", "argsLen": 12}) + "\n")
        f.write(json.dumps({"ts": "2026-10-23T09:00:00+00:00", "tool": "read",
                            "count": 4, "hash": "abc", "argsLen": 12}) + "\n")
        f.write("这不是 json\n")
    r = run("guard_stats.py", "--file", gpath, "--days", "30", "--json")
    try:
        gs = json.loads(r.stdout)
    except json.JSONDecodeError:
        gs = {}
    check("守卫命中被统计", gs.get("events") == 2, str(gs.get("events")))
    check("守卫坏行被跳过", gs.get("bad_lines") == 1, str(gs.get("bad_lines")))
    check("守卫按工具聚合", (gs.get("top_tools") or [{}])[0].get("hits") == 2,
          str(gs.get("top_tools")))

    # 15. dsh_home 口径单一：显式 > config > $DSH_HOME > ~/.dsh，相对路径按 config 目录解析
    sys.path.insert(0, SCRIPTS)
    import tg_common

    cfg_dir = os.path.join(tmp, "cfgdir")
    os.makedirs(cfg_dir, exist_ok=True)
    cfg_file = os.path.join(cfg_dir, "config.yaml")
    write_cfg(cfg_file, os.path.join(tmp, "from-cfg"))
    cfg, base = tg_common.load_config(cfg_file)
    norm = os.path.normpath
    check("dsh_home：显式参数优先于 config",
          tg_common.resolve_dsh_home(os.path.join(tmp, "explicit"), cfg=cfg, base=base)
          == norm(os.path.join(tmp, "explicit")))
    check("dsh_home：config 优先于 $DSH_HOME",
          tg_common.resolve_dsh_home("", cfg=cfg, base=base, env={"DSH_HOME": os.path.join(tmp, "env")})
          == norm(os.path.join(tmp, "from-cfg")))
    check("dsh_home：没有 config 时退回 $DSH_HOME",
          tg_common.resolve_dsh_home("", cfg={}, base=tmp, env={"DSH_HOME": os.path.join(tmp, "env")})
          == norm(os.path.join(tmp, "env")))
    check("dsh_home：都没有时退回 ~/.dsh",
          tg_common.resolve_dsh_home("", cfg={}, base=tmp, env={})
          == norm(os.path.expanduser("~/.dsh")))
    rel_cfg = os.path.join(cfg_dir, "rel.yaml")
    with open(rel_cfg, "w", encoding="utf-8") as f:
        f.write('dsh_home: "rel-home"\n')
    rel_cfg_data, rel_base = tg_common.load_config(rel_cfg)
    check("dsh_home：相对路径按 config 目录解析",
          tg_common.resolve_dsh_home("", cfg=rel_cfg_data, base=rel_base)
          == norm(os.path.join(cfg_dir, "rel-home")))
    check("dsh_home：多个 home 按真实路径去重",
          len(tg_common.resolve_dsh_homes("", cfg={"dsh_homes": [tmp, tmp + os.sep]}, base=tmp)) == 1)

    # 口径不许再手写：静态检查走「常量折叠 + 结构位置」，子串匹配会漏别名/拼接/setdefault，
    # 还会把注释里的示例误报。折叠函数把 "DSH_" + "HOME"、f"{a}sessions" 还原成字面量。
    import ast

    def const_str(node):
        """能静态求值的字符串就求出来：Constant / "a"+"b" / 纯字面 f-string。"""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            a, b = const_str(node.left), const_str(node.right)
            if a is not None and b is not None:
                return a + b
        if isinstance(node, ast.JoinedStr):
            parts = []
            for v in node.values:
                if isinstance(v, ast.FormattedValue):
                    return None
                p = const_str(v)
                if p is None:
                    return None
                parts.append(p)
            return "".join(parts)
        return None

    def code_strings(tree):
        """文件里所有「出现在可执行位置」的字符串字面量（跳过 docstring）。"""
        docs = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                body = getattr(node, "body", [])
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                        and isinstance(body[0].value.value, str):
                    docs.add(id(body[0].value))
        for node in ast.walk(tree):
            if id(node) in docs:
                continue
            s = const_str(node)
            if s is not None:
                yield node, s

    def ast_find(path):
        """返回 (DSH_HOME 读取行, sessions 拼接行)。"""
        tree = ast.parse(open(path, encoding="utf-8").read())
        home_lines, sess_lines = [], []
        for node, s in code_strings(tree):
            if s == "DSH_HOME":  # 只认「整个字面量就是键名」；帮助文本里的 $DSH_HOME 示例不算
                home_lines.append(getattr(node, "lineno", 0))
        for node in ast.walk(tree):
            hit = False
            # os.path.join(..., "sessions")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "join":
                hit = any(const_str(a) == "sessions" for a in node.args)
            # Path(home) / "sessions"
            elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                hit = const_str(node.right) == "sessions"
            # f"{home}/sessions"
            elif isinstance(node, ast.JoinedStr):
                hit = any(const_str(v) in ("sessions", "/sessions", "\\sessions")
                          for v in node.values if not isinstance(v, ast.FormattedValue))
            if hit:
                sess_lines.append(getattr(node, "lineno", 0))
        return sorted(set(home_lines)), sorted(set(sess_lines))

    offenders, builders = [], []
    for fn in sorted(os.listdir(SCRIPTS)):
        if not fn.endswith(".py") or fn == "tg_common.py":
            continue
        p = os.path.join(SCRIPTS, fn)
        try:
            home_lines, sess_lines = ast_find(p)
        except SyntaxError as e:
            offenders.append(f"{fn}(语法错误 {e.lineno})")
            continue
        offenders += [f"{fn}:{n}" for n in home_lines]
        builders += [f"{fn}:{n}" for n in sess_lines]
    check("没有脚本再手写 DSH_HOME 解析", not offenders, ", ".join(offenders))
    check("没有脚本再自己拼 sessions 路径", not builders, ", ".join(builders))

    # 这两条静态检查自己要能抓住常见绕法（变异用例，不写仓库文件）
    samples = {
        "getenv": 'import os\nx = os.getenv("DSH_HOME")\n',
        "setdefault": 'import os\nos.environ.setdefault("DSH_HOME", "x")\n',
        "concat": 'import os\nx = os.environ.get("DSH_" + "HOME")\n',
        "alias": 'from os import getenv as g\nx = g("DSH_HOME")\n',
        "subscript": 'import os\nx = os.environ["DSH_HOME"]\n',
        "comment-only": '# os.environ.get("DSH_HOME") 只是注释\nx = 1\n',
    }
    for name, code in samples.items():
        sm = os.path.join(tmp, f"static_{name}.py")
        with open(sm, "w", encoding="utf-8") as f:
            f.write(code)
        hits, _ = ast_find(sm)
        if name == "comment-only":
            check(f"静态检查不误报注释（{name}）", not hits, str(hits))
        else:
            check(f"静态检查能抓住 {name} 写法", bool(hits), "漏了")
    sess_samples = {
        "join": 'import os\np = os.path.join(h, "sessions")\n',
        "fstring": 'p = f"{h}/sessions"\n',
        "pathdiv": 'from pathlib import Path\np = Path(h) / "sessions"\n',
        "json-key": 'out = {"sessions": 3}\n',
    }
    for name, code in sess_samples.items():
        sm = os.path.join(tmp, f"sess_{name}.py")
        with open(sm, "w", encoding="utf-8") as f:
            f.write(code)
        _, s_hits = ast_find(sm)
        if name == "json-key":
            check(f"sessions 检查不误报 JSON 键（{name}）", not s_hits, str(s_hits))
        else:
            check(f"sessions 检查能抓住 {name} 写法", bool(s_hits), "漏了")

    # 18. 端到端：只给 config、不传 --dsh-home 时必须读 config 指的那份 home
    e2e_home = os.path.join(tmp, "e2e-home")
    env_home = os.path.join(tmp, "e2e-env")
    os.makedirs(env_home, exist_ok=True)
    put_session(e2e_home, "session-e2e", "retry_session.jsonl", tmp)
    put_session(env_home, "session-env1", "literal_session.jsonl", tmp)
    put_session(env_home, "session-env2", "literal_session.jsonl", tmp)
    e2e_cfg = os.path.join(tmp, "e2e.yaml")
    write_cfg(e2e_cfg, e2e_home)
    env = dict(os.environ)
    env["DSH_HOME"] = env_home  # 环境变量指向另一套：config 仍须赢
    r = subprocess.run([PY, os.path.join(SCRIPTS, "retry_patterns.py"),
                        "--config", e2e_cfg, "--days", "30", "--json"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    try:
        e2e = json.loads(r.stdout)
    except json.JSONDecodeError:
        e2e = {}
    check("只给 config 时读 config 的 home（不受 DSH_HOME 干扰）",
          e2e.get("sessions") == 1, f"sessions={e2e.get('sessions')} rc={r.returncode}")

    e2e_env_only = subprocess.run([PY, os.path.join(SCRIPTS, "retry_patterns.py"),
                                   "--days", "30", "--json"],
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", env=env, cwd=tmp)
    try:
        e2e2 = json.loads(e2e_env_only.stdout)
    except json.JSONDecodeError:
        e2e2 = {}
    check("没有 config 时退回 $DSH_HOME",
          e2e2.get("sessions") == 2, f"sessions={e2e2.get('sessions')} rc={e2e_env_only.returncode}")

    # compare_days 也要有覆盖开关（v0.12.0 由 env 优先改为 config 优先，需能压回去）
    ledger = {"days": {"2026-10-20": {"calls": 10, "costCny": 1.0}, "2026-10-21": {"calls": 10, "costCny": 2.0}}}
    os.makedirs(os.path.join(e2e_home, "storages", "cost-meter"), exist_ok=True)
    with open(os.path.join(e2e_home, "storages", "cost-meter", "ledger.json"), "w", encoding="utf-8") as f:
        json.dump(ledger, f)
    r = run("compare_days.py", "--config", e2e_cfg, "--days", "2")
    check("compare_days 只给 config 时能读到账本", r.returncode == 0 and "2026-10-21" in r.stdout,
          f"rc={r.returncode}")
    r = run("compare_days.py", "--config", e2e_cfg, "--dsh-home", os.path.join(tmp, "nope"), "--days", "2")
    check("compare_days 能用 --dsh-home 压过 config", r.returncode != 0, f"rc={r.returncode}")

    # 配置写坏时必须报错，不能静默换 home 出数。按「所有接受 --config 的脚本」遍历，
    # 并把清单与 scripts/ 的实际 argparse 选项对账（漏一个就红，避免只测 4 个的假绿）。
    bad_cfg = os.path.join(tmp, "bad.yaml")
    with open(bad_cfg, "w", encoding="utf-8") as f:
        f.write('dsh_home: "E:\\apps\\dsh"\n')  # 双引号里的反斜杠 = YAML 转义，必坏
    list_cfg = os.path.join(tmp, "list.yaml")
    with open(list_cfg, "w", encoding="utf-8") as f:
        f.write("- a\n- b\n")                 # 顶层不是映射
    gbk_cfg = os.path.join(tmp, "gbk.yaml")
    with open(gbk_cfg, "wb") as f:
        f.write('dsh_home: "D:/x"\nmemory_root: "~/记忆"\n'.encode("gbk"))

    # env_check 是自检入口：坏配置下它必须照常给出诊断，不参与「一律报错」那条
    config_scripts = ("baseline.py", "cache_audit.py", "compare_days.py", "cost_summary.py",
                      "failure_analysis.py", "guard_stats.py", "measure_modules.py",
                      "patch_snapshot.py", "preflight.py", "retry_patterns.py",
                      "schema_budget.py", "skill_usage.py", "turn_cost.py", "new_session.py",
                      "install_guard.py", "install_account.py", "handoff.py", "setup.py", "install.py")
    tolerant_scripts = {"env_check.py", "doctor.py"}
    # 对账：凡是 argparse 里有 --config 的脚本都必须在上面
    declared = set()
    for fn in os.listdir(SCRIPTS):
        if fn.endswith(".py") and fn != "tg_common.py" and "--config" in open(os.path.join(SCRIPTS, fn), encoding="utf-8").read():
            declared.add(fn)
    check("坏配置用例覆盖所有带 --config 的脚本",
          declared <= (set(config_scripts) | tolerant_scripts),
          f"漏: {sorted(declared - set(config_scripts) - tolerant_scripts)}")

    extra = {"new_session.py": ("__probe__", "sid1"),
             "install_guard.py": ("--status",),
             "install_account.py": ("--status",),
             "handoff.py": ("--session", "ab12cd34", "--slug", "t")}
    for script in config_scripts:
        args = list(extra.get(script, ())) + ["--config", bad_cfg]
        r = run(script, *args)
        ok = r.returncode != 0 and ("YAML" in (r.stderr or "") or "读不了" in (r.stderr or ""))
        check(f"坏配置：{script} 报错", ok, f"rc={r.returncode} err={(r.stderr or '')[:60]}")
        r = run(script, *list(extra.get(script, ())), "--config", list_cfg)
        check(f"顶层非映射：{script} 报错", r.returncode != 0, f"rc={r.returncode}")
        r = run(script, *list(extra.get(script, ())), "--config", gbk_cfg)
        check(f"GBK 配置：{script} 报错且不抛 traceback",
              r.returncode != 0 and "Traceback" not in (r.stderr or ""), f"rc={r.returncode}")

    # 坏配置下 new_session 不许在 ~/tg-memory 建目录
    probe_root = os.path.join(os.path.expanduser("~"), "tg-memory", "__probe_test__")
    if os.path.isdir(probe_root):
        import shutil as _sh
        _sh.rmtree(probe_root, ignore_errors=True)
    r = run("new_session.py", "__probe_test__", "sid1", "--config", bad_cfg)
    check("坏配置下 new_session 不建目录", not os.path.isdir(probe_root), f"rc={r.returncode}")

    # 19. 环境稳健性：缺依赖不抛 traceback / 环境自检判定 / 路径与编码 / 目标不可写
    def run_env(script, args, extra_env=None, path_override=None):
        env2 = dict(os.environ)
        env2["PYTHONIOENCODING"] = "utf-8"
        env2.update(extra_env or {})
        if path_override is not None:
            env2["PATH"] = path_override
        return subprocess.run([PY, os.path.join(SCRIPTS, script), *args],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=env2, cwd=ROOT)

    dep_noyaml = os.path.join(tmp, "dep_noyaml")
    dep_nozstd = os.path.join(tmp, "dep_nozstd")
    for d, mod, msg in ((dep_noyaml, "yaml", "no yaml"), (dep_nozstd, "zstandard", "no zstd")):
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, mod + ".py"), "w", encoding="utf-8") as f:
            f.write(f'raise ImportError("{msg}")\n')

    script_args = {
        "baseline.py": ["--config", e2e_cfg],
        "cache_audit.py": [],
        "compare_days.py": ["--config", e2e_cfg, "--days", "2"],
        "cost_summary.py": ["--config", e2e_cfg],
        "failure_analysis.py": ["--config", e2e_cfg, "--days", "30"],
        "guard_stats.py": ["--config", e2e_cfg],
        "measure_modules.py": ["--config", e2e_cfg, "--list"],
        "patch_snapshot.py": ["--config", e2e_cfg, "--list"],
        "preflight.py": ["--config", e2e_cfg],
        "retry_patterns.py": ["--config", e2e_cfg, "--days", "30"],
        "schema_budget.py": ["--config", e2e_cfg],
        "skill_usage.py": ["--config", e2e_cfg, "--days", "30"],
        "turn_cost.py": ["--config", e2e_cfg, "--days", "30"],
        "new_session.py": ["w", "s", "--root", os.path.join(tmp, "dep-mem"), "--config", e2e_cfg],
        "check_yaml.py": [os.path.join(tmp, "patch_sample.yml")],
        "env_check.py": ["--config", e2e_cfg],
        "audit_pack.py": [],
        "gen_metrics.py": ["--check"],
    }
    with open(os.path.join(tmp, "patch_sample.yml"), "w", encoding="utf-8") as f:
        f.write("- id: demo\n")
    py_dir = os.path.dirname(PY)
    # 去掉 PATH 里的 zstd 与模块：解压器算缺失（PATH 构造按平台来，别把 Windows 的 SystemRoot 搬到 POSIX）
    if os.name == "nt":
        sysroot = os.environ.get("SystemRoot", r"C:\Windows")
        bare_path = os.pathsep.join([py_dir, sysroot, os.path.join(sysroot, "system32")])
    else:
        bare_path = py_dir
    for label, depdir in (("缺 pyyaml", dep_noyaml), ("缺 zstandard", dep_nozstd)):
        offenders = []
        for script, args in script_args.items():
            r = run_env(script, args, extra_env={"PYTHONPATH": depdir})
            if "Traceback" in (r.stdout + r.stderr):
                offenders.append(f"{script}:{r.returncode}")
        check(f"{label} 时没有脚本抛 traceback", not offenders, ", ".join(offenders))

    # 第三组：zstd 命令与 zstandard 模块**同时**没有 —— 上一组只注入了模块，本机 CLI 还在，
    # 根本走不到「解压需要二选一」那一支（评审指出过）。
    both_missing = []
    for script, args in script_args.items():
        r = run_env(script, args, extra_env={"PYTHONPATH": dep_nozstd}, path_override=bare_path)
        if "Traceback" in (r.stdout + r.stderr):
            both_missing.append(f"{script}:{r.returncode}")
    check("解压器完全缺失时没有脚本抛 traceback", not both_missing, ", ".join(both_missing))
    r = run_env("baseline.py", ["--config", e2e_cfg], extra_env={"PYTHONPATH": dep_nozstd},
                path_override=bare_path)
    if has_builtin_zstd:
        # 3.14 起有内建解压器：CLI 与 zstandard 都没有也能跑，「二选一」提示不该出现
        check("解压器 CLI/模块都缺时 3.14 内建解压兜底",
              "解压需要二选一" not in (r.stdout + r.stderr)
              and "Traceback" not in (r.stdout + r.stderr),
              (r.stdout + r.stderr)[:80])
    else:
        check("解压器完全缺失时 baseline 给「二选一」提示",
              "解压需要二选一" in (r.stdout + r.stderr) and "zstandard" in (r.stdout + r.stderr),
              (r.stdout + r.stderr)[:80])

    # 环境自检：必需项缺失必须非零退出，且 JSON 里能点名缺什么；rc 与 JSON 的 ok 必须一致
    r = run_env("env_check.py", ["--json"])
    try:
        ec_full = json.loads(r.stdout)
    except json.JSONDecodeError:
        ec_full = {}
    check("env_check 的退出码与 JSON ok 一致",
          r.returncode == (0 if ec_full.get("ok") else 1),
          f"rc={r.returncode} ok={ec_full.get('ok')}")
    r = run_env("env_check.py", ["--json"], extra_env={"PYTHONPATH": dep_noyaml})
    try:
        ec = json.loads(r.stdout)
    except json.JSONDecodeError:
        ec = {}
    check("env_check 缺 pyyaml 时非零且点名",
          r.returncode == 1 and "pyyaml" in (ec.get("missing_required") or []),
          f"rc={r.returncode} missing={ec.get('missing_required')}")
    r = run_env("env_check.py", ["--json"], extra_env={"PYTHONPATH": dep_nozstd}, path_override=bare_path)
    try:
        ec2 = json.loads(r.stdout)
    except json.JSONDecodeError:
        ec2 = {}
    if has_builtin_zstd:
        # 3.14 起内建 compression.zstd 就是一条可用通道：自检必须与 tg_common 同口径判「齐备」。
        # 旧断言写死「缺解压器必须报缺」——在 3.14 上等于把 bug 当规格：用户被指去装
        # zstandard，而所有脚本本来就能解压。口径不一致比缺件更难排查。
        check("env_check 与 tg_common 解压口径一致（有内建时判齐备）",
              r.returncode == 0 and "unzip" not in (ec2.get("missing_required") or []),
              f"rc={r.returncode} missing={ec2.get('missing_required')}")
    else:
        check("env_check 缺解压器时非零且点名",
              r.returncode == 1 and "unzip" in (ec2.get("missing_required") or []),
              f"rc={r.returncode} missing={ec2.get('missing_required')}")

    # 解压实现只能有一份：副本会漂移成「别的脚本能跑、这个脚本说解压器缺失」
    rp_src = open(os.path.join(SCRIPTS, "retry_patterns.py"), encoding="utf-8").read()
    check("retry_patterns 复用 tg_common 的解压实现（不再自带副本）",
          "decompress_bytes" in rp_src and "def decompress(" not in rp_src, "仍有本地解压实现")

    # 守卫自检里的上游路径不许写死本机：写死后换机器就静默跳过一致性检查。
    # 只查「盘符 + 本机特征目录」，不查所有盘符字面量——正则里的 `:\s` 会被误判成 `e:\`。
    vg_src = open(os.path.join(SCRIPTS, "verify_guard.mjs"), encoding="utf-8").read()
    vg_bad = re.findall(r"[A-Za-z]:[\\/](?:AI|DSH-NEXT|local-plugins|Users)\b", vg_src, re.I)
    check("verify_guard 不含写死的本机路径（上游靠探测 + TG_UPSTREAM）",
          not vg_bad and "TG_UPSTREAM" in vg_src and "findUpstream" in vg_src,
          f"命中 {vg_bad[:2]}")

    # detect_home：home 由安装位置决定，靠桌面版指针文件也要找得到（换机器最常卡在这）
    fake_user = os.path.join(tmp, "fake-user")
    fake_app = os.path.join(fake_user, "AppData", "Roaming", "DSH Demo")
    real_home = os.path.join(tmp, "real-dsh-home")
    os.makedirs(os.path.join(fake_app, "home"), exist_ok=True)
    os.makedirs(os.path.join(real_home, "sessions", "grp", "s1"), exist_ok=True)
    os.makedirs(os.path.join(real_home, "storages"), exist_ok=True)
    with open(os.path.join(real_home, "sessions", "grp", "s1", "session.v4.jsonl.zstd"), "wb") as f:
        f.write(b"")
    with open(os.path.join(fake_app, "home", "desktop-next-location.json"), "w",
              encoding="utf-8") as f:
        json.dump({"home": real_home}, f)
    dh_env = {"USERPROFILE": fake_user, "HOME": fake_user, "DSH_HOME": "",
              "APPDATA": os.path.join(fake_user, "AppData", "Roaming"),
              "LOCALAPPDATA": os.path.join(fake_user, "AppData", "Local")}
    r = run_env("detect_home.py", ["--json", "--no-scan",
                                   "--config", os.path.join(tmp, "no-such.yaml")],
                extra_env=dh_env)
    try:
        dh = json.loads(r.stdout)
    except json.JSONDecodeError:
        dh = {}
    check("detect_home 靠指针文件找到 home（无 config）",
          os.path.normcase(dh.get("recommended") or "") == os.path.normcase(real_home),
          f"rc={r.returncode} got={dh.get('recommended')}")

    # audit_pack 的扫描面：正斜杠绝对路径写在 .mjs 里也必须抓（.mjs 曾经不在扫描范围）
    fake_pack = os.path.join(tmp, "fake-pack")
    os.makedirs(os.path.join(fake_pack, "scripts"), exist_ok=True)
    with open(os.path.join(fake_pack, "x.mjs"), "w", encoding="utf-8") as f:
        # 故意拆开字面量：这个文件本身也在 audit_pack 的扫描范围内
        f.write("const u = 'D:" + "/AI/somewhere/index.js'\n")
    r = run_env("audit_pack.py", ["--root", fake_pack])
    check("audit_pack 能抓 .mjs 里的正斜杠绝对路径",
          "绝对路径: x.mjs" in (r.stdout + r.stderr),
          (r.stdout + r.stderr).strip().splitlines()[:2])

    # env_check 也要把「配置写坏」判成缺项（否则会拿着回退的 ~/.dsh 报「齐备」）
    r = run_env("env_check.py", ["--json", "--config", bad_cfg])
    try:
        ec3 = json.loads(r.stdout)
    except json.JSONDecodeError:
        ec3 = {}
    check("env_check 在坏配置下非零且 config_error 非空",
          r.returncode == 1 and ec3.get("config_error") and "config" in (ec3.get("missing_required") or []),
          f"rc={r.returncode} err={str(ec3.get('config_error'))[:50]}")

    # BOM + 空格 + 中文路径：能跑通
    odd_home = os.path.join(tmp, "带 空格 的 home")
    put_session(odd_home, "session-odd", "baseline_session.jsonl", tmp)
    odd_cfg = os.path.join(tmp, "odd-cfg.yaml")
    with open(odd_cfg, "w", encoding="utf-8-sig") as f:  # BOM
        f.write(f'dsh_home: "{odd_home.replace(os.sep, "/")}"\n'
                f'skills_dir: "{tmp.replace(os.sep, "/")}"\n')
    r = run("baseline.py", "--config", odd_cfg)
    check("BOM + 空格 + 中文路径下 baseline 能跑", r.returncode == 0 and "samples" in r.stdout,
          f"rc={r.returncode} err={(r.stderr or '')[:60]}")

    # 中文用户名：别人机器上 `C:\Users\张三` 很常见。配置里写 `~/dsh-home`，逼 expanduser 走一遍
    # （路径拼接与编码在前一条已覆盖，这里单独盯「用户目录名含中文」这一层）
    cn_ok, cn_detail = True, "POSIX 下 ~ 由 pwd 决定，本用例只对 Windows 的 USERPROFILE 成立"
    if os.name == "nt":
        cn_user = os.path.join(tmp, "张三")
        cn_home = os.path.join(cn_user, "dsh-home")
        put_session(cn_home, "session-cn", "baseline_session.jsonl", tmp)
        cn_cfg = os.path.join(tmp, "cn-cfg.yaml")
        with open(cn_cfg, "w", encoding="utf-8") as f:
            f.write('dsh_home: "~/dsh-home"\n')
        cn_env = dict(os.environ)
        cn_env["USERPROFILE"] = cn_user
        cn_env["HOME"] = cn_user
        r = subprocess.run([PY, os.path.join(SCRIPTS, "baseline.py"), "--config", cn_cfg],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=cn_env, cwd=ROOT)
        cn_ok = r.returncode == 0 and "samples" in r.stdout
        cn_detail = f"rc={r.returncode} err={(r.stderr or '')[:60]}"
    check("中文用户名下 ~ 展开正确（Windows 走 USERPROFILE）", cn_ok, cn_detail)

    # 无 HOME / USERPROFILE：可读报错 + 明确提示 ~ 展不开（POSIX 下 expanduser 走 pwd，跳过）
    if os.name == "nt":
        # DSH_HOME 也是 home 来源之一：宿主若注入了它，就永远走不到「~ 展不开」那一支，
        # 用例会因为「跑在哪个 shell 里」而时红时绿，所以这里必须一起删掉。
        no_home_env = {k: v for k, v in os.environ.items()
                       if k.upper() not in ("HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
                                            "DSH_HOME")}
        r = subprocess.run([PY, os.path.join(SCRIPTS, "retry_patterns.py"),
                            "--days", "3", "--config", os.path.join(tmp, "nope.yaml")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=no_home_env, cwd=ROOT)
        check("无 HOME 时提示 ~ 展不开且无 traceback",
              "无法展开" in (r.stderr or "") and "Traceback" not in (r.stderr or ""),
              (r.stderr or "")[:80])
        r = subprocess.run([PY, os.path.join(SCRIPTS, "new_session.py"),
                            "w", "s", "--root", "~"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=no_home_env, cwd=ROOT)
        check("无 HOME 时 new_session 拒绝在 cwd 建字面 ~ 目录",
              r.returncode != 0 and not os.path.isdir(os.path.join(ROOT, "~")),
              f"rc={r.returncode}")
    else:
        # 平台相关用例也算一条：计数必须与平台无关，否则 data/counts.json 无法做单一真相
        for name in ("无 HOME 时提示 ~ 展不开且无 traceback",
                     "无 HOME 时 new_session 拒绝在 cwd 建字面 ~ 目录"):
            results.append((name, True, "skip"))
            print(f"PASS {name}  SKIP（仅 Windows：POSIX 的 expanduser 走 pwd）")

    # 目标不可写（用「根是文件」当探针）：可读报错、不留目录
    as_file = os.path.join(tmp, "not-a-dir")
    with open(as_file, "w", encoding="utf-8") as f:
        f.write("x")
    r = run("new_session.py", "w", "s", "--root", as_file)
    check("目标不可写时 new_session 可读报错",
          r.returncode != 0 and "Traceback" not in (r.stderr or ""), f"rc={r.returncode}")
    check("目标不可写时不留下目录", not os.path.exists(os.path.join(as_file, "w")), "")

    # 自检类脚本（env_check / doctor）在坏配置下必须照常给诊断，不能跟着一起退出
    r = run("env_check.py", "--json", "--config", bad_cfg)
    check("坏配置：env_check 仍出报告且无 traceback",
          "Traceback" not in (r.stderr or "") and r.returncode in (0, 1), f"rc={r.returncode}")
    r = run("doctor.py", "--days", "1", "--json", "--config", bad_cfg)
    check("坏配置：doctor 仍出体检单（first-run）且无 traceback",
          r.returncode == 0 and "first-run" in r.stdout, f"rc={r.returncode}")

    # CI 上的本机标识检查必须跳过：runner 的用户名不是秘密，而 docs 里出现「runner」会误报成高危
    r = subprocess.run([PY, os.path.join(SCRIPTS, "audit_pack.py")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, "CI": "true", "GITHUB_ACTIONS": "true", "USER": "runner"},
                       cwd=ROOT)
    check("CI 环境下 audit_pack 不把 runner 当泄露", r.returncode == 0,
          (r.stdout + r.stderr)[-120:])
    # 普通用户名（user/test/dev）不是泄露线索：只认路径形状，否则 Linux/macOS 一片高危
    r = subprocess.run([PY, os.path.join(SCRIPTS, "audit_pack.py")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, "USER": "test", "USERNAME": "test"},
                       cwd=ROOT)
    check("普通用户名不触发 audit_pack 高危", r.returncode == 0, (r.stdout + r.stderr)[-120:])
    # 真泄露要能抓住：造一个含本机路径形状的文件（临时目录里，不进仓库）
    leak_dir = os.path.join(tmp, "leakproj")
    os.makedirs(os.path.join(leak_dir, "docs"), exist_ok=True)
    with open(os.path.join(leak_dir, "docs", "a.md"), "w", encoding="utf-8") as f:
        f.write("路径：C:/Users/tgcanary/secret/x\n")
    with open(os.path.join(leak_dir, "README.md"), "w", encoding="utf-8") as f:
        f.write("# x\n")
    r = subprocess.run([PY, os.path.join(SCRIPTS, "audit_pack.py"), "--root", leak_dir,
                        "--local-id", "tgcanary"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT)
    check("路径里的本机标识会被 audit_pack 抓住", r.returncode == 1 and "tgcanary" not in r.stdout,
          (r.stdout + r.stderr)[-120:])

    # 「同名假模块」：能 import 但没有 API。只注入 ImportError 的 shim 抓不到这一类，
    # 必须用 VERSION-only 的 shim 把两条分支都跑一遍（env_check 判定 + 解压那句话）。
    fake_zstd = os.path.join(tmp, "fake_zstd")
    os.makedirs(fake_zstd, exist_ok=True)
    with open(os.path.join(fake_zstd, "zstandard.py"), "w", encoding="utf-8") as f:
        f.write('VERSION = "0.0.0"\n')
    r = run_env("env_check.py", ["--json"], extra_env={"PYTHONPATH": fake_zstd},
                path_override=bare_path)
    try:
        ec4 = json.loads(r.stdout)
    except json.JSONDecodeError:
        ec4 = {}
    unzip_row = next((c for c in ec4.get("checks") or [] if c.get("key") == "unzip"), {})
    if has_builtin_zstd:
        # 3.14 内建通道可用：假 zstandard 不该把 unzip 判成缺失（自检与实现同口径）
        check("假 zstandard 模块不影响内建解压通道的判定",
              r.returncode == 0 and unzip_row.get("ok") is True
              and "内建" in (unzip_row.get("detail") or ""),
              f"rc={r.returncode} {str(unzip_row.get('detail'))[:80]}")
    else:
        check("假 zstandard 模块不被 env_check 判可用",
              r.returncode == 1 and unzip_row.get("ok") is False
              and "ZstdDecompressor" in (unzip_row.get("detail") or ""),
              str(unzip_row.get("detail"))[:80])
    r = run_env("baseline.py", ["--config", e2e_cfg], extra_env={"PYTHONPATH": fake_zstd},
                path_override=bare_path)
    # Python ≥ 3.14 自带 compression.zstd：解压不再依赖 zstandard，假模块根本不会被调用。
    # 低版本 Python 才走 zstandard 回退，此时必须给一句话而不是 traceback。
    if has_builtin_zstd:
        # baseline 可能因为「会话步数不够」而正常退出 1：这里只关心它没走缺解压器的分支
        output = r.stdout + r.stderr
        check("内建 compression.zstd 优先，假 zstandard 不误伤",
              "Traceback" not in output and "解压需要二选一" not in output
              and "ZstdDecompressor" not in output, output[:120])
    else:
        # 断言的是「给一句话、不抛 traceback」，不是某一层的具体措辞：
        # 缺解压器时 tg_common 给的是「解压需要二选一…」，env_check 那条才提 ZstdDecompressor。
        # 旧写法硬要 baseline 输出里出现 ZstdDecompressor，在 py<3.14 上必然误报（2026-10-25 CI 实测）。
        out = r.stdout + r.stderr
        check("假 zstandard 模块解压时给一句话而不是 AttributeError",
              r.returncode != 0 and "Traceback" not in out and "AttributeError" not in out
              and ("解压需要二选一" in out or "ZstdDecompressor" in out),
              out[:80])

    # 徽章漂移必须阻断（「中」不阻断 CI 等于没门禁）
    badge_root = os.path.join(tmp, "badgeproj")
    for sub in ("data", "scripts", "skills", "docs"):
        os.makedirs(os.path.join(badge_root, sub), exist_ok=True)
    with open(os.path.join(badge_root, "data", "counts.json"), "w", encoding="utf-8") as f:
        json.dump({"version": "0.13.0", "scripts": 0, "skills": 0, "tests": 1}, f)
    with open(os.path.join(badge_root, "CHANGELOG.md"), "w", encoding="utf-8") as f:
        f.write("# 变更记录\n\n## v0.13.0 — x\n")
    with open(os.path.join(badge_root, "README.md"), "w", encoding="utf-8") as f:
        f.write('<img src="https://img.shields.io/badge/version-v0.13.0-x">\n'
                '<img src="https://img.shields.io/badge/scripts-0-x">\n'
                '<img src="https://img.shields.io/badge/tests-999-x">\n'
                '<img src="https://img.shields.io/badge/skills-0-x">\n')
    r = run("audit_pack.py", "--root", badge_root)
    check("徽章漂移让 audit_pack 非零退出", r.returncode == 1 and "tests" in r.stdout,
          (r.stdout or "")[-100:])

    # 16. 数字单源
    r = run("gen_metrics.py", "--check")
    check("文档数字与 metrics.json 一致", r.returncode == 0)

    # 17. 包自检
    r = run("audit_pack.py")
    check("audit_pack 通过", r.returncode == 0)

    # 21. 语法向下兼容：README 声明 Python 3.9+，CI 矩阵只跑 3.10/3.12（runner 已无 3.9），
    # 用 feature_version 把「3.9 能解析」变成可判真假的一条，而不是靠口头承诺。
    import ast as _ast
    syntax_bad = []
    for p in sorted(os.listdir(SCRIPTS)):
        if not p.endswith(".py"):
            continue
        try:
            _ast.parse(open(os.path.join(SCRIPTS, p), encoding="utf-8").read(),
                       feature_version=(3, 9))
        except SyntaxError as e:
            syntax_bad.append(f"{p}:{e.lineno}")
    try:
        _ast.parse(open(__file__, encoding="utf-8").read(), feature_version=(3, 9))
    except SyntaxError as e:
        syntax_bad.append(f"tests:{e.lineno}")
    check("全部脚本在 Python 3.9 语法下可解析", not syntax_bad, ", ".join(syntax_bad))

    # 18. 首次上手（空 home / 空技能目录）：跑独立脚本，保证它没被改坏。
    #     宿主故意给脏环境（TG_MEMORY_ROOT / DSH_HOME 有值）：模拟老用户/CI 变量在场，
    #     first_run 必须自己清掉这两个变量，否则第 8c 步会假失败（2026-10-30 实测踩到）。
    dirty = {**os.environ, "PYTHONIOENCODING": "utf-8",
             "TG_MEMORY_ROOT": os.path.join(tmp, "host-dirty-memory"),
             "DSH_HOME": os.path.join(tmp, "host-dirty-home")}
    r = subprocess.run([PY, os.path.join(ROOT, "tests", "first_run.py")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=dirty, cwd=ROOT)
    check("首次上手 10 步无 traceback（宿主 $TG_MEMORY_ROOT 不干扰）", r.returncode == 0,
          (r.stdout or "")[-200:])

    # 22. 账本 v2：从会话日志派生、计价可手算、重复 ingest 幂等、下游脚本能读
    #     （成本插件的替代物；口径 = output×0.6 + input×0.15 + cacheRead×0.003 再乘汇率）
    lhome = os.path.join(tmp, "ledger-home")
    lsess = os.path.join(lhome, "sessions", "sess-a")
    os.makedirs(lsess, exist_ok=True)
    ts = int(datetime(2026, 10, 20, 12, 0, tzinfo=timezone(timedelta(hours=8))).timestamp() * 1000)
    with open(os.path.join(lsess, "session.v4.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "session", "version": 4, "id": "sess-a",
                            "cwd": "X", "origin": "main", "createdAt": ts}) + "\n")
        f.write(json.dumps({"type": "assistant/message", "seq": 1, "time": ts,
                            "data": {"turn": 1, "step": 1,
                                     "message": {"role": "assistant",
                                                 "source": {"kind": "model",
                                                            "provider": "deepseek-account",
                                                            "model": "deepseek-flash"}},
                                     "usage": {"inputTokens": 1000000, "cacheReadTokens": 1000000,
                                               "cacheWriteTokens": 0, "outputTokens": 1000000,
                                               "totalTokens": 3000000}}}) + "\n")
    lcfg = os.path.join(tmp, "ledger-config.yaml")
    with open(lcfg, "w", encoding="utf-8") as f:
        f.write(f'dsh_home: "{lhome.replace(chr(92), "/")}"\n'
                'ledger:\n  path: "led.json"\n  timezone: "Asia/Shanghai"\n'
                '  prices:\n    currency: USD\n    fx: { usdToCny: 6.67 }\n'
                '    models:\n      deepseek-flash: { cacheMiss: 0.15, cacheHit: 0.003, output: 0.6 }\n')
    r = run("ledger.py", "ingest", "--config", lcfg)
    check("ledger ingest 成功", r.returncode == 0, (r.stdout + r.stderr)[-200:])
    led = json.load(open(os.path.join(tmp, "led.json"), encoding="utf-8"))
    got = led["days"]["2026-10-20"]["costCny"]
    expect = (1000000 * 0.15 + 1000000 * 0.003 + 1000000 * 0.6) * 6.67 / 1e6
    check("ledger 计价与手算一致", abs(got - expect) < 1e-6, f"got={got} expect={expect:.6f}")
    check("ledger 会话归因落盘", "sess-a" in led["days"]["2026-10-20"]["sessions"])

    first_days = led["days"]
    r = run("ledger.py", "ingest", "--config", lcfg)
    led2 = json.load(open(os.path.join(tmp, "led.json"), encoding="utf-8"))
    check("ledger 重复 ingest 幂等", led2["days"] == first_days and "缓存命中 1" in r.stdout,
          (r.stdout or "")[-160:])
    r = run("ledger.py", "verify", "--config", lcfg)
    check("ledger verify 自洽", r.returncode == 0 and "自洽" in (r.stdout or ""), (r.stdout or "")[-160:])
    r = run("cost_summary.py", "--config", lcfg, "--days", "3")
    check("cost_summary 读新账本", r.returncode == 0 and "2026-10-20" in (r.stdout or ""),
          (r.stdout + r.stderr)[-200:])

    # 22b. 多模型计价（2026-10-26）：免费 ¥0、未定价不折 default、provider 默认价、价表导出幂等
    phome = os.path.join(tmp, "price-home")
    psess = os.path.join(phome, "sessions", "sess-multi")
    os.makedirs(psess, exist_ok=True)

    def pev(seq, provider, model, i, cr, o):
        return json.dumps({"type": "assistant/message", "seq": seq, "time": ts,
                           "data": {"turn": 1, "step": seq,
                                    "message": {"role": "assistant",
                                                "source": {"kind": "model",
                                                           "provider": provider, "model": model}},
                                    "usage": {"inputTokens": i, "cacheReadTokens": cr,
                                              "cacheWriteTokens": 0, "outputTokens": o}}})

    with open(os.path.join(psess, "session.v4.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "session", "version": 4, "id": "sess-multi",
                            "cwd": "X", "origin": "main", "createdAt": ts}) + "\n")
        f.write(pev(1, "deepseek-account", "deepseek-flash", 1000000, 0, 0) + "\n")
        f.write(pev(2, "our-free-model", "deepseek-v4.1-flash-free", 1000000, 0, 1000000) + "\n")
        f.write(pev(3, "glm", "glm-5.3-flash", 1000000, 0, 0) + "\n")
        f.write(pev(4, "openai", "gpt-9-unlisted", 0, 0, 1000000) + "\n")

    pcfg = os.path.join(tmp, "price-config.yaml")
    with open(pcfg, "w", encoding="utf-8") as f:
        f.write('dsh_home: "%s"\n' % phome.replace(chr(92), "/") +
                'ledger:\n  path: "pled.json"\n  timezone: "Asia/Shanghai"\n'
                '  prices:\n    currency: USD\n    fx: { usdToCny: 6.67 }\n'
                '    free: { suffixes: ["-free"] }\n'
                '    providers:\n'
                '      openai: { default: { cacheMiss: 2.5, cacheHit: 1.25, output: 10 } }\n'
                '    models:\n'
                '      deepseek-flash: { cacheMiss: 0.15, cacheHit: 0.003, output: 0.6 }\n'
                '      deepseek-v4.1-flash: { cacheMiss: 0.15, cacheHit: 0.003, output: 0.6 }\n')
    r = run("ledger.py", "ingest", "--config", pcfg)
    check("多模型 ingest 成功", r.returncode == 0, (r.stdout + r.stderr)[-200:])
    pled = json.load(open(os.path.join(tmp, "pled.json"), encoding="utf-8"))
    # 手算：flash 1M input ×0.15 + free 0 + 未定价 0 + openai 默认 1M output ×10，再 ×6.67
    pexpect = (1000000 * 0.15 + 1000000 * 10) * 6.67 / 1e6
    pday = pled["days"]["2026-10-20"]
    check("多模型：免费与未定价记 ¥0、provider 默认价生效",
          abs(pday["costCny"] - pexpect) < 1e-6, f"got={pday['costCny']} expect={pexpect:.6f}")
    check("多模型：免费命中被记账", pled["pricing"]["matched"].get("free") == 1,
          json.dumps(pled["pricing"]["matched"], ensure_ascii=False))
    check("多模型：未定价单列、不折 default",
          [u["model"] for u in pled["pricing"]["unpriced"]] == ["glm-5.3-flash"],
          json.dumps(pled["pricing"]["unpriced"], ensure_ascii=False))
    check("多模型：未定价调用数落到日账", pday.get("unpricedCalls") == 1, str(pday.get("unpricedCalls")))

    pa = os.path.join(tmp, "prices-a.json")
    pb = os.path.join(tmp, "prices-b.json")
    r1 = run("ledger.py", "prices", "export", "--config", pcfg, "--out", pa)
    r2 = run("ledger.py", "prices", "export", "--config", pcfg, "--out", pb)
    a = json.load(open(pa, encoding="utf-8"))
    b = json.load(open(pb, encoding="utf-8"))
    a.pop("generatedAt", None)
    b.pop("generatedAt", None)
    check("价表导出幂等（去掉生成时间后一致）", r1.returncode == 0 and r2.returncode == 0 and a == b,
          (r1.stderr + r2.stderr)[-200:])
    check("价表带解析口径（free/providers/fx）",
          a.get("free", {}).get("suffixes") == ["-free"] and "openai" in a.get("providers", {}),
          json.dumps(a.get("free"), ensure_ascii=False))
    r = run("ledger.py", "prices", "check", "--config", pcfg, "--strict")
    check("价表对账：有未定价就以 1 退出（可进 CI）", r.returncode == 1, (r.stdout + r.stderr)[-200:])
    r = run("ledger.py", "report", "--config", pcfg, "--days", "3", "--by", "provider")
    check("按 provider 汇总可用", r.returncode == 0 and "openai" in (r.stdout or ""),
          (r.stdout + r.stderr)[-200:])

    # 漂移：导出后改 config（加一个模型价）→ check 必须报「价表过期」；重导后消失
    r = run("ledger.py", "prices", "export", "--config", pcfg)
    check("价表导出到默认位置", r.returncode == 0 and "价表 →" in (r.stdout or ""),
          (r.stdout + r.stderr)[-200:])
    with open(pcfg, "a", encoding="utf-8") as f:
        f.write('      deepseek-flash-v2: { cacheMiss: 0.2, cacheHit: 0.004, output: 0.7 }\n')
    r = run("ledger.py", "prices", "check", "--config", pcfg)
    check("价表漂移被报出来（改了 config 没重导）", "价表过期" in (r.stdout or ""),
          (r.stdout or "")[-300:])
    run("ledger.py", "prices", "export", "--config", pcfg)
    r = run("ledger.py", "prices", "check", "--config", pcfg)
    check("重导后漂移消失", "价表过期" not in (r.stdout or ""), (r.stdout or "")[-300:])

    # 19.5 交接卡：卡片是切会话唯一的重建入口，下面任一条坏掉，接手者就得回去读原对话
    put_session(home, "session-ab12cd34", "handoff_session.jsonl", tmp)
    r = run("handoff.py", "--session", "ab12cd34", "--slug", "t",
            "--dsh-home", home, "--config", prices, "--print-only")
    hout = r.stdout or ""

    def card_field(name):
        for line in hout.splitlines():
            if line.startswith(f"- {name}："):
                return line.split("：", 1)[1]
        return None

    check("交接卡带会话 id", re.search(r"> 会话 ab12cd34 ·", hout) is not None, hout[:200])
    check("交接卡目标取首条、不认系统注入",
          (card_field("目标") or "").startswith("把交接卡补上会话号") and "<system-reminder>" not in hout,
          hout[:400])
    check("交接卡记最近指令（末条是按语也不吞）", card_field("最近指令") == "继续", hout[:400])
    # 上限：切片后再拼省略号，历史上多出 1 字（80→81、160→161），这类越界靠断言守住
    check("交接卡目标/最后一句不超过 80/160 字",
          len(card_field("目标") or "") <= 80 and len(card_field("最后一句") or "") <= 160,
          f"目标={len(card_field('目标') or '')} 最后一句={len(card_field('最后一句') or '')}")
    check("交接卡关键片段标来源且跳过临时脚手架",
          "（最后一次代码改动）" in hout and "```python" in hout and "_tmp_probe" not in hout,
          hout[:400])
    # 内容块是字符串的日志变体不该崩：直接调函数，省一次子进程
    import handoff as ho
    check("交接卡兼容 content 为字符串的日志", ho.blocks_text("一段话") == "一段话"
          and ho.blocks_text([{"type": "text", "text": "a"}, {"type": "reasoning", "text": "b"}]) == "a")
    # --if-absent 不许覆盖已有卡（胶囊自动触发就靠这条，覆盖手写卡=丢交接）
    mem = os.path.join(tmp, "mem")
    keep_dir = os.path.join(mem, "default-workspace", "20261025-keep")
    os.makedirs(keep_dir, exist_ok=True)
    keep_card = os.path.join(keep_dir, "00-card.md")
    with open(keep_card, "w", encoding="utf-8") as f:
        f.write("手写卡\n")
    run("handoff.py", "--session", "ab12cd34", "--slug", "keep", "--dsh-home", home,
        "--config", prices, "--memory-root", mem, "--if-absent")
    with open(keep_card, encoding="utf-8") as f:
        check("交接卡 --if-absent 不覆盖手写卡", f.read() == "手写卡\n")

    # 19.6 卡绑任务夹（2026-10-25）：同任务多会话各有各的卡，主卡不被覆盖
    tmem = os.path.join(tmp, "tmem")
    tfile = os.path.join(tmp, "tg-current-task.json")
    r = run("handoff.py", "--session", "ab12cd34", "--set-task", "20261025-task",
            "--dsh-home", home, "--config", prices, "--task-file", tfile)
    check("任务指针写入指针文件", r.returncode == 0 and os.path.isfile(tfile), (r.stdout + r.stderr)[-160:])
    r = run("handoff.py", "--session", "ab12cd34", "--dsh-home", home, "--config", prices,
            "--task-file", tfile, "--memory-root", tmem)
    task_dir = os.path.join(tmem, "default-workspace", "20261025-task")
    main_card = os.path.join(task_dir, "00-card.md")
    check("指针命中→首张卡落任务夹 00-card.md", os.path.isfile(main_card), (r.stdout + r.stderr)[-200:])
    # 同一会话重跑：认索引写回同一张卡，不许再造 01-ab12cd34.md 分叉
    run("handoff.py", "--session", "ab12cd34", "--dsh-home", home, "--config", prices,
        "--task-file", tfile, "--memory-root", tmem)
    check("同会话重跑不另建 01-*.md",
          not os.path.isfile(os.path.join(task_dir, "01-ab12cd34.md")))
    # 另一个会话绑同一任务：只多一张 01-<sid8>.md，主卡内容不动
    before = open(main_card, encoding="utf-8").read()
    put_session(home, "session-ef56gh78", "handoff_session.jsonl", tmp)
    r = run("handoff.py", "--session", "ef56gh78", "--task", "20261025-task", "--dsh-home", home,
            "--config", prices, "--task-file", tfile, "--memory-root", tmem)
    own = os.path.join(task_dir, "01-ef56gh78.md")
    check("同任务第二会话写 01-<sid8>.md 且主卡不变",
          os.path.isfile(own) and open(main_card, encoding="utf-8").read() == before,
          (r.stdout + r.stderr)[-200:])
    with open(os.path.join(task_dir, "99-cards.json"), encoding="utf-8") as f:
        ixd = json.load(f)
    check("任务夹索引记两张卡且指向各自文件",
          {k: v.get("file") for k, v in ixd.get("cards", {}).items()}
          == {"ab12cd34": "00-card.md", "ef56gh78": "01-ef56gh78.md"}, str(ixd)[:240])

    # 20. 计数单一真相：脚本数与本文件断言数必须与 data/counts.json 同步
    # （这两项必须放在最后：下面一条把「本文件总条数」与 counts.json 对比，漏改就红）
    with open(os.path.join(ROOT, "data", "counts.json"), encoding="utf-8") as f:
        counts = json.load(f)
    real_scripts = len([x for x in os.listdir(SCRIPTS) if x.endswith((".py", ".mjs"))])
    check("data/counts.json 的脚本数与实际一致", counts.get("scripts") == real_scripts,
          f"counts={counts.get('scripts')} 实际={real_scripts}")
    check("data/counts.json 的测试数与实跑一致（加测试要同步）",
          counts.get("tests") == len(results) + 1,
          f"counts={counts.get('tests')} 实跑={len(results) + 1}")

    bad = [n for n, okk, _ in results if not okk]
    print(f"\n{len(results) - len(bad)}/{len(results)} 通过")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
