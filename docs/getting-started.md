# 上手手册：从装好到日常

> 面向第一次用 TG 的人。装之前先看 [README 的「安装」](../README.md#安装)；本文从「装完之后」开始。
> 命令默认在仓库根目录执行。Windows 用 `python`，macOS / 多数 Linux 用 `python3`。

## 0. 先花一分钟判断要不要用

**用得上**，如果你：

- 用的是 DSH（桌面版或发行版）这类会把**每一步**的 usage（输入 / 缓存读 / 输出）写进会话日志的客户端；
- 觉得「会话越用越贵」，但说不清贵在系统提示、工具清单还是工具结果；
- 愿意改一次配置，然后观察一天。

**用不上**，如果：调的是裸 API（厂商账单更准）、客户端不落逐 step 日志、或没权限改配置。
本包不联网、不上传、不需要 token；脚本只读本地日志。唯一的写操作是 `install_guard.py` 往 DSH 装循环守卫、`patch_snapshot.py` 存快照。

## 1. 装完先跑五步

| 步 | 命令 | 正常长什么样 | 不对怎么办 |
|---|---|---|---|
| 1 依赖 | `python scripts/env_check.py` | 必需项全绿，退出码 0 | 按提示装 `pyyaml` / `zstandard`（或系统 `zstd`） |
| 2 数据目录 | `python scripts/detect_home.py` | 打印一行可粘贴的 `dsh_home:` 片段 | 多套 DSH 时逐个试，选 `sessions/` 里日志最多的那个 |
| 3 配置 | `cp config.example.yaml config.yaml`，填 `dsh_home` | 文件里有 `dsh_home` / `memory_root` / `skills_dir` | 别猜 `~/.dsh`：桌面版 home 由安装位置决定，可能在任意盘 |
| 4 对话夹 | `python scripts/setup.py` | 问一句「放在哪」，回车用默认 `~/Documents/tg-memory`，建好并写回 `memory_root` | 无人值守给 `--memory-root <路径>` 或 `--yes`；不配也不会静默落到 `~/tg-memory` |
| 5 预检 | `python scripts/preflight.py --config config.yaml` | 「放行（退出码 0）」 | `1` = 待确认，逐条看冲突项；`2` = 拒绝，先修再继续 |

> PowerShell 不能照抄 bash：`$DSH_HOME` 在 PowerShell 里展开成空串，`cp -r ... "$DSH_HOME/skills/"` 会写进盘根。
> 用 `$dshHome = "E:/apps/dsh"` 显式赋值，复制用 `Copy-Item -Recurse`。

## 2. 第一天：三条命令就够

### doctor —— 一页纸看完现状

```bash
python scripts/doctor.py --days 7
```

输出五块，先看两块：

| 块 | 看什么 | 不对劲的信号 |
|---|---|---|
| 体积 | `schema KB / 工具数个`，对比 `schema_budget_kb` | 余量 < 20 KB，或刚装完插件就涨 20–50 KB |
| 缓存 | 命中率、`未命中/步`、未命中占成本 | 未命中占成本 > 30% 且命中率 < 99% |
| 任务成本 | 每轮中位 / P90 | P90 是中位的 3 倍以上（长尾任务） |
| 失败 | 有失败调用的轮次占比 | > 30% 先查协议错误，不是重试 |
| 预检 | 待确认条数 | 有「覆盖块漂移」「插件名漂移」先去修 |

### cost_summary —— 这周花了多少

```bash
python scripts/ledger.py ingest                 # 首次或日志变了：先建账本（data/ledger.json）
python scripts/cost_summary.py --days 7
```

看四个数：**合计 CNY**、**调用数**、**缓存命中率**、**CNY/次**。
第一次跑提示「没有可读的 ledger」并退出码 1 是正常的——先跑一次 `ledger.py ingest`。

### turn_cost —— 一轮任务到底多贵

```bash
python scripts/turn_cost.py --days 3
```

一条用户指令 = 一轮。看 **中位**（典型任务）和 **P90**（长尾）。
不要用「每步成本」判断省钱：步数由任务决定，轮次才是人能感知的单位。

## 3. 第三天：钱到底花在哪

| 问题 | 命令 | 看什么 |
|---|---|---|
| 哪个模块吃钱 | `measure_modules.py --session <id>`（`--list` 先找会话） | 工具 schema / 工具结果 / 技能目录 / 助手输出的占比 |
| 缓存还有空间吗 | `cache_audit.py --days 3` | 未命中/步、未命中成本占比、最近哪些操作在打穿前缀 |
| 工具清单会不会爆 | `schema_budget.py` | 按服务分组的 KB 与占比、单工具 Top |
| 为什么总在重试 | `failure_analysis.py --protocol-only` | 协议错误（没读就改 / 并发改同一文件）占比 |

## 4. 技能怎么用

10 个技能里**只有 `tg-router` 常驻**，其余标了 `disable-model-invocation`：模型侧看不见，不占每步固定开销，需要时才读。

你不用记名字，说对触发词就行：

| 你说 | 它会读 |
|---|---|
| 花太多钱 / 算账 / 收益 | `tg-cost` |
| 会话变慢变贵 / 瘦身 | `tg-context` |
| 跨天续跑 / 交接 | `tg-memory` |
| 记忆库整理 / 提炼 | `tg-memlib` |
| 难活 / 派子代理 / 质量兜底 | `tg-orchestrate` |
| 点界面 / 截图判断 | `tg-gui` |
| 某类工具又贵又少用 | `tg-isolate` |
| 改配置 / 升级 / 回滚 | `tg-ops` |
| 重复动作被点名 | `tg-loop` |

想手动点名：`read skills/tg-cost/SKILL.md`（或客户端里输入 `/tg-cost`）。装载后**重启 DSH** 才生效。

## 5. 改配置的标准动作

```bash
python scripts/baseline.py --write                          # 1) 先记基线（默认 tg-baseline.json）
python scripts/preflight.py --config config.yaml            # 2) 改前查冲突
python scripts/patch_snapshot.py --snapshot                 # 3) 存快照
#    ……改配置……
python scripts/baseline.py --compare                        # 4) 隔一批新会话再比
python scripts/patch_snapshot.py --restore latest           # 不行就回滚（回滚前会自动备份当前文件）
```

`baseline --compare` 看三项：起始上下文、schema 体积、技能目录单条。这三项是每步都重发的固定开销，变化最干净。

## 6. 常见问题

| 症状 | 原因 | 怎么办 |
|---|---|---|
| 「没有可读的 ledger」退出码 1 | 账本还没建 | 先 `python scripts/ledger.py ingest` |
| 「解压需要二选一」 | 缺 `zstandard` 与 `zstd` | `pip install zstandard`，或装系统 `zstd` |
| `python` 打不开或跳商店 | Windows 应用别名占位 | 用 `py -3` 或直接指定解释器路径 |
| PowerShell 里复制到盘根 | `$DSH_HOME` 展开为空 | 显式 `$dshHome = "..."` + `Copy-Item -Recurse` |
| 技能装了没反应 | 没重启 / 目录不对 | 确认 `skills_dir` 下有 `tg-router/SKILL.md`，重启 DSH |
| 金额带「含估算」 | 价表没覆盖某个模型 | 在 `prices_usd_per_mtok` 补该模型；账本会记 `pricing.unmatched` |
| doctor 与 cost_summary 对不上 | 窗口不同 / 一个按实付一个按折算 | 先用同一 `--days`；账本有实付时以实付为准 |
| 工具数忽多忽少 | MCP 隔离、preset 切换 | 用 `schema_budget.py` 按服务看，别只看总数 |
| 改完配置数字没变 | 旧会话的前缀已固化 | 新开会话再看；历史会话不会回溯改写 |

## 7. 一页速查

| 频率 | 命令 | 回答什么 |
|---|---|---|
| 每天一次 | `doctor.py --days 7` | 现在有没有异常、下一步做什么 |
| 每周一次 | `cost_summary.py --days 7` | 这周多少钱、命中率如何 |
| 想省钱时 | `measure_modules.py --session <id>` | 哪个模块最值得动 |
| 改配置前 | `preflight.py` + `patch_snapshot.py --snapshot` | 会不会冲突、改坏能否回滚 |
| 改配置后 | `baseline.py --compare` | 三项固定开销有没有降 |
| 出现重复调用 | `tg-loop` / `guard_stats.py` | 循环守卫命中了什么 |

## 8. 接着读

| 文档 | 什么时候读 |
|---|---|
| [README](../README.md) | 安装、配置键、技能与脚本清单 |
| [concepts.md](concepts.md) | 「按存活计息」「静态前缀」这些词到底指什么 |
| [principles.md](principles.md) | 十三条原理，每条带反面案例与验证方法 |
| [benchmark.md](benchmark.md) | 一台机器上改前改后的实测数字 |
| [operations.md](operations.md) | 循环守卫与操作纪律 |
| [ledger-v2.md](ledger-v2.md) | 账本字段与口径 |
| [adaptation.md](adaptation.md) | 没有账本 / 没有 scripts / 换客户端怎么降级 |
