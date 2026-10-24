# 变更记录

> 命名沿革：Tessera（拉丁语「凭证」）→ Metron（希腊语「度量」）→ **Token-Governance (TG)**。
> 前两个因「不够直白 / 偏统计」弃用；正文已全部改为 TG，仅此处留档。

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
