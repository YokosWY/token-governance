#!/usr/bin/env python3
"""TG 体检单入口：一条命令把账算完，给出 2–4 条带预估的动作。

为什么合并：18 个脚本各查一件事，翻手册的成本高于收益（实测 tg-* 七天只被调 1–4 次）。
doctor 只做两件事：把已有脚本的 JSON 汇成一页结论；把能安全执行的动作（快照、基线）
落到本地，其余只给命令不给结论。

用法：
  python scripts/doctor.py                 # 只读：结论 + 动作清单
  python scripts/doctor.py --days 7        # 失败与成本看几天
  python scripts/doctor.py --json          # 结构化，给 CI / 别的脚本
  python scripts/doctor.py --apply         # 额外执行可回滚动作（拍快照 + 记基线）
  python scripts/doctor.py --print-actions # 只打印 --apply 会做什么，不执行

边界：
  - 不改 cordis.patch.yml，不动 preset，不删技能；这些要人裁决。
  - --apply 只做两件可回滚的事：补丁快照（patch_snapshot）、首日基线（baseline --write）。
"""
import datetime, json, os, subprocess, sys

from tg_common import utf8_stdio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
PY = sys.executable


def safe_stdout():
    utf8_stdio()


def run_json(script, *args):
    """跑子脚本取 JSON。脚本以非 0 退出但吐了合法 JSON 时也算成功（preflight 就是这样）。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"  # 子脚本在 GBK 控制台下也要吐 UTF-8，否则解析出乱码
    try:
        r = subprocess.run([PY, os.path.join(SCRIPTS, script), *args],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180, env=env)
    except Exception as e:
        return None, f"{e.__class__.__name__}: {e}"
    out = (r.stdout or "").strip()
    if not out:
        return None, (r.stderr or "").strip().splitlines()[-1][:120] if r.stderr else "无输出"
    try:
        return json.loads(out), ""
    except json.JSONDecodeError:
        return None, "输出不是 JSON（脚本可能报错了）"


def fmt_int(x):
    try:
        return f"{int(x):,}"
    except (TypeError, ValueError):
        return "?"


def collect(days):
    """各采集器返回 (数据, 错误)。错误只提示，不让整页失败。"""
    data, errors = {}, {}
    pairs = [
        ("preflight", ("preflight.py", "--json")),
        ("schema", ("schema_budget.py", "--json")),
        ("skills", ("skill_usage.py", "--days", str(days), "--json")),
        ("failure", ("failure_analysis.py", "--days", str(days), "--json")),
        ("turns", ("turn_cost.py", "--days", str(days), "--json")),
    ]
    for key, argv in pairs:
        d, err = run_json(*argv)
        if d is None:
            errors[key] = err
        else:
            data[key] = d
    return data, errors


def build_actions(data):
    """按「改了能立刻省 / 改了少犯错」排序，最多四条。每条都要能独立执行。"""
    acts = []

    # 1. 体积：schema 预算与最大块
    sch = data.get("schema") or {}
    pf = data.get("preflight") or {}
    over = float(sch.get("over_budget_kb") or 0)
    top_g = sch.get("groups") or []
    if sch:
        top = top_g[0] if top_g else {}
        if over > 0:
            saved = int(over * 1024 * 0.4)
            acts.append({
                "id": "schema-over",
                "severity": "高",
                "title": f"工具 schema 超预算 {over:.0f} KB",
                "detail": (f"当前 {sch.get('schema_kb')} KB / {sch.get('tools')} 个工具，"
                           f"预算 {sch.get('budget_kb')} KB；最大块 {top.get('group', '?')} "
                           f"{top.get('bytes', 0)/1024:.0f} KB（{top.get('tools', 0)} 个工具）"),
                "action": f"把 {top.get('group', '最重的那组')} 移进独立 preset，或按需启用",
                "command": "python scripts/schema_budget.py --top 12",
                "estimate": f"每步约省 {saved:,} tokens（0.4 tok/B 粗估）",
            })
        elif top:
            acts.append({
                "id": "schema-watch",
                "severity": "低",
                "title": "schema 接近预算上限",
                "detail": (f"{sch.get('schema_kb')} KB / {sch.get('tools')} 个工具，"
                           f"余量 {float(sch.get('budget_kb', 0)) - float(sch.get('schema_kb', 0)):.0f} KB；"
                           f"最大块 {top.get('group')} {top.get('bytes', 0)/1024:.0f} KB"),
                "action": "新装插件前先跑一次归因，别让下一块吃掉余量",
                "command": "python scripts/schema_budget.py",
                "estimate": "防反弹；不是当期可省金额",
            })

    # 2. 失败：失败轮次占比（钱花在返工上）
    fa = data.get("failure") or {}
    tc = data.get("turns") or {}
    summ = tc.get("summary") or {}
    turns = summ.get("turns") or 0
    failed_turns = fa.get("turns_with_failure") or 0
    proto = fa.get("protocol_failed_calls") or 0
    if turns and failed_turns:
        share = failed_turns / turns * 100
        if share >= 20:
            acts.append({
                "id": "failure-rate",
                "severity": "高" if share >= 35 else "中",
                "title": f"有失败调用的轮次占 {share:.0f}%",
                "detail": (f"{failed_turns}/{turns} 轮里至少一次失败调用；"
                           f"失败调用 {fmt_int(fa.get('failed_calls'))} 次，其中协议错误 {fmt_int(proto)} 次"
                           f"（没读就改 / 并发改同一文件）"),
                "action": "协议错误不重试逻辑：先读再改、一次只改一个文件；逻辑错误才换参重试",
                "command": "python scripts/failure_analysis.py --protocol-only",
                "estimate": f"失败轮次的成本要单算；先把 {fmt_int(proto)} 次协议错误压掉，比重试便宜",
            })

    # 3. 技能：装了但从不调用
    su = data.get("skills") or {}
    never = su.get("never_used") or []
    tg_inst = su.get("tg_installed") or {}
    if never:
        acts.append({
            "id": "skill-idle",
            "severity": "低",
            "title": f"{len(never)} 个已装技能近 {su.get('window_days', '?')} 天 0 调用",
            "detail": ("常驻但没被调：" + ", ".join(never[:6]) +
                       "；待机技能模型侧不可见，命中 0 ≠ 没用，先看是不是本来就该待机"),
            "action": "按用途三选一：并进常驻路由 / 改成 disable-model-invocation / 删掉",
            "command": "python scripts/skill_usage.py --days 7",
            "estimate": "省的是目录与心智，不是每步字节",
        })
    elif tg_inst:
        idle = [k for k, v in tg_inst.items() if v == "待机"]
        if idle:
            acts.append({
                "id": "skill-standby",
                "severity": "低",
                "title": f"{len(idle)} 个 tg-* 处于待机（模型侧不可见）",
                "detail": "待机：" + ", ".join(idle[:6]) + "；只能 read 或 /名字 调起",
                "action": "确认这些技能还要不要常驻；不要就把 description 缩短或关掉",
                "command": "python scripts/skill_usage.py",
                "estimate": "省的是目录条数与误触发",
            })

    # 4. 缓存：命中率不是目标，未命中成本才是
    cache = (pf.get("cache") or "")
    if "k=" in cache:
        acts.append({
            "id": "cache-line",
            "severity": "低",
            "title": "缓存命中率体检",
            "detail": cache,
            "action": "k 高且未命中占比高时才动前缀；否则别为了命中率改配置",
            "command": "python scripts/cache_audit.py",
            "estimate": "见 preflight 的未命中占成本比例",
        })
    return acts[:4]


def apply_actions(dry_run):
    """只做可回滚的两件：补丁快照 + 首日基线。返回执行记录。"""
    done = []
    cmds = [
        ("拍补丁快照", [PY, os.path.join(SCRIPTS, "patch_snapshot.py"), "--snapshot"]),
        ("记首日基线", [PY, os.path.join(SCRIPTS, "baseline.py"), "--write"]),
    ]
    for label, argv in cmds:
        shown = " ".join(argv[2:])
        if dry_run:
            done.append({"label": label, "cmd": shown, "status": "dry-run"})
            continue
        try:
            env = dict(os.environ)
            env["PYTHONIOENCODING"] = "utf-8"
            r = subprocess.run(argv, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=120, env=env)
            ok = r.returncode == 0
            tail = ((r.stdout or "").strip().splitlines() or [""])[-1][:100]
            done.append({"label": label, "cmd": shown,
                         "status": "ok" if ok else "失败", "output": tail})
        except Exception as e:
            done.append({"label": label, "cmd": shown, "status": f"失败（{e.__class__.__name__}）"})
    return done


def main():
    safe_stdout()
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7, help="失败与成本的回看窗口")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--apply", action="store_true", help="执行可回滚动作（快照 + 基线）")
    ap.add_argument("--print-actions", action="store_true", help="只预览 --apply 会做什么")
    a = ap.parse_args()

    data, errors = collect(a.days)
    actions = build_actions(data)
    applied = []
    if a.apply or a.print_actions:
        applied = apply_actions(dry_run=a.print_actions)

    pf = data.get("preflight") or {}
    tc = data.get("turns") or {}
    summ = tc.get("summary") or {}
    out = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "days": a.days,
        "verdict": pf.get("verdict") or "未知",
        "conflicts": pf.get("conflicts"),
        "metrics": {
            "schema_kb": (data.get("schema") or {}).get("schema_kb"),
            "tools": (data.get("schema") or {}).get("tools"),
            "budget_kb": (data.get("schema") or {}).get("budget_kb"),
            "cache": pf.get("cache"),
            "turn_median_cny": summ.get("cost_median"),
            "turn_p90_cny": summ.get("cost_p90"),
            "turns": summ.get("turns"),
            "cost_total_cny": summ.get("cost_total"),
            "failed_turns": (data.get("failure") or {}).get("turns_with_failure"),
            "failed_calls": (data.get("failure") or {}).get("failed_calls"),
        },
        "actions": actions,
        "applied": applied,
        "errors": errors,
    }

    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0  # 缺数据不算失败：doctor 是只读汇总，采集失败已写进 errors

    m = out["metrics"]
    print(f"TG 体检 · {out['generated_at']}（回看 {a.days} 天）\n")
    print(f"  预检      {out['verdict']}（{out['conflicts']} 项待裁决，明细见 preflight）")
    if m.get("schema_kb"):
        print(f"  体积      {m['schema_kb']} KB / {m['tools']} 个工具（预算 {m['budget_kb']} KB）")
    if m.get("cache"):
        print(f"  缓存      {m['cache']}")
    if m.get("turns"):
        print(f"  任务成本  中位 ¥{m.get('turn_median_cny')} / P90 ¥{m.get('turn_p90_cny')}"
              f" / 共 ¥{m.get('cost_total_cny')}（{m.get('turns')} 轮）")
        print(f"  失败      {m.get('failed_turns')} 轮有失败调用，共 {fmt_int(m.get('failed_calls'))} 次")
    if errors:
        print("\n  缺数据：" + "，".join(f"{k}({v})" for k, v in errors.items()))

    print(f"\n动作（{len(actions)} 条，按收益排序）")
    for i, act in enumerate(actions, 1):
        print(f"\n  [{i}] {act['severity']}  {act['title']}")
        print(f"      {act['detail']}")
        print(f"      → {act['action']}")
        print(f"      命令：{act['command']}")
        print(f"      预估：{act['estimate']}")

    if applied:
        print("\n执行记录")
        for r in applied:
            print(f"  {r['status']:<8} {r['label']}  ({r['cmd']})")
    elif not a.print_actions:
        print("\n只读完成。要落地可回滚动作（补丁快照 + 首日基线）："
              "python scripts/doctor.py --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
