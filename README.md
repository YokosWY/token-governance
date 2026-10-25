<div align="center">

# Token-Governance (TG)

**简体中文** | [English](README_EN.md)

<img alt="license" src="https://img.shields.io/badge/license-MIT-263146?style=flat-square">
<img alt="ci" src="https://github.com/YokosWY/token-governance/actions/workflows/ci.yml/badge.svg">
<img alt="version" src="https://img.shields.io/badge/version-v1.0.1-4b6fff?style=flat-square">
<img alt="skills" src="https://img.shields.io/badge/skills-10-9C2B23?style=flat-square">
<img alt="scripts" src="https://img.shields.io/badge/scripts-26-6B6A66?style=flat-square">
<img alt="tests" src="https://img.shields.io/badge/tests-152-2f6f4f?style=flat-square">
<img alt="status" src="https://img.shields.io/badge/status-stable-2f6f4f?style=flat-square">

</div>

<div align="center">

> 给 LLM 代理做上下文成本治理的技能包：用逐 step 用量日志把开销拆到模块，
> 再改配置让每步重发的那部分变小。
>
> *A skill pack that measures and reduces context cost for LLM agents:
> break the bill down to modules from per-step usage logs, then shrink what gets resent every step.*
>
> 10 个技能、26 个脚本、1 个插件；装在 DeepSeek Harness 的配置层上。
> 必需依赖三项：Python 3.9+、pyyaml、解压器（`pip install zstandard` 或 `zstd` 命令，二选一）；Node 18+ 仅用于守卫自检。
> 换机器先跑 `python scripts/env_check.py` 确认环境。
> 日常入口是 `python scripts/doctor.py`：一条命令把体积、缓存、成本、失败合成一页，给出带预估的动作。

</div>

---

## 亮点

- **按存活计息**——成本 = 内容长度 × 剩余步数。只统计一次注入会得到差两百倍的结论，因此所有归因都按「内容 × 它存在的步数」重算。
- **同会话反事实**——配置是全局的，同一台机器没法让两套参数同时跑。`measure_modules.py --cf-schema --cf-catalog` 把旧参数代回同一个会话重演，规避任务难度差异。
- **静态前缀优先**——工具清单与技能目录是每步都发的固定开销，收益最稳。一个 200 k 窗口的样本里：工具清单 230–265 → 174 个、schema 284–312 → 154 kB，技能目录单条 42 → 9.6 kB。
- **目录旁路池**——可发现不等于可调用。10 个技能只留 `tg-router` 常驻，其余标 `disable-model-invocation`，模型侧不可见，用 `read` 或 `/名字` 调；装再多也不增加每步固定开销。
- **不改写历史**——缓存键是精确 token 前缀。一次消息改写会让改写点之后的整段前缀按输入价重算，与缓存价相差 50 倍；因此事后裁剪阈值设到不触发，体积在写入时处理（工具输出落盘）。
- **工具输出在入口瘦一次**——`dsh-tg-account` 的 `tools/post-execute` 钩子只在新结果进上下文的那一刻改写：结构感知窗口（表格带原始行号采样）、重复行折叠、错误行强制保留，双门控（≤72% 且省 ≥500 B 且 token 严格下降）不过就放行，原文先落盘、notice 里带路径供 `read` 取回。不注册新工具，零常驻 schema。本机 133 会话实测：198 次压缩、省 1.33 MB、净省约 ¥1.9（≈ 同批花费 1.8%），属零钱，不是主力。
- **压缩只当安全阀**——压缩一次把整段前缀打回未命中，省的是缓存价、赔的是输入价；实测 729 步会话压缩 4 次，净收益接近零。阈值宁高勿低（0.8），只在接近窗口上限时触发。
- **口径分三层**——单步 / 每轮（一条用户指令）/ 任务都能出数。`turn_cost.py` 按轮算钱并给中位、均值、P90，避免「每步变便宜」掩盖长尾。
- **运行时防线**——循环守卫按「工具名 + 参数哈希」严格相等计次，第 2 / 4 / 6 次追加提醒；不阻断调用、不弹窗、不结束会话。参数预览默认关闭，避免密钥进上下文。
- **能回滚**——`patch_snapshot.py` 拍快照、列快照、对比、一条命令回滚；回滚前自动备份当前文件，并校验 YAML 合法。
- **数字单源**——文档汇总表由 `data/metrics.json` 生成，`audit_pack.py` 与测试会拦住手改；`retry_patterns`、`failure_analysis` 只做观测，不做拦截。

