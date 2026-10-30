# dsh-loop-guard · 循环守卫

同一工具 + **完全相同参数**（参数哈希相等）连续重复到阈值时，向模型上下文追加一条内部提醒，提示「换哪条路」。
到 `askUserAt` 次才允许问用户一次。

v0.2 起加一层**思考级空转**（随包默认开、阈值保守）：插件看不到 reasoning，但能看「读了多少、败了几次、有没有 write/edit」，用三条代理规则兜住「一直在读、一直在想，就是不落物」。

| 项 | 值 |
|---|---|
| 类型 | DSH 本地插件（`dsh.bundle.patch`，零依赖，ESM） |
| 事件点 | `tools/post-execute`（只有这里能拿到解析后的参数）、`agent/pre-step`（新用户消息清链） |
| 观测方式 | 参数稳定序列化后取 sha1，前 16 位；同一 agent 维护一条链 |
| 默认阈值 | 第 2 / 4 / 6 次重复各提醒一次（不阻断） |
| 默认排除 | `todo_write`、`job_*`、`ask_user_question`、`send_message`、`present` |
| 思考级 | `read-storm` 连续 12 读 0 落物 / `error-streak` 连败 3 次 / `budget` 单轮 60 次 0 落物；随包开 |
| 命中日志 | `<DSH_HOME>/storages/loop-guard/events.jsonl`，每行一条；只记工具名、第几次、参数哈希与长度 |
| 失败策略 | 任何异常 fail-open（不提醒、不影响调用、不因写日志失败中断） |

## 思考级空转（v0.2）

| 规则 | 触发 | 提醒口径 |
|---|---|---|
| `read-storm` | 连续读/检索 ≥12 次、0 次 write/edit（本轮读 ≥8） | 够了就动手：写 / 跑定论命令 / 直接答 |
| `error-streak` | 连续 3 次调用失败 | 先分类错误，再换通道 |
| `budget` | 单轮 ≥60 次调用且 0 次 write/edit | 三行止损收口 |

每轮每规则最多一次，两次提醒之间至少隔 8 次调用。代码默认与随包 `cordis.patch.yml` 都是 `thinking.enabled: true`；怕吵就改 `false` 或调大阈值。

## 命中统计

日志是 append-only，一链只在 2 / 4 / 6 次各写一行，所以「行数 = 真正追加过的提醒数」。
读它用：

```bash
python scripts/guard_stats.py --days 7     # 次数 / 按工具 / 按日
python scripts/guard_stats.py --by-kind    # loop / read-storm / error-streak / budget
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
- 思考级三条规则是代理指标，不是 reasoning 计量；它只回答「有没有落物/是否连败」。
- 提醒是追加消息，不破 KV 缓存前缀；但会占用一点上下文，阈值别调太低。
- 它不替代 AGENTS 里的「空转止损」，两者是同一策略的运行时与文本两个面。

## 来源

2026-10-22 先在本机 `<DSH_HOME>\local-plugins\dsh-loop-guard` 落地，同日收编进 TG（代码按字节拷贝，`scripts/verify_guard.mjs` 会校验哈希一致）。
2026-10-29 v0.2 加思考级空转（`read-storm` / `error-streak` / `budget`），默认**开**（`thinking.enabled: false` 关）。
