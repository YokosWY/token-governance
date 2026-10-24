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
import subprocess
import sys

DEFAULT_CONFIG = "config.yaml"


def decompress_bytes(path):
    """解压 .zstd：先试 zstd 命令，没有再用 Python zstandard；两者都不可用时一句话退出。

    为什么收在公共层：这段逻辑原先在 baseline / failure_analysis / turn_cost / skill_usage /
    measure_modules 里各有一份，且都直接 `zstd.ZstdDecompressor()`——遇到同名假模块（能
    import 但没有 API）会抛 AttributeError traceback。
    """
    try:
        r = subprocess.run(["zstd", "-dc", path], capture_output=True)
        if r.returncode == 0 and r.stdout:
            return r.stdout
    except FileNotFoundError:
        pass
    try:
        import zstandard as zstd
        dctx = zstd.ZstdDecompressor()
    except ImportError:
        sys.exit("解压需要二选一：`pip install zstandard`（首选）或装 `zstd` 命令"
                 "（macOS: brew install zstd；Debian/Ubuntu: apt install zstd）。")
    except AttributeError:
        sys.exit("装的 zstandard 没有 ZstdDecompressor API（可能是同名假模块）："
                 "pip install -U zstandard")
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


def resolve_dsh_home(arg="", cfg=None, base=None, env=None):
    """单 home 版本：同 resolve_dsh_homes，取第一个。"""
    return resolve_dsh_homes(arg, cfg=cfg, base=base, env=env)[0]


def sessions_root(home):
    return os.path.join(expand(home), "sessions")


def ledger_path(home):
    return os.path.join(expand(home), "storages", "cost-meter", "ledger.json")


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
