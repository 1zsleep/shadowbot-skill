---
name: shadowbot-rpa-migration
description: Migrate 影刀 RPA apps to cloud (inlines third-party activity packs, survives weak networks); bundled PySide6 GUI + CLI script.
---

# ShadowBot (影刀) RPA App Migration & API

把本地影刀应用发布/迁移到云端账号。GUI 支持多选应用 × 多账号串行队列、DPAPI 加密存储账号、进度面板、安全停止、只重试失败项；打包时**内联第三方指令集**（绕过社区版/基础账号对高级指令集的拦截），并**适配弱网**（分段超时、分类重试、日志脱敏）。

目录内容：
- `main.py` + `src/migration_assistant/` — GUI 源码（UI/services/storage 分层），`python main.py` 直接运行
- `tests/` — pytest（扫描/打包/模型/网络重试/指令集内联/流程还原/UI），`QT_QPA_PLATFORM=offscreen pytest`
- `assistant.spec` + `app_icon.ico` + `pyproject.toml` — 打包配置

命令行入口：`scripts/migrate_app.py`（仅依赖 requests + cryptography）——`SB_PASS=<密码> python migrate_app.py --app <xbot_robot目录> --user <账号> [--name 新名]`；`--check` 只登录+列云端应用；密码优先读环境变量 SB_PASS。与 GUI 版走同一套 create 流程。

## Core facts

