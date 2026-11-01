# dsh-loop-guard（已归档）

这个目录**不再包含代码**。独立版循环守卫已于 2026-11-01 合并进另一个仓库，请看那边：

| | |
|---|---|
| 正本 | [`D:\AI\Projects\dsh-guard\plugin`](https://github.com/YokosWY/dsh-guard/tree/main/plugin) |
| 包名 | `dsh-guard-ui`（0.3.0 起单包双功能：护航按钮 + 循环守卫） |
| 旧版本代码 | 本仓库 `plugins/_archive/dsh-loop-guard-0.2.0/`（只读留档，不再同步） |
| 本机安装 | 两套 DSH 都已切到 `dsh-guard-ui` 0.3.0，`@local/dsh-loop-guard` 已卸下 |

## 为什么归档

独立版 0.2.0 已经随 token-governance v1.2.0 发过版；之后功能合并进 dsh-guard 的插件包，两边不再互相同步。保留一个「还能改但改了不生效」的目录最容易出错，所以代码移到 `_archive/`，这里只留路标。

## 还留着什么

- `scripts/install_guard.py` 仍可用（源目录已指向 `_archive/`）：主要给老机器执行 `--uninstall` 收尾，或对账已装副本
- `scripts/verify_guard.mjs` 仍可用（导入的也是归档版）：离线 15 项烟雾测试
- `scripts/guard_stats.py` 不用改：它读的是 `<DSH_HOME>/storages/loop-guard/events.jsonl`，合并版写的是同一个路径
