# AGENTS.md · 本仓库对自动化代理的硬约束

本文件给在本仓库工作的 AI 代理（含 `@YokosWY-Agent` / dsh-superagent 的 worker）读。人的约定在 `CONTRIBUTING.md`，这里只写「不做就会被 CI 或评审打回」的硬约束，和几条比 `CONTRIBUTING.md` 更严格的要求。

## 1. 开工前

1. 先读 `CONTRIBUTING.md`、`docs/operations.md`、`docs/concepts.md`。
2. 跑一次基线：`python tests/run_tests.py` 与 `python scripts/audit_pack.py`，确认改前是绿的。改前就红，先报，不要顺手修。
3. 只在本仓库内读写。不读 `_local/`、`_history/`、`config.yaml`、`~/.dsh`，不用本机绝对路径。

## 2. 数字只有一个真相

- `data/counts.json` 是脚本数、技能数、断言数、版本的唯一真相。`README.md` 徽章、正文计数、`README_EN.md`、`.github/workflows/ci.yml` 的 job 名、`CONTRIBUTING.md` 里的数字，都必须与它一致。
- **测试数量变了才改它**（`tests` 键）；改它必须同时改徽章与正文，且提交说明里写清「断言 N → M，因为加了什么」。
- 任何情况下不要为了让检查变绿去改 `data/counts.json` 的数值。文档向真值对齐，不是真值向文档对齐。
- 徽章与正文不一致时，以 `python tests/run_tests.py` 的实际通过数为准。

## 3. 不许做的事

- 不改断言数、不删测试、不把失败用例改 `skip` 来换绿灯。测试红了先判断是产品问题还是平台用例问题。
- 不重排文件、不批量格式化、不动与任务无关的行；一个 PR 只解决一件事。
- 不改 CI 的 job 结构与触发条件；不改分支保护、仓库设置、Secrets。
- 不提交 `config.yaml`、`_local/`、`_history/`、`tmp/`、`*.bak*`、`__pycache__`、`node_modules`。
- 不在代码里写死本机路径、账号、token、代理地址；需要环境信息一律走 `scripts/tg_common.py` 的解析。
- 不为了「看起来完整」补未经运行验证的结论、数字或引用。

## 4. 代码约定（摘要）

- `dsh_home` 只在 `scripts/tg_common.py` 解析，其他脚本不许自己拼路径；测试里有 AST 静态检查会拦。
- 中文输出统一 `tg_common.utf8_stdio()`。
- 新增脚本要同时进 `audit_pack` 交叉引用、README 组成表、测试；数量变化同步 `data/counts.json` 与徽章。
- 新增守卫优先写成 `tests/run_tests.py` 的用例或 `scripts/audit_pack.py` 的检查项，能纯函数判定的不要起子进程。

## 5. 提交与 PR

- 提交信息 `<type>: <一句话>`，`type` ∈ `feat` / `fix` / `docs` / `refactor` / `perf` / `test` / `chore`；陈述式，一条只做一件事。
- PR 描述三段必写：**改了什么 / 为什么 / 怎么验的**。验证要写实际命令与结果（例如 `python tests/run_tests.py` → `225/225 通过`），不写「应该没问题」。
- 开 PR 后立即回报 `state: progress` 与 PR 号，不要等 CI 结果再回报。
- CI 必须全绿才请求评审。红了不要重跑碰运气，先看日志定位。
- PR 涉及 Issue 时在描述里写 `Fixes #N`。
- 首行保留自动生成标记，不要删掉「This is by an automated agent, not a human.」。

## 6. 沟通

- 接单先 ack，再说计划；卡住就报卡点，不要静默重试超过两轮。
- 评审意见按条回应：改了就说改了并给新证据，不改就说明理由。
- 不代替人做对外发布、不代发公告、不在 Issue 里代表维护者承诺时间表。
