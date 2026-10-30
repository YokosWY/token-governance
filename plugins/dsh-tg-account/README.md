# dsh-tg-account

DSH（DeepSeek Harness）输入框旁的账目胶囊：按 `provider:model` 实时算本会话花了多少钱，顺带管交接卡、切会话、侧栏母会话标记与输出瘦身。

MIT · Node ≥ 22 · 适用 DSH 2.0.x（会话日志 `session.v4.jsonl.zstd`）

## 功能

| 模块 | 说明 | 依赖 |
|---|---|---|
| 胶囊 | `本 ¥x · 今日 ¥y · 省 z · 累计省 w`，悬停看拆分；点开面板按「花费 + 省」成对摆：本会话合计 / 本会话省 / 本会话链 / 本会话链省 / 今日花费 / 今日省 / 累计省 / 命中率（均 3 位小数），另有逐轮明细、按模型计价、上下文占用、换会话回本步数。单会话也算一条链 | 无 |
| 子代理合计 | 自动扫会话头 `parentSession`，把子代理花费并进「本」；超预算变暗红 | 无 |
| 输出瘦身 | 工具结果进上下文时压一次（原文落盘 + 回取路径），面板可逐条开关 | 无 |
| 交接卡 | **每轮回答结束都刷新**一张卡（不等「切了划算」），没超限也能随手「切过去」开新对话做特殊操作；可一键复制路径 | token-governance 的 `handoff.py`（可选） |
| 一键切会话 | 新建会话并把「读交接卡」一行预填进输入框，不自动发送 | 同上（可选） |
| 自动切换 | 胶囊右侧「自动」开关，**整条血统链一个开关**：链首（母对话）默认开，链内所有会话一致。每轮回答结束只判定「该不该切」，该切就标「待转接」并在你下次按 Enter 时带着交接卡转到新会话（只预填，不自动发送） | 同上（可选） |
| 母会话色标 | 侧栏会话行左内边距里的小点（不占官方 leading 槽，所以「进行中」那行也有），同一链同色；可调颜色/形态、链内排序。改法：会话行的 **⋯ 菜单 →「标识颜色与形态」**（按链统一，选完立刻生效） | 交接卡有记录时更准（可选） |

### 自动切换的行为

开关是**链级**的：一条血统链只有一个母对话（链首），链内所有会话共用一个开关（2026-10-29 用户定）。

| 会话 | 判定依据 | 开关 |
|---|---|---|
| 母对话 | 血统链的链首（前面没有相关对话） | 链没被手动关过 → **开** |
| 子对话 | 链上已有链首，或本插件转接出来的 | 跟链首**一致**；子对话再转接还是子对话，永远不会变成母对话 |
- 在任意一节拨开关 = 整条链一起变（含之前和之后切出来的会话）。
- 打开时以**该会话自己这一轮之前**为基线，不会把母对话的基线带过去、也不会拿旧卡立刻切走。
- 已经转接出去的对话（血统里有人拿它当 parent）胶囊显示「· 已转接」，不再弹「待转接」、也不再参与轮末判定；胶囊整条换成冷蓝底（`PILL_DONE`，与「建议切换」暗红、「超预算」琥珀区分），开关仍跟链走。

转接流程（2026-10-29 定）：

1. 每轮结束**只检查该不该切**（沿用「建议切换」判定：上下文占用 / 回本步数）。
2. 该切 + 开关开 → 胶囊显示「· 待转接」，交接卡刷新到这一轮之后。
3. 你在旧会话输入并按 **Enter** → 拦截这次发送，把`交接卡路径 + 你的输入`预填进新会话，旧会话收不到这条。
4. 新会话里按一次回车发送（不自动发送，避免直接烧一轮）。

限制：

- 只拦截 **Enter**；点发送按钮不转接（消息会正常发到旧会话）。
- 拦截失败或转接出错时，输入内容留在原输入框，再按一次回车就正常发到旧会话；胶囊会显示「· 转接失败」，悬停看原因。
- 旧会话的草稿不会被清空（Lexical 编辑器状态改不了），切回来会看到同一段文字。
- 报错 / 取消的轮次不转接；交接卡没生成好也不转接。