## 你会看到什么

装好后日常输出是这四类（示例来自一台 200 k 窗口的机器，数值随环境变）：

```text
$ python scripts/cost_summary.py --config config.yaml --days 7
窗口: 最近 7 天 | 合计 CNY 105.33 | 8324 次调用
上下文 1.974 B | 缓存命中 99.05% | 均值 237 k/次 | CNY 0.0127/次

$ python scripts/measure_modules.py --session <id> --dsh-home "$DSH_HOME"
计价: input $0.15/M cacheRead $0.003/M output $0.6/M × 7.1（默认 flash 价（估算））
步=126 轮=19 总CNY=0.719 每轮CNY=0.0378 上下文=15.9M 输出=67k
本会话 CNY 0.719（CNY 0.0378/轮） | 对照 CNY 1.082（CNY 0.0569/轮） | 差 33.6%

$ python scripts/turn_cost.py --config config.yaml --days 3
任务成本（3 个会话 / 42 轮；价格 config: config.yaml）
每轮 CNY  中位 0.0548  均值 0.0932  P90 0.22

$ python scripts/preflight.py --config config.yaml
判定：放行（退出码 0），按提示补齐即可。

$ python scripts/doctor.py --days 7
TG 体检 · 2026-10-23 16:23（回看 7 天）
  预检      待确认（13 项待裁决，明细见 preflight）
  体积      150.2 KB / 174 个工具（预算 160 KB）
  缓存      命中率 98.8% | 未命中 2630/步 | 每步上下文 228k | k=50.0 | 未命中占成本 37%
  任务成本  中位 ¥0.06 / P90 ¥0.2204 / 共 ¥47.5762（505 轮）
  失败      159 轮有失败调用，共 270 次
动作（4 条，按收益排序）
  [1] 低  schema 接近预算上限 …（每条都给命令与预估）
```

`preflight` 的判定分三档：**放行 0 / 待确认 1 / 拒绝 2**。冲突项逐条列出，包括同名技能、非空记忆根、已存在的补丁条目、覆盖块漂移与插件名漂移。

## 安装

依赖分级（缺必需项时脚本一律给一句可执行的修复提示，不会抛 traceback）：

| 级别 | 依赖 | 用来干什么 | 缺了会怎样 | 安装 |
|---|---|---|---|---|
| 必需 | Python ≥ 3.9 | 所有脚本 | 跑不起来 | 装 3.9+（3.9 / 3.11 / 3.14 验过） |
| 必需 | `pyyaml` | 读 `config.yaml` | 所有读配置的脚本拒绝运行（这是刻意的，见 `docs/operations.md` 第四节） | `pip install pyyaml` |
| 必需（二选一） | `zstandard` 模块 或 `zstd` 命令 | 解压 `session.v4.jsonl.zstd` | 看账类脚本报「解压需要二选一」 | 首选 `pip install zstandard`（跨平台、CI 同款）；也可装系统包：macOS `brew install zstd`、Debian/Ubuntu `apt install zstd`。Windows 不保证自带 `zstd.exe` |
| 可选 | Node 18+ | `verify_guard.mjs` 守卫自检 | 只少这一项自检 | `node --version` 能跑即可 |

一条命令核对：

```bash
python scripts/env_check.py            # 必需/可选/信息三档，缺项给修复命令；必需项缺失时退出码 1
python scripts/env_check.py --json     # 给安装脚本或 CI 读
```

