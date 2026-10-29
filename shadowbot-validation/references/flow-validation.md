# 流程校验详解（`ShadowBot.Runtime.Development.dll`）

来源类：`ShadowBot.Runtime.Development.Validation.ValidationTool`
命名空间链：`ShadowBot.Runtime.Development.{Validation, Blockly, Blockly.Handlers}`

---

## 1. 入口

```csharp
public ValidationResult[] Validate(IFlow flow)
{
    if (flow is VisualFlow visualFlow) return new VisualFlowValidation(visualFlow).Validate();
    if (flow is CodeFlow  codeFlow)   return new CodeFlowValidation(codeFlow).Validate();  // 恒返回空数组
    throw new ArgumentException("无法识别指定的流程类型：" + flow.Kind);
}
```

`CodeFlow` 分支是空实现——**代码流程不做任何静态校验**。

---

## 2. 校验结果模型

```csharp
public class ValidationResult
{
    public IFlow  Flow         { get; set; }
    public int    LineNo       { get; set; }   // 块在流程里的行号, 从 1 开始
    public string ErrorMessage { get; set; }
    public ValidationResultType ResultType { get; set; }   // Visual / Code

    public string DisplayFlowName =>
        Flow.IsMain ? "主流程" : Flow.Name;     // 资源键 ValidationResult_MainProcess
}
```

`Equals`/`GetHashCode` 用 `(Flow.Name, LineNo, ErrorMessage)` 三元组——UI 靠它去重。

---

## 3. 主循环

```csharp
for (_lineNo = 1; _lineNo <= _visualFlow.Blocks.Count; _lineNo++)
{
    var block = _visualFlow.Blocks[_lineNo - 1];
    if (!block.IsEnabled) continue;                 // 禁用的块整块跳过

    _variableCounter.AppendBlock(block);            // ① 作用域进出
    CheckInputs(block.Inputs, ref results);         // ② 输入五连
    CheckOutputs(block.Outputs, ref results);       // ③ 输出变量
    CheckHandlers(block, ref results);              // ④ 指令专属钩子

    // ⑤ 缩进配对
    if (block.Indent == IndentFlag.Start) _indentStack.Push(_lineNo);
    else if (block.Indent == IndentFlag.End)
    {
        if (_indentStack.Count == 0) results.Add(... "\"{标题}\"指令缺少相应的开始指令");
        else _indentStack.Pop();
    }
}
// 收尾: 栈里剩下的都是没闭合的
while (_indentStack.Count > 0) { int ln = _indentStack.Pop(); results.Add(... "缺少相应的结束指令"); }
```

**顺序很重要**：`AppendBlock` 在 `CheckInputs` 之前——所以块自己的输出变量对它自己的输入**是可见的**。

---

## 4. 输入字段五连（短路）

```csharp
foreach (var input in inputs)
{
    if (input.Value == null && !input.Prototype.Required) continue;   // 空且非必填 → 完全不查
    if (!CheckRequired(input, out r))                          results.Add(r);
    else if (!CheckSelectorQuote(input, out r))                results.Add(r);
    else if (!CheckImageQuote(input, out r))                   results.Add(r);
    else if (!CheckVariableQuote(input, out r))                results.Add(r);
    else if (!CheckText(input, out r))                         results.Add(r);
}
```

一个字段最多产出一条错误。

### 4.1 `CheckRequired`

```csharp
if (input.Prototype.Required && !new RequiredValidationRule().Validate(input, out var msg))
    → ValidationResult{ LineNo = _lineNo, ErrorMessage = msg }
```

`RequiredValidationRule`：`Value == null || IsNullOrEmpty(Value.Content)` → `"{Label ?? Name}不能为空"`。

### 4.2 `CheckSelectorQuote` / `CheckImageQuote`

两者共用一个泛型骨架 `CheckFieldValues`：

- 标量：`Value.Kind == kind && !IsNullOrEmpty(Content) && !existsFunc(Content, input, out errContent)`
  → `string.Format(errMsg, errContent)`
- `FieldValueGroupArray`：遍历 `Contents(kind)`，收集所有失败项，`Distinct()` 后逗号拼接
- 只处理 `GroupArray`，**`FieldValueArray`（15）不在这里查**

`ExistsSelector`：

