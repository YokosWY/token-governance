# 自适应矩阵：不同用户的真实情况

> 本包假设"有 DSH 类客户端 + 有计费账本"，但现实五花八门。
> 每个技能开头都会先**探测**，再选分支；下面是全部维度和降级路径。

## 一、先探测（30 秒）

```
0. 环境齐不齐？         python scripts/env_check.py（Python / pyyaml / 解压器 / node / dsh_home）
1. 有没有计费账本？      dsh_home/storages/cost-meter/ledger.json
2. 有没有会话日志？      sessions/*/session.v4.jsonl.zstd（含逐 step usage）
3. 有没有技能/插件目录？  skills_dir 存在且有 SKILL.md
4. 有没有 preset 机制？   cordis.patch.yml 或等价配置文件
5. 上下文窗口多大？      config 或模型文档（200k / 400k / 1M）
6. 计价货币？            ledger.config.currency 或问用户（CNY/USD/EUR）
7. 单人还是共享账户？    同一账本是否多机/多人出现
```

第 0 项是入口：依赖缺一层，后面全白跑。`env_check.py` 分必需/可选/信息三档，必需项缺失
退出码 1，`--json` 给安装脚本读；它只读环境，不改任何文件。

## 二、七个维度

| 维度 | 取值 | 检测 | 影响 | 调整 |
|---|---|---|---|---|
| **可观测性** | 有账本 / 只有日志 / 只有 API 账单 / 都没有 | 上面 1、2 | 能否算真钱 | 见 A |
| **窗口大小** | 128k / 200k / 1M | config | 压缩阈值 | 见 B |
| **客户端** | DSH / Claude Code / Codex / Cursor / 其他 | 工具清单特征 | 有无 preset、有无 skill | 见 C |
| **部署** | 单机 / 多机 / 共享账户 / 企业网关 | 账本是否合并 | 去重、单价 | 见 D |
| **计价** | 实付 / 单价表 / 未知 | ledger 有无 cost | 能否折钱 | 见 E |
| **系统** | Windows / macOS / Linux | `os.name`、`sys.platform` | 权限与 GUI | 见 F |
| **合规** | 个人机 / 公司机 | 用户声明 | 日志能否落盘 | 见 G |

---

## A · 可观测性降级

| 情况 | 做法 |
|---|---|
| 有 ledger | 直接读，含币种与实付 |
| 只有 session 日志 | 从 `usage{input,cacheRead,output}` 汇总 token；单价用 `config.prices`，缺省用主流 flash 价并**标注"估算"** |
| 只有 API 账单 | 只做总量和趋势，不做归因；把"归因"标为不可用 |
| 都没有 | 放弃审计，转 P3 过程指标：记录工具数、上下文均值，靠前后对比 |

**铁律**：任何估算必须在报告里写"估算 + 假设"，不能伪装成实付。

---

## B · 窗口大小决定阈值

| 窗口 | thresholdRatio 建议 | 说明 |
|---|---|---|
| 128k | 0.75–0.85 | 只做安全阀，留足空间给回复 |
| 200k | 0.70–0.80 | 同上 |
| 1M | 0.80–0.90 | 本机取 0.8，正常会话不触发；实测见 `benchmark.md` 第四节 |

低水位（0.3–0.4）会让每次压缩都重算整段前缀，短会话更贵，见 P4 与 `benchmark.md` 第三节。

回本步数仍按 P4 算：`成本 ÷ 每步节省`，窗口越大回本越慢。

---

## C · 客户端差异

| 客户端 | 有 preset | 有 skill 机制 | 调整 |
|---|---|---|---|
| DSH 系 | ✅ | ✅ | 全功能 |
| Claude Code | ❌（用 settings/agents） | ✅（SKILL.md 兼容） | 用 subagent + settings 替代 preset；工具隔离靠子代理 |
| Codex / Cursor | ❌ | 部分 | 只保留 cost/context 的"方法"部分，不依赖机制 |
| 其他 | ❌ | ❌ | 把 SKILL.md 当纯方法论文档读 |

判据：能挂"每步固定前缀"的才算有 preset 能力；否则 P7 改成"子代理隔离"。

上表是方法层。`scripts/` 里的数据脚本只认 DSH：`measure_modules.py` 读 `session.v4.jsonl.zstd` 的 `request/header` + `assistant/message`，`cost_summary.py` 读 cost-meter 的 `ledger.json`。换客户端时这两点不可用，得自己接日志或只做估算（并按 §A 标注估算与假设）。

---

## D · 部署形态

| 形态 | 坑 | 做法 |
|---|---|---|
| 单机 | 无 | 直接算 |
| 多机 | 目录/账本各一份 | `dsh_homes` 列表合并；**按 ledger 真实路径去重**（同一份账本被软链或列两次时只算一次）。账本只有日粒度、没有会话 id，按会话去重做不到 |
| 共享账户 | 总量含他人 | 只看占比与人均，不做绝对金额结论 |
| 企业网关 | 看不到单价 | 用网关账单或让用户填 `prices`；否则只报 token 不报钱 |

---

## E · 计价与币种

- ledger 的 `costCny` / `cost` / `currency` 优先。
- 没有实付时：`price × token` 折算，**固定声明汇率**（如 1 USD = 7.1 CNY）。
- 跨币种不要混加，先统一再汇总。

---

## F · 操作系统

