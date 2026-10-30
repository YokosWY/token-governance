---
name: tg-loop
description: "TG 循环守卫。工具级认同工具同参数重复；思考级认读海/连败/步数预算（雷霆大思考）。命中按固定三处置：假设/已排除/阻塞点、派子代理复核、问用户三选一。含调参、安装与验证。"
---

# tg-loop · 循环守卫

运行时的防重复层。策略文本在 AGENTS「反重复与止损」，这里是它的插件实现与操作入口。

## 它做什么、不做什么

| 做 | 不做 |
|---|---|
| 同一个工具 + **完全相同参数**连续重复到第 2/4/6 次时，往上下文追加一条提醒 | 不阻断调用、不弹窗、不结束会话 |
| 提醒里带该工具专属的「换哪条路」 | 不判断内容对错，只认参数哈希 |
| 到 `askUserAt` 次时允许问用户一次 | 不自动派子代理（要不要派由模型判断） |
| 新用户消息到达时清链 | 不跨会话记忆 |
| 思考级三条代理规则（读海 / 连败 / 步数预算，默认开） | 看不到 reasoning，只能看工具与结果 |

## 触发条件（严格）

- 键 = `工具名 + stable(参数) 的 sha1 前 16 位`；**改一个字符就不算重复**。
- 参数解析不到（空对象 / 非 JSON 字符串）→ 不计数。
- 排除工具：`todo_write`、`job_output`、`job_list`、`job_kill`、`ask_user_question`、`send_message`、`present`。
- 第 2、4、6 次各提醒一次；同一条链里同一个次数只提醒一次。

## 命中后按三处置（与 AGENTS 一致）

1. 三行写完：**当前假设 / 已排除 / 唯一阻塞点**；
2. 派一个 `reasoning_effort=max` 的子代理复核最近 10 步，问它「是否在原地打转、下一步唯一动作」；
3. 用 `ask_user_question` 给三选一：继续 / 换方法 / 只停这个子任务。

第 6 次之后仍重复，默认视为「方法错了」，换通道（bash ↔ pwsh、内置 ↔ 插件、读文件 ↔ grep 定位），不要换参数重试。

失败后重试前先分类：协议错误（`file has not been read` / `file changed since it was read` / `unknown tool`）不重试逻辑，先补 `read`、改成串行、换正确工具名——见 `docs/operations.md` 第二节。这类错误重试不解决问题，只会让整轮上下文重发。

## 思考级空转（雷霆大思考）

工具级重复只抓「同参数原地打转」。更常见的浪费是**一直在读、一直在想，就是不落物**——插件看不到 reasoning，只能看它调了什么、结果成没成、有没有 write/edit。三条代理规则（v0.2 起，随包默认开、阈值保守）：

| 规则 | 触发 | 提醒口径 |
|---|---|---|
| `read-storm` | 连续 12 次读取/检索、0 次 write/edit（且本轮读 ≥8 次） | 够了就动手：写文件 / 跑定论命令 / 直接答 |
| `error-streak` | 连续 3 次调用失败 | 先分类错误，再换通道；不换参数硬试 |
| `budget` | 单轮 ≥60 次调用且 0 次 write/edit | 按三行止损收口，别把同一思路加长 |

每轮每规则最多响一次，两次提醒之间至少隔 `cooldownCalls: 8` 次调用。嫌吵就把 `thinking.enabled` 改 `false`，或按 `guard_stats.py --by-kind` 的命中率调大阈值。

**自检三问（每步，不依赖插件）**：这步产出什么物（文件/结论/命令）？判据是什么（想完怎么算想完）？再想一步会改变什么（不会 → 停）。

**三行止损模板**：假设=…；已排除=…；唯一阻塞点=…。写完再选继续 / 换方法 / 问用户（同上面的三处置）。

**别窄化「落物」**：只认 write/edit 会逼模型为了摆脱提醒乱写文件；提醒文案留了「直说在查什么、还差哪条」这条正常出路。



协议错误不是判断失误，是顺序失误，所以能做成检查而不是提醒。每次 `edit` / `write` 之前逐条过：