## 安装

胶囊与 TG 在同一个仓库（`plugins/dsh-tg-account/`）。两种装法选一种：

**A. 跟 TG 一起装（推荐，新机器）**

```bash
git clone https://github.com/YokosWY/token-governance
cd token-governance
python scripts/install.py --with-account --dry-run    # 只打印计划
python scripts/install.py --with-account              # 真正写入
```

**B. 只装胶囊（TG 已装过）**

```bash
python scripts/install_account.py --status            # 先看现状，不改任何文件
python scripts/install_account.py --apply             # 写入
```

脚本做三件事（幂等，改前备份）：

1. 复制 `plugins/dsh-tg-account/` 到 `<DSH_HOME>/local-plugins/dsh-tg-account/`（内容相同则跳过）；
2. 在 `<DSH_HOME>/profiles/<profile>/package.json` 里补 `dependencies` 的 `link:` 与 `dsh.profile.bundles`；
3. 写 `<DSH_HOME>/_local/tg-account.json`，把 `root` 指到本仓库——价表、窗口和 `handoff.py` 从这儿找。

装完**重启 DSH**（客户端 bundle 要重建）。官方 desktop 默认 `--profile desktop`；另一套 DSH 用 `--home <那套的数据目录>`。

手动装（不想跑脚本）：把 `plugins/dsh-tg-account/` 放到任意位置，然后编辑 `<DSH_HOME>/profiles/desktop/package.json`：

```jsonc
{
  "dependencies": {
    "@local/dsh-tg-account": "link:<你放插件的目录>"
  },
  "dsh": {
    "profile": {
      "bundles": [
        // ... 原有项
        "@local/dsh-tg-account"
      ]
    }
  }
}
```

再在 profile 目录 `pnpm install`（或你平时装 DSH 插件的方式），重启。插件自带 `cordis.patch.yml`，不需要改 `cordis.yml`。

其他用户/另一套 DSH：`DSH_HOME` 指向哪套就装到哪套。两套默认互相独立。

## 可选依赖与降级

三样都不是必需，缺了会降级，不会报错：

| 依赖 | 用在哪 | 缺了会怎样 |
|---|---|---|
| `token-governance` 仓库的 `config.yaml` | 价、上下文窗口 | 用内置价（`0.15 / 0.003 / 0.6` USD/Mtok，rate 7.1） |
| 同仓库 `data/prices.json` | 按 `provider:model` 计价 | 退成单套价，未定价的模型记 ¥0 并在胶囊里报次数 |
| 同仓库 `scripts/handoff.py` + Python | 交接卡 / 一键切 / 自动切换 | 这三个入口禁用或直接隐藏，账目不受影响 |
| `zstd` 命令行 | 解多帧会话日志 | 退 Node 内置 `zlib.zstdDecompressSync`；老 Node 需要装 zstd |

面板底部会列「可选依赖：交接卡脚本缺失 · config.yaml 缺失 · 价表用内置默认」，悬停胶囊的「自动」按钮也会说明原因。

## 配置

默认不指向任何路径：没配 `root` 时用内置价、交接卡关掉（账目照算）。装的时候 `install_account.py` 已经写好 `<DSH_HOME>/_local/tg-account.json`；要改有两种方式，环境变量优先：

**A. 环境变量**

```bash
TG_ACCOUNT_ROOT=D:/path/to/token-governance     # 仓库根，下面几个默认从它派生
TG_ACCOUNT_CONFIG=.../config.yaml
TG_ACCOUNT_PRICES=.../data/prices.json
TG_ACCOUNT_HANDOFF=.../scripts/handoff.py
TG_ACCOUNT_ZSTD=C:/tools/zstd.exe
TG_ACCOUNT_PYTHON=python
TG_ACCOUNT_MEMORY_ROOT=D:/path/to/tg-memory     # 交接卡记忆根
```

