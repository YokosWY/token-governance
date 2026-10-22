# 变更记录

> 命名沿革：Tessera（拉丁语「凭证」）→ Metron（希腊语「度量」）→ **Token-Governance (TG)**。
> 前两个因「不够直白 / 偏统计」弃用；正文已全部改为 TG，仅此处留档。

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
