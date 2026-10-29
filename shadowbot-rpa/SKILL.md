---
name: shadowbot-rpa
description: "Use when working with 影刀 RPA apps, APIs, or migration."
---

# 影刀 RPA (ShadowBot / WinRobot360) 平台操作

影刀 RPA 客户端 = ShadowBot.exe。云端 API 分两处: 登录走 `api.yingdao.com`, 业务走 `api.winrobot360.com`。


## 本地数据布局 (应用列表来源!)

**"可迁移应用"列表来自本地扫描, 不是云端 API!**

```
%LOCALAPPDATA%\ShadowBot\users\<userUuid>\apps\<appUuid>\xbot_robot\package.json
```

- 每个 `apps/<appUuid>/xbot_robot/` 是一个应用: package.json (name/uuid/version/依赖) + Python 代码 (.py/.pybx) + `.dev/` (flow.json, icon/)
- 一台机器多个 userUuid 目录 = 多个账号的本地缓存; 扫描全部 `users/*/apps/*` 才是完整列表
- 凭据: `%APPDATA%\Xbot Deployer\contacts.db3` 表 `user(username,password,remark)` — **明文密码**, 原软件会读写它
- 客户端日志: `%LOCALAPPDATA%\ShadowBot\log\*.log` — 打印 `UploadAppToServer success: appId:... packageMd5:... packageJsonMd5:...`, 调试上传的黄金线索

## 登录 (OAuth2 密码模式)

POST `https://api.yingdao.com/oauth/token`
- 头: `Authorization: basic <base64("sns:T7svFcIL4foPj1j9")>` = `c25zOlQ3c3ZGY0lMNGZvUGoxajk=`; `Content-Type: application/x-www-form-urlencoded; Charset=UTF-8`; User-Agent `Mozilla/4.0 (compatible; MSIE 9.0; Windows NT 6.1)`
- 体: `username=<账号>&password=<RSA密文>&crypt=metal&grant_type=password&scope=all`
- 密码加密: RSA 公钥 (X.509 PEM, 1024 位) + **PKCS1v15** + Base64 (= JSEncrypt 兼容; cryptography 用 `padding.PKCS1v15()`)
- 返回: `{"access_token": "...", "token_type": "bearer", "expires_in": 2592000, ...}`

## 云端 API (基址 `https://api.winrobot360.com/api/client`)

| 接口 | 方法 | 说明 |
|---|---|---|
| `/app/develop/list` | POST | 云端应用列表。必须 body: `{"groupId":null,"name":"","pageType":1,"pageDTO":{"page":1,"size":30},"sortBy":"4"}` |
| `/app/file/assignUploadUrl` | POST | 获取 OSS 上传地址 (调两次, isBot 字符串 true/false) |
| `/app/develop/create` | POST | 提交应用创建 |

公共头: `Authorization: bearer <token>`, `Xybot-Client-RequestId: 57214437-d52d-4f1f-a23f-87c3e9b84adb`

## 应用迁移/上传机制 (抓包还原)

**核心**: 迁移 = 把本地应用**复制为云端新应用** (生成新 uuid + 名称加 `_云迁_接收于<时间戳>` 后缀)。

1. 读本地 package.json → uuid 改为新生成的 uuid, name 加后缀
2. `assignUploadUrl` 调**两次**:
   - `{"appId":"<新uuid>","appType":"app","version":"","isBot":"true"}` → package.bot 地址
   - `{"appId":"<新uuid>","appType":"app","version":"","isBot":"false"}` → package.json 地址
   - ⚠️ `appType` 是 `"app"` 不是 `"PC"`; `version` 是空串; `isBot` 是**字符串** "true"/"false"
3. `PUT <uploadUrl>` 上传两个文件 — **不能带 Content-Type 头** (否则 OSS 403 SignatureDoesNotMatch)
   - package.bot = 整个 xbot_robot 目录打包 zip (排除 venv/__pycache__, package.json 用改后的)
   - package.json = 改后的 package.json 内容
4. `create` 请求体关键字段:
   - `appPackage.appIcon` = `""` (空串合法; 字段名是 **appIcon** 不是 icon!)
   - `appPackage.appType` = `"app"`, `appPackage.name` = 新名
   - `elementLibraryStatus` = 0, `groupId` = `""`
   - **`packageMd5` = assignUploadUrl(isBot=false) 响应的 `fileKeyMd5`** (不是文件真实 md5!)
   - 依赖字段直接取自本地 package.json: externalDependencies/internalDependencies/internalautodependencies/ipaasDependencies

