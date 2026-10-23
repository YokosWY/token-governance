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
        print("SKIP 压缩工具不可用（zstd / python zstandard 都没有）")
        return 2
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

    # 13. doctor 单入口：采集失败也要出动作清单，不能崩
    r = run("doctor.py", "--days", "1", "--json")
    try:
        doc = json.loads(r.stdout)
    except json.JSONDecodeError:
        doc = {}
    check("doctor 给出动作清单", isinstance(doc.get("actions"), list) and len(doc["actions"]) >= 1,
          str(type(doc.get("actions"))))
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

    # 15. 数字单源
    r = run("gen_metrics.py", "--check")
    check("文档数字与 metrics.json 一致", r.returncode == 0)

    # 11. 包自检
    r = run("audit_pack.py")
    check("audit_pack 通过", r.returncode == 0)

    bad = [n for n, okk, _ in results if not okk]
    print(f"\n{len(results) - len(bad)}/{len(results)} 通过")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
