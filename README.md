# Token-Governance (TG)

[![license](https://img.shields.io/badge/license-MIT-191B1F?style=flat-square)](LICENSE) [![version](https://img.shields.io/badge/version-v0.10.17-2F5D8A?style=flat-square)](CHANGELOG.md) [![skills](https://img.shields.io/badge/skills-10-9C2B23?style=flat-square)](skills) [![scripts](https://img.shields.io/badge/scripts-18-6B6A66?style=flat-square)](scripts)

TG 是给 LLM 代理做上下文成本治理的技能包，形态是 10 个技能、18 个脚本、1 个插件，装在 DeepSeek Harness 的配置层上。

它不改模型单价，只做两件事：用逐 step 用量日志把开销拆到模块，再改配置让每步重发的那部分变小。

**读法：** 评估者看「实测」；使用者看「安装 / 快速开始」；贡献者看「自检与测试 / 贡献」。

## 特性

- **按存活计息：** 成本 = 内容长度 × 剩余步数。492 步的会话里，42 KB 的技能目录被注入 53 次，占账单 42%。
- **同会话反事实：** 配置是全局的，没法 A/B；`measure_modules.py --cf-schema --cf-catalog` 把旧参数代回同一会话重算。
- **静态前缀瘦身：** 工具清单 230–265 个降到 174 个，schema 284–312 → 154 kB，起始上下文 81–99 → 53 k。
- **目录旁路池：** 10 个技能只留 `tg-router` 常驻，其余标 `disable-model-invocation: true`，模型侧不可见，按需 `read`。
- **不改写历史：** pruner 阈值 4096 → 200000（等于关），未命中 input 从 3,697 降到 1,204 / 步。
- **运行时提醒：** 循环守卫按「工具名 + 参数哈希」计次，第 2 / 4 / 6 次提醒，不阻断调用、不结束会话。
- **口径分层：** 单步 / 每轮 / 任务三级都能出数；`turn_cost.py` 按「一条用户指令 = 一轮任务」算钱。
- **能回滚：** `patch_snapshot.py` 拍补丁快照，一条命令回到上一版，回滚前自动再备份。

## 实测

逐 step 重放真实会话日志。价格 input $0.15 / cacheRead $0.003 / output $0.6 每百万 token，汇率 7.1。

<!-- metrics:begin -->

| 指标 | 改前 | 改后 | 变化 |
|---|---:|---:|---:|
| 工具清单 | 230–265 个 / 284–312 kB | 174 个 / 154 kB | −46~51% |
| 技能目录单条 | 41.9–42.6 kB | 9.6 kB（中位） | −77% |
| 起始上下文 | 81–99 k | 53 k（中位） | −46% |
| 每轮成本 | ¥0.0688 | ¥0.0353 | −48.6% |
| 未命中 input / 步 | 3,697 | 1,204 | −67.4% |
| 缓存命中率 | 98.9% | 99.2% | +0.3 pp |
| 裁剪 / 压缩次数 | 4–12 / 3–12 | 0 / 0 | −100% |

> 数字来源 `data/metrics.json`（更新 2026-10-22）。单机、deepseek-flash、200 k 窗口。改前 = 3 个长会话（492/333/764 步），改后 = 7 个新会话（53–126 步）。

<!-- metrics:end -->

同一会话代回旧参数重放，每轮再降 30–39%。这是下限：反事实只替换 schema 与目录体积，没有模拟旧配置下裁剪与压缩造成的前缀重算。

### 还测到三件反直觉的事

| 发现 | 数值（近 3 天，72 会话 / 736 轮） | 含义 |
|---|---|---|
| 失败轮次吃掉近一半花费 | 236 次失败调用落在 142 轮（19%），这些轮次花 ¥21.42，占窗口 48.9% | 省钱要看「一次做成率」，不只压前缀 |
| 大头是协议错误 | 其中 65 次（27.5%）是「没读就改 / 文件被并行改 / 工具名写错」 | 属操作纪律，`docs/operations.md` 第二节 |
| 技能几乎没被调用 | 73 会话共 21 次；`tg-gui` / `tg-loop` / `tg-orchestrate` 为 0 | 技能是文档，脚本才是产品 |

样本口径：单机、deepseek-flash、200 k 窗口。工具数、目录体积、步数都是本机实测，换机器数值会不同；方法与脚本通用，价格按 `config.yaml` 改。

方法与逐会话数字见 [`docs/benchmark.md`](docs/benchmark.md)。

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
```

只想要路由层也可以：复制 `skills/tg-router` 与 `scripts/` 就能跑，其余技能按需加。

## 快速开始

三个动作覆盖日常：看账、拆会话、算任务。

```bash
# ① 账本趋势：日均花费、调用数、缓存命中率
python scripts/cost_summary.py --config config.yaml --days 7

# ② 拆一个会话的模块花费；--list 先选 id
python scripts/measure_modules.py --list --days 2
python scripts/measure_modules.py --session <id> --dsh-home "$DSH_HOME" \
  --cf-schema 312000 --cf-catalog 42500

# ③ 任务成本：按用户指令切轮，出中位 / 均值 / P90
python scripts/turn_cost.py --config config.yaml --days 3
```

期望输出（节选）：

```
计价: input $0.15/M cacheRead $0.003/M output $0.6/M × 7.1（默认 flash 价（估算））
步=126 轮=19 总CNY=0.719 每轮CNY=0.0378 上下文=15.9M 输出=67k
本会话 CNY 0.719（CNY 0.0378/轮） | 对照 CNY 1.082（CNY 0.0569/轮） | 差 33.6%
```

## 组成

### 技能（10 个，仅 `tg-router` 常驻）

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
| `tg-ops` | 补丁分层、备份、回滚、升级漂移 |
| `tg-loop` | 循环守卫的处置流程 |

其余 9 项 `disable-model-invocation: true`：模型侧不可见，用 `read ${SKILLS_DIR}/<名>/SKILL.md` 或 `/名字` 调。

### 脚本（18 个，纯标准库优先）

| 脚本 | 作用 |
|---|---|
| `cost_summary.py` | 读账本，出总量、通道、日均、命中率 |
| `cache_audit.py` | 缓存体检：命中率、未命中/步、前缀重建、按价差 k 折算的未命中成本占比 |
| `measure_modules.py` | 按模块复算会话；`--list` 列出最近会话可选 id |
| `turn_cost.py` | 任务成本：每轮 CNY / 步数 / 失败调用，含中位、均值、P90 |
| `compare_days.py` | 按天对比单价、上下文、未命中、输出 |
| `failure_analysis.py` | 失败重试分析；`--protocol-only` 只看协议错误 |
| `retry_patterns.py` | 换参重试观测（含「串内有报错」的失败重试） |
| `skill_usage.py` | 技能埋点：`skill` 与 `/名字` 调用次数、从未用过的技能 |
| `baseline.py` | 首日基线：记起始上下文 / schema / 目录，隔天 `--compare` |
| `preflight.py` | 安装前查冲突：同名技能、记忆根、补丁条目与预设、插件名漂移 |
| `patch_snapshot.py` | 补丁快照 / 列表 / 对比 / 回滚 |
| `gen_preset_override.py` | 升级后重生成 preset 覆盖块（`--window` 定 pruner 阈值） |
| `install_guard.py` | 安装循环守卫（默认 dry-run，带备份与卸载） |
| `verify_guard.mjs` | 循环守卫 13 项离线断言 |
| `audit_pack.py` | 包自检：结构、引用、旧词、可移植性、数字一致性 |
| `gen_metrics.py` | 数字单源：从 `data/metrics.json` 生成 / 校验文档表格 |
| `check_yaml.py` | 校验补丁 YAML（兼容 `!!js` 自定义标签） |
| `new_session.py` | 新建对话夹卡片 |

### 插件（1 个）

`plugins/dsh-loop-guard/`：零依赖 ESM 插件，`tools/post-execute` 判「工具名 + 参数哈希」严格相等，按阈值追加提醒；不阻断、不弹窗、不结束会话。参数预览默认关闭（`argumentsPreviewChars: 0`），避免密钥进上下文。

## 配置

```yaml
dsh_home: "~/.dsh"             # 数据目录：账本、会话、配置
memory_root: "~/tg-memory"     # 对话夹根目录
skills_dir: "~/.dsh/skills"    # 技能目录

# dsh_homes:                   # 多套客户端时列出来，按 ledger 真实路径去重
#   - "~/.dsh"
#   - "~/dsh-next"

budget:
  daily: null                  # 单日超了就提醒，null 关闭

prices_usd_per_mtok:           # 折算用；有实付账本时以账本为准
  input: 0.15
  cached: 0.003
  output: 0.6
rate: 7.1                      # USD→CNY

loop_guard:
  enabled: true
  thresholds: [2, 4, 6]
  askUserAt: 6
  argumentsPreviewChars: 0     # 0 = 只报长度，不写内容
  exclude: [todo_write, job_output, job_list, job_kill, ask_user_question, send_message, present,
            http_request, mcp_connector_configure, mcp_connector_reconfigure, mcp_connector_import_json]
```

路径支持 `~` 与环境变量；相对路径按 `config.yaml` 所在目录解析。Windows 路径用正斜杠或单引号，双引号里的反斜杠会被 YAML 当转义。

## 自检与测试

```bash
python scripts/audit_pack.py          # 结构 / 引用 / 旧词 / 数字一致性
python tests/run_tests.py             # 23 项断言，纯标准库，不需要 pytest
python scripts/gen_metrics.py --check # 文档表格必须与 data/metrics.json 一致
```

三条都是只读。`audit_pack` 与测试非零退出即失败；改文档数字后要跑 `gen_metrics.py --write` 再提交。

## 数据口径

| 项 | 定义 |
|---|---|
| 单步成本 | 一次模型调用的计费（input + cacheRead + output 按价折算） |
| 每轮成本 | 一条用户指令从发出到助手停止的全部步 |
| 任务成本 | 同「每轮」，用于对外表述；含失败调用与返工 |
| 未命中 input / 步 | 按输入价重算的 token，与缓存命中率分开看 |
| 价差 k | 输入价 ÷ 缓存价；k 越大，未命中越贵，越值得关自动改写 |

## 支持与不支持

- 跨客户端不保证：Claude Code、Codex、Cursor 没有 preset 机制，高费工具隔离得改走子代理，见 `docs/adaptation.md`。
- 脚本只认 DSH：`measure_modules.py` 读 DSH v4 会话日志，`cost_summary.py` 读 cost-meter 账本；换客户端只有方法能搬。
- 压缩不当杠杆：729 步会话压缩 4 次，成本约 ¥0.8、省下约 ¥1.0，净收益接近零。
- 不看账本不下结论：schema 变小、命中率上升都只是过程量。
- 补丁不随包：工具裁剪、pruner、压缩阈值都在各机器自己的 `cordis.patch.yml`，复制技能不会降低账单。
- 覆盖块是快照：组合配置每次启动重写，升级后必须重跑 `gen_preset_override.py`。
- 不接管安全与需求澄清：密钥、脱敏、上传门禁属本地约束；需求澄清在 `clarify-needs/`。

## 文档

- [`docs/benchmark.md`](docs/benchmark.md)：实测方法与逐会话数字；第十节是缓存命中（provider 无关）
- [`docs/principles.md`](docs/principles.md)：十二条原理与阈值推导
- [`docs/operations.md`](docs/operations.md)：循环守卫与协议纪律（协议错误占失败调用 27.5%）
- [`docs/concepts.md`](docs/concepts.md)：全部自造词 + 六个机制
- [`docs/adaptation.md`](docs/adaptation.md)：换机器、换客户端、没有账本时怎么降级

## 版本

| 版本 | 内容 |
|---|---|
| v0.10.16 | 文档 11 → 5 篇；协议纪律；数字单源 |
| v0.10.14 | 失败重试分析（失败轮次占 48.9% 花费） |
| v0.10.13 | 任务成本、技能埋点、补丁回滚 |
| v0.10.12 | 23 项回归测试；文档数字单源 |
| v0.10.9 | 首日基线、会话检索、插件名漂移 |
| v0.10.8 | 缓存体检（价差 k 决定一切） |
| v0.8–v0.10 | 循环守卫、`tg-isolate`、`tg-memlib`、v0.10 复测 |

完整记录见 [`CHANGELOG.md`](CHANGELOG.md)。

## 贡献

回归测试：`python tests/run_tests.py`。

先读 [`CONTRIBUTING.md`](CONTRIBUTING.md)：提交类型、表述规范、破坏性操作的前置检查。问题走 Issues，改动附复现命令与实测数字。

## 许可

MIT © 2026。见 [`LICENSE`](LICENSE)。