| 项 | Windows | macOS / Linux |
|---|---|---|
| 权限收紧 | `icacls <dir> /inheritance:r /grant:r "<user>:(OI)(CI)F"` | `chmod 700 <dir>` + `umask 077` |
| GUI 自动化 | cua / UIAutomation | `xdotool`（X11）/ `ydotool`（Wayland）/ AppleScript |
| 大文件落盘 | `%TEMP%` | `/tmp`（注意容器内可能是内存盘） |

---

## G · 合规

| 约束 | 调整 |
|---|---|
| 公司机，不能留原始输出 | `30-logs/` 只存摘要与哈希，原文不入盘 |
| 不能连网 | 跳过所有联网步骤；价格表手工填 |
| 多人共享记忆根 | 目录前缀加 `<user>/`，避免互相覆盖 |

---

## 三、自适应块的统一格式

每个技能开头都有一段：

```markdown
## 自适应（先探测再执行）
- 探测：<要查什么>
- 分支 A（常见）：<怎么做>
- 分支 B（降级）：<怎么做>
- 都不可用：<退到哪条原理>
```

原则：**探测成本必须低于执行成本**（读一个文件、跑一条命令），不许上来就跑全量扫描。

---

## H · 装了别的东西怎么办（冲突）

| 冲突 | 检测 | 处理 |
|---|---|---|
| 已有同名技能 | 技能目录已存在 `tg-*` | 问：覆盖/改名/跳过 |
| 已有别的 router | 其他 router 类技能或路由脚本 | 问：并存/接管/替换 |
| 记忆根非空 | `memory_root` 有内容 | 问：合并/另起目录 |
| 配置已存在 | `config.yaml` 内容不同 | 问：覆盖/保留/备份 |
| 补丁条目冲突 | 目标 id 已存在 | 问：合并/交给你改 |
| GUI 目标含凭据 | 文件名含 credential/key | 问：继续/换目标 |

**通用规则：只读探测自己做，写操作先问。** 预检：`python scripts/preflight.py`。

---

## 附：通用 vs 本机特化

目标：任何人 `git clone` + 填两行 config 就能跑。下表标明哪些是通用的、哪些必须按机器改。

### A. 通用（换机器不用改）

| 内容 | 说明 |
|---|---|
| 四项杠杆方法论 | 工具清单 / 技能目录 / 工具输出 / 压缩 |
| 归因口径 | 存活计息（内容 × 存活步数） |
| 收益口径 | 边际、上限不可相加、事故不计稳态 |
| 压缩经济学 | 回本 ≈ 30 步 |
| 缓存经济学 | 未命中成本占比 = `miss% × k / (miss% × k + 1 − miss%)`，`k = 入价 / 缓存价`；k < 3 不值得做（`benchmark.md` 第十节） |
| 委派分档 | 主 High / 难活 Max / 审验 Max |
| 记忆结构 | 对话夹 6 文件 + 交接单 |
| GUI 三条硬结论 | WinUI hotkey 失效 / PostMessage 丢字 / SendInput 截断 |
| 补丁分层规则 | 组合结果不可改 / preset 不可按 id 寻址 / isolate 必需 |

### B. 需要按机器改（config.yaml 里配）

| 项 | 默认 | 说明 |
|---|---|---|
| `dsh_home` | `~/.dsh` | 发行版可能不同（如 `~/dsh-next`） |
| `memory_root` | `~/tg-memory` | 会话记忆放哪 |
| `skills_dir` | `~/.dsh/skills` | 技能目录 |
| `budget` | null | 预算告警阈值 |
| 价格表 | flash 价 | 不同模型不同价 |

### C. 本机特化（本项目未硬编码，需另行讨论）

以下属于「原机器的具体配置」，已从技能正文剥离，避免污染通用版：

1. **两套 DSH 并存**（官方版 + NEXT）：路径、端口、home 各不同 → 需在多 `dsh_homes` 场景下合并对账。
2. **Memos 连接器**：别人未必装；`tg-cost` 不依赖它。
3. **cua preset 的包名**：`<vendor>/computer-use*` 因发行版而异。
4. **AI 知识库结构**（`AI-KB/memory/*.md` 五件套 + Obsidian 镜像）：个人库约定，不通用。
5. **技能路由**：不再依赖外部 CLI（旧路由工具已于 2026-10-22 下线）→ 只用 `tg-router` 五域表 + `ls`/`read`，`skillctl` 仍管激活/停用。
6. **某些技能是软链（symlink）**：Windows 上需要开发者模式或管理员；跨机器建议直接复制实体目录。
7. **ACL 收紧的具体账户名**：`<COMPUTERNAME>\<USER>` 因机器而异。
8. **中文生态依赖**（思源笔记、AnySearch MCP 等）：与本包无关，不写进默认流程。

> 安全合规（密钥、脱敏、上传门禁）不在本包内：那属于各人的本地约束，不随包分发。

### D. 上架检查清单

- [ ] `grep -rn "D:\\" skills/` 应为 0 命中
- [ ] `grep -rn "<你的用户名>\|<你的主机名>" skills/ docs/ README.md` 应为 0 命中
- [ ] 每个 SKILL.md 的 description ≤ 200 字符
- [ ] `python scripts/cost_summary.py --config config.example.yaml` 能跑（示例路径不存在时应给出清晰报错，而不是崩栈）

