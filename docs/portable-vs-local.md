# 通用 vs 本机特化

目标：任何人 `git clone` + 填两行 config 就能跑。下表标明哪些是通用的、哪些必须按机器改。

## A. 通用（换机器不用改）

| 内容 | 说明 |
|---|---|
| 四项杠杆方法论 | 工具清单 / 技能目录 / 工具输出 / 压缩 |
| 归因口径 | 存活计息（内容 × 存活步数） |
| 收益口径 | 边际、上限不可相加、事故不计稳态 |
| 压缩经济学 | 回本 ≈ 30 步 |
| 委派分档 | 主 High / 难活 Max / 审验 Max |
| 记忆结构 | 对话夹 6 文件 + 交接单 |
| GUI 三条硬结论 | WinUI hotkey 失效 / PostMessage 丢字 / SendInput 截断 |
| 补丁分层规则 | 组合结果不可改 / preset 不可按 id 寻址 / isolate 必需 |

## B. 需要按机器改（config.yaml 里配）

| 项 | 默认 | 说明 |
|---|---|---|
| `dsh_home` | `~/.dsh` | 发行版可能不同（如 `~/dsh-next`） |
| `memory_root` | `~/tg-memory` | 会话记忆放哪 |
| `skills_dir` | `~/.dsh/skills` | 技能目录 |
| `budget` | null | 预算告警阈值 |
| 价格表 | flash 价 | 不同模型不同价 |

## C. 本机特化（本项目未硬编码，需另行讨论）

以下属于「原机器的具体配置」，已从技能正文剥离，避免污染通用版：

1. **两套 DSH 并存**（官方版 + NEXT）：路径、端口、home 各不同 → 需在多 `dsh_homes` 场景下合并对账。
2. **Memos 连接器**：别人未必装；`tg-cost` 不依赖它。
3. **cua preset 的包名**：`<vendor>/computer-use*` 因发行版而异。
4. **AI 知识库结构**（`AI-KB/memory/*.md` 五件套 + Obsidian 镜像）：个人库约定，不通用。
5. **技能路由工具 `skillq` / `skillctl`**：自研 CLI，别人可能没有 → 技能里已给 `ls` 降级路径。
6. **某些技能是软链（symlink）**：Windows 上需要开发者模式或管理员；跨机器建议直接复制实体目录。
7. **ACL 收紧的具体账户名**：`<COMPUTERNAME>\<USER>` 因机器而异。
8. **中文生态依赖**（思源笔记、AnySearch MCP 等）：与本包无关，不写进默认流程。

> 安全合规（密钥、脱敏、上传门禁）不在本包内：那属于各人的本地约束，不随包分发。

## D. 上架检查清单

- [ ] `grep -rn "D:\\" skills/` 应为 0 命中
- [ ] `grep -rn "<你的用户名>\|<你的主机名>" skills/ docs/ README.md` 应为 0 命中
- [ ] 每个 SKILL.md 的 description ≤ 200 字符
- [ ] `python scripts/cost_summary.py --config config.example.yaml` 能跑（示例路径不存在时应给出清晰报错，而不是崩栈）
