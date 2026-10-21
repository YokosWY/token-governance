---
name: tg-gui
description: "tg 子技能 · GUI 交接：常规区↔GUI 区协议、cua 在 WinUI/XAML 应用上的三条硬结论。需要点界面、拖拽、截图判断时用。"
---

# tg-gui · 界面操作

## 自适应（按系统与能力）

- **探测**：`sys.platform`；有无 cua/UIAutomation；是本地桌面还是远程桌面。
- **分支 A（Windows + cua）**：按三条硬结论执行（hotkey 不可用 / foreground 打字 / 分段）。
- **分支 B（Windows 无 cua）**：`pwsh` + UIAutomation 脚本 + 截图核对。
- **分支 C（macOS）**：AppleScript / `cliclick` + `screencapture`。
- **分支 D（Linux）**：X11 用 `xdotool`；Wayland 用 `ydotool`（可能需 root）。
- **分支 E（远程桌面）**：像素坐标会漂移，每次操作前重新截图定位。
- **都不可用**：只出"操作步骤 + 校验方法"，让用户自己点。

详见 `docs/adaptation.md` 的 F 节。

## 分工

- 常规会话：GUI 自动化工具**不挂载**（省几十个工具 / 100 KB+ schema）
- **GUI 区 = 单独 preset**（含 cua 挂载）
- 简单点击优先 `pwsh` + UIAutomation / `xdotool`，不轻易切区

## 交接协议

1. 检测到 GUI 信号 → `ask_user_question` 三选一：转 GUI 区 / 脚本代做 / 取消
2. 转区 → 写 `handoff-gui.md`（目标 / 当前状态 / 目标窗口与元素 / 禁止动作 / 回传格式）
3. GUI 区执行 → 写 `handoff-back.md`
4. 回常规区先读回执，不要重问

## 三条硬结论（WinUI / XAML 应用，Windows 实测）

| 坑 | 现象 | 正解 |
|---|---|---|
| hotkey 失效 | `ctrl+n` 报 "scanned 55 elements" 未命中 | 像素点击 UIA 元素（InvokePattern） |
| 后台打字全丢 | PostMessage 返回"已发送 N 字符"，界面 0 字符 | `delivery_mode:"foreground"`（SendInput） |
| SendInput 截断 | 长文本只落前几个字符 | 分段打，每段截图校验 |

补充：窗口启动后可能 off-screen，像素点击前先置前（`bring_to_front`）。

## 安全红线

- 不关整窗（可能弹"是否保存"）
- 不保存、不发送、不付款、不改系统设置
- 目标窗口里有凭据文件 → 先报告，再决定
