# 变更记录

> 命名沿革：Tessera（拉丁语「凭证」）→ Metron（希腊语「度量」）→ **Token-Governance (TG)**。
> 前两个因「不够直白 / 偏统计」弃用；正文已全部改为 TG，仅此处留档。

## v1.6.0 — 2026-10-31（开场闸门：把「测出来」变成「每次都会看见」）

`schema_budget --check` 早就能报 126 KB，但它只有人主动跑才响；体检预算又设在 160 KB，实测永远不触发。
度量 → 动作这一环一直空着，工具清单三年没动。

- **`doctor.py --gate`**：只采 schema / preflight / turns / skills 四项，输出一行，超阈值退出码 1，没超**完全不出声**（零噪声，可直接进 CI 或会话开场）。当前实测：`schema 126KB 超 120｜工具 144｜技能 34/271`。
- **闸门阈值独立于体检预算**：新增 `config.budget.gate`（`schema_warn_kb` 120 / `visible_skills_max` 35 / `single_group_share` 0.25）。体检预算 160 KB 只负责「不冤枉」，闸门负责「该动手了」——两者混用等于没有闸门。
- **可见技能口径不依赖日志**：直接数 `SKILL.md` 里有没有 `disable-model-invocation`。日志只有 8 天，按日志数会漏；实测 271 目录 / 34 可见。
- **顺带纠正三处口径偏差**：`tg-context` 的「133 会话」标注为 2026-10-25 快照并附复现命令；`tg-isolate` 的「近 30 天」改为按实际可用窗口取；README 同处加注。实测数据面是 235 会话 / 17 天，不是 30 天。
- **两个 0 调用技能转隐藏**：`tg-loop`、`tg-orchestrate`（17 天各 0 次），`$DSH_HOME/skills` 可见条数 36 → 34。
- 断言 216 → 218（闸门阈值读取 + 可见技能计数两条，纯函数，不起子进程）。

## v1.5.1 — 2026-10-30（失败率口径修正：分母与 turn_cost 对齐，1962 → 951 轮）

`failure_analysis` 把 `system-reminder`、runtime context、时间戳这类**纯注入段**也当成用户轮，
分母虚高一倍：同一分子 386 次失败，被读成 19.7%（错）而不是 40.6%（对）。

- 切轮改用与 `turn_cost.turns_of` **同一条判据**：该段有 `assistant/message` 或 `tool/call` 才算一轮，纯注入段丢弃。实测 1962 → 951 轮，分子 386 不变。
- 新增 fixture `tests/fixtures/injection_session.jsonl`（3 条 user/message，其中 1 条只有 system-reminder）+ 3 条断言，锁住「两脚本分母必须相等」；判据再分叉会立刻变红。
- 成本字段正名：`failed_call_cost` / `cost_share` 会被读成「失败调用烧掉的钱」，实际是「有失败的轮次的整轮花费」。新增 `turns_with_failure_cost` / `turns_with_failure_cost_share`；旧键保留兼容。
- 断言 213 → 216。

## v1.5.0 — 2026-10-30（回取归因：把「压缩伤质量」从猜变成可核；并推翻 v1.4.2 的警报）

v1.4.2 用「notice 后 5 条事件里出现 spill 路径」认回取，本机报了 `table-strided` 73.9%。
升级后发现那是假的：路径出现在回显里就算命中，`find_locator` 还会把 JSON 结构的引号
（`},{`、`string`）当成路径。修完再跑，真回取 0 次。

- **回取归因**：只在 `tool/call` 里判，跳过回显；`grep` 或带 `offset>1` / `limit<=200` 的
  `read` 记为 `gap`（真回取，扣 45），整文件 `read` 记 `whole`（轻罚 8），认得出路径但
  认不出参数的记 `text`（折中扣 25）。
- **locator 校验**：路径必须 ≥8 字符且带 `/` 或 `\`，否则不认。
- **阈值不拍脑袋**：同批「样本 ≥5」的策略，真回取率取中位 ×2.5，夹在 5%–30%。
- **组件评分**：`doctor.py` 多一段 S–F（工具 schema / 缓存命中 / 失败轮次 / 压缩质量），
  每项带一句修法；阈值是经验值，只用来排序注意力。
- **实测（近 7 天，151 会话）**：392 次有损压缩、真回取 **0**、整读 8、评分 B/83.1；
  扣分几乎全来自「超长行截断」（lines-strided 35 次、lines-window 5 次）。
- 上一版对 `table-strided` 的指控**作废**；真正的修法是 `maxLine`，不是阈值。
- 断言 210 → 213。

## v1.4.2 — 2026-10-30（压缩质量闸门：压完之后模型还用不用得上）

新增 `scripts/quality_gate.py`。不花模型钱：不问「答对没」，看**行为证据**。

- **回取率**：notice 之后 5 条事件内是否 `read` 回同一个 spill 路径 —— 压过头才会回取。
- **形状**：省略行 / 采样 / 错误行 / 超长行截断，来自 notice 自带的形状位。
- **锚点**：spill 还在时核对 `path:line` 与 ≥3 位数字的覆盖率；原文已清就降级 `notice-only`
  并封顶 A（不给 S，证据不足不冒充满分）。
- 只给 `compressed`（有损）记分；`losslessly re-encoded`（无损重编码）单列，不参与评分。
- `--ab`：按策略出边际表（次数 / 省字节 / 回取率 / 截断 / 均分），>10% 回取记为压过头。
- `doctor.py` 接入：体检页多一行「压缩质量」；回取率 >10% 时把「抬高阈值或对该策略关掉」
  排到动作首位（`acts.insert(0)`），超过四条也先保它。

本机 14 天实测（492 次有损、扫 231 会话）：

| 策略 | 次数 | 回取率 | 截断 | 均分 |
|---|---|---|---|---|
| legacy（10-25 前，无策略位） | 322 | 26.7% | 0 | 76.9 |
| lines-strided | 115 | 0.0% | 34 | 80.2 |
| lines-window | 36 | 0.0% | 5 | 81.3 |
| **table-strided** | 16 | **75.0%** | 1 | 61.4 |
| collapse-repeat | 3 | 0.0% | 0 | 95.0 |

结论：表格采样压过头（16 次里 12 次回取），其余策略回取为 0。断言 203 → 207。

## v1.4.1 — 2026-10-30（面板常显交接卡状态；交接卡不再「什么都不显示」）

- `dsh-tg-account` 0.10.5 → 0.10.6：面板里的交接卡块原先在「没有卡 + 宿主没判定该生成」时整块
  消失（`if (!card && !ho.due) return null`），于是新用户看到的是空白，分不清是会话还没跑完一轮、
  还是 `handoff.py` / `memory_root` 没配好。现在**常显一行状态**：`交接卡 · N 字` / 生成中 /
  跑完一轮后生成 / 未配置 handoff.py / 生成失败（附 `handoff.py` 的原话或可手动执行的命令）。
- 状态机抽成纯函数 `cardState(card, ho, steps)`，7 条断言（Node 断言 155 → 156）；
  颜色按状态区分：有卡绿、失败琥珀、等待/未配置中性。
- 起因：同学装好胶囊后反馈「没开始切会话时交接卡不显示」——实际是空会话 + 面板整块隐藏；
  上一版已让安装器自检 `memory_root`（v1.4.0 之后那笔），这一版把面板也改成会自己解释。

## v1.4.0 — 2026-10-30（账目胶囊并入公开仓：一条命令装齐 TG + 胶囊）

- 新增 `plugins/dsh-tg-account/`：输入框旁账目胶囊（本会话 / 本会话链 / 今日 / 累计省 / 命中率、
  逐轮明细、按 `provider:model` 计价、子代理子树归并、超预算告警、交接卡、一键切会话、
  链级自动切换、侧栏母会话色标、工具输出入口瘦身）。运行时 4 个 JS 文件约 200 KB，
  Node ≥ 22，自带 155 项 `node --test` 断言。此前它是本机 `local-plugins` 里的私有副本，
  没有仓库、没有安装器，别人拿不到——这正是「胶囊是私有库」的由来。
- 新增 `scripts/install_account.py`：幂等安装胶囊（复制运行时文件 → 登记
  `dependencies`/`dsh.profile.bundles`（改前备份）→ 写 `<DSH_HOME>/_local/tg-account.json`
  把 `root` 指到本仓库）。支持 `--status` / `--apply` / `--uninstall` / `--home` / `--profile`，
  默认 dry-run。
- `install.py` 新增 `--with-account`：跟 `--with-guard` 一样顺带装插件（dry-run 时插件侧也只打印计划）。
- 胶囊去本机化：删掉硬编码的 `TG_ROOT_DEFAULT`（旧值是作者本机仓库路径）与 `zstd` 本机路径，
  改成「没配就降级」（内置价、交接卡关掉，账目照算）；`cordis.patch.yml`、README、
  两个测试夹具里的本机路径同时清掉，`audit_pack` 的可移植性门禁覆盖到插件源码。
- CI 新增 Node 22 + `node --test "plugins/dsh-tg-account/tests/*.test.mjs"` 一步：插件 155 项与 Python 203 项
  都是发布门禁。
- 交接卡的前置条件写进安装器：`install_account.py` 的 `--status`/`--apply` 现在检查 `memory_root`
  （环境变量 / 仓库 `config.yaml`），缺了就直说「交接卡 / 切会话会生成失败」并给两条修复命令——
  此前这个坑只出现在 `handoff.py` 的 stderr 里，面板上只表现成「交接卡生成失败」。
- 测试：`run_tests.py` 加 9 项安装器用例（dry-run 不写盘 / 装出插件与登记 / 复跑幂等 /
  `--status` 认已装 / `--uninstall` 摘登记 / `--with-account` dry-run 与一条命令装齐 /
  `--status` 报出 memory_root 未配置），总数 192 → 203；`data/counts.json`、README 徽章同步。

## v1.3.0 — 2026-10-30（一条命令安装；预检不再静默、不再把已装的自己判成冲突）

- 新增 `scripts/install.py`：一条命令完成「探测 DSH home → 写 config.yaml（`dsh_home` /
  `skills_dir` / `memory_root`）→ 复制 10 个技能 → 自检」。幂等：重跑只更新技能文件，
  不覆盖已配的 `memory_root` 与价表。参数：`--dry-run` / `--check` / `--dsh-home` /
  `--skills-dir` / `--memory-root` / `--yes` / `--with-guard`。
  起因：手动六步里 `skills_dir` 还留着 `~/.dsh/skills`，桌面版上预检会直接「拒绝」。
- 修预检三处新用户必踩的坑：
  1) 没有近 7 天会话时，`schema_budget.pick_session` 的 `sys.exit` 穿透 `except Exception`，
     预检把整段判定吞掉、只剩「近 7 天没有会话文件」一行。改为 `strict=False` 返回空表；
     `tests/first_run.py` 增加「预检必须给出判定」断言，`run_tests` 增加空 home 用例。
  2) 照 README 先拷技能再预检会报 10 条「同名技能」冲突。现在安装目录的 SKILL.md 与本包
     逐字节一致时显示「已安装技能」并放行，内容不同才判冲突。
  3) `setup.py` 刚建的记忆根（只有 `README.md` + `_template`）被当成「记忆根非空」。
     现在识别为安装脚手架，不计数。
- 预检的 `skills_dir` 解析：配置里仍是 `~/.dsh/skills` 且不存在、而 `<dsh_home>/skills` 存在时，
  自动改看后者，不再「拒绝：技能目录不可用」。
- `detect_home.py` 同时输出 `dsh_home` 与 `skills_dir` 两行（`--emit-config` 同），
  新用户不用猜第二行。
- 文档：README「安装」改为「一条命令（推荐）」+ 修正后的手动六步；PowerShell 复制命令补
  `New-Item -ItemType Directory -Force`、去掉尾部反斜杠、加 `-Force`（目标 `skills` 不存在时
  旧写法报「无法将容器复制到现有叶项」，重复安装也不幂等）；getting-started 补 4 条常见问题。
- 修 doctor 在新装当天的误导：窗口内没有任何会话时，旧版会建议「10 个已装技能 0 调用 → 删掉」，
  现在没有数据就只给 first-run（`has_window` 门控）。
- 评审回修（独立评审 4 条中危，全部采纳）：
  1) `install.py` 的配置/环境候选也要求结构证据，空壳 `~/.dsh` 不再被采信，继续探测；
  2) `preflight.py` 的 `skills_dir` 回退只认「`<home>/skills` 里真含 `tg-*`」，
     空目录或别的技能库仍判拒绝（否则把 rc=2 伪装成 rc=0）；
  3) `install.py` 的「下一步」改用 `sys.executable`、路径加引号、doctor 补 `--config`，
     空格/中文路径与只有 `python3` 的机器可照抄；
  4) `README_EN.md` 补 `python` / `python3` 替换说明。
- 测试：167 → 192 项（空 home 预检给判定 / 已装技能不判冲突 / 安装器 dry-run、真装、复跑、
  `--check` / 坏配置覆盖 `install.py` / 新装当天不劝删技能 / 空 `<home>/skills` 不冒充放行 /
  **零参数新 clone 路径**、**它打印的下一步可照抄**、**`--check` 比对内容**、
  **复跑不改配置字节**、**写不进去不抛 traceback**、**`$DSH_HOME` 不存在时硬失败**、
  **下一步用绝对路径** / **全新安装不留无效备份** / **记忆根建不了给人话** /
  **下一步经 cmd 与 PowerShell 真执行** / **技能内文件缺失判失败** / **空 skills_dir 不再冒充放行**）。
- 补测「真新用户」这一段（2026-10-30 端到端跑出来才发现的缺口）：
  1) 之前所有安装用例都显式传 `--config/--dsh-home/--memory-root`，而新用户是零参数跑、
     靠 `config.yaml` 自己按 example 生成、`skills_dir` 自己落到 `<home>/skills` —— 现在有两条
     断言把这条路钉住，并把 `install.py` 打印的「下一步」原样执行一遍（`preflight` 必须放行）。
  2) `tests/first_run.py` 原先继承宿主环境，若机器上设了 `TG_MEMORY_ROOT`（TG 老用户、CI 变量
     常见），第 8c 步「未配置 + 非交互 → 预期 rc=2」会读到宿主记忆根返回 0，测试假红。
     现在它自清 `TG_MEMORY_ROOT` / `DSH_HOME`——它模拟的是一台没装过 TG 的机器；
     `run_tests` 的第 18 条也故意带脏环境跑，防止回退。
- 评审回修（独立评审 5 条，全部采纳；同一份结论由两个隔离实例各测一遍）：
  1) 显式 `$DSH_HOME` 被静默丢弃：指向不存在目录时旧版会改用自动探测到的**另一套** home
     并 rc=0「安装完成」——等于把技能写进另一个真实安装。现在显式 home 不存在就 rc=2 并说清
     是 `$DSH_HOME` 给的；只有 config.yaml 里的空壳默认值才继续往下探测。
  2) `--check` 只验 SKILL.md 存在 + frontmatter name，内容换掉也报「检查通过」。现在**逐文件
     比对整棵技能目录**（缺文件/内容不同/嵌套目录没落地都算没装好），并提示「重跑 install.py 会覆盖」。
  3) 「下一步」打印的是相对路径 `scripts/preflight.py`，换 cwd 照抄就 `can't open file`。
     现在打印绝对路径（解释器用 `sys.executable`），任意目录可照抄。
  4) 路径不可写（`--skills-dir` 是文件、`--config` 父目录是文件）直接 traceback。现在两处
     都包成 `[FAIL]`/`[warn]` 的人话 + rc=1；`os.makedirs` 移进 `try` 内。
  5) 每次重跑都整篇重写 config.yaml（CRLF→LF，diff 全红）并留一份 `.bak-install-*`。现在
     按原换行风格写、内容没变就不写也不备份。
  6) 收敛复核又抓到两处同类残留：全新安装仍会留下 `.bak-install-*` / `.bak-memory-root-*`
     （同一次运行里第二次改写时「刚生成的模板」识别丢了）；`--memory-root` 落在文件底下时
     `ensure_memory_layout` 抛 traceback。现在 `tg_common` 记「本进程刚生成的配置」
     （`mark_generated` / `is_generated`），刚生成的配置不留备份、用户改过的配置仍留一份；
     安装器在写完记忆根后补一次「真能建」的检查，建不了给 `[FAIL]` + rc=1。
     备注意见：为「不留假备份」加了一条反向用例——用户配置真被改时必须留回滚点，避免优化过头。
  7) 第三轮复核（另一次独立实例）又抓到三处「看起来成功」：
     a) 「下一步」把解释器路径也加了引号，PowerShell 里原样粘贴直接 `ParserError`（cmd 正常）。
        现在只写命令名 `python`（PATH 里没有时退回完整路径并提示 `&` 前缀），路径仍绝对；
        用例改成**经 cmd 与 PowerShell 真执行打印出的整行**，不再只拆参数调 subprocess。
     b) 技能树里嵌套文件写不进时（`templates/` 被同名文件占住）旧版只打 `[warn]` 就继续、
        仍打印「安装完成」rc=0。现在 `copy_skills` 返回失败清单并判 rc=1，`verify` 也从
        「只比 SKILL.md」改成**逐文件比对整棵技能目录**（缺文件/内容不同都算没装好）。
     c) `preflight` 只看 `skills_dir` 在不在，空目录也判「放行」，与 `install.py --check`
        的 rc=1 自相矛盾。现在：一个 `tg-*` 都没有 → 待确认（附「跑 install.py」）；
        只装了一部分 → 只提示不阻断。