## Pitfalls

- `develop/list` 参数错 → 返回 `{"code":200,"success":true}` **无 data** (静默空, 不报错); 正确参数见上表
- create 报 `[应用icon不能为空]` → 字段名是 `appIcon` (空串可以), 不是 `icon`; 也别放在 appPackage 外面
- assignUploadUrl 报 `导入失败，XX存在正在编辑的版本` → 云端已有同名在编版本
- 免费账号 (PersonalBasic): `shadowbot.shell-cli studio` 系列不可用 (apiCode 5010), 但 `auth login/current` 可用
- 登录 Authorization 前缀用小写 `basic` (抓包确认原软件如此)
- 上传后会生成新 uuid — 云端列表会出现"新名_云迁_接收于..."条目

## 可视化块剪贴板格式 (复制块 → 粘贴还原可视化指令)

影刀编辑器复制块后剪贴板含 5 种格式，**识别关键 = 自定义格式 `ShadowBot.Flow.Blocks`**：
- `ShadowBot.Flow.Blocks`: 内容是 .NET `DataObject.SetData("ShadowBot.Flow.Blocks", json字符串)` 的 BinaryFormatter 产物 — 固定前缀(16字节 magic `96 A7 9E FD 13 3B 70 43 A6 79 56 10 6B B2 88 FB` + 16字节 SerializedStreamHeader + `06 01 00 00 00` + 7bit 长度) + UTF-8 JSON
- `HTML Format`: CF_HTML 文档, fragment = `<shadowbot id="blocks" style="display:none">{"version":"1.0.1","contentType":1,"data":"<base64(blocksJSON)>"}</shadowbot>`; **头部的 StartHTML/EndHTML/StartFragment/EndFragment 偏移是模板里的过期值**(影刀自己写的就是错的, 原样保留也能识别, 别"好心"重算)
- `System.String` / `UnicodeText` / `Text`: 各块**显示名**(中文标题) 用 `\r\n` 连接 (非关键格式)
- 块 JSON = 数组, 每块比 flow.json 里的多 `"__kind__":0`; 输入值编码: `10:`字符串字面量 / `11:`变量引用 / `13:`表达式·bool·globals() / `16:`列表; 输出 = `{"name":变量名,"isEnable":bool}`

**复刻工具**: shadowbot-block-to-clipboard 技能自带 `scripts\shadowbot_flow_clipboard.py <flow.json|blocks.json> [--titles "标题1|标题2"] [--new-ids]` — Python 生成载荷文件 + 内嵌 PS 脚本, 经 powershell.exe 用同款 .NET API 写入。
- Windows PowerShell 5.1 的 `SetData` 产物与影刀复制**字节级一致**
- 坑: 载荷必须用 `Get-Content -Raw` 读文本文件传字符串 — 若用 `ConvertFrom-Json` 会把 JSON 变成 PSObject, SetData 序列化的就是对象图而非原始字符串, 字节对不上
- 写后必须 `Clipboard.SetDataObject($do, $true)` (copy=true) 否则进程退出后剪贴板失效
- 粘贴进已有流程且块 id 与现有重复时用 `--new-ids` 重新生成 uuid
- 流程存储只有 `.dev/*.flow.json`; 剪贴板是复制/粘贴唯一通道, 无其他缓存文件

## 指令库 (全部内置指令定义) — 位置与用法

