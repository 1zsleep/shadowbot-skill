---
name: shadowbot-validation
description: 影刀 RPA 的校验体系（流程校验 / 发布校验 / 选择器校验）与 .NET Reactor 加密 DLL 的静态分析方法。当需要理解或预判影刀的报错原因、写出的流程被校验拦下、或要反混淆 ShadowBot.*.dll 读取真实实现时使用。
---

# 影刀校验体系与加密 DLL 抽取

影刀把「校验」拆成了横跨 4 个 DLL 的分层体系，而这些 DLL 全部是 **.NET Reactor** 保护
（方法体被抽空成 stub）。本技能提供两样东西：

1. **校验体系的真实逻辑**——每一层在什么时机、按什么规则报什么错；
2. **一套可复现的反混淆工具链**——把任意 `ShadowBot.*.dll` 的真实 IL 拿回来再反编译。

---

## 一、何时用

| 场景 | 看哪一节 |
|---|---|
| 自己生成的 `flow.json` 被影刀拒绝、报「变量不存在 / 元素不存在 / 缺少结束指令」 | §二 校验体系 + `references/flow-validation.md` |
| 想知道某类指令有没有额外的专属校验 | §二 第 3 层 + `references/flow-validation.md` |
| 应用发布时字段校验失败 | `references/other-validators.md` |
| 选择器 / 图像元素校验、XML 校验 | `references/other-validators.md` |
| 要读 `ShadowBot.*.dll` 的真实实现（任意类型，不限于校验） | §三 反混淆流程 + `references/extraction-method.md` |

---

## 二、校验体系（4 层）

### 第 1 层：流程校验引擎 —— `ShadowBot.Runtime.Development.dll`

入口 `ValidationTool.Validate(IFlow)`，按流程类型分发：

| 流程类型 | 行为 |
|---|---|
| `VisualFlow`（可视化流程） | `VisualFlowValidation` 逐块校验，产出 `ValidationResult[]` |
| `CodeFlow`（代码流程） | **直接返回空数组**——不做任何校验 |
| 其他 | 抛 `ArgumentException("无法识别指定的流程类型：{Kind}")` |

可视化流程的核心：按 `Blocks` 顺序遍历，**每个启用的块**依次做

1. `CheckInputs` — 输入字段五连：必填 → 元素引用 → 图像引用 → 变量引用 → 文本/表达式
2. `CheckOutputs` — 输出变量名合法性（Python 变量名 / 非关键字）+ 错误处理默认值里的变量引用
3. `CheckHandlers` — 逐 handler 调 `OnValidation`，**同一行只报第一个错**
4. 缩进配对 — `IndentFlag.Start/End` 必须成对

外加一个贯穿全程的**变量作用域跟踪器** `VariableCounter`：全局变量 + 按块作用域增删的局部变量，
所以「变量在使用点是否可见」是按块顺序动态判定的，不是全流程扫描。

### 第 2 层：字段级校验规则 —— 同 DLL

`IValidationRule` 三个实现，由**块定义里的 `ValidateRules`** 决定跑哪几个：

| 规则 | 作用 |
|---|---|
| `RequiredValidationRule` | 空值检查 |
| `RegularValidationRule` | 正则匹配 |
| `RangeValidationRule` | 数值 / 长度范围 |

这三个规则同时承担「必填」和「文本格式」两类检查——`CheckRequired` 和 `CheckText` 底层都落到它们身上。

### 第 3 层：指令级校验钩子 —— 同 DLL

`BaseBlockHandler.OnValidation` 是**抽象方法**，所以每个块处理器都得实现；
`DefaultBlockHandler` 提供兜底（直接 `true`）。共 197 个处理器，按类别：
Web 34 / Excel 20 / Mobile 13 / Programing 10 / Ocr 8 / Word 7 / WebService 7 / Dialog 7 /
Datetime 7 / Win32 约 30 …

> 要点：**指令专属校验只在这一层**。通用五连查不出问题时，往这里找。

### 第 4 层：其余三个 DLL 的独立校验

- `ShadowBot.Runtime.dll` → `AppPublishSettingValidator`（应用发布设置）
- `ShadowBot.UIAutomation.Tools.dll` → `SelectorValidator`、`XmlValidator`
- `ShadowBot.Shell.Development.dll` → `OutputParamValidationRule`、`FlowErrorChecker`（校验结果的调用方）

详见 `references/other-validators.md`。

---

## 三、反混淆：把真实实现拿回来

前置：`.NET 8 SDK`、`python 3`、`ilspycmd`。

```bash
dotnet tool install --global ilspycmd --version 8.2.0.7535
export DOTNET_ROLL_FORWARD=LatestMajor        # 机器只装 .NET 8 时必须
```

