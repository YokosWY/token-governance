#!/usr/bin/env python3
"""首次上手：模拟「刚 clone 下来、机器上还没有 DSH 数据」的完整流程。

为什么单列：测试套件里的端到端用例都自己造夹具，等于「有数据的世界」；而真新用户的
前五分钟是空 home、空技能目录、没有账本。doctor 的 CI 红灯（2026-10-24）就是这类
「本地有数据才绿」的断言造成的，所以把首次上手单独跑一遍，不进主套件也行。

约定：每一行是一条用户会真的执行的命令，断言只有两条——
  1) 不出现 Traceback（允许非零退出，但要说人话）；
  2) 退出码落在该命令文档写明的范围里。
用法：python tests/first_run.py [--verbose]
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
PY = sys.executable
VERBOSE = "--verbose" in sys.argv

# 这个脚本自己也要固定 UTF-8：GitHub 的 Windows runner 控制台是 cp1252，
# 直接 print 中文会 UnicodeEncodeError（本 job 第一次跑就是这么红的）。
sys.path.insert(0, SCRIPTS)
from tg_common import utf8_stdio  # noqa: E402

utf8_stdio()

failures = []


def run(name, args, expect=(0,), cwd=None, env=None):
    e = dict(os.environ)
    e["PYTHONIOENCODING"] = "utf-8"
    if env:
        e.update(env)
    r = subprocess.run([PY, *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=cwd or ROOT, env=e)
    out = (r.stdout or "") + (r.stderr or "")
    problems = []
    if "Traceback" in out:
        problems.append("出现 Traceback")
    if r.returncode not in expect:
        problems.append(f"退出码 {r.returncode} 不在预期 {expect}")
    status = "OK  " if not problems else "FAIL"
    print(f"{status} {name}  (rc={r.returncode})")
    if problems:
        failures.append((name, problems, out.strip().splitlines()[:6]))
    if VERBOSE or problems:
        for line in out.strip().splitlines()[:8]:
            print(f"      {line}")
    return r


def main():
    tmp = tempfile.mkdtemp(prefix="tg-first-run-")
    home = os.path.join(tmp, "empty-home")
    skills = os.path.join(tmp, "skills")
    memory = os.path.join(tmp, "memory")
    os.makedirs(skills, exist_ok=True)
    cfg = os.path.join(tmp, "config.yaml")
    with open(cfg, "w", encoding="utf-8") as f:
        f.write(f'dsh_home: "{home.replace(os.sep, "/")}"\n'
                f'skills_dir: "{skills.replace(os.sep, "/")}"\n'
                f'memory_root: "{memory.replace(os.sep, "/")}"\n')

    print(f"临时根：{tmp}\n")

    # 1) 复制安装段的前两步：技能入目录 + 配置就位（用 cp -r 等价：只判存在性）
    run("env_check（空机器：必需项在，数据目录空）", [os.path.join(SCRIPTS, "env_check.py"),
                                              "--config", cfg])
    # 2) 预检：没有会话/账本时允许「待确认」，但不能崩
    run("preflight（空机器）", [os.path.join(SCRIPTS, "preflight.py"), "--config", cfg],
        expect=(0, 1, 2))
    # 3) 体检单：必须给 first-run 而不是空清单
    r = run("doctor（空机器）", [os.path.join(SCRIPTS, "doctor.py"), "--config", cfg,
                             "--days", "1", "--json"])
    if "first-run" not in (r.stdout or ""):
        failures.append(("doctor 空机器未给 first-run", ["缺少 first-run 动作"], []))
        print("FAIL doctor 空机器未给 first-run")
    # 4) 看账：没有 ledger 是预期，退出码 1 + 人话
    run("cost_summary（无账本：预期 rc=1）", [os.path.join(SCRIPTS, "cost_summary.py"),
                                     "--config", cfg, "--days", "3"], expect=(1,))
    # 5) 首日基线：没有会话是预期，退出码 1 + 人话
    run("baseline（无会话：预期 rc=1）", [os.path.join(SCRIPTS, "baseline.py"),
                                 "--config", cfg], expect=(1,))
    # 6) 补丁快照列表：没有快照目录也要正常
    run("patch_snapshot --list（无快照）", [os.path.join(SCRIPTS, "patch_snapshot.py"),
                                    "--config", cfg, "--list"])
    # 7) 守卫安装器：空 home 下 status 不崩、不误写
    run("install_guard --status（空 home）", [os.path.join(SCRIPTS, "install_guard.py"),
                                     "--home", home, "--status"], expect=(0, 1))
    # 8) 建对话夹：真实写入路径（唯一会写盘的步骤）
    run("new_session（建对话夹）", [os.path.join(SCRIPTS, "new_session.py"), "demo", "s1",
                                "--root", memory, "--config", cfg])
    # 8b) 安装向导：配好的 memory_root 直接用，并把母文件夹结构建出来
    run("setup --yes（建母文件夹）", [os.path.join(SCRIPTS, "setup.py"), "--config", cfg, "--yes"])
    for rel in ("README.md", os.path.join("_template", "00-card.md")):
        if not os.path.isfile(os.path.join(memory, rel)):
            failures.append(("setup 未建出 " + rel, ["缺文件"], []))
            print("FAIL setup 未建出 " + rel)
    # 8c) 没配 memory_root + 非交互：必须退出码 2，且不许在任何默认位置建目录
    cfg_empty = os.path.join(tmp, "config-no-memory.yaml")
    with open(cfg_empty, "w", encoding="utf-8") as f:
        f.write(f'dsh_home: "{home.replace(os.sep, "/")}"\nmemory_root: ""\n')
    fallback = os.path.join(os.path.expanduser("~"), "tg-memory", "__first_run_probe__")
    r2 = run("setup（未配置 + 非交互：预期 rc=2）",
             [os.path.join(SCRIPTS, "setup.py"), "--config", cfg_empty], expect=(2,))
    r = run("setup --memory-root（非交互指定路径）",
            [os.path.join(SCRIPTS, "setup.py"), "--config", cfg_empty,
             "--memory-root", os.path.join(tmp, "probe-not-used")], expect=(0,))
    if os.path.isdir(fallback):
        failures.append(("未配置的 setup 建出了 ~/tg-memory", [fallback], []))
        print("FAIL 未配置的 setup 建出了 ~/tg-memory")
    # 9) 校验示例补丁：config.example.yaml 是 mapping，check_yaml 应拒绝（可读，不 traceback）
    run("check_yaml（示例配置不是补丁列表：预期 rc=1）",
        [os.path.join(SCRIPTS, "check_yaml.py"), os.path.join(ROOT, "config.example.yaml")],
        expect=(1,))
    # 10) 包自检：干净机器上也要过
    run("audit_pack", [os.path.join(SCRIPTS, "audit_pack.py")])

    print()
    if failures:
        print(f"失败 {len(failures)} 项：")
        for name, problems, lines in failures:
            print(f"  - {name}: {'; '.join(problems)}")
            for line in lines:
                print(f"      {line}")
        return 1
    print("首次上手 10 步全过：无 traceback，退出码符合文档。")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        # 只清理自己建的临时根，不碰用户目录
        for d in ("tg-first-run-",):
            base = tempfile.gettempdir()
            for name in os.listdir(base):
                if name.startswith(d):
                    shutil.rmtree(os.path.join(base, name), ignore_errors=True)
    sys.exit(code)
