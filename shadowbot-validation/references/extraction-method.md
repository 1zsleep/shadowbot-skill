# 加密 DLL 抽取方法（.NET Reactor）

影刀 RPA 的 `ShadowBot.*.dll` 全部由 **.NET Reactor** 保护：方法体被抽空成
`ldnull; ret` 存根，字符串常量加密，局部变量签名被清空。但同时它把真实 IL
在运行时解密后放进内存里的一张表——这条路可以完整走通。

本文件是**可复现的操作手册**，脚本在同级 `../scripts/`。

---

## 前置

```bash
dotnet tool install --global ilspycmd --version 8.2.0.7535
export DOTNET_ROLL_FORWARD=LatestMajor     # 只装了 .NET 8 运行时的话必须
```

- `.NET 8 SDK`（`sbdump` 是 net8.0 项目）
- Python 3（`reconstruct.py` 只用标准库）
- 反编译用 `ilspycmd`

---

## Step 0 — 判断目标是否被保护

```bash
ilspycmd -t "<命名空间.类名>" "<DLL>"
```

保护特征：

- 方法体只有 `ldnull; ret` 或 `return null;`
- 类里有 `[MethodImpl(MethodImplOptions.NoInlining)]` 的空方法
- 静态构造里调用一个混淆命名空间的方法，形如
  `GuwBZAWohQ92tIs0TO.WqvWUdBjXjZdpSFeKY.<随机名>()`

混淆命名空间名每次构建都不同（`GuwBZAWohQ92tIs0TO` 只是 6.2.x 的值），但
**同一个版本的所有 DLL 共用同一套混淆器命名空间**。

---

## Step 1 — 运行时解密并 dump IL

```bash
cd scripts/sbdump
dotnet build -c Release

# 全量
dotnet bin/Release/net8.0/sbdump.dll dump "<影刀安装目录>/ShadowBot.Runtime.Development.dll" "<dump目录>"

# 只导某类（体积小很多，调试时用）
dotnet bin/Release/net8.0/sbdump.dll dump ".../ShadowBot.Runtime.Development.dll" "<dump目录>" "Validation"
```

产出：

```
<dump目录>/
├── manifest.tsv     # tok(hex) \t rva \t headerSize \t ilSize \t headerHex \t ilFile
└── il/
    ├── m_06000704.il
    └── ...
```

### 原理

1. `Assembly.LoadFrom` + `RuntimeHelpers.RunModuleConstructor` 强制触发模块初始化
2. Reactor 的解密器把每个方法的真实 IL 解密后写入
   `internal static Hashtable`（6.2.x 里字段名 `OfCKUX8ib5`）
   - **key = IL 在进程内的绝对地址**（boxed `Int64`）
   - **value = `{ int size; IntPtr decrypted; IntPtr original; }`**（boxed struct，字段名每次混淆）
3. 遍历元数据 `MethodDef`，取 `RelativeVirtualAddress`，读方法头判断 tiny(1 字节)/fat(12 字节)
4. `模块基址 + RVA + 头长度` 作为 key 去命中表，`Marshal.Copy` 出 `size` 字节

脚本里所有关键点都做成了**动态查找**（不硬编码混淆名）：找"元素最多的静态 `Hashtable`"、
按 metadata 字段顺序取 `{int, IntPtr, IntPtr}`。

---

## Step 2 — PE 重建

```bash
python scripts/reconstruct.py "<原DLL>" "<dump目录>" "<输出.deob.dll>"
```

做法：

1. 解析 PE 头 / 节表 / `#~` 元数据流，定位 **MethodDef 表**的行宽与起始偏移
2. 在原文件末尾追加一个节 `.sbnew`，把所有解密 IL 用 fat 头
   （12 字节：`flags=0x3003, maxstack=8, codeSize, localVarSigTok=0`）写进去
3. 把每个 MethodDef 行的 `RVA` 列改指向新节里的位置
4. 节数 +1，`SizeOfImage` 相应放大

---

## Step 3 — 反编译

```bash
ilspycmd -p -o "<源码目录>" "<输出.deob.dll>"       # 项目模式: 每类型一个 .cs
ilspycmd -t "<命名空间.类名>" "<输出.deob.dll>"      # 单类型
```

---

## Step 4 — 字符串常量

反编译后赋值会显示成 `c08CaUGyU(0x5A3F12 ^ <Module>{guid}.字段)` 这种形式。

Reactor 的字符串表是「偏移量查表」：

```
offset = 内联常量  XOR  字段值
字段值 = A ^ B            （在 <Module>{guid} 的静态构造里算出来）
```

拿到 offset 后直接让运行时解：

```bash
dotnet bin/Release/net8.0/sbdump.dll str "<DLL>" 7DD4 7DE6 7D5A
# 0x00007DD4 -> "..."
```