- [ ] 目标文件**本会话** `read` 过？没读过就先 `read`（大文件用 `offset` / `limit` 只读目标段）。
- [ ] 这个文件此刻**只有我在改**？并行改同一文件，后一个必报 `file changed since it was read`。
- [ ] 工具名在该会话的工具清单里？不臆造（`bash` / `opt_bash` 这类会报 `unknown tool`）。

命中任一报错 → **停手，不重试**：

| 报错 | 唯一正确动作 |
|---|---|
| `file has not been read` | `read` 目标段后再 edit |
| `file changed since it was read` | 重读该文件，把改动 rebase 到新内容上再 edit |
| `unknown tool` | 查工具清单换成正确名字，不猜第二个名字 |

自检（连续两天为 0 才算做实）：

```bash
python scripts/failure_analysis.py --days 3 --protocol-only
```

**位置提醒**：本节同时以一句话写进宿主 `AGENTS.md` §5（常驻，模型每步可见）。本技能是待机状态，
只写在这里等于没写——这条规则的价值全在「不加载技能时也生效」，改它要改两处或只改常驻那处。

## 命中统计（提醒到底有没有用）

守卫只提醒不拦截，所以必须能回答「提醒了几次、之后有没有改」。每次真正追加提醒时，插件往
`<DSH_HOME>/storages/loop-guard/events.jsonl` 写一行：时间、工具名、第几次、参数哈希与长度——
**不写参数内容**（防凭据进日志）。

```bash
python scripts/guard_stats.py --days 7          # 提醒次数、按工具与按日
python scripts/guard_stats.py --by-kind         # 按类看：loop / read-storm / error-streak / budget
python scripts/guard_stats.py --json            # 给别的脚本用
```

读法：某工具反复出现在第 2 次 → 阈值有效、但在反复踩同一条路；同一工具占到第 6 次 → 提醒没改变行为，
该换通道而不是继续提醒。日志文件不存在时退出码 2，先确认装的是带统计的新版（`install_guard.py --status`）。

## 调参

改 profile 里该插件的 config（`profiles/<name>/cordis.yml` 的 `loop-guard` 条目，或插件自带的 `cordis.patch.yml`）：

```yaml
config:
  enabled: true
  thresholds: [2, 4, 6]      # 提醒点；调低会更吵，调高会更晚
  askUserAt: 6               # 到第几次才允许问用户
  argumentsPreviewChars: 160 # 提醒里带的参数预览长度
  exclude: [todo_write, job_output, job_list, job_kill, ask_user_question, send_message, present]
  include: []                # 非空时只对这些工具生效
  logFile: ''                # 命中日志：空 = <DSH_HOME>/storages/loop-guard/events.jsonl；null 关闭
  thinking:                  # 思考级空转（v0.2），随包默认开、阈值保守
    enabled: true
    readStormCalls: 12       # 连续读/检索到 12 次仍无落物 → 提醒
    readStormMinReads: 8     # 且本轮读/检索总数 ≥8
    errorStreak: 3           # 连续 3 次失败 → 提醒
    turnCallBudget: 60       # 单轮 ≥60 次调用且 0 落物 → 提醒
    cooldownCalls: 8         # 两条思考提醒之间至少隔 8 次调用
```

判断标准：提醒太频繁 → 阈值上移或把高频无害工具加进 `exclude`；一直不触发 → 检查参数是否每次都不同（很多"循环"其实是换参数重试，那种靠 AGENTS 的两次法则管）。

## 装与验

```bash
python scripts\install_guard.py --dry-run   # 看会改什么
python scripts\install_guard.py --apply     # 装到当前 DSH home
python scripts\install_guard.py --status    # 查是否已装
node   scripts\verify_guard.mjs             # 离线烟雾测试
```

`--apply` 之前会备份 `profiles/<name>/package.json`；卸载用 `--uninstall`（只摘依赖与 bundle 项，不删插件目录）。

## 边界

- 只覆盖「工具调用级」重复；模型在同一段话里换个说法重复推理，它看不到。
- 思考级三条规则是**代理指标**：插件看不到 reasoning，只认「读了多少、败了几次、有没有 write/edit」。真正的「想太久」只能靠 AGENTS 常驻一行 + 档位默认值。
- 参数里带时间戳、随机 id 的调用永远不会被判重复（设计如此）。
- 提醒是追加消息，会占一点上下文；阈值别低于 2。
