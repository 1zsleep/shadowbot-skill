---
name: shadowbot-instruction-spec
description: Use when 构建影刀指令块/流程，需遵循参数必填、输出结构、资源配对等规范。
---

# 影刀自动化指令构建规范

## Overview

用 `shadowbot_block_builder.py` 生成指令块时，仅凭指令库定义的 `default` 填参**不够**——影刀运行时有自己的必填与结构要求。本规范是实测踩坑后沉淀的规则（每条都对应一次真实报错/修正）。工具用法见 **shadowbot-block-to-clipboard**。

## 规则 1：必填参数 — default=None ≠ 可空

指令库定义里 `required=True` 但 `default=None` 的参数，影刀**运行时必填**，留 null 会报错（如 `excel.launch.driver_way` 报"驱动方式为必填项"）。

构建器已自动处理：required=True 且无默认时，select 参数自动取**第一个选项**（`driver_way`→`auto_check` 自动检测）；非 select 必填参数会打警告，需手动 `--set`。

自查：`python shadowbot_block_builder.py --info <指令>` 看 `默认: None` 且必填的参数。

## 规则 2：输出结构 — 键是 id 不是 name

指令定义输出有 `id` 和 `name` 两个字段，**flow 块里 outputs 的键 = id**，值里的 `name` = 变量名，两者可能不同！

例：`excel.get_row_count` 输出 `id=row_count`，`name=excel_row_count`，正确结构：
```json
"outputs": {"row_count": {"name": "excel_row_count", "variableLabel": "Excel总行数", "type": "int", "isEnable": true}}
```
用 name 当键 → 影刀里该块**没有输出**，后续无法引用。构建器已修复（键取 `id ?? name`，并带上 `variableLabel`/`type`）。

## 规则 3：资源配对 — 打开类指令配关闭

`excel.launch` 不是容器（无 scope），但**语义上必须配对**：流程末尾要有 `excel.close`，否则 Excel 进程/文件句柄残留。

- 配对不是紧跟在 launch 后面（会先关后读），而是放在**流程最后一步**
- `excel.close` 参数：`excel_instance=11:<实例变量>`，close_way 默认 save（保存）
- 构建器对 excel.launch 会打印配对提示
- 同类资源都要检查：打开文件↔关闭、连接数据库↔断开、打开网页↔关闭网页

## 规则 4：变量引用带 display

`11:` 变量引用的值，影刀 UI 会自动带 `"display": "<变量名>"`（select 参数则是中文选项名）。带上更贴近影刀真实产物，不必须但建议。

## 规则 5：构建 ≠ 完成，必须实测

mock/字节验证通过 ≠ 影刀运行通过。每次新指令流程，必须在影刀里**实际粘贴 + 运行**验证（本次 driver_way 必填项就是实测才暴露的）。测试失败 → 对比影刀里手动修正后的块结构（可复制回剪贴板让我解码对比）→ 把新经验补进本规范。

## 规则 6：多流程参数传递

子流程 (processN) 与主流程通过 `args` 字典传递参数：

**子流程声明参数**（flow.json 顶层 `parameters`）：
```json
{"name": "邮箱账号", "direction": "In", "type": "str", "value": "默认值", "description": "", "kind": "Text"},
{"name": "聊天记录", "direction": "Out", "type": "str", "value": "", "description": "", "kind": "Expression"}
```
- `direction: "In"` = 输入参数；`"Out"` = 输出/返回值；`kind`: Text=字面量 / Expression=表达式

**子流程入口**（生成的 processN.py）：
```python
def main(args):
    if args is None:
        邮箱账号 = "默认值"              # args 为 None 用默认值
    else:
        邮箱账号 = args.get("邮箱账号", "默认值")   # 从 dict 取值
```

**子流程返回**：Out 参数在 finally 写回 args：
```python
finally:
    args["聊天记录"] = 聊天记录          # 变量名必须与 Out 参数同名
```

**调用方**（process.run 块）：
- `process` = 子流程名（display=显示名），`package` = `11:__name__`
- `inputs` = `16:[{"参数名":"11:变量"},{"参数名":"13:表达式"}]` → 生成 `inputs={...}`
- `outputs` = `10:[{"name":"返回变量名","type":"str"}]` → 生成 `outputs=["返回变量名"]`，返回值直接落到本流程同名变量