三步走：

```bash
# 1) 编译抽 IL 工具
cd scripts/sbdump && dotnet build -c Release

# 2) 运行时解密，dump 全部方法体（可加第 3 个参数按类型名过滤，减少体积）
dotnet bin/Release/net8.0/sbdump.dll dump "<影刀安装目录>/ShadowBot.Runtime.Development.dll" "<dump目录>"
dotnet bin/Release/net8.0/sbdump.dll dump "<...>/ShadowBot.Runtime.Development.dll" "<dump目录>" "Validation"

# 3) PE 重建 → 反编译
python scripts/reconstruct.py "<原DLL>" "<dump目录>" "<输出.deob.dll>"
ilspycmd -p -o "<源码目录>" "<输出.deob.dll>"      # 每类型一个文件
ilspycmd -t "<命名空间.类名>" "<输出.deob.dll>"     # 单类型
```

原理一句话：Reactor 在模块初始化时把真实 IL 解密后塞进一个静态 `Hashtable`
（key = IL 在进程内的**绝对地址**，value = `{size, decryptedPtr, origPtr}`）。
`sbdump` 让 assembly 真加载一次触发解密，再按 `模块基址 + 方法RVA + 头长度` 去命中这张表。

细节、踩坑与参数说明见 `references/extraction-method.md`。

---

## 四、常见报错 → 真实原因

文案取自内嵌资源 `Strings.*` / `BlockMessages.*`（原代码按资源键引用）。

| 报错文案 | 触发点 | 真实原因 |
|---|---|---|
| `变量"{x}"不存在` | `CheckVariableQuote` | 该变量在**当前块位置**不可见。`VariableCounter` 按块顺序增删，块之前的输出变量在此处还没生效 |
| `"错误处理"中引用的变量"{x}"不存在` | `CheckOutputDefaultValueVariableQuote` | 输出变量的 `DefaultValue` 引用了不存在的变量 |
| `变量名称 "{x}" 无效(...)` | `CheckOutput` | 不符合 Python 变量名规则（允许中文，但不能数字开头） |
| `变量名称"{x}"无效(变量名称不能是Python关键字)` | `CheckOutput` | 撞了 Python 关键字 |
| `元素"{x}"不存在` | `CheckSelectorQuote` | 名字不在 `Workspace.Selectors` 里；`MagicBlockPrototype` 的块跳过此检查 |
| `图像"{x}"不存在` | `CheckImageQuote` | 不在 `Workspace.ImageSelectors`；多图用 `\|` 分隔，逐个查 |
| `表达式存在非法符号{x}` | `CheckText` | 字段值直接是 `Operator` 类型——运算符只能作为数组元素出现 |
| `"{title}"指令缺少相应的结束指令` | 缩进配对 | `IndentFlag.Start` 没有配对的 `End` |
| `"{title}"指令缺少相应的开始指令` | 缩进配对 | 出现 `End` 但栈里没有 `Start` |

**优先级规则**：输入字段按 必填 → 元素 → 图像 → 变量 → 文本 顺序短路，
前一步失败就不再查后面的；handler 阶段同一行只报第一个错。

---

## 五、关键常量（已还原的加密字符串）

| 常量 | 值 | 用途 |
|---|---|---|
| 变量引用正则 | `^(__)[a-z]+(__)$` | 形如 `__xxx__` 的内建变量直接视为存在，不查变量表 |
| 错误列表分隔符 | `", "` | 多个缺失元素/变量拼一条消息 |
| 图像多选分隔符 | `\|` | 一个字段存多个图像名 |
| HTML 标签正则（Runtime） | `<[^>]+>` | 发布设置校验前剥标签 |
| 发布字段名（Runtime） | `appname` / `description` / `instruction` | 注意主字段是 `appname`，不是 `name` |

`FieldValueKind` 取值：Text=10 / Variable=11 / Selector=12 / Expression=13 /
Image=14 / Array=15 / GroupArray=16 / Operator=17 / APExpr=18。

> 这个编号与 `flow.json` 里 `inputs` 值前缀（`10:` `11:` `12:` …）**是同一套**——
> 校验器直接按这些 kind 分派分支，所以前缀写错会让校验走到意外分支。

---

## 六、边界

- `CodeFlow` 不走任何校验，代码流程写错只能靠运行时炸。
- 变量可见性是**顺序相关**的：同一个 `flow.json`，块顺序换了就可能从通过变报错。
- 校验只检查「引用是否存在」，**不检查类型兼容**。
- 缩进配对用的是 `IndentFlag`（`[Flags]`），不是 `ScopeFlag`（后者供 `VariableCounter` 用）。
