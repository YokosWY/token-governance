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
import time

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


# ---------------------------------------------------------------- 对话夹母文件夹
# 口径（本文件是唯一实现）：config.yaml 的 memory_root  >  $TG_MEMORY_ROOT  >  没有就问
# 不再兜底 ~/tg-memory：那条静默降级会把「路径配错 / 忘配」伪装成「用户没配置」，
# 2026-10-25 就是它把交接卡写进了 C:\Users\<user>\tg-memory（插件 spawn 没传 --config）。

DEFAULT_MEMORY_ROOT_PARTS = ("Documents", "tg-memory")


class MemoryRootUnset(Exception):
    """没有 memory_root，且当前不是可交互终端。调用方应转成「说人话 + 非零退出码」。"""


def default_memory_root():
    """新装机器的推荐路径 ~/Documents/tg-memory（跨平台；不往家目录根丢东西）。"""
    return os.path.join(os.path.expanduser("~"), *DEFAULT_MEMORY_ROOT_PARTS)


def memory_root_from(cfg=None, base=None, env=None):
    """config 的 memory_root > $TG_MEMORY_ROOT；都没有返回 ""（空串＝未配置，由调用方决定问还是报错）。"""
    env = os.environ if env is None else env
    if isinstance(cfg, dict):
        v = cfg.get("memory_root")
        if isinstance(v, str) and v.strip():
            return expand(v, base)
    v = (env or {}).get("TG_MEMORY_ROOT")
    if isinstance(v, str) and v.strip():
        return expand(v)
    return ""


def is_interactive():
    """能不能问用户：stdin 与 stdout 都是终端才算（管道 / 插件 spawn / CI 一律不阻塞）。"""
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except Exception:
        return False


def _write_text_atomic(path, text):
    """先写临时文件再 os.replace：崩溃/断电时不会留下半截配置。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


MEMORY_README = """# 对话夹（会话临时记忆库）

母文件夹由 `python scripts/setup.py` 创建；长期结论仍回写 `memory/` 主库。

```
<workspace>/<任务名>/
  00-card.md       会话卡：目标 / 进度 / 下一步（≤40 行，覆盖写）
  01-<sid8>.md     同任务的其他会话卡（不互相覆盖）
  99-cards.json    本夹索引
  30-logs/         原始输出（正文只留路径）
```

- 当前位置：`{root}`
- 换位置：`python scripts/setup.py --memory-root "<新路径>"`（会改 `config.yaml` 的 `memory_root`）
- 以前没有配置时会静默落到 `~/tg-memory`，这个兜底已删除。
"""

MEMORY_TEMPLATE_CARD = """# 会话卡 · <标题>

- 会话：<sid>
- 工作区：<workspace>
- 状态：进行中

## 目标