- 版本面：v1.2.0 → v1.3.0；`data/counts.json`、README 徽章与正文计数同步。

## v1.2.0 — 2026-10-30（循环守卫 v0.2：思考级空转三条代理规则）

- `dsh-loop-guard` **0.1.1 → 0.2.0**：工具级重复之外再加一层**思考级空转**代理检测。同一状态机在
  `tools/post-execute` 维护 per-turn 计数（calls / reads / artifacts / errStreak）；插件拿不到 reasoning，
  只回答「有没有落物、是否连败」：
  - `read-storm`：连续读/检索 ≥12 次、本轮读 ≥8、期间 0 次 write/edit；
  - `error-streak`：连续 3 次调用失败；
  - `budget`：单轮 ≥60 次调用且 0 次 write/edit。
  - 每轮每规则最多一次，两条思考提醒之间隔 ≥8 次调用；`thinking.enabled: false` 可关，阈值在
    `cordis.patch.yml` 的 `thinking:` 段可调（随包与代码默认都是**开**、阈值保守）。
- 验证：`scripts/verify_guard.mjs` 换成自带最小 harness（一个 ctx、一个 agent，真 fire 工具调用、数追加的
  提醒条数，并核对日志条数、去重与上游一致），**53 项检查全过**；`scripts/guard_stats.py` 加 `--by-kind`
  （loop / read-storm / error-streak / budget），旧日志无 `kind` 按 loop 计，链统计只数 loop。
- 文档：`plugins/dsh-loop-guard/README.md` 加「思考级空转」规则表与边界（是代理指标，不是 reasoning 计量）；
  `docs/operations.md` 把文本层载体写成宿主 `AGENTS.md` §1「空转止损」并补运行时这层；`skills/tg-loop/SKILL.md`
  同步三条规则与配置样例。
- 口径统一：此前 README / `package.json` / SKILL 三处把 v0.2 写成「默认关」，与代码和随包 patch
  （`enabled: true`）矛盾，本次统一为「默认开」。
- 版本面：项目 `v1.1.1 → v1.2.0`（v1.1.1 只补了 CHANGELOG，没单独打 tag）；`data/counts.json` 与
  README 徽章同步。

## v1.1.1 — 2026-10-27（胶囊「今日」口径修正：跨会话不再互相污染）

- 修一个成立了的错数：`_local/tg-day.json` 是**共享缓存**，但建缓存时按调用者路径把「本会话」排除掉，
  而 `dayTotal()` 又把本会话实付加回去 —— 于是**谁建的缓存，谁的数就对；其他会话读同一份缓存时，
  自己那份被计两遍**。切回旧对话的第一屏必中（缓存是别人 60 秒内建的）。实测同一份日志（真值 ¥10.39）：
  ace3e531 自己建缓存时读到 ¥10.29（对）、缓存换成另一个会话建之后同一对话读到 ¥11.69（+它自己那份）；
  反向推算若 ace3e531 建、本会话读则得 ¥9.10（−本会话自己的 ¥1.29 差额）。
- 修法：`refreshDay()` 扫**全部**会话（`dayFiles(since)` 不再收 `selfPath`），缓存只存事实；读取侧新增纯函数
  `todayFromCache(cache, selfPath, selfTodayCny, fresh)` 做 `total − cache.files[self] + 本会话今日额`，谁读都对。
- 修第二个口径不一致：本会话原先按**整段累计**计入今日，而其他会话按日志每行 `time` 切本地日。现在
  `summarizeSession()` 顺带累加 `cnyToday`（同一个循环，零额外 IO），跨天会话不再把昨天的钱带进今天；
  「本」仍显示会话累计（语义不变）。
- 修第三个：`account()` 的 memo 命中会直接返回旧 payload，导致「今日」要等本会话下一步才更新。
  现在 memo 命中时只重算 `today`（缓存读 + 减法，微秒级），重活照旧复用。
- 回调：`dsh-tg-account` 0.5.1 → 0.5.2；新增 `tests/today.test.mjs`（日切、缓存减项、新会话、跨天、负数守卫
  5 例），全套 73 例通过；四视角验收脚本实测 ace3e531 / 本会话 / 真值三数一致（差 3.4e-5，round4 噪声）。

## v1.1.0 — 2026-10-26（多模型计价：不再局限于 DeepSeek）

- 价表从「一套价」升级为「按 `provider:model` 计价」，解析顺序在 `ledger.py` 与 DSH 胶囊两端
  完全一致：`provider:model` → `model` → 免费 → 缺 source 折 default → provider 默认 → 家族前缀
  （整词边界）→ 未定价。
- 修两个已成立的错数：`deepseek-v4.1-flash-free` 原先被 `deepseek-v4.1-flash` 的「包含」匹配吃掉，
  按 0.15/0.003/0.6 收费（本应 ¥0，虚高 100%）；非 flash 模型（如 `deepseek-v4-pro`）一律按 flash
  价低报 4.4 倍。现在免费先于家族匹配，`-free` / `:free` / `*/free` 三条兜底规则不依赖名单。
- **未定价不再折 default**：有名有姓但价表里没有 → 钱记 ¥0、tokens 照记，进 `pricing.unpriced`
  与 `days[date].unpricedCalls`，`report` 末尾提示。缺 `message.source` 的事件（标题生成）仍按
  `default` 折算，归属 `missing-source`，与「未定价」分开统计。
- 新增 `providers.<p>.default`（只在显式配置时兜底）与 `free.catalogs`（从 DSH 免费模型目录
  `our-free-model/catalog.json` 自动并入免费 id；本机实测并入 24 个）。
- 峰时价只对声明了 `peak` 的模型生效，不再对没写 peak 的模型默认 ×2（别家峰谷窗口跟 DeepSeek 不同）。
- 新增 `ledger.py prices export|show|check`：`export` 把价表归一成 `data/prices.json`（含内容指纹，
  不含生成时间，重复导出逐字节一致），DSH 胶囊直接读它，不在 JS 里二次解析 YAML；`check` 拿日志里
  真实出现的 `provider:model` 对账价表，并检测「改了 config 忘导出」的漂移，`--strict` 有未定价/
  漂移就退出码 1（可进 CI）。