```bash
# 1) 先自动找 DSH 数据目录。别猜 ~/.dsh：桌面版 home 由安装位置决定，可以是任意盘任意目录
python scripts/detect_home.py                     # 输出可直接粘贴的 dsh_home 片段
cp config.example.yaml config.yaml                # 2) 把上一步那行填进 dsh_home（memory_root / skills_dir 同看）
python scripts/env_check.py                       # 3) 依赖齐不齐（缺必需项时退出码 1）
cp -r skills/tg-* "$DSH_HOME/skills/"             # 4) 技能入目录，重启 DSH 后生效
python scripts/preflight.py --config config.yaml  # 5) 只读预检：放行 0 / 待确认 1 / 拒绝 2
```

> `python` 与 `python3`：Windows 上是 `python`，macOS / 多数 Linux 发行版只有 `python3`（`python` 可能不存在，
> 或是 Windows 商店的占位别名）。下面所有 `python scripts/...` 在那些机器上换成 `python3 scripts/...` 即可。

同一组命令在 **PowerShell** 里不能照抄：`$DSH_HOME` 是 bash 写法，PowerShell 里会展开成空串，
`cp -r ... "$DSH_HOME/skills/"` 就变成往当前盘根目录写；`cp -r` 也不是 bash 的递归复制。对应写法：

```powershell
# 把 detect_home.py 建议的路径填进来（这一步就是替代 bash 里的 $DSH_HOME）
$dshHome = "E:/apps/dsh"
Copy-Item -Recurse skills\tg-* "$dshHome\skills\"
Copy-Item config.example.yaml config.yaml
python scripts\preflight.py --config config.yaml
```

装好先正常用一天。账本由 `ledger.py` 从会话日志派生（`data/ledger.json`），先跑一次 `python scripts/ledger.py ingest`；空环境跑体检会提示「没有可读的 ledger」并退出码 1，属正常：

```bash
python scripts/cost_summary.py --config config.yaml --days 3
```

只要路由层也可以：复制 `skills/tg-router` 与 `scripts/` 就能跑，其余技能按需加。

### 只装技能（没有 `scripts/`）

从技能市场装、或手动只拷 `skills/tg-*` 时，`scripts/` 与 `docs/` 不在。`tg-router` 会先探测
（`ls scripts/tg_common.py` → `NO-SCRIPTS`），然后按**形态 B** 降级：只讲口径、不报具体金额，
并明确说「要跑数得补仓库」。这是刻意的——宁可少给结论，也不给一个没算过的数。

要跑数就补齐：把 `scripts/`、`docs/`、`config.example.yaml` 拷到技能根，或整体重装。

循环守卫单独安装：`python scripts/install_guard.py --home "$DSH_HOME" --apply`，默认 dry-run，改 `package.json` 前备份；卸载用同一脚本 `--uninstall`。

**升级**：客户端升级后组合配置会被重写，需重跑 `gen_preset_override.py --window <窗口 token>` 对齐覆盖块；`preflight` 会报出覆盖块与插件名漂移。

## 使用说明

| 你想做什么 | 先跑 | 看什么数 |
|---|---|---|
| 这周花了多少 | `cost_summary --days 7` | 日均 CNY、调用数、缓存命中率 |
| 钱花在哪个模块 | `measure_modules --session <id>` | 技能目录 / 工具 schema / 工具结果占比 |
| 一轮任务到底多贵 | `turn_cost --days 3` | 每轮中位、均值、P90、失败轮次 |
| 为什么总在重试 | `failure_analysis --protocol-only` | 协议错误占比、Top 报文 |
| 缓存还有没有空间 | `cache_audit --days 3` | 命中率、未命中/步、未命中成本占比 |
| 改完配置有没有用 | `baseline --write` → 隔天 `--compare` | 起始上下文、schema、目录的前后变化 |
| 升级后配置对不对 | `preflight --config` | 覆盖块漂移、插件名漂移 |

场景路由由 `tg-router` 负责；10 个技能各自写清「什么时候用、什么时候别用」，正文在 `skills/<名>/SKILL.md`。

## 组成

### 技能（10 个，仅 `tg-router` 常驻）

