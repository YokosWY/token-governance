# Token-Governance (TG)

[![license](https://img.shields.io/badge/license-MIT-191B1F?style=flat-square)](LICENSE) [![version](https://img.shields.io/badge/version-v0.10.10-2F5D8A?style=flat-square)](CHANGELOG.md) [![skills](https://img.shields.io/badge/skills-10-9C2B23?style=flat-square)](skills) [![scripts](https://img.shields.io/badge/scripts-13-6B6A66?style=flat-square)](scripts)

TG 是给 LLM 代理做上下文成本治理的技能包，形态是 10 个技能、13 个脚本、1 个插件，装在 DeepSeek Harness 的配置层上。

它不改模型单价，只做两件事：用逐 step 用量日志把开销拆到模块，再改配置让每步重发的那部分变小。

## 特性

- **按存活计息：** 成本 = 内容长度 × 剩余步数。492 步的会话里，42 KB 的技能目录被注入 53 次，占账单 42%。
- **同会话反事实：** 配置是全局的，没法 A/B；用 `--cf-schema` / `--cf-catalog` 把旧参数代回同一会话重算。
- **静态前缀瘦身：** 工具清单 230–265 个降到 174 个，schema 312 → 154 kB，起始上下文 98 → 53 k。
- **目录旁路池：** 10 个技能只留 `tg-router` 常驻，其余标 `disable-model-invocation: true`，模型侧不可见。
- **不改写历史：** pruner 阈值 4096 → 200000（等于关），未命中 input 从 3,697 降到 1,204 / 步。
- **运行时提醒：** 循环守卫按「工具名 + 参数哈希」计次，第 2 / 4 / 6 次提醒，不阻断调用，不结束会话。

## 实测

逐 step 重放真实会话日志。价格 input $0.15 / cacheRead $0.003 / output $0.6 每百万 token，汇率 7.1。改前取 3 个长会话（492 / 333 / 764 步），改后取 7 个新会话（53–126 步）。

| 指标 | 改前 | 改后 | 变化 |
|---|---:|---:|---:|
| 工具清单 | 230–265 个 / 284–312 kB | 174 个 / 154 kB | −46~51% |
| 技能目录单条 | 41.9–42.6 kB | 9.6 kB（中位） | −77% |
| 起始上下文 | 81–99 k | 53 k（中位） | −46% |
| 每轮成本 | ¥0.0688 | ¥0.0353 | −48.6% |
| 未命中 input / 步 | 3,697 | 1,204 | −67.4% |
| 缓存命中率 | 98.9% | 99.2% | +0.3 pp |
| 裁剪 / 压缩次数 | 4–12 / 3–12 | 0 / 0 | −100% |

同一会话代回旧参数重放，每轮再降 30–39%。这是下限：反事实只替换 schema 与目录体积，没有模拟旧配置下裁剪与压缩造成的前缀重算。

样本口径：单机、deepseek-flash、200 k 窗口。工具数、目录体积、步数都是本机实测，换机器数值会不同；方法与脚本通用，价格按 `config.yaml` 改。

方法与逐会话数字见 [`docs/benchmark.md`](docs/benchmark.md)，复现命令在该文第八、九节。

## 安装

前提：Python 3.9+、pyyaml（`pip install pyyaml`）；解压会话日志要 `zstd` 命令或 `pip install zstandard`，二选一；循环守卫自检需要 Node 18+。

```bash
cp -r skills/tg-* "$DSH_HOME/skills/"              # 1) 技能入目录，重启 DSH 后生效
cp config.example.yaml config.yaml                 # 2) 改 dsh_home、memory_root、skills_dir 三行
python scripts/preflight.py --config config.yaml   # 3) 只读预检：放行 0 / 待确认 1 / 拒绝 2
```

装好先正常用一天。账本由 cost-meter 记，空环境跑体检会提示「没有可读的 ledger」并退出码 1，属正常：

```bash
python scripts/cost_summary.py --config config.yaml --days 3   # 输出含 合计 CNY
python scripts/cache_audit.py --config config.yaml --days 3    # 只算缓存：命中率、未命中/步、价差 k
```

没有账本也能测：`python scripts/cache_audit.py --jsonl <session.jsonl>`，或 `--template` 打印手工采集格式。

只想要路由层也可以：复制 `skills/tg-router` 与 `scripts/` 就能跑，其余技能按需加。

## 快速开始

复算任意会话的模块花费，再把旧配置代回去对照：

```bash
python scripts/measure_modules.py --session <id> --dsh-home "$DSH_HOME"
python scripts/measure_modules.py --session <id> --dsh-home "$DSH_HOME" \
  --cf-schema 312000 --cf-catalog 42500
```

输出：`本会话 CNY 0.719（CNY 0.0378/轮） | 对照 CNY 1.082（CNY 0.0569/轮） | 差 33.6%`。

## 组成

技能，仅路由层常驻：

| 技能 | 管什么 |
|---|---|
| `tg-router` | 入口与场景路由 |
| `tg-cost` | 审计、归因、算收益 |
| `tg-context` | 工具清单、技能目录、上下文 |
| `tg-memory` | 会话内存、跨区交接 |
| `tg-memlib` | 长期记忆：CORE 层、分级提炼 |
| `tg-orchestrate` | 子代理分档与审验 |
| `tg-gui` | 常规区与 GUI 区交接 |
| `tg-isolate` | 高费低频工具隔离为独立 preset |
| `tg-ops` | 补丁分层、备份、升级漂移 |
| `tg-loop` | 循环守卫的处置流程 |

脚本：

| 脚本 | 作用 |
|---|---|
| `cost_summary.py` | 读账本，出总量、通道、日均、命中率 |
| `cache_audit.py` | 缓存体检：命中率、未命中/步、前缀重建事件、按价差 k 折算的未命中成本占比（只读，纯标准库） |
| `measure_modules.py` | 按模块复算会话，可做配置对比；`--list` 列出最近会话可选 id |
| `compare_days.py` | 按天对比单价、上下文、未命中、输出 |
| `baseline.py` | 首日基线：记起始上下文/schema/目录，隔天 `--compare` 对比 |
| `preflight.py` | 安装前查冲突，含覆盖块与插件名漂移 |
| `audit_pack.py` | 包自检：结构、引用、旧词、可移植性 |
| `check_yaml.py` | 校验补丁 YAML |
| `gen_preset_override.py` | 升级后重生成 preset 覆盖块 |
| `new_session.py` | 新建对话夹卡片 |
| `install_guard.py` | 安装循环守卫（默认 dry-run） |
| `verify_guard.mjs` | 循环守卫 13 项离线断言 |
| `retry_patterns.py` | 换参重试观测：同工具连续 ≥3 次且参数各不同的串数，可再筛「串内有报错」的失败重试（只统计不拦截） |

## 不支持

- 跨客户端不保证：Claude Code、Codex、Cursor 没有 preset 机制，高费工具隔离得改走子代理，见 `docs/adaptation.md`。
- 脚本只认 DSH：`measure_modules.py` 读 DSH v4 会话日志，`cost_summary.py` 读 cost-meter 账本；换客户端只有方法能搬。
- 压缩不当杠杆：729 步会话压缩 4 次，成本约 ¥0.8、省下约 ¥1.0，净收益接近零。
- 不看账本不下结论：schema 变小、命中率上升都只是过程量。
- 补丁不随包：工具裁剪、pruner、压缩阈值都在各机器自己的 `cordis.patch.yml`，复制技能不会降低账单。
- 覆盖块是快照：组合配置每次启动重写，升级后必须重跑 `gen_preset_override.py`。
- 不接管安全与需求澄清：密钥、脱敏、上传门禁属本地约束；需求澄清在 `clarify-needs/`。

## 文档

- [`docs/benchmark.md`](docs/benchmark.md)：实测方法与逐会话数字
- [`docs/principles.md`](docs/principles.md)：十二条原理与阈值推导
- [`docs/guard.md`](docs/guard.md)：循环守卫的事件点、参数哈希与已知限制
- [`docs/cache-hit.md`](docs/cache-hit.md)：缓存命中（provider 无关）：三类缓存、价差倍数 k、打穿前缀的 7 个来源
- 其余在 `docs/`：`mechanisms.md` 六个机制、`adaptation.md` 换环境降级、`portable-vs-local.md` 可移植边界、`glossary.md` 术语、`design.md` 色板

## 贡献

先读 [`CONTRIBUTING.md`](CONTRIBUTING.md)：提交类型、表述规范、破坏性操作的前置检查。问题走 Issues，改动附复现命令与实测数字。

## 许可

MIT © 2026。见 [`LICENSE`](LICENSE)。
