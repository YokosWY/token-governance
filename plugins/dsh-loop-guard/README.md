# dsh-loop-guard · 循环守卫

同一工具 + **完全相同参数**（参数哈希相等）连续重复到阈值时，向模型上下文追加一条内部提醒，提示「换哪条路」。
到 `askUserAt` 次才允许问用户一次。

| 项 | 值 |
|---|---|
| 类型 | DSH 本地插件（`dsh.bundle.patch`，零依赖，ESM） |
| 事件点 | `tools/post-execute`（只有这里能拿到解析后的参数）、`agent/pre-step`（新用户消息清链） |
| 观测方式 | 参数稳定序列化后取 sha1，前 16 位；同一 agent 维护一条链 |
| 默认阈值 | 第 2 / 4 / 6 次重复各提醒一次（不阻断） |
| 默认排除 | `todo_write`、`job_*`、`ask_user_question`、`send_message`、`present` |
| 命中日志 | `<DSH_HOME>/storages/loop-guard/events.jsonl`，每行一条；只记工具名、第几次、参数哈希与长度 |
| 失败策略 | 任何异常 fail-open（不提醒、不影响调用、不因写日志失败中断） |

## 命中统计

日志是 append-only，一链只在 2 / 4 / 6 次各写一行，所以「行数 = 真正追加过的提醒数」。
读它用：

```bash
python scripts/guard_stats.py --days 7     # 次数 / 按工具 / 按日
```

配置 `logFile: null` 可关闭写日志；`logFile: "<绝对路径>"` 可改落点。
写日志全程 try/catch，磁盘满、目录不可写都不影响工具调用。

## 装到本机

```bash
# 把本目录链接/复制到 DSH home，并按 README 在 profile 里登记
python scripts\install_guard.py --dry-run
python scripts\install_guard.py --apply
```

## 已知边界

- 只认「完全相同参数」。同一命令改一个字符就不会命中，这是有意的：宁可漏报，不可误伤。
- 参数解析不到（如空对象）时不参与计数。
- 提醒是追加消息，不破 KV 缓存前缀；但会占用一点上下文，阈值别调太低。
- 它不替代 AGENTS 里的「反重复与止损」，两者是同一策略的运行时与文本两个面。

## 来源

2026-10-22 先在本机 `<DSH_HOME>\local-plugins\dsh-loop-guard` 落地，同日收编进 TG（代码按字节拷贝，`scripts/verify_guard.mjs` 会校验哈希一致）。