- `report --by provider` 新增；`pricing` 段新增 `schema / fingerprint / providers / free / default / unpriced`。
- DSH 胶囊（`dsh-tg-account` 0.4.0 → 0.5.0，本机 `local-plugins/`）：逐事件按 `message.source`
  取 provider/model 计价，`account()` 的账目循环抽成可测的 `summarizeSession(raw, book)`；面板新增
  「按模型」小表（调用数 / ¥ / 免费标记）与橙色「未定价 N 次」；「今日合计」按同一价表逐条重算，
  缓存键带价表指纹（改价后当天数字立即更新）；读不到 `prices.json` 时退回单套价（旧行为，不会更糟）。
  顺带修掉汇率双真源：胶囊此前抓不到 `rate:` 就退回 7.1，而账本用 `fx.usdToCny=6.6702`（差 6.4%）。
- 对账：同一份会话日志两端逐分一致（`session-a40d176d` ¥1.0059 / 账本 1.005856；
  `session-3d32065c` ¥0.6827 / 0.682744）；本机全量 `prices check` 5 个 `provider:model` 全部可解释
  （含 `workbuddy` / `workbuddy-ai` 两个非 DS 通道）。
- 计数：断言 155 → **167**（多模型计价 9 条：免费/未定价/provider 默认/导出幂等/漂移/按 provider 汇总）。

## v1.0.2 — 2026-10-25（对话夹母文件夹：装的时候问宿主放哪；删掉 `~/tg-memory` 静默兜底）

- 新增 `scripts/setup.py`（安装向导）：问一句「对话夹放哪」，回车用默认 `~/Documents/tg-memory`；
  建 `<root>/README.md` + `<root>/_template/00-card.md`，并把答案写回 `config.yaml` 的 `memory_root`（先备份、原子落盘、保留其它项与注释）。
  无人值守：`--memory-root <路径>` / `--yes`（已配过就沿用，不覆盖）；非交互且未配置 → 退出码 **2** 并给命令；`--show` 只看现状。
- 删静默兜底：`memory_root` 未配置时，`handoff.py` / `new_session.py` 不再落到 `~/tg-memory`
  （2026-10-25 交接卡被写进 `C:\Users\<user>\tg-memory` 就是这条降级路径）。统一走 `tg_common.ensure_memory_root()`：
  `config.yaml` > `$TG_MEMORY_ROOT` > 可交互就问 > 否则 `MemoryRootUnset` 并给三条出路。
- `handoff.py --print-only` 不受影响（预览不落盘，也不要求母文件夹已配置）。
- `preflight.py`：`memory_root` 未配置时单列一行「记忆根未配置 + 跑 setup.py」，不再把它当成 `~/tg-memory` 去检查。
- `config.example.yaml`：`memory_root` 默认留空，并注明「留空＝首次运行时询问」。
- 文档：README 安装段加第 3 步 `python scripts/setup.py`；`docs/getting-started.md` 四步 → 五步；`docs/adaptation.md` 两条按机器改的项同步。
- 计数：脚本 26 → **27**、断言 152 → **155**（坏配置注入脚本清单加 `setup.py`，+3 条）。

## v1.0.1 — 2026-10-25（新增上手手册；修 3 处过期描述）

- 新文档 `docs/getting-started.md`：面向第一次用的人——适用判断 → 装完四步（`env_check` / `detect_home` / `config` / `preflight`）→ 三条日常命令（`doctor` / `cost_summary` / `turn_cost`）→ 归因四件套 → 技能路由 → 改配置与回滚 → 常见问题 → 一页速查。README「文档」段置顶链接。
- 修 README 过期描述：安装段「账本由 cost-meter 记」→ `ledger.py` 从日志派生（`dsh-cost-meter` 已于 v0.14.0 卸载）；「脚本（25 个）」→ **26**；「十二条原理」→ **十三条**；已知边界里的 cost-meter 账本同步改。
- `config.example.yaml` 的 schema 参考值 174 个 / 154 KB → **142 个 / 122.7 KB**（2026-10-25 实测）。
## v1.0.0 — 2026-10-25（版本号转 1.0；README 计数校正）

- 版本语义：接口冻结，之后按 1.x 递增。功能与 v0.14.8 完全相同，无行为变更。
- README 计数校正（对齐 `data/counts.json`）：脚本数 25/23 → **26**、断言数 147/126 → **152**；
  中文版 4 处、英文版 3 处。此前正文长期落后于徽章。
- tag `v1.0.0` + GitHub Release；`v0.14.8` 保留为历史正式版，不再是最新。

## v0.14.8 — 2026-10-25（胶囊压缩记账只认真实通知，面板露出「省」）

- 修：压缩收益此前按「行内正则」统计，`read` 到的源码注释、`ops_bash` 回显的示例都被当成真压缩。
  实测单会话虚计 83 条 / ¥0.20；另两处会话同一压缩重复计（`ca1h:50,696->16,907`）。
- 记账改走事件：`createCompScanner` 只认 `tool/result` 结果文本末尾的整条 notice；`read` / `save_token_expand` 跳过；
  同 id 同体积去重（key 带 before/after，防 4 位 id 碰撞误伤）。
- 性能：记账并入主循环一次解析（原为两遍 `JSON.parse`），84 会话 p50 6.0ms（与旧实现持平），13.8MB 会话 86→32ms。
- 面板：「输出瘦身」行露出「省 ¥X · N 次」（只算工具结果压缩，7 条输出纪律无基线不计金额）；`snapshotTrim().on` 不再写死 `true`。
- 对账：84 个会话新账 ¥1.76 / 旧账 ¥2.44，差额逐条可归因（假阳性 + 重复），无真实压缩漏计；
  `8ba1f7ea` 4 条 ¥0.0232 新旧一致。旧口径缓存 `_local/tg-saved.json` 已备份删除，重算后「累计」会变小。
- 测试：`tests/notice.test.mjs` 补记账口径用例，插件 28 条在 NEXT 与官方版两端全过。
- **发布**：首个正式版。README 状态徽章 `status: beta → stable`；tag `v0.14.8` + GitHub Release（覆盖 v0.11.1 之后的 0.12–0.14 全部改动）。

## v0.14.7 — 2026-10-25（官方版并行：多 home 账目 + 胶囊双端隔离）

- `config.yaml` 打开 `dsh_homes`：`D:/AI/DSH-NEXT` + `D:/AI/DSH-official/home`（官方版应急用；`~/.dsh` 是它的 junction）。
  脚本走 `resolve_dsh_homes` 自动两端扫描，`resolve_dsh_home`（单 home 口径）仍取第一个＝NEXT，历史对比不被悄悄改口径。
- `ledger.py`：日账新增 `byHome`（home → `{calls, costCny}`），会话行新增 `home`；`report --by home` 出两端分账。
  实测最近 7 天 NEXT ¥100.75 / 官方版 ¥9.57，合计 ¥110.31——此前官方版完全没入账。
- 账目胶囊（`dsh-tg-account`）去掉写死的 `D:/AI/DSH-NEXT`：改 `process.env.DSH_HOME || ~/.dsh`，
  两端各存一份 `_local/tg-*.json`，trim 开关互不影响（原写法在官方版会把 NEXT 的账显示出来、状态也写进 NEXT）。
- `ensureHandoff()` 调 `handoff.py` 补 `--dsh-home`：不带会去第一个 home 找会话，官方版会话找不到 → 交接卡不生成。
- 官方版侧装独立副本（不 junction 源码）：`D:/AI/DSH-official/home/local-plugins/dsh-tg-account`，
  profile `dependencies` + `bundles` 各一条，`node_modules/@local/dsh-tg-account` junction 指向副本。
  版本记录更正：官方版已随 nightly 升到 **0.2.1-rc.3**（旧记录 rc.2），其 asar 实测含 `conversation.session.header.actions`（11 处）与 `webServer` prefix 路由（5 处），与 NEXT 持平。
- 对账：官方会话 `bf5b7c30` 胶囊 ¥4.4674，`turn_cost.py --dsh-home D:/AI/DSH-official/home` 逐轮求和同为 ¥4.4674；插件 20 条测试两端各跑一遍全过。


## v0.14.6 — 2026-10-25（胶囊加「今日」：跨会话当日消费合计）

- **新增 `payload.today`**：`{ cny, others, sessions, at, stale }`。口径＝本地日内 `assistant/message` 的 usage ×
  与「本会话消费」同一套价格（不是「今天写过的会话全量」：跨天会话只计今天那段）。
  后台异步扫 + `_local/tg-day.json` 缓存 60 秒，按 `path+mtime` 增量复用；**扫描时跳过当前会话文件**（它每步都在变，重解析最贵），
  由 `account()` 自己算完再加进去，所以胶囊上的「今日」每步都是最新。
