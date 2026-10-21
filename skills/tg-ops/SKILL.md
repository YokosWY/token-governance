---
name: tg-ops
description: "tg 子技能 · 配置运维：补丁分层、备份脱敏、死配置坑、preset 覆盖漂移、升级后检查。改配置或升级后必读。"
---

# tg-ops · 配置运维

## 自适应（按配置体系与权限模型）

- **探测**：有无 `cordis.patch.yml` 或等价补丁层；文件是否被程序重写；是否容器内。
- **分支 A（DSH 系）**：按三层结构（组合结果/补丁/preset）操作 + 目录级权限。
- **分支 B（其他客户端）**：找它们的"用户配置 vs 默认配置"分界，只改用户层。
- **分支 C（容器内）**：配置靠 env / volume 注入，别改镜像内文件。
- **分支 D（无权限收紧能力）**：改用"密钥只放环境变量 + 不落盘"。
- **都不可用**：只做备份与 diff，不做自动改。

详见 `docs/adaptation.md` 的 F/G 两节。

## 三层结构（DSH 类配置）

| 层 | 文件 | 谁写 |
|---|---|---|
| 组合结果 | `${DSH_HOME}/profiles/<profile>/cordis.yml` | **应用启动重写**，不要手改 |
| 补丁层 | `${DSH_HOME}/profiles/<profile>/cordis.patch.yml` | 人改（UI 也可能改，如模型选择器） |
| 预设 | 补丁里 `- insert: preset-*` | 新增 preset 只能走 insert |

其他发行版的对应关系：只要存在「组合结果 + 补丁层」，规则相同。

## 三个已踩过的坑

1. **死配置**：补丁顶层的条目可能命中 base 里 `disabled: true` 的"影子"实例 → 改了不生效，还误导人。真配置在 preset 的 `plugins` 内部。
2. **preset 不可按 id 寻址**：只能整段覆盖（一份 176 行快照），升级后要重生成。
3. **服务型插件要 `group + isolate`**，否则 preset "加载失败"。

## 改配置的标准动作

```bash
cp cordis.patch.yml cordis.patch.yml.bak-$(date +%Y%m%d-%H%M%S)
# 改完校验（!!js 自定义标签需专用 loader，普通 yaml 会报错）
python scripts/check_yaml.py cordis.patch.yml
# 备份立刻脱敏：把 VITE_*/API_KEY/TOKEN 换成 <REDACTED-SEE-LIVE-PATCH>
```

## 升级后检查清单

- [ ] preset 覆盖块是否还在（升级会重建组合）
- [ ] 覆盖块 vs 新 base 的 diff，必要时重生成
- [ ] 连接器存储里是否残留已删服务的 token（如 snapshots 表）
- [ ] 目录权限是否仍在（看有没有又出现 `Authenticated Users`）
- [ ] 新会话工具数 / schema KB 是否回到预期

## 回滚

`.bak-*` 都已脱敏 → 回滚后要把 key 从主文件补回，否则相关服务起不来。
