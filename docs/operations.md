# 运行时防线与操作纪律

> 循环守卫（防重复）+ 协议纪律（防操作错误）。原 `guard.md` 与 `protocol.md` 合并到此。

## 一、循环守卫

### 1. 问题

长会话里最常见的浪费不是模型太笨，是**同一个动作被反复做**：同一条命令重跑、同一个文件整读第二遍、同一组参数换个写法再试。
表现是步数上涨、缓存命中下降、上下文变长，最后模型自己也说不清在验什么。

### 2. 设计

- **只观测，不拦截**。拦截需要 pre-execute 参数，而那一层拿不到解析后的参数（2026-10-22 实测），硬拦必然误伤。
- **判据是参数哈希**，不是语义相似。`stable()` 对对象键排序后序列化，再取 `sha1(tool|args)` 前 16 位。
  严格相等意味着：改一个字符就不算重复——宁可漏报，不可误伤。
- **提醒是追加消息**（`role: 'user'`，`source.kind = 'loop-guard'`，`form: 'notice'`），append-only，不破 KV 缓存前缀。
- **链按 agent 维护**，新用户消息（`source.kind === 'user'`）到达时清空，避免把新任务的正常重复算进旧链。
- **fail-open**：任何异常都不提醒，绝不影响正常调用。

### 3. 事件点

| 事件 | 用途 | 为什么是这里 |
|---|---|---|
| `tools/post-execute` | 取参数、计数、产出提醒 | 只有这一阶段能拿到 `exec.arguments` 的解析结果 |
| `agent/pre-step` | 新用户消息 → 清链 | 用户换了任务，旧链失效 |

`tools/post-execute` 拿到 `next()` 之后把提醒塞进 `additionalContexts`：

```js
return { ...downstream, additionalContexts: [reminder, ...existing] }
```

### 4. 阈值语义

`thresholds: [2, 4, 6]` = 第 2、4、6 次各提醒一次；`askUserAt: 6` = 到第 6 次才允许问用户。
设计意图：第 2 次是「你可能没注意」；第 4 次是「这条路不通」；第 6 次是「该问人了」。
每条链对每个阈值只提醒一次（`fired` 集合去重）。

### 5. 与 AGENTS 规则的分工

| 层 | 载体 | 何时生效 |
|---|---|---|
| 文本策略 | `AGENTS.md` §反重复与止损 | 每次推理都在上下文里，靠模型自觉 |
| 运行时 | 本插件 | 命中才追加，强制看见 |

两者不是替代关系：文本管「怎么想」，插件管「想漏了怎么办」。
插件触发的三处置（三行假设、派 max 子代理、问用户三选一）写在 `skills/tg-loop/SKILL.md`。

### 6. 已知限制

1. 只认完全相同参数；「换参数重试」这类循环它看不见，靠两次法则。
2. 参数含时间戳/随机 id 的调用永不命中。
3. 同一工具 + 相同参数但**目的不同**（例如同样读一个文件做两次不同检查）也会命中——提醒里已给出「若确实需要，请说明为什么重跑」的余地。
4. 追加提醒占上下文；阈值低于 2 会明显变吵。
5. 与其它「重复工具提醒」类插件（例如 `dsh-repeat-tool-reminder`）同时安装会重复提醒；只保留一个。
6. 提醒消息里会带参数预览，默认 `argumentsPreviewChars: 0`（只报长度）。调大就会把参数前 N 个字符写进上下文；带 `Authorization` / token 的工具别调大，或加进 `exclude`。
7. 哈希算的是整个参数对象，`description` 这类元字段也算：命令一模一样、只是描述换了一句，就不算重复。实测（2026-10-22）：两条 `echo probe` 描述不同不触发；连描述一起相同才在第二次触发。

### 7. 验证

`scripts/verify_guard.mjs` 用假 ctx 跑六组断言：第 2 次触发、第 4 次触发、同阈值不重复、参数不同不触发、exclude 生效、新用户消息清链。
`scripts/install_guard.py` 负责把 `plugins/dsh-loop-guard/` 装进 DSH home 并登记到 profile（幂等，带备份与卸载）。

### 8. 版本

- 0.1.0（2026-10-22）：本机 `local-plugins` 落地，阈值 2/4/6。
- 0.1.1（2026-10-22）：收编进 TG；`package.json` 描述改为准确表述（只提醒、不阻断）；补离线烟雾测试与安装脚本。

## 二、协议纪律

代理调用里最常见的失败不是配置问题，是操作顺序问题：没读就改、并行改同一文件、工具名写错。
这类错误重试也救不回来，只能从顺序上避免。压前缀同样救不了——一次失败会让整轮上下文重发。

### 三条硬规则

1. **改前必读。** 本会话没 `read` 过的文件不 `edit` / `write`；大文件用 `offset` / `limit` 只读目标段。
   命中报错：`file has not been read`。
2. **一次只改一个文件。** 同一文件被并行编辑会撞前缀校验，后一个必失败。
   命中报错：`file changed since it was read`。
3. **工具名先对照清单。** 不臆造工具（`bash`、`opt_bash` 这类），用清单里的 `ops_bash` / `pwsh` / `read` / `edit`。
   命中报错：`unknown tool`。

### 动手前清单

- [ ] 目标文件本会话读过？没读先 `read`。
- [ ] 这个文件和别的动作同时在改？串行。
- [ ] 工具名在工具清单里？
- [ ] 命令可能超时（>60 s）？拆批或后台跑。

### 自检