- **App list is LOCAL, not API.** Scanning `%LOCALAPPDATA%\ShadowBot\users\<userUuid>\apps\<appUuid>\xbot_robot\package.json` yields the migratable apps (one dir per app). Multiple `users\*` dirs = cached accounts; scan them all.
- **Account NAME resolution (userUuid → 账号名)**: the ID dirs (`users\<userUuid>\` etc.) are opaque uuids, but two local files map them to human account names:
  - `users\Account.xml` — `<ArrayOfAccountInfo>` of every cached login: `<Name>`=login id (e.g. `demo-account-1`), `<UserName>`/`<UserInfoDisplayName>`=显示名 (e.g. `演示账号一`), plus `<Password>` base64 blobs and `<UserGradeKind>` free/enterprise. Best source; one file covers all accounts. Note `<Password>` blobs for OTHER enterprise accounts are plaintext-ish base64 in the client's own file — not our doing, but don't log it.
  - `users\<userUuid>\user.db3` — table `developmentsync_apps_v2` has an `ownerName` column = account name per synced app. Handy when you need per-app ownership, but note a single dir can hold apps from several owners (shared/team machine).
  - **Reverse lookup (账号名 → userUuid)**: scan every `users\*\user.db3` with `SELECT DISTINCT ownerName FROM developmentsync_apps_v2`; the dir whose ownerName matches is that account's local apps root. Use it as `--out` for `create_app.py` so the new app shows up in that account's client (verified: `demo-account-1` → `users\<其userUuid>\apps`).
  - UI convention: `AppInfo.owner_name` = login id (falls back to display name when unknown), `AppInfo.display_name` = 显示名 (falls back to owner name when Account.xml has no entry, e.g. `demo-account-1`). Scan count drops vs naive glob because only dirs with a real `apps\` subdir count.
- **Cloud APIs live under `https://api.winrobot360.com/api/client/app/...`**, auth via `https://api.yingdao.com/oauth/token`. The `/app/...` paths WITHOUT `/api/client` prefix return 404; `/api/rpa/...` 404s too.
- **Migration semantics**: each upload creates a NEW cloud app — fresh `uuid4()` + name suffix `_云迁_接收于<YYYY年MM月DD日 HH时MM分SS秒>`.
- ShadowBot official CLI: 影刀安装目录下的 `shadowbot.shell-cli.exe` (auth login/current, studio create/open/save). Studio APIs need an **enterprise** account; free/basic accounts get `API 不可用`.
- **运行本地应用（真实执行，非模拟）**: `shadowbot.shell-cli console task run --app-id <uuid> --app-type developed`（默认同步模式带流式日志，`--wait-timeout 120s` 限时）。app 列表用 `console app --page-size 50`（含本地开发的 developed 应用，appId=本地 uuid）；客户端需在线（`system health` → console-restapi ready）。注意 Code-Activity/Zh-CN 里的 xbot/xbot_visual 是**代码模板**（visual_action 全 pass、xbot_ai 包不存在），脱离客户端 python 直跑 main.py 不可行。
- Original tool stores creds plaintext in `%APPDATA%\Xbot Deployer\contacts.db3` (table `user`).

## Migration workflow (per app)

1. Login (OAuth2 password grant, password RSA-encrypted — see below).
2. Read local `package.json`; generate new uuid + 云迁 name.
3. Rewrite package.json (new uuid/name); zip the `xbot_robot` dir (exclude `venv`, `__pycache__`; KEEP `.dev/`) → this is `package.bot`.
4. `assignUploadUrl` with `isBot:"true"` → uploadUrl for package.bot; PUT raw zip bytes.
5. `assignUploadUrl` with `isBot:"false"` → uploadUrl for package.json; PUT rewritten json bytes.
6. `develop/create` with `packageMd5` = the fileKeyMd5 from the isBot=false response.

Exact request bodies and full field lists: `references/captured-api-format.md`.

## 内联第三方指令集（绕过「社区版或基础账号不能使用高级指令集」）

影刀只对**官方市场**指令集放行；第三方/自定义指令集（`xbot_extensions/` 下非官方来源）在社区版/基础账号上同步或发版会被拒。原因是服务端按 `package.json` 的 `internaldependencies` / `internalautodependencies` 声明判定，而上传的 `xbot_robot/**` 并不含 `xbot_extensions/`，所以声明一旦存在就必然失败。

`src/migration_assistant/services/custom_activity_inliner.py` 在**打包时于内存中**做等价替换（不碰磁盘源文件）：

1. 从 `xbot_extensions/` 挑出**流程里真正调用过**的非官方指令集流程（`package_version >= 8` 才判为「高级」，`<= 7` 直接放过）；
2. 每个被调用的指令集流程搬成应用自己的 `processN`：
   - `<flow>.py` 直接用指令集自带的生成代码（运行行为 1:1），只改流程名与内部调用指向；
   - `.dev/<flow>.flow.json` 由 `shadowbot_flow` 从该 `.py` **反向还原**（供设计器显示）；
   - 指令集自带的网页元素（选择器）并进应用元素库；
3. 应用里对扩展指令的调用同步改成调用新的应用内流程（`.py` 与 `.flow.json` 一起改）；
4. 从 `package.json` 依赖声明里删掉这些指令集。

**无损原则（重要）**：某流程若无法完整还原成可视化块（例如含 `programing.snippet` 代码块），该指令集**既不转换也不动依赖**，只记进 `InlineReport.skipped` 交人工 —— 否则会出现「缺块的 flow.json，用户一保存就把代码冲掉」。

`InlineReport` 另外给出 `converted` / `dropped_deps` / `kept` / `warnings`，迁移结束要把它展示给用户，别静默吞掉。

**create 必须用改写后的 package.json**：`Payload.package` 保存的是内联后重写的依赖声明；`develop/create` 若仍提交原始 `package.json`，服务端按原声明判定，照样报「社区版或基础账号不能使用高级指令集」。

## 弱网容错与日志（`src/migration_assistant/services/netutil.py` + `logging_setup.py`）

弱网下 `requests` 的 timeout **不覆盖 DNS 解析**，`getaddrinfo` 失败会在几秒内直接抛错，单纯拉长超时救不回来。因此分三层处理：

| 层 | 做法 |
| --- | --- |
| 超时 | 连接 120s / 登录与接口 180s / 上传传输间隔 600s（`src/migration_assistant/config.py` 可调） |
| 重试 | 登录 3 次、获取上传地址 3 次、上传 2 次，间隔 2s、5s；**`create` 不重试** —— 重复创建会在目标账号留下同名应用，语义比重试重要 |
| 分类 | `netutil.classify()` 把 DNS 失败 / TLS 中断 / 连接重置 / 拒绝连接 / 代理异常 / 超时 / 5xx 拆成不同 `code` 并标注 `retryable`；失败后 `dns_probe()` 复核一次解析耗时（带超时上限，不拖住主流程） |

日志按天分文件（`%LOCALAPPDATA%\YingdaoMigrationAssistant\logs\assistant-YYYY-MM-DD.log`，保留 14 天），`SensitiveDataFilter` 对密码字段、`Authorization` 头、预签名 URL 的 query 统一打 `[REDACTED]`。排障先看这个文件——它会写明是解析不到域名、连接被重置，还是服务器返回了什么。

## Pitfalls

- **`isBot` is a STRING** `"true"`/`"false"`, not a boolean; `appType` is `"app"` (not `"PC"`); `version` is `""` (not `"1"`). Wrong types → `400 Failed to read request`.
- **`develop/create` uses `packageMd5`**, which equals the server's `fileKeyMd5` from assignUploadUrl — NOT the md5 of the uploaded bytes. Field name is `appIcon` (empty string OK), not `icon`. `elementLibraryStatus` is int `0`.
- **PUT to OSS must NOT send Content-Type** — any Content-Type breaks the presigned signature → `403 SignatureDoesNotMatch`.
- `develop/list` is POST with `pageType:1` and nested `pageDTO:{page,size}`; GET → 405, `pageType:4` → 500 or empty data. sortBy is the string `"4"`.
- **`develop/create` fails with `系统出现异常，请联系系统管理员` when `appId` is the LOCAL app uuid** — assignUploadUrl (both isBot calls) AND create must all use the NEW uuid4() (the same one written into package.json). The server ties the created app to that new uuid; local uuid passes login/upload but dies at the CREATING stage with a generic business error —— mock/单测测不出来，验收要跑一次真实迁移（login→assign→PUT→create→`develop/list` 确认）。
- DNS/connect flakiness to these hosts is common — wrap requests in a small retry (3 tries, 5s wait).

## 指令库依赖

运行时的指令块库 `src/migration_assistant/resources/shadowbot_blocklib.json` 已随源码提供；需要按最新指令库重建时：

```bash
python scripts/build_blocklib.py          # 自动定位 ../shadowbot-block-to-clipboard/scripts/shadowbot_blocklib
```

脚本从本技能目录向上逐级查找同名技能目录，也接受环境变量 `SHADOWBOT_BLOCKLIB_DIR` 或命令行第一个参数指定目录。

## 构建 exe

- `python -m PyInstaller --noconfirm --clean assistant.spec` → `dist/影刀迁移助手.exe`。spec 用 PyInstaller 内置的 `SPECPATH` 推导全部路径（脚本 `main.py`、`pathex=src`、`datas=theme.qss / app_icon.png / shadowbot_blocklib.json`、`icon=app_icon.ico`），`console=False`。**spec 内没有 `__file__`**（PyInstaller 直接 exec），要用 `SPECPATH`；也不要写死绝对路径。
- **强制打包解释器自带的 OpenSSL**（`libssl-3-x64.dll` / `libcrypto-3-x64.dll`）：PyInstaller 的 crypto hook 可能从 System32 抓到别的软件的副本，与 Python 的 `_ssl.pyd` 版本不匹配，exe 内 `import ssl` 失败 → 登录报 `Can't connect to HTTPS URL because the SSL module is not available`。spec 已把这两个从 `a.binaries` 剔除并改回 `os.path.join(sys.base_prefix, 'DLLs', name)`。
- **CLI**（旧版）：`--onefile --clean --console`；不要 `--windowed`。

**PyInstaller pitfalls**: exe file locked by a lingering process → `PermissionError: [WinError 5]` on rebuild; `taskkill /F /IM 影刀迁移助手.exe` first. GUI exe must not be launched with a console wrapper.

## 从0创建应用并迁移 (无现成应用时)

> 完整交付流程（需求→建应用→写指令→迁移→验证，含凭据索取规则）见 **shadowbot-instruction-spec** 技能规则 8。本节约为脚本用法。

`templates/app_template/` 提供脚手架，无需现成应用即可搭出完整应用目录：

1. **脚手架**: `python scripts/create_app.py --name <应用名> --out <影刀apps目录>` — 从 `templates/app_template/` 生成完整应用目录（新 uuid4；含 package.json / __init__.py / package.py / settings.json / imagesV2.xml / selectorsV2.xml / package.sigstore / main.py / .dev\main.flow.json / .dev\workspace.state.json），自动填 uuid+name、重新生成 sigstore
2. **写指令**: 用 shadowbot-block-to-clipboard 技能构建块 → 写入 `.dev\main.flow.json`（无 __kind__）+ 按生成代码模式写 `main.py`（见 shadowbot-instruction-spec 规则7）。**批量流程直接 `scripts/build_flow.py --app <xbot_robot目录> --spec blocks.json` 一次生成两者**（详见下文「多块流程组装」）
3. **本地创建云端认**: 影刀客户端创建的空应用会登记 `user.db3 developmentsync_apps_v2` (isSynchronized=1, ownerName=账号), 且出现在云端 develop/list 里 — 本地直建目录也可被扫描到
4. **迁移**: `python scripts/migrate_app.py --app <生成的xbot_robot目录> --user demo-account-1 [--name 云端名]`（自包含脚本，login → assignUploadUrl×2 → PUT → create），云端用**另一个新 uuid4**
5. **验证要点**: `develop/list` 的 data 是 list; **记录顶层 `name` 为 None, 名字在 `appName` 字段**; 用请求体 `name:<应用名>` 服务端过滤确认命中 (返回含 appId/appName/authority/versionStatus)。登录/列表对 api.winrobot360.com 常超时, 包 3 次重试

### 多块流程组装

`scripts/build_flow.py` 从块规格 JSON 一次生成 `.dev\main.flow.json` + `main.py`（不走剪贴板）。值编码与构建器一致（`10:`字符串 / `11:`变量 / `13:`表达式·bool / `16:`列表，裸值自动推断）；容器块（if/for/try）自动补结束标记，结束块紧跟开启块。

blocks.json 格式（顺序 = 流程顺序；块间引用用上块输出变量名 = 指令定义 outputs 的 name，如 excel_instance / excel_row_count / excel_data）：
```json
[{"name": "excel.launch", "overrides": {"launch_way": "10:open", "open_filename": "10:C:\\path\\a.xlsx"}},
 {"name": "excel.get_row_count", "overrides": {"workbook": "11:excel_instance"}},
 {"name": "programing.log", "overrides": {"text": "11:excel_row_count"}},
 {"name": "excel.close", "overrides": {"excel_instance": "11:excel_instance", "close_way": "10:notsave"}}]
```

以 6 块流程为例（launch → get_row_count → read_data_from_workbook → log×2 → close），要点：
- 读单元格 = `excel.read_data_from_workbook` read_way=`10:cell` + cell_row_num=`10:65` + cell_column_name=`10:B`；总行数 = `excel.get_row_count`（两者 sheet_name 留 `10:` = 当前活动表）；打印 = `programing.log`（type 默认 `10:info`，text 传 `11:变量`，输出走影刀日志面板）
- **纯读取流程 `excel.close` 用 `close_way=10:notsave`（不保存）**，避免把没改过的文件写回；有修改的才用 save
- 交付前用 openpyxl 本地读一次目标文件，记下预期值（总行数 / 目标单元格值），汇报时给用户对照运行结果
- 迁移后独立复核：`SB_PASS=<密码> python migrate_app.py --check --user <账号> | grep <应用名>` 确认云端可见