| 技能 | 管什么 | 常驻 |
|---|---|---|
| `tg-router` | 入口与场景路由 | 是 |
| `tg-cost` | 审计、归因、算收益 | 待机 |
| `tg-context` | 工具清单、技能目录、上下文 | 待机 |
| `tg-memory` | 会话内存、跨区交接 | 待机 |
| `tg-memlib` | 长期记忆：CORE 层、分级提炼 | 待机 |
| `tg-orchestrate` | 子代理分档与审验 | 待机 |
| `tg-gui` | 常规区与 GUI 区交接 | 待机 |
| `tg-isolate` | 高费低频工具隔离为独立 preset | 待机 |
| `tg-ops` | 补丁分层、备份、回滚、升级漂移 | 待机 |
| `tg-loop` | 循环守卫的处置流程 | 待机 |

### 脚本（26 个）

| 组 | 脚本 | 解决什么 |
|---|---|---|
| 体检 | `env_check` / `detect_home` / `doctor` / `schema_budget` | 换机器先查依赖与数据目录；自动探测 DSH home（别猜 `~/.dsh`）；报计价来源（价表 / 汇率 / 时区）；一条命令出结论与动作；工具 schema 按服务归因、卡预算 |
| 看账 | `ledger` / `cost_summary` / `cache_audit` / `compare_days` | 从会话日志重建账本（日/模型/会话）；花了多少、命中多少、按天趋势；**价表没覆盖的模型会计入 `pricing.unmatched` 并报「含估算」**，不再静默套默认价 |
| 拆账 | `measure_modules` / `turn_cost` / `failure_analysis` / `retry_patterns` / `skill_usage` | 钱花在哪个模块、哪一轮、哪类失败 |
| 改配置 | `preflight` / `gen_preset_override` / `patch_snapshot` / `install_guard` | 装前查冲突、升级后重生成、改坏能回滚 |
| 守质量 | `baseline` / `audit_pack` / `gen_metrics` / `check_yaml` / `new_session` / `guard_stats` | 首日基线、包自检、数字单源、会话卡、守卫命中统计 |
| 公共层 | `tg_common` / `verify_guard` | `dsh_home` 单一口径、UTF-8 输出、配置读取；守卫离线断言 |

### 插件（1 个）

`plugins/dsh-loop-guard/`：零依赖 ESM 插件，`tools/post-execute` 阶段判「工具名 + 参数哈希」严格相等，按阈值追加一条内部提醒；不阻断调用、不弹窗、不结束会话。每次真正发出提醒时往 `storages/loop-guard/events.jsonl` 追加一行（工具名、第几次、参数哈希与长度，**不含参数内容**），用 `guard_stats.py` 读。`verify_guard.mjs` 15 项离线断言，`install_guard.py` 幂等安装。

## 实现结构

```text
token-governance/
  README.md  README_EN.md  LICENSE  CHANGELOG.md  CONTRIBUTING.md
  config.example.yaml          # 三行必改，其余可选
  data/metrics.json            # 文档数字的唯一来源
  skills/                      # 10 个技能（9 个待机）
  scripts/                     # 26 个脚本：体检 / 看账 / 拆账 / 改配置 / 守质量 / 公共层
  plugins/dsh-loop-guard/      # 循环守卫插件源码（含命中日志）
  tests/                       # 152 项断言 + fixtures
  docs/                        # 6 篇：benchmark / principles / operations / concepts / adaptation / ledger-v2
  .github/workflows/ci.yml     # Windows 验收门禁 + Ubuntu/macOS 兼容矩阵（门禁）；Linux 探测不阻塞
```

