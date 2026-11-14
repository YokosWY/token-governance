# 自动协作代理（dsh-superagent）

本仓库接了一个自动化协作代理：官方研究原型 [deepseek-ai/dsh-superagent-prototype](https://github.com/deepseek-ai/dsh-superagent-prototype)。它在自己的环境里跑 DSH，用独立 GitHub 账号读本仓库的 Issue / PR 评论，按需改代码、跑测试、开 PR。人（`@YokosWY`）是唯一的授权触发者。

本文只写「怎么用、边界在哪、坏了怎么修」。代理自身的架构与设计取舍见上游 `docs/`，不在本仓库复述。

## 1. 现状

| 项 | 值 |
|---|---|
| 上游 | `deepseek-ai/dsh-superagent-prototype`，单提交原型 `8c32a9d`，无 release |
| 部署 | WSL2 `Ubuntu-24.04`，代码在 `/root/dsh-superagent`，DSH_HOME `/root/.dsh`（profile `superagent`） |
| 代理身份 | GitHub 账号 `YokosWY-Agent`（scope：`repo` / `read:org` / `gist` / `workflow`） |
| 授权触发者 | 仅 `YokosWY`；其他账号 @ 它不会被受理 |
| 仓库绑定 | 单实例单仓库，当前只绑 `YokosWY/token-governance` |
| 面板 | 由 Windows 侧 `keepalive.ps1` 把带 token 的地址写进 `panel-url.txt`（本机路径，不入库）；token 每次重启重生成，不要存书签 |
| 自动合并 | 关闭。它的 PR 一律等人评审 |

## 2. 怎么用它

1. 开 Issue 写清要求，或在本仓库 Issue / PR 的评论里 `@YokosWY-Agent <要求>`。
2. 它先回一条 ack，再干活；改代码的活会自己开 PR，PR 描述带「This is by an automated agent, not a human.」。
3. 评审就按普通 PR 走：看 diff、看 CI、写 review comment。它会读 review 并按评论改，改完在 review thread 里回。
4. 合并由人做（squash）。

给它的要求越像验收标准越好：**要改什么、改完怎么验、不许动什么**。它读仓库根目录的 `AGENTS.md`，那里的硬约束不用每次都重复。

适合的活：定位并修测试/脚本里的具体缺陷、补守卫、对齐文档与代码口径、按现有约定加小功能。

不适合的活：跨仓库操作、需要生产凭据的事、大范围重构、要它自己判断产品方向的活。

## 3. 边界（它不会做，也别要求它做）

- 不改 `config.yaml`（本机真值，已 gitignore），不读 `_local/`、`_history/`。
- 不动 `data/counts.json` 里的真值来「让测试通过」——只允许让文档向真值对齐，反过来不行。
- 不自动合并、不改仓库设置、不动分支保护。
- 不代发对外内容（release notes、公告）。

## 4. 运维

状态：

```bash
wsl -d Ubuntu-24.04 -u root -- bash -lc 'cd /root/dsh-superagent && scripts/dsh-superagent status'
```

十项 health（`runtime` / `relay` / `notice` / `launch` / `delivery` / `assessment` / `reply` / `evals` / `models` / `github`）应全 `ready`、`errors` 为空。

起停：把 `status` 换成 `start` / `stop`。

自愈：Windows 侧有一个 `keepalive.ps1`（与 `keepalive.log` 同目录，本机路径不入库）由登录启动项 `dsh-superagent-keepalive.cmd` 和计划任务 `DSH-SuperAgent-Keepalive` 拉起，负责刷新 WSL 内 GitHub 域名解析（`/etc/hosts` 九个域名经代理走 DoH 现查）并确认服务存活。

常见故障：

| 现象 | 处理 |
|---|---|
| 面板 401 | token 已轮换，重读 `panel-url.txt` |
| 它不回话 | `status` 看 `github` 是否 `ready`；再看 `/root/dsh-superagent/.artifacts/*.log` 最新一份 |
| WSL 内解析不到 GitHub | 手动跑一次 `keepalive.ps1`，看 log 里 `9/9 resolved` |
| PR 卡在 CI | 先判定是产品问题还是平台用例问题，别为了过 CI 改断言 |

## 5. 已验证的一次端到端

2026-11-14：Issue #3（`run_tests.py` 在 `TMPDIR` 指向仓库内时自我递归复制）→ 它定位到 `tempfile.gettempdir()` 落在仓库内的根因 → 新增 `make_tmpdir()` 把临时目录改到仓库父目录、`copytree` 忽略列表补 `tg-test-*`、`.gitignore` 加 `tg-test-*/` → 自验 `223/223` → 开 PR #4 → CI 十二个 check 全绿 → squash 合并（`26b8eac`）。断言数未动，未重排文件，未顺手改别的测试。

回滚：`git revert 26b8eac`。
