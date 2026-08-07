---
name: shadowbot-rpa-migration
description: Migrate 影刀 RPA apps to cloud;
---

# ShadowBot (影刀) RPA App Migration & API

Publish/migrate local 影刀 RPA apps to the cloud,re-derive a Windows app's real API calls. GUI supports multi-select apps × accounts (N×M serial queue), DPAPI-encrypted account storage, progress panel, safe stop, and retry-failed-only.

**自包含迁移脚本: `scripts/migrate_app.py`**（仅依赖 requests + cryptography）——`SB_PASS=<密码> python migrate_app.py --app <xbot_robot目录> --user <账号> [--name 新名]`；`--check` 只登录+列云端验证；密码优先读环境变量 SB_PASS。已实测登录+列表通过。

## Core facts

- **App list is LOCAL, not API.** Scanning `%LOCALAPPDATA%\ShadowBot\users\<userUuid>\apps\<appUuid>\xbot_robot\package.json` yields the migratable apps (one dir per app). Multiple `users\*` dirs = cached accounts; scan them all.
- **Account NAME resolution (userUuid → 账号名)**: the ID dirs (`users\856795317222100994\` etc.) are opaque uuids, but two local files map them to human account names:
  - `users\Account.xml` — `<ArrayOfAccountInfo>` of every cached login: `<Name>`=login id (e.g. xxx), `<UserName>`/`<UserInfoDisplayName>`=显示名 (e.g. xxx), plus `<Password>` base64 blobs and `<UserGradeKind>` free/enterprise. Best source; one file covers all accounts. Note `<Password>` blobs for OTHER enterprise accounts are plaintext-ish base64 in the client's own file — not our doing, but don't log it.
  - `users\<userUuid>\user.db3` — table `developmentsync_apps_v2` has an `ownerName` column = account name per synced app. Handy when you need per-app ownership, but note a single dir can hold apps from several owners (shared/team machine).
  - **Reverse lookup (账号名 → userUuid)**: scan every `users\*\user.db3` with `SELECT DISTINCT ownerName FROM developmentsync_apps_v2`; the dir whose ownerName matches is that account's local apps root. Use it as `--out` for `create_app.py` so the new app shows up in that account's client (verified: xxx → `users\<其userUuid>\apps`).
  - UI convention: `AppInfo.owner_name` = login id (falls back to display name when unknown), `AppInfo.display_name` = 显示名 (falls back to owner name when Account.xml has no entry, e.g. `xxx`). Scan count drops vs naive glob because only dirs with a real `apps\` subdir count.
- **Cloud APIs live under `https://api.winrobot360.com/api/client/app/...`**, auth via `https://api.yingdao.com/oauth/token`. The `/app/...` paths WITHOUT `/api/client` prefix return 404; `/api/rpa/...` 404s too.
- **Migration semantics**: each upload creates a NEW cloud app — fresh `uuid4()` + name suffix `_云迁_接收于<YYYY年MM月DD日 HH时MM分SS秒>`.
- ShadowBot official CLI: 影刀安装目录下的 `shadowbot.shell-cli.exe` (auth login/current, studio create/open/save). Studio APIs need an **enterprise** account; free/basic accounts get `API 不可用`.
- Original tool stores creds plaintext in `%APPDATA%\Xbot Deployer\contacts.db3` (table `user`).

## Migration workflow (per app)

1. Login (OAuth2 password grant, password RSA-encrypted — see below).
2. Read local `package.json`; generate new uuid + 云迁 name.
3. Rewrite package.json (new uuid/name); zip the `xbot_robot` dir (exclude `venv`, `__pycache__`; KEEP `.dev/`) → this is `package.bot`.
4. `assignUploadUrl` with `isBot:"true"` → uploadUrl for package.bot; PUT raw zip bytes.
5. `assignUploadUrl` with `isBot:"false"` → uploadUrl for package.json; PUT rewritten json bytes.
6. `develop/create` with `packageMd5` = the fileKeyMd5 from the isBot=false response.

Exact request bodies and full field lists: `references/captured-api-format.md` (from live packet capture of Xbot Deployer.exe).

## Pitfalls (each cost real debugging time)

- **`isBot` is a STRING** `"true"`/`"false"`, not a boolean; `appType` is `"app"` (not `"PC"`); `version` is `""` (not `"1"`). Wrong types → `400 Failed to read request`.
- **`develop/create` uses `packageMd5`**, which equals the server's `fileKeyMd5` from assignUploadUrl — NOT the md5 of the uploaded bytes. Field name is `appIcon` (empty string OK), not `icon`. `elementLibraryStatus` is int `0`.
- **PUT to OSS must NOT send Content-Type** — any Content-Type breaks the presigned signature → `403 SignatureDoesNotMatch`.
- `develop/list` is POST with `pageType:1` and nested `pageDTO:{page,size}`; GET → 405, `pageType:4` → 500 or empty data. sortBy is the string `"4"`.
- **`develop/create` fails with `系统出现异常，请联系系统管理员` when `appId` is the LOCAL app uuid** — assignUploadUrl (both isBot calls) AND create must all use the NEW uuid4() (the same one written into package.json). The server ties the created app to that new uuid; local uuid passes login/upload but dies at the CREATING stage with a generic business error. This is THE "mock tests pass, real upload fails" trap — run one real E2E migration (login→assign→PUT→create→verify via `develop/list`) with a real account before declaring the GUI done.
- **SQLite + Qt worker threads**: `sqlite3.connect(..., check_same_thread=False)` is mandatory — the repo connection is created on the main thread but `AccountService.validate_and_save` / `decrypt_password` run inside QThread workers. Without it the generic `except Exception` in `AccountValidationWorker.run` swallows `ProgrammingError: SQLite objects created in a thread...` and the user sees a useless "发生未知错误" (real login succeeded underneath).
- **QDialog Enter key**: the first focusable button wins — the password "显示" toggle grabbed Enter. Fix: `setFocusPolicy(Qt.NoFocus)` on the toggle, `setDefault(True)` on the primary button, and `password_edit.returnPressed → start_validation`. User expects Enter = primary action.
- **Progress panel**: `on_task_started(task, index, total)` must get the real running index + total — passing `(task.attempt, task.attempt)` makes the counter show 0%/wrong numbers. Keep done/total as instance counters, never parse the label text. After completion DISABLE the stop button (`_on_migration_completed` must not re-enable it); re-enable fresh in `_start_migration` — user complained "都失败了为什么还有停止迁移按钮".
- `getpass.getpass()` shows no prompt in PyCharm Run window — use `input()`.
- Windows console output is GBK: when capturing Python/exe stdout into a pipe, decode with `gbk` (bash shows mojibake otherwise).
- DNS/connect flakiness to these hosts is common — wrap requests in a small retry (3 tries, 5s wait).



## GUI architecture & PySide6 pitfalls (all cost real debugging time)

Layered: UI (Qt) → services (scan/pack/client/runner) → storage (SQLite + DPAPI). Qt main thread only does UI; network in `QThread` + `moveToThread` workers that emit Signals; never `QThread.terminate()` — use a stop flag checked between tasks. Detailed patterns: `references/pyside6-gui-architecture.md`.

- **`getpass.getpass()` shows no prompt in PyCharm Run window** — the user hits enter on account and nothing appears. Use plain `input("影刀密码: ")` (visible password, reliable).
- **`QCheckBox.stateChanged` emits `int` (0/1/2), NOT `Qt.Checked` enum** — `state == Qt.Checked` is always False in PySide6; compare `int(state) == 2`. Hit in both list widgets' "select all".
- **Selection sets must filter unselectable rows**: `set_selected_ids` must intersect with selectable (verified) account ids, and `selected_accounts()` must re-filter — otherwise unavailable accounts silently stay selected.
- **Qt object ownership for workers**: keep strong refs to QThread + worker on the window; connect `thread.finished` to cleanup (`deleteLater` both); on close during migration, prompt, request_stop, `thread.quit()` + `wait(5000)` — no leaked "QThread: Destroyed while thread is still running".
- **Account password safety**: `Account` model never holds plaintext; `AccountService.decrypt_password()` unprotects on demand and does not cache. DPAPI via ctypes `CryptProtectData/CryptUnprotectData` with `CRYPTPROTECT_UI_FORBIDDEN`, entropy bytes, `LocalFree` the output blob.
- **`MigrationClient._put` must use `self.session.put`, not module-level `requests.put`** — otherwise the fake session injection in tests is bypassed and uploads are untestable.
- **pytest for Qt**: run with `QT_QPA_PLATFORM=offscreen`; `tests/` needs `__init__.py` (or `tests/ui/` won't collect); put fake HTTP fakes in a plain module (`tests/fake_http.py`) — importing them from `conftest.py` via `from tests.conftest import ...` collides with the interpreter's own `tests` package; fake session needs a `headers` attribute or `MigrationClient`'s UA update crashes.
- **Qt QSS does NOT support CSS `linear-gradient()`** — the rule is silently dropped (no error), buttons fall back to default gray and, on Windows dark mode, text renders white-on-white (the user's "白字白底看不清" complaint). Must use `qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #6157f5, stop:1 #8178ff)`. Diagnose QSS without a vision tool: render offscreen (`QT_QPA_PLATFORM=offscreen`, `win.grab().save(png)`) then PIL-count purple-ish pixels (b>200, b>r+40): ~0% = gradient dropped, healthy UI ≈ 5–10%.
- **PyInstaller onefile GUI lingers after clicking window X** — closeEvent must wait for EVERY background thread (`_migration_thread.wait(8000)`, `_scan_thread.wait(5000)`), then `QApplication.quit()`; after `app.exec()` returns, close the DB connection in `run()` (unclosed sqlite conn keeps the onefile child process alive → parent lingers too). Init `self._scan_thread = None` in `__init__` or closeEvent raises AttributeError pre-scan; `deleteLater()` the scan thread in its finished slot. Verify: EnumWindows → `PostMessageW(hwnd, WM_CLOSE)` → process exits <2s rc 0; 3 launch/close cycles with zero `tasklist` residue.

- **账号名/显示名 toggle switch** (user requested "左右滑动开关, 左账号名右显示名, 默认显示名"): `AppInfo` carries both `owner_name` (login id) and `display_name` (显示名) with `""` defaults; scanner fills them from `users\Account.xml` (login↔display map) + `users\<id>\user.db3` `developmentsync_apps_v2` uuid→ownerName, falling back to owner_name when Account.xml has no display entry (e.g. `xxx`). List rows render `应用名 (显示名)` / `应用名 (账号名)`; search must match name + owner_name + display_name (searching "xxx" or "xxx" both filter). Widget pattern: a checkable `QPushButton` subclass (48×24 track) that paints the white knob in `paintEvent` (`isChecked()` → knob at right), QSS colors the track (`QPushButton#modeSwitch:checked` = purple gradient), and two side labels whose active state is toggled via a QSS **property selector** — `QLabel#switchLabel[active="true"]` — which requires `lbl.style().unpolish(lbl); lbl.style().polish(lbl)` after `setProperty` or the highlight never repaints. `stateChanged`/`clicked` still deliver int; track `self._mode` explicitly.
- **Single-instance (user explicitly wants it)**: double-launching must NOT open a second window — activate the existing one instead. Pattern: module-level `QSharedMemory(_SINGLE_INSTANCE_KEY)`; `attach()` succeeds → existing instance: `detach()` + `_activate_existing_window()` + `return 0` from `run()` before creating the window; else `create(1)` and hold the reference (GC dropping it releases the lock). Activation = `EnumWindows` matching the exact title `影刀迁移助手` + `IsWindowVisible`, then bypass the Windows foreground lock: `ShowWindow(hwnd, SW_RESTORE)` (restores minimized), `AttachThreadInput(current_thread, fg_thread, True)` + `keybd_event(VK_MENU,0,0,0)` (Alt down) + `SetForegroundWindow` + Alt up + `BringWindowToTop`, then detach thread input. Verify with two real exe launches: 2nd exits rc 0, `EnumWindows` count == 1, minimized window gets restored; after closing, a fresh launch works again (lock released).

## 从0创建应用并迁移 (无现成应用时)

> 完整交付流程（需求→建应用→写指令→迁移→验证，含凭据索取规则）见 **shadowbot-instruction-spec** 技能规则 8。本节约为脚本用法。

模板和脚手架**随技能打包**，新电脑无需任何现成应用即可搭建完整应用目录：

1. **脚手架**: `python scripts/create_app.py --name <应用名> --out <影刀apps目录>` — 从 `templates/app_template/` 生成完整应用目录（新 uuid4；含 package.json / __init__.py / package.py / settings.json / imagesV2.xml / selectorsV2.xml / package.sigstore / main.py / .dev\main.flow.json / .dev\workspace.state.json），自动填 uuid+name、重新生成 sigstore
2. **写指令**: 用 shadowbot-block-to-clipboard 技能构建块 → 写入 `.dev\main.flow.json`（无 __kind__）+ 按生成代码模式写 `main.py`（见 shadowbot-instruction-spec 规则7）。**批量流程直接 `scripts/build_flow.py --app <xbot_robot目录> --spec blocks.json` 一次生成两者**（实测 6 块 Excel 读取流程一次通过，详见下文「多块流程组装」）
3. **本地创建云端认**: 影刀客户端创建的空应用会登记 `user.db3 developmentsync_apps_v2` (isSynchronized=1, ownerName=账号), 且出现在云端 develop/list 里 — 本地直建目录也可被扫描到
4. **迁移**: `python scripts/migrate_app.py --app <生成的xbot_robot目录> --user xxx [--name 云端名]`（自包含脚本，login → assignUploadUrl×2 → PUT → create），云端用**另一个新 uuid4**
5. **验证要点**: `develop/list` 的 data 是 list; **记录顶层 `name` 为 None, 名字在 `appName` 字段**; 用请求体 `name:<应用名>` 服务端过滤确认命中 (返回含 appId/appName/authority/versionStatus)。登录/列表对 api.winrobot360.com 常超时, 包 3 次重试

### 多块流程组装（实测一次通过）

`scripts/build_flow.py` 从块规格 JSON 一次生成 `.dev\main.flow.json` + `main.py`（不走剪贴板）。值编码与构建器一致（`10:`字符串 / `11:`变量 / `13:`表达式·bool / `16:`列表，裸值自动推断）；容器块（if/for/try）自动补结束标记，结束块紧跟开启块。

blocks.json 格式（顺序 = 流程顺序；块间引用用上块输出变量名 = 指令定义 outputs 的 name，如 excel_instance / excel_row_count / excel_data）：
```json
[{"name": "excel.launch", "overrides": {"launch_way": "10:open", "open_filename": "10:C:\\path\\a.xlsx"}},
 {"name": "excel.get_row_count", "overrides": {"workbook": "11:excel_instance"}},
 {"name": "programing.log", "overrides": {"text": "11:excel_row_count"}},
 {"name": "excel.close", "overrides": {"excel_instance": "11:excel_instance", "close_way": "10:notsave"}}]
```

实测 6 块 Excel 读取流程（launch → get_row_count → read_data_from_workbook → log×2 → close）要点：
- 读单元格 = `excel.read_data_from_workbook` read_way=`10:cell` + cell_row_num=`10:65` + cell_column_name=`10:B`；总行数 = `excel.get_row_count`（两者 sheet_name 留 `10:` = 当前活动表）；打印 = `programing.log`（type 默认 `10:info`，text 传 `11:变量`，输出走影刀日志面板）
- **纯读取流程 `excel.close` 用 `close_way=10:notsave`（不保存）**，避免把没改过的文件写回；有修改的才用 save
- 交付前用 openpyxl 本地读一次目标文件，记下预期值（总行数 / 目标单元格值），汇报时给用户对照运行结果
- 迁移后独立复核：`SB_PASS=<密码> python migrate_app.py --check --user <账号> | grep <应用名>` 确认云端可见

## Re-deriving an app's API (packet capture method)

When strings/PE analysis gives URLs but not exact request shapes:
1. Check `%TEMP%` / `%LOCALAPPDATA%\Temp` for runtime artifacts (pkg/Node apps leave `tmp*/xbot_robot.zip`; `*.tmp.js` are the Node runtime bundle, not app code).
2. Scan local client data dirs first — many "list" features are purely local.
3. `pip install mitmproxy`; set BOTH the IE proxy (reg `ProxyEnable=1`, `ProxyServer=127.0.0.1:8080`) AND `netsh winhttp set proxy 127.0.0.1:8080` — WinHTTP ignores the IE proxy (this is why traffic misses the proxy).
4. Install mitm CA: `certutil -addstore -f Root <mitmproxy-ca-cert.cer>` (from `~/.mitmproxy/`).
5. Have the user run the original exe manually; parse `xbot_capture.flow` with `mitmproxy.io.FlowReader`.
6. Extract exact bodies/headers per endpoint; verify md5/field relationships against the capture before coding.