```bash
python scripts/failure_analysis.py --config config.yaml --days 3 --protocol-only
```

看三个数：协议错误次数、占失败调用比例、Top 报文。连续两天为 0 才算把这条规矩做实。

### 为什么要单列

失败轮次的花费不是"多试一次"的钱，而是整轮上下文重发的钱：一轮失败会把该轮的每步前缀再算一遍。
所以协议错误在账上按轮计，不按调用计——减少一次试错，省的是整轮。

## 三、体检入口

脚本一多，翻手册的成本会高于收益：十几个脚本各查一件事，实际一周只被调 1–4 次。
因此把只读采集合成一个入口：

```bash
python scripts/doctor.py --days 7          # 结论 + 2–4 条动作（每条带命令与预估）
python scripts/doctor.py --json            # 给 CI / 别的脚本
python scripts/doctor.py --apply           # 只拍补丁快照 + 记首日基线，可回滚
python scripts/doctor.py --print-actions   # 只演一遍 --apply 会做什么
```

`doctor` 不替代子脚本：它只做汇总与排序，细节仍回各自的脚本查。动作按「改了能立刻省」>「改了少犯错」
排序，四个来源：schema 体积、失败轮次占比、技能 0 调用、缓存判据。

### schema 预算与归因

工具 schema 每步重发，是「每步成本」里最大的固定块，也最容易反弹——新装一个 MCP 回来几十 KB，
隔离省下的空间会被悄悄吃回去。所以单列一条能卡预算的命令：

```bash
python scripts/schema_budget.py --top 12   # 按服务/族归因，给 KB 与占比
python scripts/schema_budget.py --check    # 超 config.yaml 的 schema_budget_kb 时退出码 1
```

`preflight.py` 与 `doctor.py` 都会带这一项的只读结论，不参与冲突计数、不改退出码。
字节取自 `request/header.tools`；token 换算按 0.4/字节粗估，只作量级——省的是每步重发量，与命中率分账。

### 重工具隔离的落地路径

MCP 工具走 connector 层，不进 `cordis.patch.yml`，所以不能像 GUI 那样做 preset。可用的三种力度：

| 力度 | 做法 | 何时用 |
|---|---|---|
| 连接级 | 停用整个 connector | 整块长期不用 |
| server 级 | `mcp_connector_policy` 对某个 server 设 `deny` | 高费低频（实测：siyuan 31 工具 / 26.6 KB，近 7 天只调 9 次） |
| 会话级 | `mcp_connector_session_tools` 按会话临时激活，默认 30 分钟 | 偶尔要用一次 |

server 级 deny 之后，需要时按会话激活，不必改回持久配置；`action=rollback` 可回滚到上一 revision。
工具是「一个工具带多个 action」（如 `block` 同时管读与写）时，工具粒度拆不开，只能整块隔离。

### 守卫命中的读法

`python scripts/guard_stats.py --days 7`：

- 同一工具反复出现在第 2 次 → 阈值有效，但仍在踩同一条路，该改方法而不是继续提醒。
- 同一工具占到第 6 次 → 提醒没改变行为，换通道（bash ↔ pwsh、读文件 ↔ grep 定位）。
- 日志不存在（退出码 2）→ 先确认装的是带统计的版本，或近几天根本没触发。

日志只记工具名、第几次、参数哈希与长度，不记参数内容；读它不会把凭据带进上下文。

## 四、数据目录口径

所有脚本读同一份会话日志与账本，前提是 `dsh_home` 只按一个顺序解析。这条顺序写在
`scripts/tg_common.py`，脚本里不再各写一遍：

```text
--dsh-home / --home  >  config.yaml 的 dsh_homes / dsh_home  >  $DSH_HOME  >  ~/.dsh
```

- 相对路径两种语义：config 里的值按 config 目录解析，CLI 参数按当前目录解析。`~` 与环境变量都展开。
- 显式参数永远压过配置与环境变量。v0.12.0 之前 `measure_modules`、`schema_budget` 的 `--list`
  分支把 `$DSH_HOME` 排在显式参数前面，同一个脚本的两条路径会读不同 home。
- **配置读不出来一律报错**（文件不存在除外）。配置文件在、但 YAML 写坏或没装 pyyaml 时，
  `load_config` 直接退出并给排查提示；不再「静默退回 `~/.dsh` 交一份看起来正常的报表」。
  最常见的坏法是双引号里写 Windows 反斜杠：`dsh_home: "D:\AI\DSH-NEXT"`，改用正斜杠或单引号。
- 机器上装两套客户端时最容易撞：「官方版写 `~/.dsh`、发行版写自己的 home」。顺序定死之后，
  读哪一份是可预测的；要临时换，用 `--dsh-home`（`cost_summary` / `compare_days` / `preflight`
  在 v0.12.0 补上了这个开关）。
- 注意 `compare_days.py` 是本版唯一一处优先级翻转（旧 `$DSH_HOME` > config，新 config > env）。
  升级后第一次对比若两天数字与以前不同，先确认读的是哪份 ledger。
- 自检：`python scripts/tg_common.py` 打印解析链与每个 home 的 `sessions` 是否存在；测试套件里有
  6 项用例锁这条顺序、4 项端到端（只给 config / 退回环境变量 / 开关可压 / 坏配置报错）、
  2 项 AST 静态检查（不许直读 `DSH_HOME`、不许自己拼 `sessions` 路径）。

改了这条顺序等于改所有脚本的读数口径：先改 `tg_common.py`，再跑测试与 `audit_pack`，别在单个脚本里绕过。