**B. `$DSH_HOME/_local/tg-account.json`**（改完 3 秒内生效，不用重启）

```json
{
  "root": "D:/path/to/token-governance",
  "config": "D:/path/to/token-governance/config.yaml",
  "prices": "D:/path/to/token-governance/data/prices.json",
  "handoff": "D:/path/to/token-governance/scripts/handoff.py",
  "zstd": "C:/tools/zstd.exe",
  "python": "python",
  "memoryRoot": "D:/path/to/tg-memory"
}
```

其他状态文件也都在 `$DSH_HOME/_local/`：

| 文件 | 内容 |
|---|---|
| `tg-trim.json` | 输出瘦身总开关与逐条规则 |
| `tg-autoswitch.json` | 自动切换状态：每个会话的开关与基线轮次；`_children` 放切出来的子对话（默认关） |
| `tg-turn-end.json` | 最近轮末信号与交接卡就绪状态 |
| `tg-lineage.json` | 手工记录的「子 ← 母」 |
| `tg-compress.json` | 瘦身阈值覆盖 |
| `tg-budget.json` | 子代理预算（步数 / 金额） |

## 性能

胶囊每 5 秒拉一次 `/tg-account/api/cost`，所以宿主侧单次请求的开销直接决定「有没有感」。

2026-10-29 优化前后的实测（本机 222 个会话、Windows，`tmp/tg-bench*.mjs` 可复跑）：

| 动作 | 优化前 | 优化后 | 做法 |
|---|---|---|---|
| `account()` 稳态 | 20–45 ms | **0.2–0.6 ms** | `lib/scan.js`：按 sid 的常驻路径表，命中只 stat 一个文件 |
| `savedFiles()` | 22 ms | 0.01 ms | 同上，全量列表 2 秒 TTL 复用 |
| `dayFiles()` | 22 ms | 0.01 ms | 同上 |
| 一次请求里的目录遍历 | 3–4 遍 | ≤1 遍（2 秒一次） | `findSession` / `savedFiles` / `dayFiles` / `treeFiles` 共用一个索引 |
| 瘦身开关读取 | 每步一次 `readFileSync` | 命中缓存 | `readTrim()` 按 mtime+size 缓存，返回克隆 |
| 侧栏链标重排 | `document.body` 整棵子树 | 只观察侧栏行容器 | `MutationObserver` 收窄 + 80 ms 合并 |

宿主侧状态文件（`tg-trim` / `tg-autoswitch` / `tg-lineage` / `tg-day` / `tg-saved` / `tg-tree` / `tg-turn-end`）改成**先写 `.tmp` 再 rename** 的原子写：进程被杀不会再留半截 JSON 被当成「没有」。

## 测试

```bash
node --test "tests/*.test.mjs"   # 155 条，纯 Node，不需要 DSH 在跑
```

覆盖：计价与今日口径、汇总/子代理、瘦身压缩与统计、交接卡解析、血统与链级开关、会话解析与排序、扫描索引、瘦身开关缓存、HTTP 路由形状。

## 已知限制

- 读的是 DSH 内部会话日志格式（`sessions/<ws>/session-*/session.v4.jsonl.zstd`）。DSH 大版本改格式就要跟着改。
- 交接卡与自动切换绑定 token-governance 的 `handoff.py` 参数；换工具要改 `lib/index.js` 的 `handoffArgs()`。
- 自动切换**只预填、不自动发送**：新会话要你按回车。这是为了不烧钱、不连环切。
- Windows 优先（`python` / `py` 探测、`zstd.exe`）；Linux/macOS 只要 `python3`、`zstd` 在 PATH 上也能用。

## 回滚

```bash
python scripts/install_account.py --uninstall --apply   # 摘掉 dependency 与 bundles（插件目录保留）
```

或手删 profile `package.json` 里的两行，重启 DSH 即回到没有插件的状态。`_local/tg-*.json` 留着不会影响别的插件，要清就一起删。

## 许可

MIT © 2026 YokosWY
