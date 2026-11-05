# 别人家的「价格统计表」怎么做的（2026-11-05 调研）

调查口径：7 组查询、10 个候选，3 组同类对比。只列对 `token-governance` 有迁移价值的做法，
每条后面跟「我们怎么改」。

## 1. 基准表（样本）

| 项目 | 形态 | 关键设计 |
|---|---|---|
| [litellm cost map](https://docs.litellm.ai/docs/proxy/model_cost_map) | 单个大 JSON，仓库内维护，启动时拉最新 | 全部 `USD per billable unit`；`cache_read_input_token_cost` / `cache_creation_input_token_cost` 与 input/output 平级；变体用后缀（`_above_200k_tokens`、`_batches`、`_priority`）；原币非 USD 时**只换一次并把汇率写进 `metadata.comment`**；未定价请求记 $0 并打 `litellm_zero_cost_requests_total` 指标 |
| [models.dev](https://github.com/anomalyco/models.dev) | 每 provider/model 一个 TOML + 脚本生成 `api.json` | 数据在 git 里、按 provider 分文件、可 PR 审；`provider.toml` 的注释里写清文档 URL 与**访问日期**；对外给 JSON/SDK 快照 |
| [llm-prices](https://github.com/simonw/llm-prices) | 每 vendor 一个 JSON，`scripts/build.py` 生成线上 JSON | 核心是 `price_history[]` + `from_date`/`to_date`（**左闭右开**、`null` = 无限）；`updated_at` 写在产物里 |
| [ccusage](https://github.com/ryoppippi/ccusage) | 本地日志 → 报表 | 价格表不在运行时联网，**随包内置快照**，`just update-litellm-pricing` 显式刷新；cache read / creation 分列；`--json` 出机读结果 |
| [dsh-client-pricing](https://github.com/Miyazawai/dsh-client-pricing) | DSH 顶栏插件 | 内置价表按官方文档人工核对；保留**生效前**的固定价表，三态徽标（未生效/高峰/空闲），30 秒重算一次 |

## 2. 六条可直接搬的经验

1. **原币种优先，汇率不参与计价。** 官方价是人民币（flash 空闲 0.02/1/4、高峰 0.04/2/8；pro 0.15/4.5/13.5、0.30/9/27，元/百万 token）。
   我们 config 里的 USD 价 ×6.6702 恰好还原成这组人民币数——说明 6.6702 是官方 USD 报价的换算率，数值没错，
   但**把汇率固化进配置会让 CNY 数字随汇率漂移**。litellm 的做法是「换算一次 + 记录汇率与出处」，llm-prices 干脆只存 USD 并标 `updated_at`。
2. **价格必须带生效期。** llm-prices 的 `from_date`/`to_date` 是最省事的模型：峰谷价 2026-08-17 生效（社区文档口径），
   早于这天的历史不能按峰价重算。我们现在的 `peak.enabled` 是全局开关，没有"从哪天起"。→ `peak.since: 2026-08-17`。
3. **计价键按 token 类型平铺，变体用后缀。** cache 命中/写入/输入/输出四类分开；长上下文、批量、优先级都是同单位的后缀变体，不另起结构。
   DeepSeek 没有独立的 cache write 价 → 记入 cacheMiss（我们已这么做，正确）。
4. **未知模型记 ¥0 要吵。** litellm 有专门的 zero-cost 指标。我们的 `prices check` 有这能力，但只有 `--strict` 才退出 1；日常 ingest 只打印一行。
5. **表在 git 里、产物自动生成、刷新是显式动作。** 三家的共同点：源数据可 diff 审查，JSON 是构建产物，刷新有命令有提交记录。
6. **时区写进数据，不写进运行环境。** 峰谷窗口必须是 `Asia/Shanghai` 的 09–12/14–18，并在字段旁注明 UTC 等价（01–04/06–10）。
   我们 `[1,4]/[6,10]` 那次事故就是"UTC 数字当本地小时用"。

## 3. 对本地这套的改动清单（按性价比排序）

| # | 改动 | 文件 | 风险 |
|---|---|---|---|
| 1 | `peak` 段加 `since: "2026-08-17"`，早于该日不计峰价 | `config.yaml` + `ledger.py:is_peak` | 低，只影响历史重算 |
| 2 | 加 `source: {url, fetchedAt}` 到 `peak` 与 `models` 段 | `config.yaml` | 无 |
| 3 | 未知模型默认报出并计数（ingest 末尾给一行 `未定价 N 个`，已有）→ 再给 `--strict` 到每日报表 | `ledger.py` | 低 |
| 4 | CNY 作为展示主币种、USD 只做中间量；或反向：直接存人民币价、去掉 `fx` | `config.yaml` + `prices.py` | 中，动全量历史数字 |
| 5 | 价表 diff 友好化：一个 provider 一个文件（抄 models.dev） | `config.yaml` 拆分 | 中，动加载路径 |
| 6 | 价格变更走 `price_history` 而不是原地改 | 新增 `docs/price-history.json` | 低，但需要长期维护 |

## 4. 与 11-04 对账的关系（更正前一轮结论）

- 官方文档第 (2) 注确认：高峰 = 周一至周五 9:00–12:00、14:00–18:00（北京时间），其余时段含周末/节假日 = 空闲，空闲价 = 高峰价的一半。
- 社区文档称该方案 **2026-08-17 00:00 起**生效。若成立，11-04 属于新方案，平台**应当**收峰价，
  则我们的 ¥32.24 应接近实扣，而平台报的 ¥27.47 偏低 15% —— 与前一轮「平台没加峰价」的解释相反。
- 两条解释都需要同一份数据才能裁决：平台「用量」页 11-04 的分类 token 数。缺这一条，结论只能停在"二选一"。

## 5. 检索回执口径

- 查询 7 组：litellm schema / llm pricing json cache_read / ccusage / openrouter models api / models.dev api.json / simonw llm-prices / peak off-peak pricing yaml
- 候选 10 个：litellm cost map、litellm schema、ccusage、models.dev（provider.toml + 4 个 model.toml）、llm-prices、OpenRouter models API、Apify OpenRouter scraper、dsh-client-pricing 及其介绍文、aidev-index ccusage 专题
- 同类对比 3 组：价格表数据源（litellm vs models.dev vs llm-prices）／成本统计工具（ccusage vs OpenRouter 抓取）／峰谷展示（dsh-client-pricing vs 官方文档）
