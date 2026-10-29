# 其余三类校验

流程校验之外，影刀还有三套相互独立的校验，分布在另外三个 DLL 里。
它们与 `ValidationTool` 没有调用关系，触发时机也不同。

---

## 一、应用发布设置校验

**DLL**：`ShadowBot.Runtime.dll` · **类**：`ShadowBot.Runtime.Validator.AppPublishSettingValidator`

### 触发时机

应用发布（上传到云端）时，校验应用名 / 描述 / 使用说明三个字段。

### 前置开关

```csharp
if (User.Current.EnterpriseCustomSettings.AppPublishSettings != null)
{
    var rules = ...CustomAppPublishRules;
    if (rules != null && rules.Enabled)   // ← 企业没配规则就整体放行
        foreach (var field in fields) { ... }
}
return ValidationResult.Success();
```

### 字段映射（注意主字段名）

```csharp
public FieldRule GetFieldRule(string fieldName)
{
    switch (fieldName.ToLower())
    {
        case "appname":     return fields.Name;
        case "description": return fields.Description;
        case "instruction": return fields.Instruction;
    }
    return null;                 // 其他字段不校验
}
```

> **`appname` 而不是 `name`** —— 拼错就查不到规则、静默放行。

### 单字段校验

```csharp
var rule = GetFieldRule(fieldName);
if (rule == null) return Success();

if (rule.Required && string.IsNullOrEmpty(value)) return Fail(GetTips(fieldName));

foreach (var r in rule.Rules)
{
    if (fieldName.ToLower() == "instruction")
        value = StripHtmlTags(value);         // ← 只有使用说明会剥 HTML

    if (!string.IsNullOrEmpty(r.PatternForWin) &&
        !string.IsNullOrEmpty(r.Message) &&
        !Regex.IsMatch(value ?? "", r.PatternForWin))
        return Fail(r.Message);
}
```

要点：

- `Required` 失败用**系统内置文案**（`Strings.AppInfoEditView_*NotEmpty`）
- 正则失败用**企业自定义的 `r.Message`**——`Message` 为空时这条规则被静默跳过
- `instruction` 会先剥标签再匹配，所以富文本编辑器的 HTML 不会误伤正则

### `StripHtmlTags`

```csharp
html = Regex.Replace(html, "<[^>]+>", "");   // 去标签
html = WebUtility.HtmlDecode(html);          // 反转义 &amp; 之类
html = html.Replace('\u00a0', ' ');          // 不换行空格 → 普通空格
return html.Trim();
```

### 结果模型

```csharp
public class ValidationResult      // ← 与流程校验的 ValidationResult 同名但不同类, 无 LineNo
{
    public bool   IsValid      { get; set; }
    public string ErrorMessage { get; set; }
    public static ValidationResult Success() => ...;
    public static ValidationResult Fail(string message) => ...;
}
```

---

## 二、选择器校验

**DLL**：`ShadowBot.UIAutomation.Tools.dll` · **类**：`ShadowBot.UIAutomation.Tools.Common.SelectorValidator`

### 入口

```csharp
public static bool IsValid(this Selector selector, ref string error)
{
    if (!ValidateSelector(selector, ref error)) return false;              // 结构
    if (!ValidSelectorNameAndGroup(((SelectorModel)selector).Name, ref error)) return false;  // 名称
    return true;
}
```

### 名称校验（`ValidSelectorNameAndGroup`）

按顺序三条：

| 条件 | 报错资源键 |
|---|---|
| 名称为空 | `SelectorEditWindow_Element_Name_Cannot_Be_Empty` |
| 含 XML 转义字符（`XmlElementExtension.HasEscapeChars`） | `..._Cannot_Contain_Special_Character` |
| **能被 `Guid.TryParse` 解析** | `SelectorValidator_InvalidFormat` |

> 第三条容易踩：影刀给元素自动生成的占位名可能是 GUID 形态，
> 这种名字会被判为「格式不合法」，必须改成可读名。

### 结构校验（`ValidateSelector`）

| 检查 | 条件 | 报错键 |
|---|---|---|
| 空节点 | XPath 未启用 **且** `Path` 为空 | `SelectorEditWindow_Tips_Empty_Nodes` |
| XPath 空段 | XPath 启用但 `Segments` 全空 | `SelectorEditorViewModel_XPathCannotEmpty` |
| 属性空名 | 任一节点的任一 `attr.Name` 为空 | `SelectorEditWindow_Element_Node_Name_Cannot_Be_Empty` |

---

## 三、XML 校验

**DLL**：同上 · **类**：`ShadowBot.UIAutomation.Tools.XmlValidator`

```csharp
public static bool IsValid(object value)
{
    if (value == null || !(value is string) || (string)value == "") return false;
    if (((string)value).IndexOf(':') >= 0) return false;   // 含命名空间前缀 → 无效
    XmlConvert.VerifyName((string)value);                  // 非法 XML 名会抛 XmlException
    return true;
}
```

要点：

- **含 `:` 一律无效**（哪怕 `XmlConvert.VerifyName` 本身允许命名空间前缀）
- `VerifyName` 抛异常即为非法——调用方需要自己 try/catch，或依赖特性框架捕获
- 空串 / 非字符串 / null 直接无效

---

## 四、UI 层校验

**DLL**：`ShadowBot.Shell.Development.dll`

### `OutputParamValidationRule`（WPF 全局变量编辑框）

```csharp
public override ValidationResult Validate(object value, CultureInfo cultureInfo)
{
    if (!CanEmpty && IsNullOrEmpty(value))            → GlobalVariableEditViewModel_VariableNotEmpty
    if (!Utility.IsValidPythonVariableName(v, true, true)) → ..._VailVariableName
    if (Utility.IsPythonKeyWord(v))                   → ..._VariablePythonKeyWord
    if (v.Length > 50)                                → ..._VariableLongWords
    return ValidationResult.ValidResult;
}
```

> 全局变量名**限长 50**——流程内变量没有这个限制，两者不是一套规则。

### `FlowErrorChecker`（校验结果的调用方）

```csharp
private ValidationResult[] ValidateFlow(IFlow flow) => new ValidationTool().Validate(flow);
```

把每个流程的结果包成 `FlowValidationResult { IFlow Flow; ValidationResult[] Results; }`，
再通过 `ErrorChangeNotification(ValidationScope.AllFlow / SingleFlow)` 推给错误列表 UI。

**这是 UI 侧唯一调用 `ValidationTool` 的地方**——想确认"编辑器里报的红线从哪来"，
链路就是：`FlowErrorChecker` → `ValidationTool.Validate` → `VisualFlowValidation`。
