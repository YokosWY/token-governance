# 账本 v2：从会话日志派生成本

> 2026-10-24 起，`storages/cost-meter/ledger.json`（由 `dsh-cost-meter` 插件写入）不再是数据源。
> 新账本 = `scripts/ledger.py` 派生出来的缓存文件，默认 `data/ledger.json`。

## 为什么改成派生

旧账本的写方是插件：插件卸载 → 账本停更 → `cost_summary` / `cache_audit` / `doctor` 一起失去「实付」分支，
只能退回「按 token × 价格估算」。改成派生之后：

| 性质 | 旧（插件写） | 新（日志派生） |
|---|---|---|
| 写方 | 第三方插件 | 本仓库 `ledger.py` |
| 数据源 | 运行时事件流（跨机器不可复现） | `sessions/**/session.v4.jsonl.zstd`（只读） |
| 换机器 | 账本要一起搬 | 日志在就能重算 |
| 价格改了 | 只影响以后 | `ingest --rebuild` 全量重算 |
| 幂等 | 不保证 | 同一批日志反复 ingest 结果完全一致 |

代价：账本不再是「账单原样」，而是「日志 × 价格表」的折算值。它把「用了多少」和「怎么计价」分开，
计价规则必须写在 `config.yaml` 的 `ledger.prices` 里——这恰好是旧账本最不透明的地方。

## 用法

```bash
python scripts/ledger.py ingest                 # 增量：只重解析 size/mtime 变了的日志
python scripts/ledger.py ingest --rebuild       # 价格或解析逻辑变了才需要
python scripts/ledger.py report --days 7        # 按天
python scripts/ledger.py report --days 7 --by model
python scripts/ledger.py report --days 7 --by session --top 20
python scripts/ledger.py day 2026-10-24         # 单日明细（模型 + 会话）
python scripts/ledger.py verify                 # 自洽校验，非零退出 = 账本坏了
```

实测：107 份日志 / 8 050 次调用，首次 3.6 s，二次（全命缓存）0.2 s；缓存文件与账本同目录
（`data/ledger.json.cache.json`），只缓存「解析结果」不缓存「成本」，所以改价格不必重扫日志。

## 口径

一条 usage 事件四个计数，分别对应价格表的三档价（USD / 百万 token）：

| usage 字段 | 价格档 | 说明 |
|---|---|---|
| `inputTokens` | `cacheMiss` | 新输入 |
| `cacheWriteTokens` | `cacheMiss` | 写缓存，按未命中计（保守，宁高不低） |
| `cacheReadTokens` | `cacheHit` | 缓存命中 |
| `outputTokens` | `output` | 输出 |

```
cost_usd = (input + cacheWrite) × cacheMiss / 1e6
         + cacheRead × cacheHit / 1e6
         + output × output / 1e6
cost_cny = cost_usd × fx.usdToCny
```

「日」按 `ledger.timezone` 切（默认 `Asia/Shanghai`）。Windows 上 Python 常缺 `tzdata`，
`Asia/Shanghai` 这类无夏令时时区会自动退到固定 +08:00，`ledger.timezone` 字段会写成 `Asia/Shanghai(fixed)`。

## 多模型计价（2026-10-26 起，不再局限于 DeepSeek）

「一套价算所有模型」是账本最危险的错数来源：`deepseek-v4-pro` 按 flash 价会低报 4.4 倍，
免费通道按付费价会虚高 100%（`deepseek-v4.1-flash-free` 当初就是被 `deepseek-v4.1-flash`
的「包含」匹配吃掉的）。现在的解析顺序（`ledger.py` 与 DSH 胶囊两端同一套语义）：

| 顺序 | 规则 | 命中方式 | 钱 |
|---|---|---|---|
| 1 | `provider:model` 全名在 `models` 里 | `exact` | 该价 |
| 2 | `model` 在 `models` 里 | `model` | 该价 |
| 3 | 免费：名单（含 DSH 免费模型目录自动并入）／后缀 `-free`、`:free`／通配 `*/free` | `free` | **¥0**，tokens 照记 |
| 4 | `provider` 或 `model` 是 `unknown`（日志缺 source，如标题生成） | `missing-source` | `default` 价 |
| 5 | `providers.<p>.default` 显式配了 | `provider-default` | 该默认价 |
| 6 | 家族前缀（整词边界，`deepseek-v4-pro-2027` 认，`deepseek-v4-proX` 不认） | `family` | 该家族价 |
| 7 | 其它 | `unpriced` | **记 ¥0**，tokens 与次数单列报出来 |

两条硬规矩：

- **未定价绝不折 default**。以前一律按 DeepSeek 价折算，数字看着正常、其实套错了价；现在
  `pricing.unpriced` 单列清单，`report` 末尾提示「含 N 次未定价调用」，胶囊显示橙色「未定价」。
- **峰时价只对声明了 `peak` 的模型生效**。没写就没有峰谷概念，不再默认 ×2（别家的峰谷窗口跟
  DeepSeek 不是一回事）。

免费名单从哪来：`ledger.prices.free.catalogs` 指向 DSH 的免费模型目录
（`<DSH_HOME>/our-free-model/catalog.json`），导出时把 `entries` 与含 `free` 的 `kiloRows.id`
并入清单；目录更新后重跑 `prices export` 即可。

### 价表快照 prices.json（两端一份价）