**内置指令定义 = 影刀安装目录 `ShadowBot.Runtime.Development.dll` 的 .NET 内嵌资源** (资源名 `ShadowBot.Runtime.Development.Resources.Zh_CN.*.blocks.json`, 43 个分类文件, 425 个指令, 无重名; 已提取版随 shadowbot-block-to-clipboard 技能打包)。用反射提取:
```powershell
$asm = [System.Reflection.Assembly]::LoadFrom("<影刀安装目录>\ShadowBot.Runtime.Development.dll")
$fs = $asm.GetManifestResourceStream("ShadowBot.Runtime.Development.Resources.Zh_CN.workflow.blocks.json")
```
已提取版随 shadowbot-block-to-clipboard 技能打包在 `scripts\shadowbot_blocklib\` (文件名 `Zh_CN_<分类>_blocks_json`; 同 DLL 还有 `En_US.*`、`Zh_TW.*`、`buildin_types.json`=类型系统)。文件结构 `{"types":[...],"blocks":[...]}`; 每个块定义字段: name / title(显示名) / comment(带%参数%占位) / isCondition·isLoop(容器块, 带 indent/scope/foldState) / inputs[{name,label,required,tips,type,default,defaultDisplay,editor{kind,options[{value,display}]}}] / outputs[{name,type,tips}] / statement / function / settingsControl。

**指令构建器与用法**见 shadowbot-block-to-clipboard 技能（`scripts\shadowbot_block_builder.py`）。

### 扩展自定义指令 (指令市场/用户自建)

**扩展指令定义 = 每个 app 的 `xbot_extensions\<code>\prototype.block.json`** (同 DLL 资源格式, 但 name 带 `xbot_extensions.<code>.` 前缀, 另有 `extension`=扩展显示名, `statement:"process.invoke_activity"`, `function` 指向扩展内 processN)。已并入构建器 catalog: 扫描 `users\*\apps\*\xbot_extensions\*\prototype.block.json` (数量随已装扩展而变, 如 拼多多数据获取/shadowbot_pdd_decode、activity_excel_v2、shadowbot_list)。
- **扩展块 flow.json 里多一个 `"block_title": "<扩展名>/<指令title>"`** (扩展名取定义的 `extension` 字段, 如 "拼多多数据获取/数据中心-交易数据-交易概况-数据总览"), 构建时必须带上
- 扩展块输入名可以是中文 (如 网页对象/日期时间/数据导出目录); 默认值形如 `13:None`/`10:昨日` 直接按定义填
- **标准库没有 `excel.read_cell`** — 读单元格 = `excel.read_data_from_workbook` 的 `read_way=cell` + `cell_row_num`(行号) + `cell_column_name`(列字母); `excel.get_row_count` 返回 max_row; `sheet_name` 留 `10:` = 当前活动表
- **多块流程合成**: CLI 一次只生成一个指令; 多块用 `import shadowbot_block_builder as sb` + 循环 `build_block(name, overrides)` 收集 blocks/titles + `sb.to_clipboard(blocks, '|'.join(titles))` 一次粘贴; 块间用上块输出变量名做下块 `11:` 输入; Windows 反斜杠路径别放 `python -c` 单行 (`\U` 转义 SyntaxError), 写临时 .py 执行
- 指令市场扩展按 app 安装 (每 app 一份 xbot_extensions), 粘贴到某 app 前提是该 app 已装对应扩展
- 扩展定义可能跨 app 重复 (同扩展多 app 安装), catalog 按 name 去重即可

## 静态分析应用流程（不开影刀读流程）

**两个 selectorsV2.xml 别搞混**：真正生效的元素库是 `xbot_robot\selectorsV2.xml`（体积大、选择器全）；
`xbot_selectors\element_<code>\selectorsV2.xml` 可能只是占位壳（几 KB，criteria 指向官网）。
`xbot_selectors\requirements.txt` 记录引用了哪个元素库 code。

**flow.json 结构**（`.dev\processN.flow.json`）：
```json
{"name":"2.2.获取评分和排名","memo":null,"kind":"...","blocks":[...],"parameters":[...]}
```
每个 block：`{id, name, isEnabled, foldState, inputs, outputs}`。`name` 是**指令名**（如 `web.element.get_details`、
`dictionary.set`、`workflow.if`、`xbot_extensions.activity_feishu_bitable.add_records`、`process.run`）。

`inputs` 是 **dict**（不是 list）：`{"参数名":{"value":"<前缀>:<值>"}}`。值前缀：
- `10:` 字符串字面量　`11:` 变量引用　`12:` **元素选择器引用**（对应 selectorsV2.xml 里的 selector name）　
  `13:` 表达式/bool　`15:` 表达式数组　`16:` 列表

**遍历子块**要同时看 `blocks` / `children` / `body`（容器块）与 `branches` / `cases`（条件块），否则会漏掉大半流程。

**应用级信息在 `xbot_robot\package.json`**：`flows[]` 列出全部流程（`name` + `filename`，filename 对应 processN）、
`variables[]` 是**应用变量（含明文凭据！）**、`internaldependencies` 是扩展依赖。
`process.run` 的 `process = "10:process16"` 就是子流程调用链入口。

**元素库选择器定位链**：`<selector>` 下是 `<web x:name=标签 id=... class=... index=...>` 与 `<optional id=... index=...>`
嵌套。`index` 即**同标签第 N 个**（0 基）—— 这是选择器脆弱性的来源：`dp#idx=35`、`ppd#idx=7` 这类位置索引
会随页面模块增减漂移，改版即失配；而 `#id` 型（`corePriceDisplay_desktop_feature_div` 等）稳定得多。
`winTitle` 属性会保留**采集时所在页面标题**，可据此反推这套选择器覆盖了哪些站点/商品。

