# 贡献指南

## 提交信息

格式：`<type>: <一句话说明>`，`type` 取 `feat` / `fix` / `docs` / `refactor` / `perf` / `test` / `chore`。

- 陈述式，写清改了什么，不写宣传性表述（「全新」「重磅」「一站式」等）。
- 一条提交只做一件事；格式化与语义修改分开提交。
- 语言用中文或英文均可，同一批提交保持一致。

示例：

```
feat: 初始版本 v0.7.0
docs: 将上下文压缩定位为安全阀
fix: 修正 README 语句错误
```

## 文档表述

- 先结论，后方法与限制；数据必须标口径（样本、步数、价格版本）。
- 收益数字须能追溯到 `docs/benchmark.md` 的测量方法或 `scripts/` 的输出；估算值标注「估算」。
- 不使用营销词与口号（省钱、神器、秒杀、必看等），不写排比句与堆叠破折号。
- 未验证的结论明确写「未验证」，并附验证方法。

## 不进入仓库的内容

- 宣传物料（封面图、海报、社群推广图）、安装包与二进制文件。
- 本机路径、账号、密钥、内部清单与发布流程；这些放 `_local/`（已 gitignore）。
- 未经实测的收益表。

## 破坏性操作的前置检查

- 验证权限（scope、token 能力）只能用只读调用：`gh auth status`、`gh api <资源>`（GET）。**不拿真实仓库或资源发 DELETE、transfer、archive 等写请求去「试探」权限。**
- 删除类命令执行前：① 逐字确认目标路径或仓库名；② 先确认目标存在；③ 有条件时先备份（`git bundle` 或导出）。
- 改写远端历史（`filter-branch`、`push --force`）前必须先备份，并把备份路径写进会话卡。
- 访问 GitHub 直连 443 失败时自备代理（`HTTPS_PROXY` 按本机设置，值不进仓库、不进提交说明）。

## 提交前自查

1. `python scripts/audit_pack.py`（包完整性）或 `python scripts/check_yaml.py <补丁>`（配置语法）。
2. `python tests/run_tests.py`（122 项回归；Windows 为验收环境，CI 跑同一套命令）。
3. `git diff --stat` 确认改动范围；`git status` 确认没有把 `_local/`、`config.yaml` 带进来。
4. 若改动了文档中的数据，同步更新 `CHANGELOG.md` 与相关 `docs/`。

## 脚本约定

- `dsh_home` 只在 `scripts/tg_common.py` 里解析，优先级见 `docs/operations.md` 第四节。脚本里不许再出现 `$DSH_HOME` 直读或自己拼 `sessions` 路径——测试里有两项 AST 静态检查会拦。
- 中文输出统一走 `tg_common.utf8_stdio()`，不在各脚本里重写 `reconfigure`。
- 新增脚本要进 `audit_pack` 的交叉引用、README 组成表与测试；数量变化时同步 `README.md` 与 `README_EN.md`。

## CI

`.github/workflows/ci.yml` 有三个 job：

- `test (windows · 验收)`：合并门禁，跑测试与 `audit_pack`，红了不能合。
- `matrix (ubuntu/macos · py3.10/3.12)`：兼容性门禁（2026-10-24 首轮全绿后转正）。
- `probe (linux · 不阻塞)`：只提前暴露路径与编码类问题，不阻塞合并。

本机没有 GitHub 直连时（常见于内网/代理环境），用上面三步本地自查代替，不要为了过 CI 改断言。
