# 失败轮次基线 · 2026-10-30（v1.5.1 口径）

> 一周后（2026-11-06 起）用**同一条命令**对比绝对值，不要对比百分比——百分比随轮数波动。

## 复现命令

```bash
cd <TG 仓库根>            # 即含 scripts/ tests/ 的目录
python scripts/failure_analysis.py --days 7 --protocol-only --json
python scripts/turn_cost.py --days 7 --json
```

## 基线（近 7 天，155 会话）

| 指标 | 值 |
|---|---|
| 轮数（分母，与 turn_cost 一致） | 953 |
| 有失败的轮次 | 386（40.6%） |
| 失败调用 | 818 |
| 其中协议错误 | 271（33.1%） |
| 归一化（每千轮） | 284 次协议错误 / 千轮 |

### 协议错误 271 的四个桶

| 桶 | 次数 | 典型报文 |
|---|---|---|
| 未读就改 | **180**（edit 145 / write 35） | `file has not been read` |
| 文件已变更 | 49（edit 34 / write 15） | `file changed since it was read` |
| 工具名幻觉 | **39**（`bash` 34 / 其他 5） | `unknown tool "bash"` |
| 其他 | 3 | `old_string and new_string must differ` |

## 判据（一周后怎么算有效）

- **主判据**：协议错误 271 → **≤150 次/7 天**，且轮数在 800–1100 区间（否则分母不可比）。
- 分桶判据：未读就改 180 → ≤80；工具名幻觉 39 → ≤5。
- 无效：降到 200–250 属噪声范围（84% 的失败本是单次失误，提示只能救「首次」）。
- 反向信号：若「文件已变更」那桶涨了，说明提示把行为推向「先读后立刻改」，反而撞上并发变更。

## 口径坑（别再踩）

1. `failure_analysis` 旧版把 `system-reminder`、runtime context、时间戳当用户轮 → 分母虚高一倍（1962 vs 953），同一分子 386 被读成 19.7%。v1.5.1 已与 `turn_cost` 同判据，并有测试锁。
2. `failed_call_cost` / `cost_share`（63%）是「有失败的轮次的整轮花费」，不是失败烧掉的钱。正名键：`turns_with_failure_cost_share`。
3. 轮级指标噪声大（一轮内失败 3 次仍算 1 轮），对外报数用**失败调用次数**的绝对值。

## 本次改动（可回滚）

- `scripts/failure_analysis.py`：切轮过滤纯注入段 + 成本键正名。
- `tests/run_tests.py` + `tests/fixtures/injection_session.jsonl`：锁住两脚本分母一致。
- `data/counts.json` / README×2 / CHANGELOG：1.5.1、断言 216。
- `$DSH_HOME/AGENTS.md` 与 `~/.dsh/AGENTS.md`（两份副本 md5 必须相等）§1 新增一条红线：写前先读 + 命令只走 `ops_bash`/`pwsh`。

回滚：

```bash
git -C <TG 仓库根> checkout -- scripts/failure_analysis.py tests/run_tests.py \
    data/counts.json README.md README_EN.md CHANGELOG.md
rm -f tests/fixtures/injection_session.jsonl
cp "$DSH_HOME/AGENTS.md.bak-20261030-fail" "$DSH_HOME/AGENTS.md"
cp ~/.dsh/AGENTS.md.bak-20261030-fail ~/.dsh/AGENTS.md
```
