---
name: shadowbot-cli
description: Operate ShadowBot (影刀) client via shadowbot.shell-cli.exe — login, run/stop apps & tasks, triggers, messages, Studio. Use when user requests 影刀登录/运行应用/停止任务/触发器/消息中心/切换模式/Studio 操作.
---

# ShadowBot CLI Skill（本地化整合）

> 整合自影刀官方技能 [ying-dao/skills → shadowbot-cli](https://github.com/ying-dao/skills/blob/main/shadowbot-cli/SKILL.md)（MIT 风格开源），保留官方全部规则与 Fast Paths，按本机环境适配。官方原文（SKILL.md + README.en/zh-CN）见本目录 `references/`。

## 运行时契约（本机环境）

- CLI 可执行文件：`D:\ShadowBot\shadowbot.shell-cli.exe`（影刀安装目录，PATH 中通常可直接调用）
- **本机（Hermes）直接用 bash 调用**，无需 PowerShell 包装：
  - `shadowbot.shell-cli.exe <子命令>`（bash 中直接执行）
  - 在 PowerShell/CMD 中则为：`powershell -NoProfile -ExecutionPolicy Bypass -Command "shadowbot.shell-cli.exe <子命令>"`
- 所有输出为结构化 JSON：`{"ok": true/false, "data": {...}}`，`ok=false` 时看 `message`。

## 硬规则（官方）

1. **只执行真实命令**。绝不发明命令/flag/ID。
2. 命令/flag/参数值失败时，报告确切错误并停止，不要猜替代方案。
3. Fast Paths 直接执行，不用 -h 探索链。
4. 保持用户提供的值原样（用户名/密码/名称/路径）。
5. Windows 路径用引号。
6. 命令数最小化，只在依赖边界验证。
7. 非 Fast Path 场景先 `-h` 发现再执行。
8. 会话检查失败（未登录/查不到当前账号/连不上本地 ShadowBot）→ 先走登录恢复，再执行业务命令。
9. 向用户要密码前，先 `auth account list` 列出记住的账号，复用其用户名（密码可选）；用户没给账号时取列表第一个账号免密登录。

## 最小工作流（官方）

1. 解析意图与所需实体（账号、应用、任务、触发器、id、页面、主题）。
2. 会话预检：`shadowbot.shell-cli.exe auth current`；失败 → 登录恢复。
3. 命中 Fast Paths → 直接执行映射命令。
4. 未命中 → `-h` 发现正确命令/flag。
5. 返回简洁证据：执行了什么 + 关键输出/结果。

## 登录恢复（官方）

触发信号：`auth current` 失败/未登录；本地端点/会话不可用；缺少账号上下文。

1. `shadowbot.shell-cli.exe auth account list` 查记住的用户名；用户未给凭据时取第一条当当前用户名（免密，不带 `--password`）。
2. 用户名已知：`auth login --username <u> --password <p>`；无密码则 `auth login --username <u>`（免密登录）。
3. 登录后验证：`auth current`。
4. 用户名未知：问用户要用户名（及可选密码）再重试。

登录失败追问模板（只问缺失项）：
- 缺用户名：`请提供影刀登录用户名。若你希望我直接尝试登录，也可以同时提供密码。`
- 用户名已知、免密失败：`当前账号 <u> 的免密登录失败。请提供该账号密码，我将重试登录并继续执行命令。`
- 有凭据仍失败：`登录失败。请确认账号或密码是否正确，或确认影刀客户端是否已可用后让我重试。`

## Fast Paths（官方 + 本地实测标注）

### 登录
1. `shadowbot.shell-cli.exe auth login --username <u> --password <p>`

### 打开影刀并执行 CLI
请求形如"帮我打开影刀并执行CLI程序"：`auth current` 预检 → 登录恢复（如需）→ 直接执行请求的 CLI 命令 → 返回输出摘要与关键证据。

### 运行应用（✅ 本地实测 2026-08-13 success=true）
1. `shadowbot.shell-cli.exe console app`（实测：`--page-size 50` 可一次列全，含本地 developed 应用，appId = 本地 uuid）
2. 从列表定位目标应用，提取 `<app_id>`
3. `shadowbot.shell-cli.exe console task run --app-id <app_id>`（实测可用 `--app-type developed --wait-timeout 120s` 同步模式带流式日志；默认即同步，返回 `[task-summary] taskId=... success=true`）
4. 记录返回的 `<task_id>` 供后续 status/logs/stop。

### 停止应用
1. 用已知 `<task_id>`（来自上次 run 结果）。
2. 未知则 `console task history --page 1 --page-size 20` 定位。
3. `shadowbot.shell-cli.exe console task stop --task-id <task_id>`

## 本地实测补充（2026-08-13）

- `system health` → `{"ok":true,"data":{"service":"console-restapi","status":"ready"}}`；`system state` → 含 `currentMode`（console/studio）、`hasRunningTask`、`runningTask` 等，跑任务前先看 `hasRunningTask` 避免冲突。
- `console app` 的 developed 列表包含**本地开发的未迁移应用**（appId 与本地 package.json 的 uuid 一致），可直接 run —— 无需先迁移到云端。
- 运行"新建应用1"（main.py = 打开 lingxing ERP 页）实测：7.4s 完成，`success=true`，桌面弹出可见 Chrome（`silent_running=False` 时）。
- **脱离客户端 python 直跑应用 main.py 不可行**：`Resources\Code-Activity\Zh-CN\` 下的 xbot/xbot_visual 是代码模板（`visual_action` 实现全 pass、`xbot_ai` 包不存在于安装目录），真实执行必须经客户端 REST API（即本技能命令）。
- 相关技能：运行/迁移类细节见 `shadowbot-rpa-migration`（云端 API、migrate_app.py 自包含脚本）。

## 其他常用场景（-h 发现，官方清单）

| 场景 | 发现命令 |
|------|----------|
| 应用管理（列表/详情/删除/发布） | `console app -h` |
| 任务观测（history/status/logs/stop） | `console task -h`、`console task history -h`、`console task status -h`、`console task logs -h`、`console task stop -h` |
| 触发器（增改/启停/删） | `console trigger -h` |
| 消息中心（未读/已读/全部已读） | `console message -h` |
| 页面/主题/外链 | `console page -h`、`console theme -h`、`console link -h` |
| 扩展（安装/状态） | `console extension -h` |
| Studio（打开/创建/编辑/保存/同步） | `studio -h` |
| 模式切换（assistant/console）与系统查询 | `mode -h`、`system -h` |

## 失败处理（官方）

- exe 不在 PATH → 报告缺失可执行文件并停止。
- CLI 返回 JSON 错误 → 直接呈现该错误。
- 场景有歧义 → 问缺失实体（如应用名/id）或 `-h` 发现。
- 业务命令因登录/会话失败 → 做一次登录恢复，再重试一次。

## 输出风格（官方）

默认：简洁结果（执行的命令 + 关键证据）。请求 JSON 时返回：

```json
{
  "ok": true,
  "summary": "one-line result",
  "commands": ["exact executed commands"],
  "assertions": ["key checks and evidence"]
}
```