> 需求澄清技能 `clarify-needs` 已于 v0.11.1 迁出，独立仓库：
> [dsh-clarify-tier](https://github.com/YokosWY/dsh-clarify-tier)（`skill/clarify-needs`）。

数据流：会话日志（`sessions/**/session.v4.jsonl.zstd`）→ `ledger.py` 派生账本（默认 `data/ledger.json`）→ 计量脚本 → 报告与文档表格。除 `ledger.py ingest` 写自己的账本外，其余脚本只读，不写用户的会话与账本。

## 验收情况

```bash
python scripts/env_check.py           # 环境：Python / pyyaml / 解压器 / node / dsh_home 可读性
python tests/first_run.py             # 首次上手：空 home 下按安装段跑 10 步，只判「不崩 + 退出码」
python scripts/audit_pack.py          # 结构 / 引用 / 旧词 / 数字一致性
python tests/run_tests.py             # 152 项断言；只需 pyyaml + 解压器，不用 pytest
python scripts/gen_metrics.py --check # 文档表格必须与 data/metrics.json 一致
python scripts/doctor.py              # 体检汇总：结论 + 2–4 条带预估的动作
```

都是只读，非零退出即失败。测试覆盖：解压回退（`zstd` / Python `zstandard` / 3.14 内建 `compression.zstd`）、账本 v2 派生与幂等、计价与手算一致、外来日志拒绝、换参与失败重试口径、协议错误单列、插件名漂移、首日基线可写可比、补丁快照回滚、schema 归因与预算档位、doctor 缺数据不崩、`dsh_home` 解析优先级与端到端（只给 config / 退回环境变量 / 开关可压）、14 个脚本 × 3 类配置故障注入（坏 YAML / 顶层非映射 / GBK）、缺依赖不抛 traceback、环境自检判定、BOM/空格/中文路径、10 项静态检查变异用例、文档数字一致、包自检。

`.github/workflows/ci.yml` 有三个门禁：Windows 验收 job、Ubuntu/macOS × Python 3.10/3.12 兼容矩阵（2026-10-24 首轮全绿后转正）、首次上手 job（空 home 跑 10 步）；另有 Linux 全量测试探测，不阻塞。

`doctor --apply` 只执行两件可回滚的事：拍补丁快照、记首日基线。它不改 `cordis.patch.yml`、不删技能、不动 preset——这些要人裁决。

### 案例：一台机器上的改前改后

下面是**一台机器、一个模型**的对照，不是对所有环境的承诺。方法与复现命令在 [`docs/benchmark.md`](docs/benchmark.md)；你也可以用 `baseline.py` 与 `cost_summary.py` 在自己的环境量一遍。

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

> **2026-10-25 复测**（MCP 隔离、技能增装之后，同一台机器）：工具 142 个 / schema 122.7 kB（较上表「改后」再降 20%）；技能目录单条 12.4 kB（技能装多了，单条反升 29%）；本会话起始上下文 44.7 k；近 7 天未命中 2,101/步、命中率 99.1%。每轮成本随任务构成波动（近 3 天中位 ¥0.0753，含开发调试），不与上表直接比。自查用 `python scripts/doctor.py --days 7`。

同一会话代回旧参数重放，每轮再降 30–39%；这是下限——反事实只替换 schema 与目录体积，没有模拟旧配置下裁剪与压缩造成的前缀重算。

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

schema_budget_kb: 160          # 工具 schema 预算；超了 preflight / doctor 会报出来，0 = 关闭

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

`dsh_home` 的解析只有一处实现（`scripts/tg_common.py`），优先级固定：

```text
--dsh-home / --home  >  config.yaml 的 dsh_homes / dsh_home  >  $DSH_HOME  >  ~/.dsh
```

机器上装了两套客户端时（官方版写 `~/.dsh`、发行版写自己的 home），这条优先级决定脚本读哪一份会话与账本；用 `--dsh-home` 可以临时压过配置。

## 已知边界

- **跨客户端不保证**：Claude Code、Codex、Cursor 没有 preset 机制，高费工具隔离得改走子代理，见 `docs/adaptation.md`。
- **脚本只认 DSH**：`measure_modules.py` 读 DSH v4 会话日志，`cost_summary.py` 读 `ledger.py` 派生的账本；换客户端只有方法能搬。
- **补丁不随包**：工具裁剪、pruner、压缩阈值在各机器自己的 `cordis.patch.yml`，复制技能不会降低账单。
- **覆盖块是快照**：组合配置每次启动重写，升级后要重跑 `gen_preset_override.py`。
- **不看账本不下结论**：schema 变小、命中率上升都只是过程量。
- **压缩不是杠杆**：低水位只会增加前缀重算次数；只有接近窗口上限时才值得触发。
- **不接管安全与需求澄清**：密钥、脱敏、上传门禁属本地约束；需求澄清已独立成 [dsh-clarify-tier](https://github.com/YokosWY/dsh-clarify-tier)。

## 安全与隐私

- 脚本只读用户的会话日志与账本，不上传任何数据；报告里的路径按原样输出，贴到公开场合前请自行检查。
- 循环守卫默认 `argumentsPreviewChars: 0`：提醒只报参数长度，不写参数内容；参数含凭据的工具建议加入 `exclude`。
- 仓库不含任何凭据；`config.yaml` 已 gitignore；`_local/` 用于本机私有清单。

## 开发

```bash
python tests/run_tests.py              # 改完先跑这一步
python scripts/audit_pack.py           # 提交前自查
python scripts/gen_metrics.py --write  # 改过 data/metrics.json 后同步文档
```

提交规范见 [`CONTRIBUTING.md`](CONTRIBUTING.md)：`type: 一句话`，陈述式，一条提交只做一件事；数字必须标口径，未实测的收益写「未验证」。

## 文档

- [`docs/getting-started.md`](docs/getting-started.md)：新用户上手：四步装好、三条日常命令、技能路由、改配置与回滚、常见问题
- [`docs/benchmark.md`](docs/benchmark.md)：实测方法与逐会话数字；第十节是缓存命中（provider 无关）
- [`docs/principles.md`](docs/principles.md)：十三条原理与阈值推导
- [`docs/operations.md`](docs/operations.md)：循环守卫与协议纪律
- [`docs/concepts.md`](docs/concepts.md)：自造词解释 + 六个机制
- [`docs/adaptation.md`](docs/adaptation.md)：换机器、换客户端、没有账本时怎么降级
- [`docs/ledger-v2.md`](docs/ledger-v2.md)：账本 v2 schema、计价口径、峰谷为什么默认关、与旧账本的逐日校验

## 版本

| 版本 | 内容 |
|---|---|
| v0.14.0 | 账本 v2：`ledger.py` 从会话日志派生日/模型/会话三级成本（幂等 + 文件级缓存），替代已卸载的 `dsh-cost-meter`；`tg_common` 补 3.14 内建 `compression.zstd` 与 `ledger_paths`；下游 `cost_summary`/`cache_audit`/`env_check` 兼容新旧账本；测试 132 项 |
| v0.13.1 | 首次上手成门禁（`tests/first_run.py` + CI job）；`install_guard --config`；测试 126 项 |
| v0.13.0 | `env_check` 环境自检；缺依赖不抛 traceback；`check_yaml`/`new_session` 错误处理；依赖分级写进 README；`doctor` 空数据给 first-run 并支持 `--dsh-home`；CI 修复 + 跨平台矩阵；测试 126 项 |
| v0.12.0 | 抽 `tg_common.py` 统一 `dsh_home` 解析与配置读取；配置读不出改为报错；`compare_days` 优先级翻转并补 `--dsh-home`；测试 98 项 |
| v0.11.4 | 守卫顽固循环率；协议规则落在常驻层 |
| v0.11.2 | 守卫命中计数 + 协议错误硬检查；siyuan 整块隔离；测试 32 项 |
| v0.11.1 | 需求澄清迁出为独立仓库 |
| v0.11.0 | `doctor` 单入口 + `schema_budget` 归因与预算；CI；测试 29 项 |
| v0.10.19 | README 按公开框架整改；补英文版、实现结构、安全与隐私、验收 |
| v0.10.18 | 对外口径收口，移除本机工作流统计 |
| v0.10.16 | 文档 11 → 5 篇；协议纪律；数字单源 |
| v0.10.13 | 任务成本、技能埋点、补丁回滚 |
| v0.10.12 | 23 项回归测试；文档数字单源 |
| v0.8–v0.10 | 循环守卫、`tg-isolate`、`tg-memlib`、v0.10 复测 |

完整记录见 [`CHANGELOG.md`](CHANGELOG.md)。

## 许可

MIT © 2026。见 [`LICENSE`](LICENSE)。

本项目为独立工具，与任何模型提供方无隶属、认可或赞助关系。案例数字来自单机实测，不构成对你环境的收益承诺。
