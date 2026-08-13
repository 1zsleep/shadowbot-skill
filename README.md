# shadowbot-skill

影刀 RPA (ShadowBot) 技能集，用于 Hermes Agent。包含 4 个技能：

| 技能 | 用途 |
|------|------|
| `shadowbot-rpa` | 影刀 RPA 应用、API、迁移相关工作 |
| `shadowbot-rpa-migration` | 影刀 RPA 应用到云端迁移；GUI 源码（PySide6 迁移助手）+ 自包含迁移脚本 |
| `shadowbot-block-to-clipboard` | 把影刀可视化指令块写入剪贴板供影刀粘贴还原，或查找指令库定义 |
| `shadowbot-instruction-spec` | 构建影刀指令块/流程的参数必填、输出结构、资源配对等规范 |

`shadowbot-rpa-migration` 内含完整 GUI 源码（`main.py` + `src/migration_assistant/`，PySide6 迁移助手）及可移植的 PyInstaller spec（`assistant.spec`，基于内置 `SPECPATH` 自动定位路径），拷到任何电脑后执行 `python -m PyInstaller --noconfirm --clean assistant.spec` 即可构建 `dist/影刀迁移助手.exe`。exe 为构建产物，不入库。

每个技能目录包含 `SKILL.md`（说明文档）及 `scripts/`、`templates/`、`references/` 等配套文件，可独立移植使用。

## 使用方式

将本仓库内容放入 Hermes 的 skills 目录（如 `~/AppData/Local/hermes/skills/shadowbot/`）即可被 Hermes 加载。