```python
xbot_visual.process.run(process="process2", package=__name__, inputs={
    "group": dialog_result.获取目标,
    }, outputs=["聊天记录"], _block=(...))
```

## 规则 7：直接写入 flow.json（不走剪贴板）

批量/程序化构建流程时可直接改 `xbot_robot\.dev\<流程名>.flow.json`：
- 结构 `{"name","memo","kind":"visual","blocks":[...],"parameters":[...]}`；写前先备份
- **flow.json 块不带 `__kind__`**（那是剪贴板格式字段，写入前 pop 掉）
- 块 id 用新 uuid4；JSON 用 2 空格缩进 + `\r\n` 结尾（影刀原生风格）
- **⚠️ 只写 flow.json 只有可视化不能运行**——必须同步写 `xbot_robot\main.py`！最可靠 = 从同款流程的参考 app 复制已验证的 main.py（参考 app 需本机存在；无参考时用下方生成代码模式手写）
- **最稳做法：整包复制参考 app 的原生文件**——`.dev\main.flow.json` + `main.py` 一起从已验证 app 复制（md5 比对确认），目标 app 立即完整可运行
- 影刀保存 flow.json 时会**丢弃 comment 字段**（粘贴带 comment 的块，保存后也没了）——comment 是装饰性元数据，有无都不影响运行
- `variableLabel` 部分块**动态生成**（如 read_data_from_workbook 按 read_way 变 "Excel单元格内容"，定义里是通用值）——装饰性差异，忽略
- 生成代码模式（xbot_visual 调用 + _block 元组）：
  ```python
  def main(args):
      try:
          变量 = xbot_visual.<模块>.<函数>(参数=值, ..., _block=("main", 块序号, "块标题"))
      finally:
          pass
  ```
  - `_block` 元组 = (流程名, 块在 blocks 数组的 1 基序号, 块标题)，必须与 flow.json 一一对应
  - 值映射：`10:X`→`"X"`、`11:X`→变量 `X`、`13:expr`→`expr`(布尔/表达式)、`16:[...]`→python 列表/字典、null→`None`
  - 输出：`输出变量 = xbot_visual...` 接在调用前
- ⚠️ 影刀运行中直接改文件：需在影刀里**关闭该应用（不保存）再重新打开**才生效；影刀打开/保存时会按 flow.json 重新生成 .py（会覆盖手写的）,建议用户先退出应用编辑，否则可能影刀会报错数据被篡改
  - 构建器 `build_block()` 返回 (blocks, titles) 可直接复用，再自行组装多块流程

## 规则 8：完整交付流程（需求 → 云端应用）

用户要求"建一个应用 + 写指令 + 放到某账号"时，按固定流程执行：

1. **明确需求** — 指令内容（哪些块、参数）、应用名；不确定的参数用 shadowbot-block-to-clipboard 的 `--search`/`--info` 查指令库
2. **账号密码** — 用户给了 → 用（密码**只进环境变量 SB_PASS**，不打印不落地）；**没给 → 主动向用户要**（直接问/用 clarify），不能猜、不能默认用某个账号、不能去翻本地缓存
3. **创建空白应用** — shadowbot-rpa-migration 技能 `scripts/create_app.py --name <应用名> --out <apps目录>`（模板随技能打包，新 uuid4，无需现成应用）
4. **写入指令** — 构建器组装块 → 写入 `.dev\main.flow.json`（去 `__kind__`，2空格+`\r\n`）→ 按规则 7 生成代码模式写 `main.py`
5. **迁移账号** — `SB_PASS=<密码> python scripts/migrate_app.py --app <生成的xbot_robot目录> --user <账号> --name <应用名>`
6. **验证汇报** — develop/list 按 `name=<应用名>` 服务端过滤确认 `appName/appId/authority` → 汇报云端 appId + 账号，提醒凭据注意保管

流程顺序固定：**创建 → 写入 → 迁移**。各步骤脚本都在对应技能的 `scripts/` 里，新电脑可独立跑（模板/指令库随技能打包）。

## 值编码速查

| 前缀 | 含义 | 例子 |
|---|---|---|
| `10:` | 字符串字面量 | `10:昨日` |
| `11:` | 变量引用 | `11:excel_instance` |
| `13:` | Python 表达式/bool | `13:False` |
| `16:` | 列表/字典 | `16:[]` |
