---
name: shadowbot-block-to-clipboard
description: Use when 需要把影刀可视化指令块写入剪贴板供影刀粘贴还原，或查找指令库定义。
---

# 影刀指令 → 剪贴板

## Overview

影刀编辑器复制指令块 = 剪贴板写 5 种格式，识别关键 = 自定义格式 `ShadowBot.Flow.Blocks`（.NET `DataObject.SetData` 的 BinaryFormatter 序列化字符串，内容是块 JSON 数组）。用同款 .NET API 写入即可字节级还原，影刀 Ctrl+V 直接出现可视化块。二进制格式底层细节见 **shadowbot-rpa** 技能（剪贴板格式章节）。

## When to Use

- 要把某个指令构建成可视化块放进剪贴板（标准指令或扩展自定义指令都行）
- 需要完整容器结构：if 要带 endif、循环要带 endloop、try 要带 endtry（构建器自动补）
- 不知道指令的参数名/类型/选项 —— 指令库定义里有全部信息，不用猜

## 工具

脚本在 `scripts/`，指令库在 `scripts/shadowbot_blocklib/`（43 个分类文件 + `buildin_types.json`）。剪贴板写入走 `powershell.exe` 的 .NET API，仅 Windows。

| 脚本 | 作用 |
|---|---|
| `scripts/shadowbot_block_builder.py` | 主入口：按指令名+参数生成块 → 剪贴板 |
| `scripts/shadowbot_flow_clipboard.py` | 底层：任意 blocks JSON 数组 → 剪贴板 |
| `scripts/shadowbot_blocklib/` | 标准指令库（Zh_CN，从 ShadowBot.Runtime.Development.dll 提取） |

- 扩展自定义指令自动扫描 `%LOCALAPPDATA%\ShadowBot\users\*\apps\*\xbot_extensions\*\prototype.block.json`；环境变量 `SB_USERS_DIR` 可覆盖影刀用户目录

## Quick Reference

```bash
python shadowbot_block_builder.py --search 判断              # 搜指令 (name/title/description)
python shadowbot_block_builder.py --list [关键词]             # 列全部指令
python shadowbot_block_builder.py --info workflow.if          # 查看任意指令的输入/输出定义
python shadowbot_block_builder.py workflow.if --set operand1=11:x --set operator=10:== --set operand2=10:1
python shadowbot_block_builder.py datetime.add --set duration=10:2 --set unit=10:day
python shadowbot_block_builder.py "xbot_extensions.shadowbot_pdd_decode.数据中心-交易数据-交易概况-数据总览"
python shadowbot_block_builder.py workflow.forin --no-end     # 只要循环块本身
python shadowbot_flow_clipboard.py blocks.json --titles "标题1|标题2" --new-ids
```

生成后自动写剪贴板 → 影刀编辑器 **Ctrl+V**。`--info` 列出该指令全部输入（名称/类型/默认值/select 选项）与输出（名称/类型），不用猜参数。

**构建规范（必填参数/输出结构/资源配对）见 shadowbot-instruction-spec 技能** —— 生成后必须在影刀实测，失败就对比手动修正版。

## 值编码（--set 的值）

| 前缀 | 含义 | 例子 |
|---|---|---|
| `10:` | 字符串字面量 | `10:取消` |
| `11:` | 变量引用 | `11:dialog_result.pressed_button` |
| `13:` | Python 表达式 / bool | `13:False` |
| `16:` | 列表 / 字典 | `16:[]` |

裸值自动按参数 type 推断（bool→`13:`，其他→`10:`）；select 参数自动从 options 补中文 `display`。

## 容器块自动补结束标记

构建 `indent/scope=1` 的容器开启块时自动附带结束标记（`--no-end` 关闭）：

| 容器开启块 | 自动附带 |
|---|---|
| workflow.if / multiple_conditions_if / file.if_exist / dir.if_exist / image.exist / ai.operation_screen.if | + workflow.endif |
| workflow.for / while / forin / forin_expr / infinite_loop / loop_dict | + workflow.endloop |
| programing.try | + programing.catch + programing.endtry |

中间块（elseif / else / catch / finally）不自动补。

## 指令库位置（构建器自动加载）

- **标准指令 425 个**：本技能 `scripts/shadowbot_blocklib/`（`Zh_CN_<分类>_blocks_json`，提取自影刀安装目录 `ShadowBot.Runtime.Development.dll` 的内嵌资源，重提方法见 shadowbot-rpa 技能）
- **扩展自定义指令**：数量随本机已安装的扩展而变（未装扩展时为 0），自动扫描 `users\*\apps\*\xbot_extensions\*\prototype.block.json`
- 每块定义含：`name` / `title`(中文显示名) / `comment`(%参数%模板) / `inputs`(name·label·type·default·editor.options) / `outputs`(name·type) / `isCondition`·`isLoop`
- 扩展块额外字段：`block_title` = `扩展名/指令名`（如 `拼多多数据获取/数据中心-交易数据-交易概况-数据总览`），构建器自动带

## Common Mistakes

- **if/循环不带结束标记** → 默认自动补；显式 `--no-end` 才不补
- **扩展指令粘到没装该扩展的 app** → 粘贴前确认目标 app 的 `xbot_extensions\` 里有对应扩展
- **块 id 与现有流程重复** → `shadowbot_flow_clipboard.py --new-ids` 重新生成 uuid
- **手写 BinaryFormatter 字节** → 别手写，走工具（`.NET SetData`，产物与影刀复制字节级一致）
- **用 ConvertFrom-Json 传载荷** → 会把 JSON 变成对象导致序列化字节对不上；必须 `Get-Content -Raw` 传字符串