- **胶囊在「本」与「省」之间插入「· 今日 ¥X」**，悬停给「本会话 + N 个其他会话 · 按本地日切」，首次扫描没回来前显示「…」
- 对账：独立脚本同日同口径复算 **¥6.6489**（22 个候选日志里 13 个有今日用量），插件重启后应一致
- 修：`ensureHandoff()` 调 `handoff.py` 补 `--config`（原先漏传 → 相对路径解析不到 → 交接卡落 `~/tg-memory`，即 C 盘）；
  `~/tg-memory` 下 8 个夹已迁到 `D:\AI\Knowledge\AI-KB\sessions\default-workspace\`
- 修字：面板标题与注入块「词元管控」→「词元掌控」（`lib/client.js` 胶囊标题、`lib/index.js` 输出规范块首行）
- **交接卡改绑任务夹**（`handoff.py`）：新增 `--task` / `--task-file` / `--set-task` / `--no-task`。
  会话→任务由指针 `_local/tg-current-task.json` 决定（精确 id > sid8 > `*` 默认），
  `--set-task` 一条命令写指针。任务夹里首张卡仍是 `00-card.md`，同任务的其他会话各写 `01-<sid8>.md`，
  不再互相覆盖；`99-cards.json` 记「哪个会话在哪张卡」。同一会话重跑按索引写回原卡（不造分叉）。
  未绑定任务的会话退化为 `20261025-auto-<sid8>/00-card.md`（旧行为不变）。
- `handoff.py --config` 缺省改为脚本旁的 `../config.yaml`（不再依赖 cwd，手跑也不会误落 `~/tg-memory`）
- 插件侧卡片定位同步三处来源：`01-<sid8>.md` → `99-cards.json` 索引 → 旧 `*-auto-<sid8>/00-card.md`，
  取 mtime 最新；新增 `tests/card.test.mjs`（5 条）

## v0.14.5 — 2026-10-25（融合 save-token 压缩核：入口改写一次，落盘可逆，零常驻 schema）

把 `dsh-plugin-save-token@2.4.1` 的核心原理并进 TG 自身（`dsh-tg-account`），随后停用该插件。

- **新增 `lib/compress.js`（296 行，纯函数）**：结构感知窗口（表格走「头 + 带原始行号步进采样 + 尾」）、
  空行/重复行折叠（无损优先）、字节预算窗口（仅 ≥8KB 或 ≥240 行大输出）、单行 >420 字符首尾截断、
  错误行 ±1 上下文保护
- **红线照抄**：双门控（字节 ≤72% 且省 ≥500B 且估算 token 严格下降，任一不过原样放行）；
  先 `spillStore` 落盘成功才替换，失败就放弃（可逆是硬前提）
- **挂载方式**：`tools/post-execute` prepend，只在结果进上下文那一刻改写一次，历史字节不动（保护前缀缓存）；
  `read` / 回读结果豁免；面板「输出瘦身」总开关同时管机械层
- **废弃项（依据本机 133 会话/10,224 请求实测）**：TOON 无损重编码 200 次里仅 1 次命中、跨轮去重 0 命中 →
  都不抄；`save_token_expand` 工具每请求常驻约 110 token 换 2 次调用 → 不注册，改 notice 内嵌原文 locator +
  现有 `read`/`grep` 取回
- **触发面做对**：按字节而非行数触发（save-token 的 `maxLines:240` 让 <240 行输出一条不压，本机 1,435 条
  超阈值结果只压了 197 条 = 14%）
- **账不变**：NOTICE 正则兼容 `[save-token #…]` 与 `[tg-trim #…]` 两代标记，`component` 口径继续按
  「首次按输入价 + 之后每步按缓存价」复原
- 测试：新增 `tests/compress.test.mjs`（8 条）、`tests/hook.test.mjs`（4 条）、`tests/notice.test.mjs`（4 条），16/16 通过
- 面板：「输出规范」→「输出瘦身」，总开关同时关掉规范注入与机械压缩；压缩收益不单独成块，只留在数据层
  （`payload.trim` / `component`）
- **悬停改成逐模块列金额**：工具 schema / 技能目录 / 工具输出压缩 / 换会话 100 步（预测），不写合计、口径与
  「未计入」。累计口径统一为「配置反事实 + 工具输出压缩」（`savedCur.v=4`，新增 `cfg`/`comp`/`compCount`/`spillCount`）
- 新增可复原指标：`analyzeSession` 顺带数出 DSH 原生落盘次数（`spillCount`，21 个近期会话 55 次）；
  该模块无法从日志还原金额，只报次数，不编钱
- 可调阈值：`_local/tg-compress.json` 覆盖 `DEFAULT_CFG` 任意键（5 秒生效，不用重启）；不建则用默认值
- 文档：README 亮点补「工具输出在入口瘦一次」；`tg-context` 明确区分 `compaction`（整段前缀重算）与本机制
- 停用：`profiles/desktop/package.json` 移除 `dsh-plugin-save-token` 依赖与 bundle 行（备份
  `package.json.bak-20261025-165305`），包体留在 node_modules 便于回滚

## v0.14.4 — 2026-10-25（交接卡第二轮：目标取首条、「做到哪」降格为「最后一句」）

两路独立审查（评审 10 条 + 对抗审 5 条，含 130 个真实会话实测）打回的硬伤，逐条改：

- **目标改成取首条真人输入**：末条 32%（24/74）是「继续」「可以」「1,2」这类按语，顶上去等于没交接；
  末条另立一行「最近指令」，说明上一轮是不是按语。压缩会话（`compact-checkpoint`）人工输入被过滤时，
  退到 `session/title`（LLM 生成的会话名），实测 5 个压缩会话从「结束本轮对话…」回到真实任务
- **「做到哪」降格为「最后一句」**：最后一条助手文本只证明最后说了什么，不证明进度——本会话实测压出来的
  是 `` ``` ══ 第 1/8 轮 ══ `` 这种框线。现在先剥围栏、跳过纯表格/框线（词字符 ≥12 且占比 ≥35% 才收），
  再按句读压到 160 字；进度仍要人填
- **截断不再越界**：`[:80]+"…"` 会产出 81 字（74 张卡里 70 张踩到），改 `[:limit-1]`；西文标点仅在
  其后是空格/结尾时才算句读，否则会把 `00-card.md` 切成 `00-card.`
- **片段来源不再说谎**：来源按「本会话是否有代码改动」判——旧版按取到那段扩展名判，40 个会话里 5 个
  明明改过 `.js/.py` 却写「无代码改动」；片段选择去掉「最后 8 个候选」窗口（长会话末段全是文档时永远取不到代码）
- **临时路径判据放宽**：`(?i)(^|[\/_.-])(tmp|temp)([\/_.-]|$)`——`_tmp_probe.py`、`Tmp/` 都算临时，
  `template.py` 不误伤；围栏长度改为「最长反引号 +1」，正文含四反引号也不再被截断
- **路径归一化改 `os.path.normcase`**：原来一律小写，POSIX 上 `a/B` 与 `a/b` 会被并成一个
- 测试 143 → 147：新增「目标取首条 / 最近指令 / 80·160 上限 / 临时脚手架不入片段 / `--if-absent` 不覆盖 /
  content 为字符串的日志变体」

## v0.14.3 — 2026-10-25（交接卡：把「状态」从占位改成自动）

- 卡片头一行补回**会话 id**（`> 会话 <id> · 轮/步/¥/ctx · 时间`）：旧版只有对话夹名，接手者拿不回原日志
- 「状态」不再两行空占位：
  - **目标**＝最后一条真人用户输入的首行（≤80 字）。只认 `source.kind=user`；宿主注入的 system-reminder /
    时间快照 / 技能目录不算，老日志无 `source` 时用「首字符不是 `<`」兜底——这条不兜底就会把技能目录当目标
  - **做到哪**＝最后一条助手文本（只取 `type=text` 块，压成一行 ≤160 字，超长带 `…`）
  - 取不到写「（无）」，不空着
- 「关键片段」标来源：`最后一次代码改动` / `无代码改动，取最后写入的文本`；`tmp/` 下的脚手架一律跳过
  （旧版会把 `tmp/make_usage_docx.py` 当交接重点）
- 测试 140 → 143：交接卡三条（会话 id / 目标自动填且不认注入 / 片段标来源）+ 新夹具
  `tests/fixtures/handoff_session.jsonl`；`handoff.py` 并入「坏配置必须报错」遍历（此前漏测）

## v0.14.2 — 2026-10-24（第二轮：只装技能能降级、CI 补 3.13/3.14、计价兜底可观测）
- **计价兜底不再静默**（换模型时最危险的错数来源）：`price_for` 返回命中方式（exact/short/contains/default），
  `ledger.py` 把 `pricing.matched` / `pricing.unmatched` / `pricing.estimated` 写进账本，`ingest` 的人读输出
  直接列出「哪些模型不在价表、按 default 折算了多少次」；`cost_summary` 末尾加「含估算」脚注，`--json` 带
  `estimated`。本机真账本第一次跑就抓到 `unknown:unknown`（8 次）——在此之前完全静默
- `env_check` 新增 `[信息] pricing`：报「用哪套价、几个模型、汇率与 asOf、日按哪个时区切」；没有价表时
  提示「ledger.py 会用内置 deepseek 价，换模型时钱会算错」
- **只装技能的降级**：`tg-router` 加「先认安装形态」（`ls scripts/tg_common.py` → `NO-SCRIPTS`），
  形态 B（只有 `skills/`，技能市场常见）三条处置：只用方法论、明确告知补仓库、**禁止编造没跑过的数**；
  README 加同名小节
- **CI**：matrix 扩到 3.10 / 3.12 / 3.13 / 3.14；新增 `py314-windows` job **故意不装 zstandard**，
  守住 v0.14.1 修的那个「门禁比实现严」；`tests/compress()` 同步支持 3.14 内建压缩
  （否则夹具造不出来，大批用例会静默 SKIP，看起来还是绿的）
- **测试**：新增「中文用户名下 `~` 展开」（Windows 走 USERPROFILE）；修「无 HOME 时 ~ 展不开」用例的
  环境依赖——宿主注入 `DSH_HOME` 时前提不成立，用例会随「跑在哪个 shell 里」时红时绿，现在一并清掉
- 测试 136 → 137

## v0.14.1 — 2026-10-24（换机器可用性：口径统一 + 探测补上）
- 修「门禁比实现严」：`env_check` 曾只认 `zstd` 命令与 `zstandard` 模块，漏掉 Python ≥3.14 的内建
  `compression.zstd`。在「3.14 + 没装 zstandard + 没有 zstd 命令」的机器上判「必需项缺失、退出码 1」，
  而所有看账脚本其实都能解压（实测同环境解 1.5 MB 会话成功，`env_check` 却判缺）。现在
  `tg_common.unzip_channels()` 是唯一判据，`env_check` 与 `decompress_bytes` 共用，
  三条通道（命令 / 内建 / 模块）按序降级
- 修解压实现副本漂移：`retry_patterns.py` 自带的那份没有内建通道、没有假模块保护，导致同一台机器上
  「别的脚本能跑、它自己说解压器缺失」。改为复用 `tg_common.decompress_bytes`
- 新增 `scripts/detect_home.py`：桌面版 home 由安装位置决定（可在任意盘任意目录），默认猜 `~/.dsh`
  在别人机器上会安静指向空目录 → 报「没有数据」。探测顺序：显式参数 → `$DSH_HOME` → config →
  桌面版指针文件 `<app>/home/*location*.json` → 标准位置 → 名称含 dsh 的目录；按结构证据打分
  （sessions 里有 `session*.jsonl*` 得分最高），输出可直接粘贴的 `dsh_home:` 片段；只读，不改文件
- 补 `audit_pack` 的漏检：扫描面加入 `.mjs/.js/.cmd/.sh/.ps1/.json`（`verify_guard.mjs` 里写死的
  本机路径因此长期未被发现）；绝对路径正则补正斜杠形式（旧写法只认反斜杠）；`.gitignore` 里声明
  不发布的 `config.yaml` / `data/ledger.json*` / `_local/` 不再计入告警
- `verify_guard.mjs` 去掉写死的上游路径：按 `TG_UPSTREAM` → config 的 `dsh_home` → `$DSH_HOME` →
  用户目录 找上游，找不到就明说「跳过」而不是静默通过；本机长期「跳过」的哈希比对恢复成真比对
- 文档：`benchmark.md` / `operations.md` 的示例路径改为通用写法（不再出现本机目录）；
  README 徽章与 `data/counts.json` 同步（脚本 24→25、测试 132→136）
- 测试 132 → 136：env_check 与 tg_common 口径一致、假 zstandard 在内建通道下不误判、
  `retry_patterns` 不再自带解压副本、`verify_guard` 不含盘符绝对路径、`detect_home` 靠指针文件命中、
  `audit_pack` 能抓 `.mjs` 里的正斜杠绝对路径

## v0.14.0 — 2026-10-24（账本 v2：记账的写方从插件换成自己的脚本）
- 新增 `scripts/ledger.py`：从会话日志（`assistant/message.data.usage`）派生日 / 模型 / 会话三级成本，
  写 `data/ledger.json`。`ingest` 幂等（按文件 size+mtime 缓存解析结果，全量 107 份日志 3.6 s，二次 0.2 s）、
  `--rebuild` 全量重算、`report --by day|model|session`、`day`、`verify`（校验「模型合计 / 会话合计 / 总量」自洽）
- 背景：`dsh-cost-meter` 插件 2026-10-24 两套 home 全卸载（旧账本 10 天留档在 `D:\AI\_backups\cost-meter-removed-20261024`），
  记账不能跟着插件一起消失 → 改为「日志是源，账本是派生缓存」，换机器/换 home 只要日志在就能重算
- 口径写死并注明：`input/cacheWrite → cacheMiss`、`cacheRead → cacheHit`、`output → output`，USD/百万 token × `fx.usdToCny`；
  **峰谷默认关闭**——拿旧账本校验发现 10-22 / 10-24 与「无峰谷」重算零差异，10-21 / 10-23 却多出 1.42× / 1.44×，
  旧插件的峰谷判定复现不出来，与其抄一个算不准的规则，不如关掉并留着可配置
- `tg_common` 两处补强：`decompress_bytes` 增加 Python ≥3.14 内建 `compression.zstd` 分支（本机 3.14.6 不装
  `zstandard` 也能解日志）；新增 `ledger_paths(cfg, base, homes)`，统一「新账本优先、旧插件路径兜底」
- 下游兼容：`cost_summary` / `cache_audit` / `env_check` 改读 `ledger_paths`，新账本沿用
  `days[date].{calls,input,cacheRead,cacheWrite,output,costCny,byProviderModel}` 旧字段，所以报告无需改口径；
  两套账本同时存在时只认 `schema=tg-ledger/2`，避免同一笔钱算两遍
- 测试 126 → 132：账本派生、计价手算、重复 ingest 幂等、`verify` 自洽、`cost_summary` 读新账本，
  另把「假 zstandard 模块」用例改成按 Python 版本分流（3.14+ 走内建解压，期望值反过来）
- 文档：新增 `docs/ledger-v2.md`（schema 字段表、口径、校验方法与已知差异）

## v0.13.1 — 2026-10-24（首次上手成一条可跑的门禁）
- 新增 `tests/first_run.py`：模拟「刚 clone、机器上没有 DSH 数据」的前十分钟，按安装段的顺序跑
  `env_check → preflight → doctor → cost_summary → baseline → patch_snapshot → install_guard → new_session → check_yaml → audit_pack`，
  断言只有两条：**不出现 `Traceback`**、退出码落在文档写明的范围。10 步全过才算通
- CI 新增 `first-run (windows)` job 跑同一脚本；这是「本地有数据才绿」那类问题的整类防线（`doctor` 的 CI 红灯就是这么来的）
- `install_guard.py` 补 `--config`：换机器的人可以用自己那份配置决定装到哪个 home，不再写死读仓库的 `config.yaml`
- 测试 122 → 126（`first_run.py` 一条 + `install_guard` 坏配置三条）
- 实测：首次上手脚本本机 10/10 通过；`preflight` 空机器 rc=1、`cost_summary`/`baseline` rc=1、`doctor` 给 `first-run` 动作，都要说人话

## v0.13.0 — 2026-10-24（换机器能跑：依赖分级 + 环境自检 + 跨平台矩阵）
- 新增 `scripts/env_check.py`：一条命令核对 Python 版本 / pyyaml / 解压器（`zstd` 或 `zstandard`）/ Node / `dsh_home` 可读与可写。分必需 / 可选 / 信息三档，缺必需项退出码 1，`--json` 给安装脚本或 CI 读。检测用「真的 import 一次」而不是 `find_spec`——后者只看得到文件，模块自己抛 `ImportError` 时会误判为可用（自测时抓到的假阳性）
- 修 `scripts/check_yaml.py`：缺 pyyaml 时抛 `Traceback`（模块顶层 `import yaml`）；改为延迟导入 + 退出码 2。同时补文件不存在 / 非 UTF-8 / YAML 错 / 顶层非数组四类可读报错
- 修 `scripts/new_session.py`：`--root` 指向文件或不可写时抛 `FileNotFoundError`；改为建目录前先判断并给一句话，写文件失败也报路径
- 补 UTF-8 出口：`gen_metrics.py`、`gen_preset_override.py`、`check_yaml.py` 在 GBK 控制台下会输出乱码
- README 的依赖声明改正：原文写「零第三方运行时依赖」，同一份文档的安装段却写「前提：pyyaml」——事实是 pyyaml 必需（配置读取）、解压器必需（会话日志）、Node 可选（守卫自检）。新版给依赖分级表（级别 / 用途 / 缺了会怎样 / 安装），并说明缺必需项时的行为是「一句修复提示」而不是 traceback；README_EN 同步
- `docs/adaptation.md` 的「先探测」加第 0 项：`env_check.py`；`README` 验收段与安装段都指向它
- CI：Windows 验收 job 增加 `env_check`；新增 Ubuntu/macOS × Python 3.9/3.12 的兼容矩阵（非阻塞，验收环境仍是 Windows）。本机没有 WSL/Docker，跨平台只能静态扫描 + 矩阵，**不声称已实测**
- 修 CI（自 2026-10-23 `99dc697` 起一直红，5 个提交没人发现）：`doctor 给出动作清单` 断言依赖本机真实数据，GitHub runner 上没有会话/账本 → actions 为空 → 失败。这类「空数据」正是新用户第一天的样子：
  - `doctor` 没有动作时补一条 `first-run`（先查环境、正常用一天），不再输出空清单
  - `doctor` 新增 `--dsh-home` / `--config` 并透传给 5 个子脚本：整个体检看哪份数据，一处指定、处处一致
  - 测试改用空 home 断言 `first-run`，不再依赖本机数据
- 无 HOME / USERPROFILE 时，解析链会提示「无法展开 ~」，而不是把字面 `~` 原样塞进错误信息
- 第二轮独立评审（同一评审员，基准 `ad926e2`）判「仍需修」，5 条全部落实：
  - **`env_check` 曾把坏配置报成「环境齐备」**：`--config` 坏掉时只打一行 stderr 警告，然后拿回退的 `~/.dsh` 当体检结果，stdout / `--json` / 退出码全绿。现在 config 单列必需项（解析失败 → rc=1，JSON 加 `config_error`），`dsh_home` 行注明「按默认 home 检查」
  - **模块检测只判 import**：同名假模块（能 import、没有 API）会被判可用。改为 `_probe_module(名字, *必需属性)`（`yaml.safe_load` / `zstandard.ZstdDecompressor`）；解压逻辑同时收进 `tg_common.decompress_bytes`，遇到无 API 的 zstandard 给一句话而不是 `AttributeError`
  - **`audit_pack` 把普通用户名当泄露**：`USER=user/test/dev` 在 Linux/macOS 上会触发几十条「高」。改为只认路径形状（`/home/<id>/`、`/Users/<id>/`、`Users\<id>\`），并加 `--local-id`；`SKIP_DIRS` 改按目录名比较（原先把 `.github` 当 `.git` 整棵跳过，`ci.yml` 从没被扫过）
  - **README 徽章与计数无人 gate**：徽章停在 v0.11.2 / 21 / 32，正文已是 v0.13.0 / 23 / 118。新增 `data/counts.json` 作单一真相，测试套件自校验「断言条数 = counts.tests」，`audit_pack` 校验徽章与 `CHANGELOG` 最新版本
  - **「纯标准库 / 换机器不用装依赖」的旧话**：`docs/benchmark.md`、`cache_audit.py` 文档头、`tg_common` 注释一并改成「核心计算纯标准库；读 config 要 pyyaml；读 .zstd 要 zstandard 或 zstd」
- 补充修正：`CONTRIBUTING` 的 job 数（3 个）、README/README_EN 的「Linux 只做探测」改成「Ubuntu/macOS 矩阵 + Linux 探测，均非阻塞」、zstd 安装建议把 `pip install zstandard` 放首位（Windows 不保证自带 `zstd.exe`）、`install_guard` 的 `cscript` 验证提示标注仅 Windows
- 解压器缺失的测试补第三组（PATH 剥空 + 模块注入）：此前只注入模块，本机 CLI 还在，`解压需要二选一` 那一支从未被执行
- 无 HOME 时 `new_session --root ~` 拒绝在 cwd 建字面 `~` 目录；测试套件在压缩工具完全缺失时返回 0（SKIP）而不是 2（此前与 CI 的「跳过不算失败」相反）
- 测试 98 → 119：18 个脚本 × 缺 pyyaml / 缺 zstandard / 两者都缺 三种注入（断言无 `Traceback`）、`env_check` 四条判定（齐备 / 缺 pyyaml / 缺解压器 / 坏配置）与退出码自洽、BOM + 空格 + 中文路径、无 HOME 可读报错且不建 `~` 目录、目标不可写可读报错且不留目录、普通用户名不误报、路径形状泄露能抓到、计数单一真相自校验、Python 3.9 语法可解析
- CI 矩阵的 Python 3.9 条目在 `ubuntu-latest`（26.04）上已不可用（setup-python 报 version not found）→ 运行时矩阵改为 3.10 / 3.12；「3.9 还能跑」改由 `ast.parse(feature_version=(3, 9))` 静态断言覆盖，不再靠口头承诺
- 平台相关用例（仅 Windows 的两条）在 POSIX 上记为跳过但**仍计入总数**，否则 `data/counts.json` 的计数单一真相在跨平台时会假红（第一次跑矩阵就是这么红的）
- 第三轮复核指出「徽章有检测、无门禁」：徽章不一致被判成「中」，CI 只看退出码 → 坏徽章仍然全绿，与 CONTRIBUTING 的「红了不能合」矛盾。现在徽章缺失/不一致、`counts` 的 version/skills/scripts 与实际不符一律算「高」（阻断）；并补一条用例：造一个 `tests-999` 徽章的最小仓库，断言 `audit_pack --root` 退出 1
- 补「同名假模块」回归用例：只 `raise ImportError` 的注入抓不到「能 import 但缺 API」，现在用只定义 `VERSION` 的 `zstandard.py` 跑两条分支（`env_check` 判缺失、`baseline` 给一句话）
- 测试 119 → 122

## v0.12.0 — 2026-10-24（公共层收敛：dsh_home 一个口径）
- 新增 `scripts/tg_common.py`：`utf8_stdio()` / `expand()` / `load_yaml()` / `load_config()` / `resolve_dsh_home(s)` / `sessions_root()` / `ledger_path()` / `add_common_args()` / `emit_json()`。原先这些事在 14 个脚本里各写一遍，`reconfigure` 出现 22 次
- **口径统一为**：`--dsh-home` / `--home` > `config.yaml` 的 `dsh_homes` / `dsh_home` > `$DSH_HOME` > `~/.dsh`；多 home 按真实路径去重。相对路径两种语义：config 里的值按 config 目录解析，CLI 参数按当前目录解析。文档见 `docs/operations.md` 第四节，自检 `python scripts/tg_common.py` 打印解析链
- **配置读不出来不再静默**：`load_config` 默认 strict——文件不存在属正常（返回 `{}`），文件在但 YAML 坏、或没装 pyyaml，一律报错退出并给排查提示。修前实测：同一份写坏的 config（`dsh_home: "D:\AI\DSH-NEXT"` 双引号写反斜杠）下，`baseline` / `cache_audit` / `compare_days` / `guard_stats` / `retry_patterns` / `schema_budget` 六个脚本 **rc=0 照常出报告**（读的是 `~/.dsh`），另外六个报错——同机两套互相矛盾的报表
- 各脚本旧口径与实际变化（变好/变坏都列全，这是本版最该被质疑的部分）：
  - `measure_modules.py`：`--list` 分支 `$DSH_HOME` 排在显式参数前；`--config` 默认空串所以默认不读 config。修后：默认 `config.yaml`，`--list` 与 `--session` 同一口径。本机 30 天：默认 **36 → 52** 个会话
  - `schema_budget.py`：`--list` 分支同样 env 压参数；现在走 `tg_common`，`dsh_homes` 与相对路径也生效
  - `retry_patterns.py`：完全不读 config，环境变量也没有，直接落 `~/.dsh`；新增 `--config`。本机 30 天：**49 → 99** 个会话
  - `compare_days.py`：**唯一一处顺序翻转**（旧 `$DSH_HOME` > config，新 config > env），并新增 `--dsh-home` 压回。同一条 `DSH_HOME=~/.dsh` 命令，旧版报 +169.4%、新版报 −0.9%，方向相反——这是本版行为变化最大的一处，用户升级后第一次对比结论可能不同
  - `baseline.py` / `cost_summary.py`：**新增** `$DSH_HOME` 通道（原本没有），语义是「配置缺 home 时才用它」，不是顺序翻转
  - `cache_audit.py`：给 `--config` 时不再丢环境变量；`--home` 保持**并集**语义（显式 home 排前，config 的 home 仍计入），改成覆盖会少算一份账本、成本与命中率偏小——自审时试过覆盖，已回退
  - `install_guard.py`：**新增** config 通道（原本只有 `--home` 与环境变量），本机目标从 `~/.dsh` 变成配置里的 `D:\AI\DSH-NEXT`（装到正确客户端）；config 坏时现在直接报错，不会静默指到另一个 home
- `cost_summary` / `compare_days` / `preflight` 补 `--dsh-home`（README 一直写「用 --dsh-home 可以临时压过配置」，但这三个脚本原先没有这个开关）
- `measure_modules.load_price_config` 与 `schema_budget.load_budget` 并进 `tg_common`，不再一个报「配置解析失败」、一个静默用默认
- `audit_pack.py`：跳过 `__pycache__` / `*.pyc` / `*.bak`（按后缀与 `.bak-` / `.bak_` 判定，不误伤 `bakery.py`）。修前跑一次 `py_compile` 或测试，包内文件数就从 75 变 93，自检结果随运行顺序漂移
- 私有 YAML 读法全部收编：`new_session` / `patch_snapshot` / `turn_cost` / `skill_usage` / `failure_analysis` 原本各自 `yaml.safe_load(...) or {}`，坏配置时行为不一致——`new_session` 会静默把对话夹建到 `~/tg-memory`（config 注释里写明的废弃目录），`patch_snapshot` 会去拍 `~/.dsh` 的补丁。现在统一走 `tg_common.load_config`，坏 YAML / GBK 存盘 / 顶层写成列表三种情况都报错退出
- `preflight.py` 的配置校验补 `UnicodeDecodeError` 与「顶层不是映射」两类：GBK 存盘的 config 不再抛 traceback，给的是「另存为 UTF-8」的可读提示
- 错误提示改掉一句不成立的引导：原先写「或用 `--dsh-home` 直接指定」，但严格模式下 `--dsh-home` 不能绕过配置读取（实测 `baseline --dsh-home X --config 坏配置` 仍退出），现在明确写「先改好配置」
- `docs/operations.md` 的坏配置示例改成 `C:\dsh-next`：原示例用了本机绝对路径，触发 `audit_pack` 的绝对路径规则（2 条「中」），CI 只对「高」失败，等于放行了
- 测试 32 → 98：6 项锁 `dsh_home` 优先级 + 4 项端到端（只给 config / 退回 `$DSH_HOME` / `--dsh-home` 能压 / 坏配置必须非零退出）+ **14 个带 `--config` 的脚本 × 3 类故障注入**（坏 YAML / 顶层非映射 / GBK 编码，且与 `scripts/` 实际 argparse 对账，漏一个脚本就红）+ 坏配置下 `new_session` 不许建目录 + **10 项静态检查变异用例**（`getenv` / `setdefault` / 字符串拼接 / 别名 / 下标 / 注释不误报；`join` / f-string / `Path` 除法 / JSON 键不误报）
- 验收：测试 98/98、`audit_pack` 0 问题（高 0 / 中 0）、`gen_metrics --check` 通过、`verify_guard` 15/15；**显式传 flag 的路径**用固定 home（14 个历史会话 + 真实 ledger）跑旧/新两版，`cost_summary` / `failure_analysis` / `turn_cost` / `skill_usage` / `schema_budget` / `cache_audit` / `preflight` / `baseline` 逐字节一致（`retry_patterns` 因新增 `--config` 除外）。**默认路径是有意变更的**（见上面 49→99、36→52），不适用「逐字节一致」这条

## v0.11.4 — 2026-10-24（顽固循环率 + 协议规则落在常驻层）
- `guard_stats.py`：新增「参数链」口径——按 `(工具, 参数哈希)` 归链，取链内最高提醒次数，输出停在 2 / 升到 4 / 升到 6 的分布与**顽固率**（到第 6 次仍在重复的链占比）。这是「提醒没改变行为」的唯一可观测代理；口径写明不能证明之后是否止损
- `tg-loop`：协议硬检查节补「位置提醒」——本节同时在宿主 `AGENTS.md` §5 常驻一句话，技能是待机状态，只写在技能里等于没写
- 本机配套（不入库）：`AGENTS.md` §5 增一句协议纪律（+192 B/步）；`AGENTS-tasks.md` §1 增 siyuan 按需激活通道（按需读，不占每步）；`mcp_connector.json` 快照清掉 3 处残留 `bearerToken`（anysearch 旧值 1 + memos 已删连接 2）
- 验收：测试 32/32、`audit_pack` 0 问题、`guard_stats` 新字段实测（1 条链、顽固率 0%）

## v0.11.3 — 2026-10-24（日志定位 + 测试时间炸弹）
- `guard_stats.py`：`--dsh-home` 未给时改为读 `config.yaml` 的 `dsh_home`（同 `schema_budget`），再退回 `DSH_HOME` / `~/.dsh`。多套客户端时官方版写 `~/.dsh`、发行版写自己的 home，默认值猜错会误报「没有守卫日志」
- 测试修时间炸弹：`put_session` 落盘后把 mtime 归零到当前时间。`zstd -f src -o dst` 会继承源 fixture 的 mtime（2026-10-22），隔天再跑 `--days 1` 的 12 项用例全红——不是回归，是测试自己过期
- 实测：`D:\AI\DSH-NEXT` 下守卫日志已写入（`ops_bash` 第 2 次，argsLen 83），`guard_stats.py` 直接读到正确的那份
- 验收：测试 32/32、`audit_pack` 0 问题

## v0.11.2 — 2026-10-23（守卫计数 + 协议硬检查 + 重工具隔离）
- 循环守卫新增命中计数落盘：每次真正追加提醒时往 `<DSH_HOME>/storages/loop-guard/events.jsonl` 写一行（工具名、第几次、参数哈希与长度，**不写参数内容**）；`logFile` 可改路径或关闭；写失败全程 fail-open，不影响工具调用
- 新增 `scripts/guard_stats.py`：读 events.jsonl 出提醒次数 / 按工具 / 按日；无日志退出码 2 并给排查提示；坏行跳过
- `verify_guard.mjs` 13 → 15 项：新增「命中日志条数 = 提醒次数」「日志只含元数据、不含参数内容」；测试日志写临时目录，不污染真实文件
- `install_guard.py`：复制前逐文件比对（原来只比 `index.js`，README / patch 的改动永远同步不过去）
- `tg-loop` 新增「协议错误的硬检查」节：edit/write 前三问（本会话读过？只有我在改？工具名在清单？），三类报错各给唯一正确动作，并挂 `failure_analysis --protocol-only` 自检
- `tg-loop` 新增「命中统计」节：读法（第 2 次反复 = 阈值有效但仍在踩同一条路；占到第 6 次 = 提醒没改变行为，该换通道）
- 隔离落地：`siyuan` MCP 整块 deny（31 个工具 / 26.6 KB，近 7 天 83 会话只调用 9 次且全是 `http_request`）；用 `mcp_connector_policy` server 级 deny（revision 1，可回滚到 revision 0），需要时用 `mcp_connector_session_tools` 按会话激活 30 分钟
- 配置模板补 `loop_guard.logFile`
- 验收：测试 29 → 32 项全过、`audit_pack` 0 问题、`verify_guard` 15 项通过

## v0.11.1 — 2026-10-23（需求澄清迁出）
- `clarify-needs/` 整体迁出到独立仓库 [dsh-clarify-tier](https://github.com/YokosWY/dsh-clarify-tier) 的 `skill/clarify-needs/`，TG 不再维护它
- TG 侧同步：README / README_EN 目录树去掉该行并加指向；`docs/concepts.md` 第五组术语加迁移说明
- 理由：它是常驻技能、有独立版本节奏，跟着 TG 发版只会互相拖累

## v0.11.0 — 2026-10-23（体检入口 + schema 预算 + CI）
- 新增 `scripts/doctor.py`：一条命令把 preflight / schema_budget / skill_usage / failure_analysis / turn_cost 汇成一页，按收益排序给 2–4 条动作（每条带命令与预估）；`--json` 给机读，`--apply` 只做补丁快照 + 首日基线两件可回滚的事，`--print-actions` 只演不做
- 新增 `scripts/schema_budget.py`：工具 schema 按服务（`mcp:<server>`）与工具族归因，出 KB / 占比 / 单工具 Top；`--check` 对 `schema_budget_kb` 卡预算并退出 1
- `preflight.py`：新增第 9 项 schema 预算体检（只读、不计入冲突、不改退出码）；新增 `--json`（含 rows / cache / schema / verdict）
- `config.example.yaml` + `README` 配置段：新增 `schema_budget_kb`（默认 160，0 = 关闭）
- 工程化：`tests/run_tests.py` 与 `audit_pack.py` 固定 UTF-8 输出（GBK 控制台下曾以 `UnicodeEncodeError` 崩掉，看起来像测试失败）；子进程传 `PYTHONIOENCODING=utf-8`
- 新增 `.github/workflows/ci.yml`：Windows 上跑测试 29 项 + audit_pack 作为门禁；Linux 只做探测、不阻塞（验收环境是 Windows）
- 测试 23 → 29 项：新增 schema 归因 / 工具总数 / 超预算与预算内退出码 / doctor 动作清单 / doctor 缺数据不崩；新增 fixture `schema_session.jsonl`
- 文档同步：README 脚本 18 → 20、测试 23 → 29、加 CI 徽章与 doctor 示例；README_EN 同步；`docs/operations.md` 新增第三节「体检入口」；`tg-cost` / `tg-context` / `tg-router` 挂上新入口
- 边界：`clarify-needs/` 明确为姊妹项目（独立版本号、独立 CHANGELOG），不并入 TG 的 10 个技能与 20 个脚本
- 口径：schema 归因的 token 换算按 0.4 token/字节粗估，只作量级；省的是每步重发量，与缓存命中率分账
- 验收：`audit_pack` 0 问题、测试 29/29（未设 `PYTHONIOENCODING` 的 GBK 控制台下同样通过）

## v0.10.20 — 2026-10-23（注入链分层 + clarify-needs 五档）
- `tg-router` 新增「注入链体检」节：宿主 `AGENTS.md` 常驻 14.7 KB → 5.4 KB 的分层基线、任务说明下沉 `AGENTS-tasks.md`（按触发读）、两份副本 md5 校验与四文件体积复测命令
- `clarify-needs` v3.0：四档 L0–L3 → **五档 off/low/high/max/ultra**，模式并入档位，默认 `high`（详见 `clarify-needs/CHANGELOG.md`）
- `docs/concepts.md` 同步档位术语

## v0.10.19 — 2026-10-23（README 按公开框架整改）
- README 重排为公开仓库框架：居中标题 + 徽章行 + 中英双语入口 → 亮点 → 你会看到什么（四段真实输出）→ 安装（含升级与卸载）→ 使用说明（场景表）→ 组成 → 实现结构 → 验收情况（含案例）→ 配置 → 已知边界 → 安全与隐私 → 开发 → 文档 → 版本 → 许可
- 新增 `README_EN.md`：英文版（亮点、输出、安装、用法、结构、验收、边界、安全、许可）
- 补缺：实现结构树、数据流说明、验收清单、安全与隐私、开发流程、独立声明
- 不加 Star 趋势图（按约定）
- 修 `audit_pack.py` 的 Windows 编码问题：调用 `gen_metrics --check` 时显式 `encoding="utf-8"`，不再因继承 `PYTHONIOENCODING=utf-8` 而抛 `UnicodeDecodeError`
- 验收：`audit_pack` 0 问题、测试 23/23

## v0.10.18 — 2026-10-22（对外口径收口）
- README 删除「还测到三件反直觉的事」整节：那是我在一台机器上的工作流统计（失败轮次占 48.9%、协议错误 27.5%、技能 0 调用），对公共读者既不可复现也不具代表性
- 同类数字从 `docs/operations.md`、`skills/tg-loop`、`skills/tg-ops` 移除，改为陈述做法（协议错误只能从顺序上避免）
- `docs/benchmark.md` 第九节会话 id 改为「样本 A/B/C」，不暴露本机会话标识
- `docs/benchmark.md`、`docs/concepts.md` 去第一人称，改陈述句
- README 实测节标题改为「案例：一台机器上的改前改后」，明确不是对所有环境的承诺
- 验收：`audit_pack` 0 问题、测试 23/23

## v0.10.17 — 2026-10-22（README 补全信息）
- README 重写为完整版（236 行 / 12 KB）：特性 8 条、实测 + 三件反直觉发现、三个快速开始示例（账本 / 拆会话 / 任务成本）、技能与脚本完整清单（10 / 18）、插件说明、完整 config 示例（含 loop_guard 与 prices）、自检三条命令、口径定义表、边界清单、版本摘要
- 保留九段骨架与数字单源块；表格由 `gen_metrics.py --write` 重新生成
- 验收：`audit_pack` 0 问题、测试 23/23、`gen_metrics --check` 通过

## v0.10.16 — 2026-10-22（P2-b：文档收敛 11 → 5）
- 合并：`glossary.md` + `mechanisms.md` → `concepts.md`；`guard.md` + `protocol.md` → `operations.md`；`portable-vs-local.md` 并入 `adaptation.md` 附录；`cache-hit.md` 并入 `benchmark.md` 第十节
- 归档：`design.md`（色板与版式）→ `_history/design-20261022.md`，不在 `docs/` 露出
- 结果：`docs/` 只剩 5 篇（benchmark / principles / operations / concepts / adaptation），README 文档段加「评估者 / 使用者 / 贡献者」三行读法
- 引用全部改指新文件：README、`skills/tg-context`、`skills/tg-loop`、`skills/tg-ops`、`clarify-needs/README.md`、`scripts/cache_audit.py`
- `audit_pack.py`：文档引用检查跳过 `CHANGELOG.md`（历史条目提旧名属当时事实），其余文件仍严格校验
- 验收：`audit_pack` 0 问题、测试 23/23、`gen_metrics --check` 通过

## v0.10.15 — 2026-10-22（P2-a：协议纪律）
- 新增 `docs/protocol.md`：改文件前 30 秒的三条硬规则（改前必读、一次一个文件、工具名对照清单）+ 清单 + 自检命令
- `failure_analysis.py` 新增 `--protocol-only`：只列 `file has not been read` / `file changed since it was read` / `old_string/new_string` / `unknown tool` 四类。本机近 3 天：协议错误 **65 次 / 236 次失败（27.5%）**，其中 edit 44、write 14、未知工具 7（含把工具名打成 `opt_bash`）
- `tg-ops`：标准动作改为先 `patch_snapshot --snapshot`，并挂回滚命令与协议清单；`tg-loop`：失败重试前先分类，协议错误不重试逻辑
- `tests/run_tests.py` 扩到 23 项（协议错误单列与工具归类两项）
- 数据口径更正：先前「近一半花费」指**有失败调用的轮次**（48.9%），不是协议错误单独造成

## v0.10.14 — 2026-10-22（失败重试分析）
- 新增 `scripts/failure_analysis.py`：失败调用数、受影响轮次、这些轮次的花费占比、按工具与「报文首行」分类的 Top 清单（数字归一、路径打码）
- 本机实测（近 3 天，72 会话 / 736 轮）：失败调用 236 次，落在 **142 轮（19%）**，这些轮次花 **¥21.42 = 窗口总花费 48.9%**
- Top 报文：`edit: file changed since it was read` 22、`ops_bash Traceback` 20、`edit/write: file has not been read` 29、`ops_bash timed out` 6、`unknown tool "bash"` 6 —— 大头是**工具协议错误**（没读就改、并发改同一文件），不是配置问题
- `tests/run_tests.py` 扩到 21 项（新增失败调用/工具归类/失败轮次三项）
- `README` 脚本表与徽章同步（18 个脚本）

## v0.10.13 — 2026-10-22（P1：任务成本、技能埋点、补丁回滚）
- 新增 `scripts/turn_cost.py`：按「一条用户指令 = 一轮任务」聚合，出每轮 CNY、步数、上下文/步、失败调用数，再给中位/均值/P90 与总花费。本机实测（近 1 天 347 轮）：每轮中位 ¥0.0506、均值 ¥0.0872、P90 ¥0.1979；有失败调用的轮次 104/347
- 新增 `scripts/skill_usage.py`：统计 `skill` 工具调用与 `/名字` 直接调用，并标出「已装/待机」与从未被调用的 `tg-*`。本机实测：tg-gui / tg-loop / tg-orchestrate 近 7 天 0 次
- 新增 `scripts/patch_snapshot.py`：`--snapshot` 拍补丁、`--list` 列快照、`--diff` 对比、`--restore latest` 回滚（回滚前先备份当前、校验 YAML 合法）；本机已拍第一张快照并验证 diff/restore 往返
- `tests/run_tests.py` 扩到 18 项（新增任务成本、技能埋点、快照回滚三项）
- `README` 脚本表与徽章同步（17 个脚本）

## v0.10.12 — 2026-10-22（P0：回归测试 + 数字单源）
- 新增 `tests/run_tests.py` + `tests/fixtures/`：11 项断言（解压回退、计价与手算一致、外来日志拒绝、换参/失败重试口径、插件名漂移、首日基线可写可比、文档数字一致、包自检），纯标准库，不需要 pytest
- 新增 `data/metrics.json` + `scripts/gen_metrics.py`：README 与 `benchmark.md` §9.5 的汇总表由 json 生成（`--write`），`--check` 由 `audit_pack.py` 调用；手改文档数字会被抓出（已做变异测试：改一个数字 → 测试红）
- `audit_pack.py` 增加数字一致性检查；`README` 补测试入口与脚本表
- 修 Windows 配置崩溃：`config.yaml` 里写 `C:\Users\...`（双引号）会让 YAML 解析抛栈；`preflight.py` / `cost_summary.py` 现在给一行可操作提示并以退出码 2 结束
- 版本号整理：另一个会话的「预检加缓存体检」原写成 v0.10.10（与已发布 tag 撞号），改为 v0.10.11

## v0.10.11 — 2026-10-22（预检加缓存体检）
- `preflight.py` 新增第 8 项**只读缓存体检**：读账本算命中率、未命中/步、每步上下文、价差 k、未命中成本占比，给三档判定（值得做 → 关自动改写历史 / 边际 → 缩前缀与步数 / 不值得做 → 价差太小）
- 体检**不参与冲突计数、不改退出码**；无账本降级为「正常用一天再看 / `cache_audit.py --jsonl`」；异常只打一行「跳过」，不阻断预检
- 复用 `cache_audit.collect_ledgers`（多 home 合并 + 真实路径去重），阈值与 `docs/cache-hit.md` §2 同源
- 本机三档实测：k=50 →「值得做」；k=5 →「边际」；k=2 →「省不到 3%」

## v0.10.10 — 2026-10-22（漂移检测修正）
- 修 `preflight.py` 两处漏判/误报：① 没有 `preset-*` 覆盖块时整段 profile 被 `continue` 跳过，插件名漂移查不到；② 把 `desktop.bak-*` 这类备份目录当成生效配置扫描，报出无效漂移
- 端到端验证：假 home（patch 引用 `@test/ghost-plugin`、组合里没有）能报出「插件名漂移」并以退出码 1 结束；真实 home 只剩 `preset-minimal` 一条预期提示

## v0.10.9 — 2026-10-22（首日基线、会话检索、插件名漂移）
- 新增 `scripts/baseline.py`：不需要 ledger，直接记静态量（起始上下文 / 工具 schema / 技能目录单条 / 步轮比，取最近 5 个 ≥5 步会话的中位数），`--write` 记基线、`--compare` 隔天比对；解决新装当天「什么都没发生」
- `measure_modules.py`：新增 `--list`（`--days`/`--limit`）列出最近会话（id、时间、步、轮、CNY、schema、首条消息），`--session` 改为非必填
- `preflight.py`：新增「插件名漂移」——patch 里引用的 `@scope/pkg` 不在组合 `cordis.yml` 里就报出来（插件被删或改名会导致启动失败）。实测 patch 引用 38 个包当前全部命中；模拟改名可稳定报出
- 本机实测：基线五会话中位 = 起始 54 k / schema 154 kB / 目录 9.8 kB；把基线改成旧值后 `--compare` 输出 −45.2% / −50.6% / −77.0%，与实测表一致
- `README` 脚本表与徽章同步（脚本 13 个）

## v0.10.8 — 2026-10-22（缓存命中：provider 无关化）
- 新增 `docs/cache-hit.md`：三类缓存判据（自动前缀 / 显式 TTL / 无缓存）、三个公式、**价差倍数 k 决定一切**（k=2/10/50 时 miss 1.3% 分别占成本 2.6%/11.6%/39.7%）、打穿前缀的 7 个来源与 DSH/Claude Code/Codex 映射、无账本测法
- 新增 `scripts/cache_audit.py`：只读、纯标准库；三源自动降级（DSH ledger / 任意 jsonl / 手工模板）；输出命中率、未命中/步、按 k 折算的未命中成本占比、前缀重建事件（「全量重算」input 占比 >50%、「前缀突变」上下文 >1.5 倍两种判据）；显式 `--ledger/--home` 不存在直接报错，不回退到别的 home
- 口径更正：本机 `config.yaml` 价差 k=50（$0.15 vs $0.003/M），未命中 1.3% 占上下文成本约 39%；此前按 k=10 估的 8.6% 作废
- 首跑（本机近 7 天）：命中率 98.73%、未命中 2,953/步、未命中成本占比 39.1%（按 config 价格表口径，k=50）
- `README` 快速开始/脚本表/文档列表、`portable-vs-local.md` A 表、`tg-context` 同步登记；`audit_pack.py` 0 问题

## v0.10.7 — 2026-10-22（失败重试口径）
- `retry_patterns.py`：新增失败判据——非零退出码（`[exit code: N]`）或以 `Error` / `Traceback` / `ENOENT` 开头；只有串内至少一次报错的才算「失败重试」
- 修 bug：`tool/result` 的 callId 在 `data.message.source.callId`，不在 `data.toolCallId`；v0.10.6 第一版映射取错字段，失败数恒为 0
- 首跑（近 7 天 73 个会话）：换参候选 388 处，其中失败重试 **75 处 / 23 个会话**（ops_bash 57、pwsh 10），字面重复 6 处 —— 失败后换参数再试是主形态，比例约 12:1
- `README` 脚本表与 `tg-cost` 数据源段同步

## v0.10.6 — 2026-10-22（换参重试观测脚本）
- 新增 `scripts/retry_patterns.py`：按会话切段，统计「同工具连续 ≥N 次且参数各不相同」（换参重试）与「相邻参数完全相同」（字面重复）两种串数，只观测不拦截；支持 `--days` / `--min-run` / `--json`
- 首跑（近 7 天 73 个会话）：换参 388 处 / 字面重复 6 处，比约 65:1 —— 说明重复的主体不是完全相同的调用，循环守卫的覆盖面对的是少数派
- 口径提醒：388 是**候选上限**，含正常的多步操作（连续跑 build/test/lint 也会进这个口径），只作趋势观察
- `README` 脚本表与 `tg-cost` 数据源段同步登记；脚本徽章 10 → 11

## v0.10.5 — 2026-10-22（多机账本、相对路径、并发读、参数泄露）
- `cost_summary.py`：多 `dsh_homes` 按 **ledger 真实路径去重**，同一份账本被软链或列两次只算一次；文档里"按会话 id 去重"的说法作废（账本只有日粒度）
- `cost_summary.py` / `preflight.py` / `new_session.py`：相对路径改为按 `config.yaml` 所在目录解析，兑现 `config.example.yaml` 的承诺
- `cost_summary.py`：`read_ledger` 加异常处理，读到写了一半的账本时提示重跑，不再抛 traceback
- `preflight.py`：新增「覆盖块漂移」检查——patch 覆盖了组合文件里已不存在的 preset，或组合里新增的 preset 没被覆盖，都会报出来（本机实测：`preset-minimal` 未覆盖）
- 循环守卫：`argumentsPreviewChars` 默认 160 → **0**（只报参数长度，不把内容写进上下文）；`exclude` 补 `http_request` 与 3 个 `mcp_connector_*` 写操作工具。已同步到本机安装副本（带备份，`check_yaml.py` 通过）
- 文档：`adaptation.md` / `tg-cost` 改去重口径；`benchmark.md` 第九节加"勿把下限当总收益"；`guard.md` 补参数预览限制

## v0.10.4 — 2026-10-22（解压零外部依赖）
- `measure_modules.py`：`zstd -dc` 失败或缺命令时回退到 Python `zstandard`（逐帧解，兼容多帧文件）；两者都没有才退出，并给出二选一的安装提示
- 本机实测：去掉 PATH 里的 zstd、只用 `PYTHONPATH` 指向 `zstandard`，同一会话输出与原先一致（126 步 / ¥0.0378 每轮）
- `README.md`：前提改为「`zstd` 命令或 `pip install zstandard` 二选一」

## v0.10.3 — 2026-10-22（非本机可用性：价格、日志格式、阈值）
- `measure_modules.py`：价格不再写死——新增 `--config`（读 `prices_usd_per_mtok` 与 `rate`）与 `--rate`，默认仍是 flash 价，但输出与 `--json` 都会标「默认 flash 价（估算）」或配置来源
- `measure_modules.py`：遇到非 DSH v4 日志（缺 `request/header` / `assistant/message`）直接说明「不支持的日志格式」并列出实际类型，不再笼统报「没有 usage 记录」；缺 `zstd` 命令时给安装提示
- `gen_preset_override.py`：新增 `--window`（上下文窗口 token），pruner 阈值默认取窗口大小；`--pruner-threshold 0` 即跟随 `--window`。阈值不再只按本机 200 k 写死
- `config.example.yaml`：补 `rate: 7.1`，并把价格表说明改为「measure_modules 折算用，有实付以实付为准」
- `preflight.py`：补丁条目探测补 `preset-ptc` / `preset-cordis`
- `new_session.py`：`--root` 缺省读 `config.yaml` 的 `memory_root`，不再与配置脱节
- `CONTRIBUTING.md`：删掉本机代理地址，改为「直连失败时自备代理，值不进仓库」
- 文档：README 与 `benchmark.md` 标样本口径（单机、flash、200 k 窗口）；`principles.md` 的 160/56 计数标「本机样本」；`adaptation.md` §C 明确脚本只认 DSH

## v0.10.2 — 2026-10-22（空环境接入收口）
- `preflight.py`：冲突清单补 `tg-memlib` / `tg-isolate` / `tg-loop`（7 → 10 个技能）；此前这三个同名已存在时预检报「放行」，复制时静默覆盖
- `preflight.py`：缺 pyyaml 从「打印一行后按默认 `~/.dsh/skills` 探测」改为直接拒绝（退出码 2），避免查错目录还给出结论
- `cost_summary.py`：无账本时列出查过的路径并说明「刚装好属正常，先用一天」，退出码仍为 1
- `README.md`：安装段补 pyyaml 前提、改三行配置、预检三档退出码；体检挪到「先用一天」之后；「不支持」补一条「补丁不随包」

## v0.10.1 — 2026-10-22（数据脚本 Windows 控制台修复 + 实测记录）
- `cost_summary.py` / `measure_modules.py`：`¥` 改 `CNY`，Windows 默认 GBK 控制台不再抛 `UnicodeEncodeError`（此前第一次跑体检直接崩，退出码 1；v0.7.9 只修了 `audit_pack.py`）
- `measure_modules.py`：模块表「助手输出」tokens 列恒为 `0.00M`（`tokens` 字典只含上下文模块）→ 补 `out_tok`，与费用占比自洽
- `measure_modules.py`：错误信息区分「文件无可解析事件（zstd 失败/截断）」与「无 usage 记录」
- 实测（真实 ledger，`${DSH_HOME}` 下 cost-meter）：8 天 271.02 CNY / 10999 次；`byProviderModel` token 和与日合计差 0；高价日 10-18 可由 `workbuddy-ai:gpt-6-astra` 66 次 / 101.00 CNY 解释

## v0.10.0 — 2026-10-22（长期记忆库分层）
- 新增 `skills/tg-memlib/`：CORE 永久层（≤4KB，每次必读）+ INDEX 路由 + 分级提炼判据 + 手工晋升/降级 + 冲突覆盖写法 + 收尾入库链与三项验收
- 与 `tg-memory` 明确分工：短期对话夹 vs 跨会话长期/永久
- 关键取舍：**不做 tier 字段、自动晋升、时间衰减**——无引用计数埋点，自动判定会误升误删；先人工跑顺，有埋点再谈
- `tg-router` 五域表、`README` 技能表/场景表/目录结构登记（9 → 10 个技能）
- 移植安全：路径全用 `${MEMLIB_ROOT}` 占位符，正文不含本机绝对路径

## v0.9.0 — 2026-10-22（高费低频工具隔离模板）
- 新增 `skills/tg-isolate/`：把「GUI 区」抽象成通用流程 —— 测量 → 判定 → 生成独立 preset → **询问是否安装** → 落补丁 → 验证回滚；适用于 GUI/浏览器/MCP/音视频/大型文档等重工具，不限 GUI
- 新增 `skills/tg-isolate/templates/preset-isolate.yml`：可直接改占位符的 preset 骨架（含 isolate/group 必需写法、基础段、压缩段）
- `tg-router` 五域表与 `README` 技能表/场景表登记入口
- 落地提示：宿主 skills 目录建议用 **Junction** 挂载（Windows 下 `ln -s` 报 EPERM；PowerShell `New-Item -ItemType Junction` 可用）。TG 正文不含本机路径

## v0.8.0 — 2026-10-22（循环守卫接入）
- 新增 `plugins/dsh-loop-guard/`：插件源码（与上游 `local-plugins` 字节一致，`verify_guard.mjs` 会校验哈希）
- 新增 `skills/tg-loop/`：命中后的三处置、阈值调参、安装与验证入口
- 新增 `scripts/install_guard.py`（幂等安装/卸载，默认 dry-run，改 package.json 前备份）与 `scripts/verify_guard.mjs`（13 项离线断言）
- 新增 `docs/guard.md`：事件点、参数哈希判据、阈值语义、与 AGENTS「反重复与止损」的分工、四条已知限制
- 插件版本 0.1.1：`package.json` 描述改为准确表述（只追加提醒，不阻断调用，不自动派子代理）
- 实测：`verify_guard.mjs` 13 项全过；`install_guard.py --status` 在已装环境识别为「已存在」，不重复写入

## v0.7.9 — 2026-10-22（preset 覆盖收口）
- 新增 `scripts/gen_preset_override.py`：从组合配置整段重生成 preset 覆盖块，升级后可一条命令对齐
- `preset-ptc`、`preset-cordis` 也改为整段覆盖，pruner 阈值对齐 200000（此前切过去会回到 8192）
- `audit_pack.py` 成功行改用 ASCII 标记，Windows GBK 控制台不再抛 UnicodeEncodeError

## v0.7.8 — 2026-10-22（改写成本）
- 新增 `benchmark.md` 第四节：pruner 改写历史导致的前缀重算实测（12 次触发 vs 0 次，未命中/步差 3–4 倍）
- P4 补充裁剪的盈亏条件：条目大小 × 剩余步数 > 50 × 前缀，才值得改写一次
- 配置口径：单条体积在写入时处理（工具输出落盘）；事后裁剪阈值设到不触发；压缩留 0.8 作安全阀
- `adaptation.md` 阈值表改为安全阀口径（128k 0.75–0.85 / 200k 0.70–0.80 / 1M 0.80–0.90），与 `benchmark.md` 对齐
## v0.7.7 — 2026-10-22（机制命名体系）
- 五个机制统一改名：重放口径→存活计息、反事实重算→影子重放、待机库→旁路池、四档澄清→分级澄清、前沿整轮→前沿扫描
- 「四把刀」一并改为「四项杠杆」
- 术语表保留旧称对照；`tg-context`、`tg-cost` 的技能描述同步更新（模型目录已生效）

## v0.7.6 — 2026-10-22（准入协议）
- 机制 M4 从「冲突先问」升级为「准入协议」：按不可逆程度分放行 / 待确认 / 拒绝三档，退出码 0 / 1 / 2 一一对应
- `preflight.py` 落实三档判定，并修复 dsh_home 缺少 profiles 目录时的崩溃
- 三档实测：干净环境 0、本机冲突环境 1、技能目录不可用 2
- 术语表与 README 同步更新

## v0.7.5 — 2026-10-22（文案专业化）
- 章节标题去口语化：成本结构 / 组成与集成 / 快速开始 / 环境适配 / 能力边界 / 适用范围
- 正文表述改为陈述式：去掉「所以省钱的思路很朴素」「体检输出长这样」等口语
- 提要统一表述为「基于用量日志的上下文成本治理」
- `docs/mechanisms.md` 导语改为「由实际调优过程沉淀而来，均附带验证方法与失效条件」

## v0.7.4 — 2026-10-22（视觉）
- 配色改为「纸 / 墨 / 账簿蓝 / 印章红」：纸张色底、深墨表头、蓝色标题细线、红色只标关键数字
- 章节标题去编号，改用双细线；因为章节之间不是步骤，编号会误导
- 去掉文案里的中点分隔（页脚与封面元信息）
- README 顶部加四个徽章
- 新增 `docs/design.md`，写清色板与版式，便于后续维护

## v0.7.3 — 2026-10-22（新机制梳理）
- 新增 `docs/mechanisms.md`：六个自研机制，逐个写「解决什么 / 怎么运作 / 怎么验证 / 何时失效」
- README 增加「六个自研机制」章节，后续章节顺延

## v0.7.2 — 2026-10-22（术语表）
- 新增 `docs/glossary.md`：按「计费口径 / 技能与工具 / 上下文处理 / 流程产物 / clarify-needs」五组解释自造词
- README 增加「术语表」章节（14 个最常用词），并加「几个容易混的词」对照
- 待机库、常驻税、重放口径、覆盖块、影子实例、前沿等词第一次有了正式定义

## v0.7.1 — 2026-10-22（去敏与文风）
- 文案整体改写：去掉排比腔与破折号堆叠，句子改短，读起来更像人写的
- 去敏：示例路径改通用写法；检查器不再硬编码本机用户名，改为读环境变量

## v0.7 — 2026-10-22（首次发布）
- LICENSE 定为 MIT；仓库收录 `clarify-needs/`（预发）
- `.gitignore` 增加 QA 产物

## v0.6 — 2026-10-21（实测 + 发布准备）
- 新增 `docs/benchmark.md`：真实会话逐 step 复算，模块级花费归因
- 实测结论：每轮降 24–48%（长会话 44–48%，短会话 24–39%）
- 关键发现：技能目录在长会话占 42%（注入 53 次 × 42 KB）；输出/推理已成为最大单项（37%）
- README 增加"实测效果"章节
- 新增 `scripts/measure_modules.py`：一条命令复算任意会话的模块花费与反事实对比
- 发布准备：`.gitignore`（排除本地 config / 历史备份 / 本地挂载）；README 增加发布检查清单
- 数字已用脚本复核（原报告 44.2% → 核实 42.5%，区间 23.7–46.1%）

## v0.5 — 2026-10-21（全量安装）
- 本机装齐 7 个技能：router / cost / context / memory / orchestrate / gui / ops（全部 junction）
- 记忆根 `D:\AI\Knowledge\TG-memory`（新建，与 AI-KB 分开）
- 新增 `scripts/audit_pack.py`：一键自检结构 / frontmatter / 交叉引用 / 陈旧词 / 可移植性
- `tg-router` 移除「本机未装」标记

## v0.4 — 2026-10-21（当前）
- 技能名统一 `tg-` 前缀，入口改名 `tg-router`（原 `tg` / `tg-metron`）
- 上线安装：本机装 `tg-router` / `tg-cost` / `tg-context` / `tg-orchestrate` / `tg-gui`；
  `tg-memory` / `tg-ops` 暂不装（路由器里已标注「本机未装」并给兜底）
- 替代 `skill-router`（已下线，源码归档 `_archive\skill-router-20261021-2320`）
- 新增 `scripts/preflight.py` 冲突预检（只读、有冲突退出码 1）
- 文档改为「包内 docs/」写法，兼容单技能目录安装
- 修正 `preflight.py` 默认记忆根 `~/metron-memory` → `~/tg-memory`

## v0.3 — 2026-10-21
- 新增 `docs/adaptation.md`（7 维度自适应矩阵 A–H），每个技能开头加「自适应」段
- 移除 `tg-guard`（安全合规属本地约束，不随包分发）
- 新增「冲突处理：先问，不要直接改」硬规则

## v0.2 — 2026-10-21
- 全量去绝对路径：`${DSH_HOME}` / `${TG_MEMORY}` / `config.yaml` 注入
- 新增 `config.example.yaml`、`scripts/cost_summary.py`、`scripts/check_yaml.py`、`scripts/new_session.py`
- 新增 `docs/portable-vs-local.md`（通用 / 需配置 / 本机特化三段）

## v0.1 — 2026-10-21（草稿）
- 由当天的 token 审计实战提炼：8 个技能 + `docs/principles.md`（原理十二条）
- 首次系统化：重放口径、压缩回本曲线、过程指标 vs 结果、常驻最小化、目录级权限等
