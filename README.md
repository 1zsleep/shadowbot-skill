# shadowbot-skill

影刀 RPA (ShadowBot) 技能集，共 6 个：

| 技能 | 用途 |
|------|------|
| `shadowbot-cli` | 操作影刀客户端（`shadowbot.shell-cli.exe`）：登录、运行/停止应用与任务、触发器、消息中心、Studio |
| `shadowbot-rpa` | 影刀平台：本地数据布局、云端 API、剪贴板格式、流程与运行日志分析 |
| `shadowbot-rpa-migration` | 应用迁到云端账号（PySide6 迁移助手 + 命令行脚本） |
| `shadowbot-block-to-clipboard` | 把可视化指令块写入剪贴板供影刀粘贴还原；查询指令库定义 |
| `shadowbot-instruction-spec` | 构建指令块/流程时的参数必填、输出结构、资源配对规范 |
| `shadowbot-validation` | 影刀的校验体系，以及 .NET Reactor 加密 DLL 的静态抽取工具链 |

每个技能目录含 `SKILL.md`，脚本与参考资料在同目录的 `scripts/`、`templates/`、`references/` 下。
