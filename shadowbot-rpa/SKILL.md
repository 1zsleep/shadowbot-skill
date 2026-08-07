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
- 一台机器多个 userUuid 目录 = 多个账号的本地缓存; 扫描全部 `users/*/apps/*` 才是完整列表 (本会话用户看到 35 个)
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
- 已验证: Windows PowerShell 5.1 的 `SetData` 产物与影刀复制**字节级完全一致** (1158/117/1771 字节逐一对比)
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

**指令构建器**: shadowbot-block-to-clipboard 技能自带 `scripts\shadowbot_block_builder.py`（在技能 scripts 目录下运行）
- `--search 关键词` 按 name/title/description 搜; `--list [关键词]` 列全部
- `python shadowbot_block_builder.py <指令名> [--set 参数=值 ...]` → 生成块 JSON 并写剪贴板 (走 shadowbot_flow_clipboard.py)
- 自动处理: 未指定参数用定义 default (None→null, "10:xxx"→原样, 有 defaultDisplay 补 display); select 参数自动从 options 匹配 value/display 补 display; bool 型裸值自动加 `13:`; 容器块自动加 foldState; comment 自动带 %占位%; outputs 自动生成 {name,isEnable:true}
- 值前缀: `10:`字符串字面量 / `11:`变量引用 / `13:`表达式·bool / `16:`列表; 裸值按参数 type 推断 (bool→13:, 其他→10:)

### 扩展自定义指令 (指令市场/用户自建)

**扩展指令定义 = 每个 app 的 `xbot_extensions\<code>\prototype.block.json`** (同 DLL 资源格式, 但 name 带 `xbot_extensions.<code>.` 前缀, 另有 `extension`=扩展显示名, `statement:"process.invoke_activity"`, `function` 指向扩展内 processN)。已并入构建器 catalog: 扫描 `users\*\apps\*\xbot_extensions\*\prototype.block.json` (本机 59 个扩展 ~4585 个自定义指令, 如 拼多多数据获取/shadowbot_pdd_decode 113 个、activity_excel_v2 1156 个、shadowbot_list 352 个)。
- **扩展块 flow.json 里多一个 `"block_title": "<扩展名>/<指令title>"`** (扩展名取定义的 `extension` 字段, 如 "拼多多数据获取/数据中心-交易数据-交易概况-数据总览"), 构建时必须带上
- 扩展块输入名可以是中文 (如 网页对象/日期时间/数据导出目录); 默认值形如 `13:None`/`10:昨日` 直接按定义填
- **标准库没有 `excel.read_cell`** — 读单元格 = `excel.read_data_from_workbook` 的 `read_way=cell` + `cell_row_num`(行号) + `cell_column_name`(列字母); `excel.get_row_count` 返回 max_row; `sheet_name` 留 `10:` = 当前活动表
- **多块流程合成**: CLI 一次只生成一个指令; 多块用 `import shadowbot_block_builder as sb` + 循环 `build_block(name, overrides)` 收集 blocks/titles + `sb.to_clipboard(blocks, '|'.join(titles))` 一次粘贴; 块间用上块输出变量名做下块 `11:` 输入; Windows 反斜杠路径别放 `python -c` 单行 (`\U` 转义 SyntaxError), 写临时 .py 执行
- 指令市场扩展按 app 安装 (每 app 一份 xbot_extensions), 粘贴到某 app 前提是该 app 已装对应扩展
- 扩展定义可能跨 app 重复 (同扩展多 app 安装), catalog 按 name 去重即可

## 参考

- `references/migration-api-samples.md` — 抓包还原的完整请求/响应样本