## 读运行日志 / 给流程做耗时分析

影刀有**两层**日志，别只看第一层：

### 1. 框架日志 `%LOCALAPPDATA%\ShadowBot\log\`

`YYYYMMDD.log`（主）+ `YYYYMMDD.engine.log`（引擎）+ `.provider.log` / `.browserbridge*.log` / `.wer.log`。
- 只记：studio 开/关、`RefreshLocalData` 包刷新、`Create engine,engineIndex=`、`xbot engine running, pid=`、
  `xbot engine exited, exitCode=`、崩溃截图上传 —— **没有模块级耗时，也没有逐步执行痕迹**
- `[LastRunBrowserMetadata] start_run flowId=main` 是**全应用共用**的，**不带 appId**，不能直接归因到某个应用
- **归因方法**：用 `engine.log` 的 `engine start pid: N` ↔ `[APP_INFO] appId: '<uuid>'` ↔ `engine end pid: N`
  配对，得出「哪个应用在几点跑了多久」。注意 `engine end` 段可能缺失（未正常结束）
- `users/<uuid>/task_logs/*.tasklog` 只覆盖**调度任务**；`users/<uuid>/tasks.db3` 表 `tasks` 是调度任务历史
- `users/<uuid>/user.db3`、`%LOCALAPPDATA%\ShadowBot\recording.db3` **都没有运行历史表** —— 别在这找

### 2. 流程自己的日志（做耗时分析必须用这个）

流程里 `programing.log` 指令（编译成 `xbot_visual.programing.log(...)`，裸 `print` 来自 `from xbot import print`）
的输出**不落盘到 log 目录**，只在 studio 运行面板里。**要落盘必须用「导出日志」指令**：

```python
xbot_visual.programing.exportlog(custom_folder_path=<目录>, file_name="日志文件.txt", _block=("main", 213, "导出日志"))
```

- 产出格式 `[信息] [2026-09-15 16:38:47.164] 文本` —— **带毫秒时间戳**，且流程里常自带 `N.模块名开始执行/执行完毕` 标记
- ⚠️ **每次运行覆盖同名文件**，只保留最后一轮 → 要前后对比必须先另存改名
- **所以给流程加埋点不需要写代码**：在目标块前后各插一个「打印日志」指令，跑一轮再解析导出日志即可
- 解析套路：按 `N.模块名开始执行` / `执行完毕` 切段 → 算耗时；细粒度则按相邻两行的**后一行内容**给时间差归类
  （如后一行是 `xxx下载成功` → 归为下载；后一行是 `已将…插入/已经[{...}]` → 归为飞书写入）

### 3. 编译产物 `xbot_robot/*.py` —— 比 flow.json 更好读

应用每次保存/运行会把流程编译成 `xbot_robot\main.py` + `processN.py`（`main.py` = 主流程）。
**每个块都带定位标签**，可直接还原结构，不必再解析 `.dev/*.flow.json`：

```python
xbot_visual_...(...  , _block=("4.广告统计", 170, "批量添加记录"))
xbot_visual.process.run(process="process21", package=__name__, inputs={...}, _block=("main", 185, "调用流程"))
```

- `_block=(模块名, 块序号, 指令名)` → 模块名就是流程里的注释分区，能直接读出业务模块划分
- 结构信号是靠 `#region/#endregion`、`#if/#endif`、`#endloop` 注释 + 缩进表达的（Python 缩进本身就是真相）
- 常见模式：`for _xbot_retry_time in range(4): try: … except: …; time.sleep(3)` 是影刀自带的**失败重试 3 次 + 3s 间隔**
- `xbot_visual.decrypt(glv['xxx'])` 说明应用变量里是密文；`glv` = `package.variables`