```csharp
if (field.Block.Prototype is MagicBlockPrototype) return true;   // 魔术块跳过
return _selectorNameSet.Contains(content) || IsNullOrEmpty(content);
```

`_selectorNameSet` 来自 `Workspace.Selectors.AllSelectors.Select(SelectorFieldValueConverter.ToFieldValueContent)`。

`ExistsImages`：

```csharp
if (IsNullOrEmpty(content)) return true;
var parts = content.Split('|');        // 多图分隔符
收集不在 _imageNameSet 里的项 → errorContent = string.Join(", ", list)
```

`_imageNameSet` 来自 `Workspace.ImageSelectors.Select(m => m.Name)`。

### 4.3 `CheckVariableQuote` —— 三个分支

| 分支 | 条件 | 判定 |
|---|---|---|
| ① 标量变量 | `Value.Kind == Variable && !IsNullOrEmpty(Content)` | `ExistsVariable(Content)` |
| ② `FieldValueArray` | `Value is FieldValueArray` | 遍历 `Contents(Variable)`，逐个 `_variableCounter.HasVariable(c)` |
| ③ `FieldValueGroupArray` | `Value is FieldValueGroupArray` | 遍历 `Contents(Variable)`，逐个 `ExistsVariable(c)` |

注意 ② 和 ③ 用的判定函数不同：② 直接 `HasVariable`，③ 走 `ExistsVariable`（多数 `__x__` 豁免）。

`ExistsVariable`：

```csharp
if (Regex.IsMatch(expression, "^(__)[a-z]+(__)$")) return true;      // 内建变量, 无条件放行
variableName = VariableExtension.ExtractVariableFromExpression(expression);
return _variableCounter.HasVariable(variableName);
```

### 4.4 `CheckText`

按 `Value.Kind` 分流：

| Kind | 处理 |
|---|---|
| `Text` (10) | 依次跑 `input.Prototype.ValidateRules`（Required / Regular / Range），**第一个失败即停** |
| `Array` (15) | 若 `ContainsKind(Operator)` → `FieldValueArrayPythonTools.ConvertFieldArrayToPythonCode(...)`；`Status == Error` 时把 `ErrorMsg` 当错误 |
| `GroupArray` (16) | 遍历 `Groups[].Items[]`，对每个含运算符的 `FieldValueArray` 做同上处理，错误消息前缀 `item.Key` |
| `Operator` (17) | 直接报 `"表达式存在非法符号{Content}"`——运算符不允许单独作为字段值 |
| 其他 | 不查 |

**关键点**：表达式类字段（含运算符的数组）是**转成 Python 代码后做语法校验**的，
所以写错括号/运算符会被这里抓住；纯 `Text` 只跑正则/范围。

---

## 5. 输出变量校验

```csharp
foreach (var output in outputs)
{
    if (!CheckOutput(output, out r))                        results.Add(r);
    else if (!CheckOutputDefaultValueVariableQuote(output, out r)) results.Add(r);
}
```

`CheckOutput`（仅 `output.IsEnable`）：

```csharp
if (!Utility.IsValidPythonVariableName(output.Name, true /*allowChinese*/, false))
    → "变量名称 \"{name}\" 无效(请输入字母、数字、中文、下划线的组合，且不能以数字开头"
else if (Utility.IsPythonKeyWord(output.Name))
    → "变量名称\"{name}\"无效(变量名称不能是Python关键字)"
```

`CheckOutputDefaultValueVariableQuote`（错误处理默认值）：

- 标量：`DefaultValue.Kind == Variable && !IsNullOrEmpty(Content) && !ExistsVariable(...)`
  → `"错误处理"中引用的变量"{x}"不存在`
- `FieldValueArray`：逐个 `HasVariable`，收集缺失项

注意这里**只查 `FieldValueArray`，不查 `GroupArray`**——与输入侧不同。

---

## 6. 处理器钩子

```csharp
foreach (var handler in block.Handlers)
{
    if (!handler.OnValidation(block, out string msg) &&
        !validationResults.Any(p => p.LineNo == _lineNo))
    {
        validationResults.Add(new ValidationResult{ LineNo = _lineNo, ErrorMessage = msg });
        break;                     // 同一行只报一条
    }
}
```

