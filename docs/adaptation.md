# 自适应矩阵：不同用户的真实情况

> 本包假设"有 DSH 类客户端 + 有计费账本"，但现实五花八门。
> 每个技能开头都会先**探测**，再选分支；下面是全部维度和降级路径。

## 一、先探测（30 秒）

```
1. 有没有计费账本？      dsh_home/storages/cost-meter/ledger.json
2. 有没有会话日志？      sessions/*/session.v4.jsonl.zstd（含逐 step usage）
3. 有没有技能/插件目录？  skills_dir 存在且有 SKILL.md
4. 有没有 preset 机制？   cordis.patch.yml 或等价配置文件
5. 上下文窗口多大？      config 或模型文档（200k / 400k / 1M）
6. 计价货币？            ledger.config.currency 或问用户（CNY/USD/EUR）
7. 单人还是共享账户？    同一账本是否多机/多人出现
```

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
| 多机 | 目录/账本各一份 | `dsh_homes` 列表合并；**按会话 id 去重**（同一会话可能被同步两份） |
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