`config.yaml` 仍是唯一真源；`prices export` 把它归一成 `data/prices.json`（模型价、provider
默认价、免费规则、汇率、峰谷、指纹），DSH 胶囊直接读这个文件 —— 不在 JS 里二次解析 YAML，
也不让两端各写一份价。

```bash
python scripts/ledger.py prices export     # 改完价必须重跑（默认 data/prices.json）
python scripts/ledger.py prices show       # 看当前生效的价（含峰时价与免费名单）
python scripts/ledger.py prices check      # 与日志里真实出现的 provider:model 对账
python scripts/ledger.py prices check --strict   # 有未定价或 prices.json 过期 → 退出码 1（可进 CI）
```

`prices check` 同时做漂移检测：文件里的指纹（`sha1:…`，只由价格内容决定）与当前 config 算出
的不一致 → 报「价表过期」。胶囊读不到 `prices.json` 时退回旧的单套价，不会更糟，但会在面板
里显示价源为 `config.yaml 单套价`。

## 峰谷：默认关闭，原因写在数据里

旧插件配置了 `peakWindows = [01–04), [06–10)` 且峰时价 ×2。用旧账本（10 天，卸载前留档）逐日校验：

| 日期 | 旧账本 ¥ | 无峰谷重算 ¥ | 旧/新 |
|---|---|---|---|
| 2026-10-22 | 19.63 | 19.61 | 1.000 |
| 2026-10-24 | 20.58 | 20.58 | 1.000 |
| 2026-10-21 | 35.85 | 25.17 | 1.42 |
| 2026-10-23 | 16.84 | 11.65 | 1.44 |

10-22 / 10-24 完全对得上（说明 token 口径、价格表、汇率都对），10-21 / 10-23 却多出 42% / 44%。
按「本地小时」和「UTC 小时」两种解释去套都复现不出来，说明旧插件的峰谷判定还掺了别的逻辑
（按事件/按 provider 的混合），不是单纯的时区问题。

结论：**默认 `enabled: false`**，机制留着。要用就 `enabled: true`，并拿一天的真实账单核对后再信。
装糊涂比算错便宜——账本的首要要求是能被解释。

## 账本结构

```jsonc
{
  "schema": "tg-ledger/2",
  "generatedAt": "2026-10-24T14:41:00+08:00",
  "timezone": "Asia/Shanghai",
  "currency": "CNY",
  "pricing": { "currency": "USD", "fx": {...}, "peak": {...}, "models": [...] },
  "sources": [{ "home": "...", "files": 107, "exists": true }],
  "totals": { "days": 7, "range": ["2026-10-15","2026-10-24"], "calls": 8071,
              "tokens": { "input":…, "cacheRead":…, "cacheWrite":…, "output":…, "total":… },
              "costCny": 78.38, "costUsd": 11.75, "peakCostCny": 0.0 },
  "days": {
    "2026-10-24": {
      "date": "2026-10-24", "calls": 2175,
      "input": …, "cacheRead": …, "cacheWrite": …, "output": …,
      "tokens": { "total": … },
      "costUsd": …, "costCny": 21.29, "peakCostCny": 0.0,
      "byProviderModel": { "deepseek-account:deepseek-flash": { "calls":…, "input":…, "cacheRead":…,
                            "cacheWrite":…, "output":…, "costUsd":…, "costCny":… } },
      "sessions": { "session-…": { "origin": "main|subagent", "parent": "…", "cwd": "…",
                                   "title": "…", "calls":…, "costCny":…, "firstAt":…, "lastAt":… } }
    }
  }
}
```

兼容约定：`days[date]` 顶层保留旧字段 `calls / input / cacheRead / cacheWrite / output / costCny /
byProviderModel`，所以 `cost_summary.py`、`cache_audit.py`、`doctor.py` 的读取逻辑不用改口径。
新增字段（`costUsd`、`peakCostCny`、`sessions`、`tokens`）只增不改。

## 已知差异与边界

- **子代理**：子代理有自己的会话文件，成本独立计入（`sessions[sid].origin = "subagent"`），不会重复计父会话。
- **没有 source 的事件**：7 条 usage 事件缺 `message.source`（标题生成等），归到 `unknown:unknown`，价格走 `default`。
- **未定价模型**：日志里出现但价表没登记的 `provider:model`，钱记 ¥0、tokens 与次数进
  `pricing.unpriced`，`days[date].unpricedCalls` 可查。**这不是漏记，是明示**——比起按 DeepSeek 价
  折算出一个看着正常的错数，宁可少记一行并报出来。
- **损坏行**：日志尾部偶有半行（进程被杀），解析时计入 `damaged` 并跳过；不会中断整份账本。
- **日志被清**：账本是派生缓存，日志删了，重算后那几天的数字会变小——这是事实，不是 bug。要长期留档就 `cp data/ledger.json`。
- **旧账本同时存在**：`cost_summary` / `cache_audit` 只认 `schema=tg-ledger/2`，避免新旧两份叠加。

## 验收

```bash
python scripts/ledger.py ingest && python scripts/ledger.py verify
python scripts/ledger.py prices export && python scripts/ledger.py prices check
python scripts/cost_summary.py --days 7          # 数字应与 ledger report 一致
python tests/run_tests.py                        # 192 项，含多模型计价 12 项
```

