---
name: tg-context
description: "tg 子技能 · 上下文工程：工具清单/技能目录/工具输出三项主杠杆，压缩只作安全阀，含 preset 定制与 isolate 坑。会话变慢变贵时用。"
---

# tg-context · 上下文工程

## 自适应（按窗口与能力选刀）

- **探测**：上下文窗口多大；有无 `cordis.patch.yml`；有无 spill/压缩配置项。
- **分支 A（1M 窗口 + 有 patch）**：三项主杠杆全上；压缩阈值 0.55–0.65，只当安全阀。
- **分支 B（200k 窗口）**：压缩阈值 0.65–0.75，裁剪更激进（4096/2048/512）。
- **分支 C（128k 窗口）**：压缩阈值 0.70–0.80，且必须配长会话纪律（每 100 步换会话）。
- **分支 D（无 preset 机制）**：把重工具挪进子代理，而不是 preset。
- **分支 E（无 spill/pruner）**：靠"命令自带收口 + read 带 offset/limit"人工控制。
- **都不可用**：只调工具与输出，不动压缩（压缩多数时候不划算）。

详见 `docs/adaptation.md` 的 B/C 两节。

## 三项主杠杆（按收益排序）+ 一个安全阀

| 刀 | 做法 | 实测（本机样本，换机器数值不同） |
|---|---|---|
| 工具清单 | 不常用整套工具（如 GUI 自动化 56 个 / 107 KB）移进专用 preset | 304.7 → **145.5 KB** |
| 技能目录 | 批量 `disable-model-invocation: true`，常驻只留 20–30 个 | 160 → **27 条**（约 -86%） |
| 工具输出 | `spill-policy.maxInlineTokens` 落盘；pruner 只在压缩期裁 | >6 k tokens 落盘给路径 |
| 压缩（安全阀，别当杠杆） | `thresholdRatio` + `retainRatio` | 462 k → 185 k，但见下节 |

工具清单要能定位到「谁装上来的」：`python scripts/schema_budget.py --top 12` 出一个会话里
工具 schema 按服务（`mcp:<server>`）、按工具族的 KB 排行，并对 `config.yaml` 的
`schema_budget_kb` 卡预算（`--check` 超了退出码 1）。实测一台机器 174 个工具 / 150 KB，
最大块是某个 MCP 的 27.7 KB —— 新装一个重 MCP 常一次涨 20–50 KB，比慢慢调阈值划算得多。

## 命中率是另一格（别和前四项混算）

前三项降的是**每步重发量**；命中率降的是**未命中那一小块**，且价值随 provider 变：
`k = 入价 / 缓存价`，k=2 时未命中只值个位数百分比，k=50 时能占到上下文成本约 40%。

打穿前缀的 7 个来源（自动裁剪、压缩、改配置、新会话、换模型、TTL、显式缓存过期）、三个公式、
各客户端映射与无账本测法，见 `docs/benchmark.md` 第十节。一条命令体检：`python scripts/cache_audit.py`。

## 压缩的真实经济学（结论：不产生净收益）

```
成本 = 压缩后首步按输入价重算整个新前缀（183 k ≈ ¥0.20，按 flash 价）
节省 = (压缩前 − 压缩后) × 缓存价 ≈ ¥0.0067/步
回本 ≈ 30 步
```

实测一个 729 步的长会话：压缩 4 次，成本约 ¥0.8，省下约 ¥1.0，净收益接近零。
原因是缓存命中价比输入价便宜几十倍，而压缩一次就把整段前缀打回未命中。

所以：

- **压缩是安全阀，不是降本手段。** 只用来防止上下文爆窗口。
- **阈值宁高勿低。** 0.30–0.40 会把压缩频率放大两三倍，每次都要重算整个前缀。
- **短会话（剩余 <30 步）不要动阈值**，动了必亏。
- 真正降成本的是工具清单和技能目录——它们每步都在，但不会触发重算。

## 改配置的隐性成本（2026-10-22 实测）

改技能目录、改补丁、改工具清单，**当天缓存会失效**，下一轮按输入价重算整个前缀。

实测：改动最密集那天（10-21）单次未命中 input 冲到 4.4 k，是前一日的 2.4 倍；单次成本 ¥0.0189，反而不是最低的一天。

所以：

- 优化不要一天改八遍，攒成一批再动。
- 评估收益至少看**三天趋势**，别用改动当天和前一天比。
- 用 `scripts/compare_days.py` 按天看单次成本、上下文、未命中 input，比拍脑袋可靠。

## preset 定制（把重工具隔离出去）

```yaml
- id: preset-gui
  name: '<vendor>/agent-preset'          # 按你的发行版写包名
  config:
    id: gui
    order: 5
    plugins:
      - ...
      - id: computer-use-stack
        name: cordis:group
        group: true
        isolate:                          # 服务型插件必须包组 + isolate
          computerUse: true
        config:
          - id: computer-use
            name: '<vendor>/computer-use'
          - id: computer-use-cua-driver-native
            name: '<vendor>/computer-use-cua-driver-native'
```

少了 `isolate` 那两行，整个 preset 报 `Preset services require isolate realms` 并"加载失败"。

## 补丁层能改到哪一层

- 顶层 id ✅
- `group: true` 且 config 是数组的子项 ✅
- **preset 的 `plugins` 是对象数组，不可按 id 寻址** ❌ → 只能整段覆盖该 preset

## 检查清单

- [ ] 工具数、schema KB 前后对比（`request/header` 可量）
- [ ] 技能目录条数
- [ ] 压缩是否真触发（`compaction/*` 事件）
- [ ] 剩余步数是否够回本