### 4. 扩展指令的源码就在盘上 —— 排查性能问题的关键

`apps/<appUuid>/xbot_extensions/<code>/` 里是**明文 Python**（如 `activity_feishu_bitable/` 有 `bitable.py` 42 KB +
每个指令一个瘦包装 `update_record.py` / `add_records.py` / `get_record_by_filter.py` …）。**慢的指令直接读源码找根因**，别猜。

常见根因（飞书多维表格扩展）：
- `table_kind_identity="NAME"` 会在**每次**调用里 `get_tables()`（一次 list-tables API）；改成 `"ID"` + 直接填 table_id 即可省掉
- `get_fields` 用 `page_size=5` 翻页，字段多的表要发多次请求
- `FieldsFactory` 按 table_id 用**类属性**缓存，所以跨 `process.run` 是否复用取决于引擎是否重载模块
- `request()` 内置 10 次重试 + `time.sleep(2*i)`；token 失效码 `4001 / 99991663 / 2200`

**量化方法**：这些扩展只依赖 `requests`/`http.client` + app_id/app_secret，**可以在影刀外面用同一套凭据照抄它的连接写法复现**。
照抄要点：每次 `http.client.HTTPSConnection(host, port=443)` 新建（不 keep-alive），这跟 `requests.Session` 的延迟差异很大。
同一基座下约：`list tables` 0.3s、`get fields` 0.5s、`PUT 更新 1 条` 1.0s，**但约 1/3 概率单次飙到 20~25 秒**（服务端/网络长尾）——
所以"某个指令慢"要先区分是**代码问题**还是**长尾抖动**：连打 6~10 轮看分布，别只看一次。

### 5. 唯一可用的并发/异步手段：扩展 `activity_b354cbb0`「并发调用子流程」

**影刀没有内置的异步指令**，并行只能靠这个扩展（`apps/*/xbot_extensions/activity_b354cbb0`）。
它**不在**每个 app 里 —— 新应用要用时把目录复制过去，或从指令市场安装。

| 参数 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `流程名` | str | — | **必须是 `package.json` 里 flows 的 `name`（不是 `filename`），且不能是 `main`** |
| `流程参数` | dict | — | 传给子流程的入参 |
| `daemon` | bool | `True` | `True` → 线程随主流程结束被回收，**主流程不等**（=异步/fire-and-forget）；`False` → 主流程结束时 Python join 它，**会一直等到结束**（=并发但等待） |

源码事实（`invoke.py`）：实现就是 **同进程 `threading.Thread`** 跑 `mod.main(inputs)`，返回 thread 对象存进 `args["thread_instance"]`。
它靠 `traceback` 反推当前应用目录再按 `name` 找流程 → **只能调用本应用的流程**。

**5 个必知限制**（决定它能不能用在生产流程里）：
1. 子线程只有 `try/finally`、**没有 `except`** → 子流程报错**不会传回主流程**，异步失败是**静默**的。要在子流程里自己 `programing.log` 记错。
2. **没有并发上限**，发几个就开几个线程；几十个写飞书的线程会打爆限流。**建议一批 5~10 个**。
3. 目标必须是**独立流程**，所以要先把"一次动作"包成子流程再发起。
4. 传进去的对象（如飞书 `bitable_instance`）**多线程共用是否安全未验证** → 并发子流程内各自 `create_instance` 更保险。
5. 影刀引擎主流程是单线程的；`web.*` 浏览器指令在子线程里行为未验证 → **只把纯 HTTP 类操作（飞书写入等）放进并发子流程，别放浏览器操作**。

**判断一个写入点能不能异步的通用规则**（做这类分析时按顺序问 4 个问题）：
1. 该指令的 `outputs` 之后有没有被引用？（生成代码里看有没有 `var = xbot_visual.process.run(...)` 且 `var` 后文是否出现）
2. 同文件**之后**有没有读同一张表？
3. **跨流程 / 跨产品轮次**有没有人读同一张表？（main 常按产品循环调用子流程，前一轮写、后一轮读，是隐藏依赖）
4. 它是不是「读视图拿 record_ids → `batch_delete_record` 清空整表 → 重写」的覆盖模式？→ 这种**绝对不能异步**，会和下一轮的清空交错。
