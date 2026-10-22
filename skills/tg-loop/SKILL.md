---
name: tg-loop
description: "TG 循环守卫。同一工具 + 相同参数重复到阈值时按固定三处置止损：写清假设/已排除/阻塞点、派子代理复核、问用户三选一。含调参、安装与验证。"
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

失败后重试前先分类：协议错误（`file has not been read` / `file changed since it was read` / `unknown tool`）不重试逻辑，先补 `read`、改成串行、换正确工具名——见 `docs/operations.md` 第二节。协议错误占失败调用 27.5%；有失败的轮次合计占窗口花费 48.9%。

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
- 参数里带时间戳、随机 id 的调用永远不会被判重复（设计如此）。
- 提醒是追加消息，会占一点上下文；阈值别低于 2。
