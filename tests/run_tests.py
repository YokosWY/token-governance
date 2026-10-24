#!/usr/bin/env python3
"""TG 回归测试：纯标准库，不需要 pytest。

用法：python tests/run_tests.py
覆盖：解压回退、计价、日志格式拒绝、换参重试口径、预检漂移、首日基线、数字单源。
"""
import json, os, subprocess, sys, tempfile

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
    try:
        r = subprocess.run(["zstd", "-q", "-f", src, "-o", dst], capture_output=True)
        if r.returncode == 0 and os.path.isfile(dst):
            return True
    except FileNotFoundError:
        pass
    try:
        import zstandard as zstd
    except ImportError:
        return False
    with open(src, "rb") as f:
        data = f.read()
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
    tmp = tempfile.mkdtemp(prefix="tg-test-")
    prices = os.path.join(tmp, "prices.yaml")
    with open(prices, "w", encoding="utf-8") as f:
        f.write("prices_usd_per_mtok:\n  input: 0.15\n  cached: 0.003\n  output: 0.6\nrate: 7.1\n")

    home = os.path.join(tmp, "home")
    os.makedirs(os.path.join(home, "skills"), exist_ok=True)
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
    r = run("doctor.py", "--days", "1", "--json", "--dsh-home", empty_home)
    try:
        doc = json.loads(r.stdout)
    except json.JSONDecodeError:
        doc = {}
    acts = doc.get("actions") or []
    check("doctor 空数据也给动作清单（first-run）",
          isinstance(acts, list) and any(a.get("id") == "first-run" for a in acts),
          f"actions={[a.get('id') for a in acts]}")
    check("doctor 缺数据不崩", r.returncode == 0, str(r.returncode))

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
        f.write('dsh_home: "C:\\dsh-next"\n')  # 双引号里的反斜杠 = YAML 转义，必坏
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
                      "install_guard.py")
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
             "install_guard.py": ("--status",)}
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
    check("env_check 缺解压器时非零且点名",
          r.returncode == 1 and "unzip" in (ec2.get("missing_required") or []),
          f"rc={r.returncode} missing={ec2.get('missing_required')}")

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

    # 无 HOME / USERPROFILE：可读报错 + 明确提示 ~ 展不开（POSIX 下 expanduser 走 pwd，跳过）
    if os.name == "nt":
        no_home_env = {k: v for k, v in os.environ.items()
                       if k.upper() not in ("HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH")}
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
    check("假 zstandard 模块不被 env_check 判可用",
          r.returncode == 1 and unzip_row.get("ok") is False
          and "ZstdDecompressor" in (unzip_row.get("detail") or ""),
          str(unzip_row.get("detail"))[:80])
    r = run_env("baseline.py", ["--config", e2e_cfg], extra_env={"PYTHONPATH": fake_zstd},
                path_override=bare_path)
    check("假 zstandard 模块解压时给一句话而不是 AttributeError",
          r.returncode != 0 and "ZstdDecompressor" in (r.stdout + r.stderr)
          and "Traceback" not in (r.stdout + r.stderr),
          (r.stdout + r.stderr)[:80])

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

    # 18. 首次上手（空 home / 空技能目录）：跑独立脚本，保证它没被改坏
    r = subprocess.run([PY, os.path.join(ROOT, "tests", "first_run.py")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=ROOT)
    check("首次上手 10 步无 traceback", r.returncode == 0, (r.stdout or "")[-160:])

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
