#!/usr/bin/env python3
"""TG 公共层：UTF-8 输出、配置读取、dsh_home 解析、JSON 输出。

为什么单列：这几件事原先散在 9 个脚本里各写一遍，优先级互不相同（v0.12.0 修的就是这个）。
最坏的后果不是报错，而是**读错 home → 报「没有数据」/数字对不上**，看起来像脚本坏了。

dsh_home 唯一口径（本文件是唯一实现，脚本里别再手写）：
    --dsh-home / --home  >  config.yaml 的 dsh_homes / dsh_home  >  $DSH_HOME  >  ~/.dsh
    相对路径按 config 文件所在目录解析；`~` 与环境变量都展开。

用法（脚本侧）：
    from tg_common import add_common_args, load_config, resolve_dsh_home, sessions_root
    a = ap.parse_args(); cfg, base = load_config(a.config)
    home = resolve_dsh_home(a.dsh_home, cfg=cfg, base=base)
    root = sessions_root(home)
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

DEFAULT_CONFIG = "config.yaml"


def unzip_channels():
    """解压通道探测：返回按优先级排列的三条通道（命令 / 内建 / 模块）。

    为什么单列：`env_check` 曾自己写一套判断（只认 `zstd` 命令与 `zstandard` 模块），
    而 `decompress_bytes` 还有第三条通道——Python ≥ 3.14 内建的 `compression.zstd`。
    两边口径不一的后果不是报错，而是**门禁比实现严**：在「3.14 + 没装 zstandard +
    没有 zstd 命令」的机器上，自检判「必需项缺失、退出码 1」，把用户指去装一个根本
    用不到的包；而所有看账脚本其实都能解压。通道与顺序只能有一份，两边都调这里。
    """
    exe = shutil.which("zstd")
    chans = [{
        "name": "command", "ok": bool(exe), "label": "zstd 命令",
        "detail": f"zstd 命令 {exe}" if exe else "没有 zstd 命令",
    }]
    ok, why = False, ""
    try:
        from compression import zstd as _cz  # noqa: F401  Py ≥ 3.14
        if hasattr(_cz, "decompress"):
            ok = True
        else:
            why = "compression.zstd 能导入但缺 decompress（同名假模块？）"
    except ImportError:
        why = (f"Python {sys.version_info[0]}.{sys.version_info[1]} 没有内建 "
               "compression.zstd（3.14+ 才有）")
    except Exception as e:  # 残缺/损坏的模块：能 import 但一用就炸
        why = f"compression.zstd 导入失败：{e}"
    chans.append({"name": "builtin", "ok": ok, "label": "Python 内建 compression.zstd",
                  "detail": "Python 内建 compression.zstd" if ok else why})
    ok, why = False, ""
    try:
        import zstandard as _zs  # noqa: F401
        if hasattr(_zs, "ZstdDecompressor"):
            ok = True
        else:
            why = "zstandard 能导入但缺 ZstdDecompressor（同名假模块？）"
    except ImportError:
        why = "没有 zstandard 模块"
    except Exception as e:
        why = f"zstandard 导入失败：{e}"
    chans.append({"name": "module", "ok": ok, "label": "zstandard 模块",
                  "detail": "zstandard 模块" if ok else why})
    return chans


def unzip_available():
    """可用的解压通道列表：非空即「能解压」。

    env_check 与 decompress_bytes 必须用同一个判据，否则会出现
    「自检说缺件、脚本却跑得动」这种最难排查的分歧。
    """
    return [c for c in unzip_channels() if c["ok"]]


def decompress_bytes(path):
    """解压 .zstd：命令 → 内建 → 模块，逐条降级；都不行时一句话退出（不抛 traceback）。

    为什么收在公共层：这段逻辑原先在 baseline / failure_analysis / turn_cost / skill_usage /
    measure_modules / retry_patterns 里各有一份，且都直接 `zstd.ZstdDecompressor()`——遇到
    同名假模块（能 import 但没有 API）会抛 AttributeError traceback；更糟的是副本之间会漂移：
    `retry_patterns.py` 那份至今没有内建通道，于是同一台机器上「大部分脚本能跑、它自己说不能」。
    """
    have = {c["name"]: c["ok"] for c in unzip_channels()}
    if have.get("command"):
        try:
            r = subprocess.run(["zstd", "-dc", path], capture_output=True)
            if r.returncode == 0 and r.stdout:
                return r.stdout
        except OSError:
            pass  # 命令在但执行不了（权限/损坏）：继续降级，不在这一层判死
    if have.get("builtin"):
        from compression import zstd as czstd
        try:
            with open(path, "rb") as f:
                return czstd.decompress(f.read())
        except Exception as e:
            sys.exit(f"zstd 解压失败（内建 compression.zstd）：{path}（{e}）")
    if have.get("module"):
        import zstandard as zstd
        dctx = zstd.ZstdDecompressor()
        with open(path, "rb") as f:
            data = f.read()
        out = []
        while data:
            dobj = dctx.decompressobj()
            try:
                out.append(dobj.decompress(data))
            except Exception:
                sys.exit(f"zstd 解压失败（文件损坏或不是 zstd 流）: {path}")
            if not dobj.eof:
                break
            data = dobj.unused_data
        return b"".join(out)
    sys.exit("解压需要二选一：`pip install zstandard`（首选，跨平台）或装 `zstd` 命令"
             "（macOS: brew install zstd；Debian/Ubuntu: apt install zstd）。"
             "Python ≥ 3.14 自带 compression.zstd，不需要装任何东西。")


def utf8_stdio():
    """Windows 控制台默认 GBK：固定 UTF-8，避免中文报告崩在 UnicodeEncodeError。

    幂等，重复调用无害；reconfigure 不存在或流不可改（被重定向）时静默跳过。
    """
    for name in ("stdout", "stderr"):
        try:
            getattr(sys, name).reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def expand(p, base=None):
    """~ 与环境变量展开；给了 base 且结果是相对路径时按 base 拼接。"""
    s = os.path.expanduser(os.path.expandvars(str(p if p is not None else "")))
    if base and not os.path.isabs(s):
        s = os.path.join(base, s)
    return os.path.normpath(s) if s else s


def config_base(config_path):
    """config 的基准目录：文件存在用其目录，否则用当前目录。"""
    try:
        if config_path and os.path.isfile(config_path):
            return os.path.dirname(os.path.abspath(config_path))
    except OSError:
        pass
    return os.getcwd()


def _yaml_ok():
    try:
        import yaml  # noqa: F401
        return True
    except ImportError:
        return False


def _config_fail(path, msg, strict):
    """配置读不出来时的唯一出口：strict → 退出；否则 stderr 警告后返回 {}。

    为什么不允许静默：读错 home 的后果是「没有数据」或偏小的数字，看起来像脚本坏了。
    """
    if strict:
        sys.exit(f"{path}: {msg}\n（配置读不出来就先改好它；`--dsh-home` 不能绕过这一步，它只覆盖 home。"
                 f"排查解析链：python scripts/tg_common.py）")
    print(f"[warn] {path}: {msg}；已退回 $DSH_HOME / ~/.dsh", file=sys.stderr)
    return {}


def load_yaml(path, strict=False):
    """读 YAML。三种情况分开处理：

    - 文件不存在 → 返回 {}（这是正常情况，不算错）
    - 文件存在但 YAML 坏 → strict 退出，否则 stderr 警告
    - 没装 pyyaml → strict 退出，否则 stderr 警告（pyyaml 是配置读取的必需项，见 README 依赖分级）
    """
    if not path or not os.path.isfile(path):
        return {}
    try:
        import yaml
    except ImportError:
        return _config_fail(path, "没装 pyyaml，配置读不到（pip install pyyaml）", strict)
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        return _config_fail(path, f"YAML 读不了：{str(e).splitlines()[0]}", strict)
    if data is None:
        return {}
    if not isinstance(data, dict):
        return _config_fail(path, f"顶层不是映射（读到 {type(data).__name__}）", strict)
    return data


def load_config(config_path=DEFAULT_CONFIG, strict=True):
    """返回 (cfg, base)。strict=True：配置存在但读不出来时直接退出。

    默认 strict 而不是容错：显式写进仓库的 config.yaml 读不出来，继续跑只会给出错数。
    真想要「没有配置也能跑」，传 strict=False 并接受 stderr 警告。
    """
    cfg = load_yaml(config_path, strict=strict) if config_path else {}
    return cfg, config_base(config_path)


def _cfg_homes(cfg, base):
    """config 里的 home 列表：dsh_homes 优先（列表），退回单个 dsh_home。"""
    if not isinstance(cfg, dict):
        return []
    raw = cfg.get("dsh_homes")
    if raw:
        seq = raw if isinstance(raw, (list, tuple)) else [raw]
    else:
        one = cfg.get("dsh_home")
        seq = [one] if one else []
    homes = []
    for h in seq:
        if not isinstance(h, str) or not h.strip():
            print(f"[warn] config.yaml 的 dsh_home(s) 里有非字符串项，已跳过: {h!r}", file=sys.stderr)
            continue
        homes.append(expand(h, base))
    return homes


def resolve_dsh_homes(arg="", cfg=None, base=None, env=None):
    """多 home 版本：显式参数 > config 列表 > $DSH_HOME > ~/.dsh，保持顺序、按真实路径去重。

    显式参数（CLI）按当前目录解析，config 里的值按 config 目录解析——两者语义不同：
    CLI 是「我现在在这里」，config 是「这份配置旁边」。
    """
    env = os.environ if env is None else env
    homes = []
    if arg:
        homes = [expand(h) for h in (arg if isinstance(arg, (list, tuple)) else [arg])
                 if isinstance(h, str) and h]
    if not homes:
        homes = _cfg_homes(cfg or {}, base)
    if not homes:
        env_home = (env or {}).get("DSH_HOME")
        homes = [expand(env_home or "~/.dsh")]
    out, seen = [], set()
    for h in homes:
        # expanduser 展不开时会原样返回 "~/..."：报错里出现字面 ~ 最容易被看错
        if h.startswith("~"):
            print(f"[warn] 无法展开 {h}（HOME / USERPROFILE 都没设）；"
                  "请用 --dsh-home 或 config.yaml 的 dsh_home 指定绝对路径", file=sys.stderr)
        real = os.path.realpath(h)
        if real in seen:
            continue
        seen.add(real)
        out.append(h)
    return out


def home_candidates(cfg=None, base=None, env=None):
    """[(home, 来源)]：config → $DSH_HOME → 默认 ~/.dsh，顺序固定并带来源标签。

    为什么单列：detect_home 要把「配置/环境给的」与「我自己扫盘扫到的」分开报出来，
    否则报告里看不出哪条是用户自己配的；但脚本里不许自己读 DSH_HOME（口径只在本文件），
    所以「读取 + 标注来源」一起放在这里，探测脚本只消费结果。
    """
    env = os.environ if env is None else env
    out = [(h, "config.yaml") for h in _cfg_homes(cfg or {}, base)]
    env_home = (env or {}).get("DSH_HOME")
    if env_home:
        out.append((expand(env_home), "$DSH_HOME"))
    if not out:
        out.append((expand("~/.dsh"), "默认 ~/.dsh"))
    return out


def resolve_dsh_home(arg="", cfg=None, base=None, env=None):
    """单 home 版本：同 resolve_dsh_homes，取第一个。"""
    return resolve_dsh_homes(arg, cfg=cfg, base=base, env=env)[0]


def sessions_root(home):
    return os.path.join(expand(home), "sessions")


def ledger_path(home):
    """旧口径（cost-meter 插件账本）。2026-10-24 起账本由 scripts/ledger.py 派生，见 ledger_paths。"""
    return os.path.join(expand(home), "storages", "cost-meter", "ledger.json")


def ledger_paths(cfg=None, base=None, homes=None, explicit=""):
    """账本候选路径，按优先级：显式参数 > config 的 ledger.path > 旧插件路径。

    为什么保留旧路径：迁移期/别的机器上可能还没跑 ledger.py，读旧账本总比报「没有数据」好；
    两个都存在时，新账本的 schema=tg-ledger/2 会被优先使用（读方按顺序取第一份有效文件）。
    """
    out = []
    if explicit:
        out.append(expand(explicit))
    if isinstance(cfg, dict):
        raw = ((cfg.get("ledger") or {}).get("path") or "").strip()
        if raw:
            out.append(expand(raw, base))
    for h in (homes or []):
        out.append(ledger_path(h))
    return out


def add_common_args(ap, config=True, home=True, json_flag=False):
    """统一 --config / --dsh-home（含 --home 别名）/ --json 的名字与说明。"""
    if config:
        ap.add_argument("--config", default=DEFAULT_CONFIG,
                        help=f"配置文件，默认 {DEFAULT_CONFIG}（相对路径按当前目录）")
    if home:
        ap.add_argument("--dsh-home", "--home", dest="dsh_home", default="",
                        help="DSH home；不给则读 config 的 dsh_home/dsh_homes，再退回 $DSH_HOME / ~/.dsh")
    if json_flag:
        ap.add_argument("--json", action="store_true", help="输出 JSON（给脚本/管道用）")
    return ap


def emit_json(obj, indent=2):
    print(json.dumps(obj, ensure_ascii=False, indent=indent))


def main():  # 自检：python scripts/tg_common.py 打印当前口径解析结果
    utf8_stdio()
    cfg, base = load_config(DEFAULT_CONFIG)
    print(f"config: {DEFAULT_CONFIG}  基准目录: {base}")
    print(f"cfg.dsh_home: {cfg.get('dsh_home')!r}  env DSH_HOME: {os.environ.get('DSH_HOME')!r}")
    for h in resolve_dsh_homes(cfg=cfg, base=base):
        print(f"  → {h}  (sessions 存在: {os.path.isdir(sessions_root(h))})")


if __name__ == "__main__":
    main()