- `BaseBlockHandler.OnValidation` 是 `abstract`，**197 个 handler 各自实现**
- `DefaultBlockHandler` 兜底 `return true`
- 若前面的输入/输出检查已经在该行报过错，handler 的错误会被跳过（`Any(p => p.LineNo == _lineNo)`）

各类别 handler 数量：Web 34 / Excel 20 / Mobile 13 / Programing 10 / Ocr 8 / Word 7 /
WebService 7 / Dialog 7 / Datetime 7 / Win32 ≈30 / 其余若干。

---

## 7. 字段级规则（`IValidationRule`）

由块定义 JSON 的 `validationRules[]` 反序列化，`Kind` 决定实现：

| 实现 | JSON 字段 | 行为 |
|---|---|---|
| `RequiredValidationRule` | `message` | `Value == null \|\| Content 为空` → `"{Label ?? Name}不能为空"` |
| `RegularValidationRule` | `pattern` / `message` | **只对 `Kind == Text`**；`!Regex.IsMatch(Content, Pattern)` → `"{Block.Title}的\"{name}\"格式不正确"` |
| `RangeValidationRule` | `min` / `max` / `message` | **只对 `Kind == Text`**；`double.TryParse` 失败或超出 `[min, max]` → 报错 |

`RangeValidationRule` 三种文案：

- 有 min 有 max：`"{Block.Title}的\"{name}\"必须在 {min} 到 {max} 之间"`
- 只有 min：`"...不能小于 {min}"`
- 只有 max：`"...不能大于 {max}"`
- 都没有：`throw new InvalidOperationException("未设置数值范围")`

> 由此推论：数字型字段如果值写成裸数字（非 `10:` 前缀的字符串），`double.TryParse` 仍能过；
> 但如果带了非数字内容，`RangeValidationRule` 会直接报错而非跳过。

---

## 8. 变量作用域跟踪（`VariableCounter`）

```csharp
public VariableCounter(Workspace workspace, IFlow flow)
{
    _globalVariables = new HashSet<string>(workspace.GlobalVariables.Select(m => m.Name));
    if (flow?.Parameters != null)
        foreach (var p in flow.Parameters) Increase(p.Name);      // 流程入参视为已定义
}

public void AppendBlock(Block block)
{
    if (block.Scope.HasFlag(ScopeFlag.End))
    {
        while (_blockStack.Count > 0)
        {
            var popped = _blockStack.Pop();
            foreach (var output in popped.Outputs)
                if (output.IsEnable) Decrease(output.Name);
            if (popped.Scope.HasFlag(ScopeFlag.Start)) break;      // 弹到配对的开块为止
        }
    }
    if (block.Scope != 0 && !block.Scope.HasFlag(ScopeFlag.Start)) return;   // 纯 End 块到此为止

    _blockStack.Push(block);
    foreach (var output in block.Outputs)
        if (output.IsEnable) Increase(output.Name);
}
```

`HasVariable` 三种模式：

| 模式 | 查哪里 |
|---|---|
| `Local` | 只看局部计数器 |
| `Global` | 只看全局变量集 |
| `All`（默认） | 先全局，再局部 |

`Increase`/`Decrease` 对**全局变量名直接跳过**——全局变量不进局部计数器。

**推论**：

- 循环体内定义的变量在循环结束后消失（`End` 块弹出时 `Decrease`）
- 同一变量在嵌套作用域里可能出现多次，靠引用计数区分
- 全局变量永远可见，不受块顺序影响

---

## 9. 常量与枚举

| 名称 | 值 |
|---|---|
| 变量引用正则 | `^(__)[a-z]+(__)$` |
| 列表拼接分隔符 | `", "` |
| 图像多选分隔符 | `\|` |

```csharp
public enum FieldValueKind { Text=10, Variable=11, Selector=12, Expression=13,
                             Image=14, Array=15, GroupArray=16, Operator=17, APExpr=18 }
public enum ValidationResultType { Visual, Code }
public enum ReferenceSearchMode { All, Global, Local }
[Flags] public enum IndentFlag { None=0, Start=1, End=2 }    // 缩进配对
[Flags] public enum ScopeFlag  { None=0, Start=1, End=2 }    // 变量作用域
public enum ValidationRuleKind { Required, Regular, Range }
```

`FieldValueKind` 的数值与 `flow.json` 中 `inputs` 值前缀（`10:` `11:` `12:` …）完全对应——
校验器就是按这些 kind 分派分支的。