## 下一步
"""


def ensure_memory_layout(root):
    """建母文件夹（幂等）：根 + README.md + _template/00-card.md。返回是否新建了根目录。"""
    root = expand(root)
    created = not os.path.isdir(root)
    os.makedirs(root, exist_ok=True)
    os.makedirs(os.path.join(root, "_template"), exist_ok=True)
    readme = os.path.join(root, "README.md")
    if not os.path.isfile(readme):
        _write_text_atomic(readme, MEMORY_README.format(root=root.replace("\\", "/")))
    tpl = os.path.join(root, "_template", "00-card.md")
    if not os.path.isfile(tpl):
        _write_text_atomic(tpl, MEMORY_TEMPLATE_CARD)
    return created


_GENERATED = set()   # 本进程里刚由 config.example.yaml 生成出来的配置路径（realpath）


def mark_generated(path):
    """登记「这份配置是本进程刚生成的」：它没有旧内容可回滚，写它不必留备份。"""
    try:
        _GENERATED.add(os.path.realpath(path))
    except OSError:
        pass


def is_generated(path):
    try:
        return os.path.realpath(path) in _GENERATED
    except OSError:
        return False


def _read_text_and_nl(path):
    """原样读并识别换行风格，返回 (归一化成 \\n 的文本, 原换行符)。

    为什么不用 text 模式读：Windows 上读进来是 `\\n`、写回去也是 `\\n`，用户原本 CRLF 的
    config.yaml 会被整篇重写（git diff 全红），看起来像工具改了他一堆配置。
    """
    with open(path, "rb") as f:
        raw = f.read()
    nl = "\r\n" if b"\r\n" in raw else "\n"
    return raw.decode("utf-8").replace("\r\n", "\n"), nl


def _write_bytes_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def write_memory_root(config_path=DEFAULT_CONFIG, value="", example="config.example.yaml"):
    """把 memory_root 写回 config.yaml：按行替换（保留其它项、注释与换行风格），原子落盘。

    三条硬规矩（2026-10-30 评测定，安装器与 setup.py 共用）：
      1) 内容没变就不写、不备份——重复运行不该在仓库里堆 `.bak-*`；
      2) 换行风格跟原文件走（CRLF 保持 CRLF）；
      3) 刚从 example 生成的新配置本来就没有旧内容，不留无意义备份。

    返回 (config_path, 备份路径或 "")。
    """
    path = config_path or DEFAULT_CONFIG
    bak = ""
    base_dir = config_base(path)
    ex = example if os.path.isabs(example) else os.path.join(base_dir, example)
    created = is_generated(path)   # 同一次运行里安装器刚生成的那份
    if not os.path.isfile(path):
        if os.path.isfile(ex):
            shutil.copyfile(ex, path)
            created = True
            mark_generated(path)
        else:
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
            _write_text_atomic(path, "# Token-Governance 配置（setup.py 生成）\n"
                                     "memory_root: \"%s\"\n" % str(value).replace("\\", "/"))
            return path, bak
    if not created:
        # 也可能是同一次运行里刚由安装器从 example 生成的：内容仍与模板逐字节一致
        # 就当作「新配置」，没有可回滚的旧内容，不该留备份。
        try:
            with open(path, "rb") as f:
                cur = f.read()
            if os.path.isfile(ex):
                with open(ex, "rb") as f:
                    created = cur == f.read()
        except OSError:
            created = False
    old, nl = _read_text_and_nl(path)
    lines = old.splitlines() if old else []
    newline = 'memory_root: "%s"' % str(value).replace("\\", "/")
    hit = False
    for i, s in enumerate(lines):
        if s.lstrip().startswith("memory_root:") and not s.lstrip().startswith("#"):
            lines[i] = newline
            hit = True
            break
    if not hit:
        lines.append(newline)
    new = nl.join(lines)
    if not new.endswith(nl):
        new += nl
    data = new.encode("utf-8")
    with open(path, "rb") as f:
        if f.read() == data:
            return path, ""          # 已经是目标值：不写、不留备份
    if not created:
        bak = path + ".bak-memory-root-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copyfile(path, bak)
    _write_bytes_atomic(path, data)
    return path, bak


def ensure_memory_root(cfg=None, base=None, config_path=None, env=None, ask=None, persist=True):
    """取对话夹母文件夹并保证目录存在。

    已配置 / 已设 $TG_MEMORY_ROOT  → 直接用（缺目录就建，第一次会自动创建）
    都没配 → 可交互就问宿主放哪并写回 config.yaml；不可交互抛 MemoryRootUnset。

    返回 (root, info)；info = {"source": "config"|"env"|"prompt", "created": bool}
    """
    env = os.environ if env is None else env
    root = memory_root_from(cfg, base, env)
    if root:
        src = "config" if (isinstance(cfg, dict) and str(cfg.get("memory_root") or "").strip()) else "env"
        return root, {"source": src, "created": ensure_memory_layout(root)}
    if ask is None:
        ask = is_interactive()
    if not ask:
        raise MemoryRootUnset(
            "没有配置对话夹母文件夹（memory_root），当前也不能问你放在哪（非交互终端）。\n"
            f"  推荐：{default_memory_root()}\n"
            "  三选一：\n"
            '    1) python scripts/setup.py --memory-root "<路径>"\n'
            "    2) 设置环境变量 TG_MEMORY_ROOT=<路径>\n"
            "    3) 在终端里直接跑 python scripts/setup.py（会问）\n"
            "  说明：以前没配置会静默落到 ~/tg-memory，这个兜底已删除。")
    dft = default_memory_root()
    print(f"对话夹母文件夹（会话临时记忆库）将放在：{dft}")
    print("  直接回车＝用这个；也可输入别的路径（~ 会展开，相对路径按 config 所在目录）。")
    while True:
        try:
            s = input("放在哪？ ").strip()
        except EOFError:
            s = ""
        p = expand(s or dft, base)
        if os.path.exists(p) and not os.path.isdir(p):
            print(f"  {p} 是文件不是目录，换一个。")
            continue
        try:
            created = ensure_memory_layout(p)
        except OSError as e:
            print(f"  建不了 {p}（{e.strerror or e}），换一个。")
            continue
        if persist and config_path:
            try:
                write_memory_root(config_path, p)
                print(f"  已写入 {config_path}")
            except OSError as e:
                print(f"  [warn] 写不进 {config_path}：{e}（本次仍用 {p}）", file=sys.stderr)
        return p, {"source": "prompt", "created": created}


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