`sbdump str` 会自动在混淆命名空间里找「`static string f(int|uint)`」那个方法。

> 如果只知道字段名不知道 `A`/`B`，用 `ilspycmd -t "<Module>{guid}"` 看该字段的初始化表达式，
> 把 `A ^ B` 手算成 32 位无符号数，再与内联常量异或。

---

## Step 5 — 局部变量类型还原（手工）

Reactor 清空了方法体的 `LocalVarSigTok`，所以反编译出来的局部变量会变成
`/*Error: ... out-of-bounds*/` 或 `object`。要拿到可读源码只能按 IL 语义手工还原：

1. 看 `stloc`/`ldloc` 前后的 `call`/`callvirt` 签名，反推变量类型
2. 看 `newobj` 推断容器类型（`List<>` / `HashSet<>` / `Stack<>`）
3. 看 `isinst` 判断分支，确认实际类型
4. 常见模式：`Dictionary<string,int>` 计数器、`Stack<int>` 缩进栈、`HashSet<string>` 名称集

`sbdump` 保留了每个方法原始的方法头（`manifest.tsv` 第 5 列），但 `LocalVarSigTok`
本身已被清零，无法自动恢复。

---

## 目标 DLL 与解出规模（6.2.23）

| DLL | 解出方法数 | 主要校验内容 |
|---|---|---|
| `ShadowBot.Runtime.Development.dll` | 4902 | `ValidationTool` + 3 规则 + 197 handler |
| `ShadowBot.Runtime.dll` | 3745 | `AppPublishSettingValidator` |
| `ShadowBot.UIAutomation.Tools.dll` | 4834 | `SelectorValidator` / `XmlValidator` |
| `ShadowBot.Shell.Development.dll` | 14791 | `OutputParamValidationRule` / `FlowErrorChecker` |

> 解出数 ≈ 表中条目数。有少量方法拿不到（abstract / extern / P/Invoke，
> 本来就没有 IL），属正常。

---

## 踩坑清单

### 1. 表的 key 是 `Int64`，不是 `IntPtr`

最容易卡住的一条。Hashtable 里 `Int64` 和 `IntPtr` 是两个不同类型，`ContainsKey`
互相不认。**永远命中 0 条时先查这个**：

```csharp
tbl.ContainsKey(candLong)      // ✅
tbl.ContainsKey(new IntPtr(candLong))   // ❌ 永远 false
```

反射取值时同理：`Convert.ToInt64(de.Key)`，不要强转 `(IntPtr)`（会抛
`InvalidCastException: Unable to convert 'System.Int64' to 'System.IntPtr'`）。

### 2. 新增节的名称必须正好 8 字节

PE 的节表每项固定 40 字节，节名占前 8 字节。名字短于 8 字节却没补 `\0`
会让后面所有字段错位，ILSpy 报 `Missing data directory` 或干脆识别不出托管程序集。

```python
name = b".sbnew".ljust(8, b"\0")   # ✅
name = b".sbnew"                   # ❌ 整体偏移 -2，全乱
```

### 3. `ilspycmd -o` 单文件模式会漏类型

全量导出必须用项目模式：

```bash
ilspycmd -p -o "<dir>" "<dll>"    # ✅ 每类型一个文件
ilspycmd -o "<file>.cs" "<dll>"   # ❌ 类型会丢
```

### 4. manifest 里的 token 是十六进制

`reconstruct.py` 读第一列用 `int(p[0], 16)`。若自己改脚本输出了十进制，记得同步改解析。

### 5. 依赖 DLL 必须同目录

`Assembly.LoadFrom` 失败通常不是目标 DLL 的问题，而是它依赖的
`ShadowBot.Common.dll` / `Newtonsoft.Json.dll` 等没找到。
脚本已注册 `AssemblyResolve` 从目标 DLL 所在目录按名加载——**不要把目标 DLL 单独拷出来**。

### 6. `DOTNET_ROLL_FORWARD=LatestMajor`

`ilspycmd` 8.2 是 net6 目标，机器只有 .NET 8 运行时不设这个变量会直接
`You must install .NET to run this application`。

### 7. 新版可能换混淆器命名空间

`GuwBZAWohQ92tIs0TO.WqvWUdBjXjZdpSFeKY`、`c08CaUGyU`、`OfCKUX8ib5` 都是 6.2.x 的实际值。
升级后如果 `sbdump` 报 `no decryption table found`，把 `-l c` 列表里形如
`[A-Za-z0-9]{18,}\.[A-Za-z0-9]{18,}` 的异常命名空间找出来，替换 `Strings()` 里的前缀判断即可
（`dump` 模式本身不依赖名字，靠"元素最多的静态 Hashtable"定位）。
