# PySide6 GUI Architecture for 影刀迁移助手 

Working patterns from the GUI build. All pitfalls below were hit and fixed in a real session.

## Layering (keep Qt out of services)

```
UI (main_window / list_widgets / account_dialog / progress_panel / result_dialog)
  → services (app_scanner, package_builder, migration_client, account_service, task_runner)
  → storage (database / account_repository / credential_protector)
```

- UI widgets never touch sqlite or requests directly — inject services via constructor (`MainWindow(scanner=..., account_service=..., account_repository=...)`).
- Domain models are frozen dataclasses (`AppInfo`, `Account`, `PackagePayload`, `ScanResult`, `TaskResult`); task state is mutable (`MigrationTask`).
- `TaskRunner.run(tasks, app_by_id, account_by_id) -> list[TaskResult]` is pure orchestration, testable without Qt. `build_tasks(apps, accounts)` = N×M, account-major order. `failed_tasks(tasks)` returns new pending retry tasks with `attempt+1`.

## QThread + worker pattern (no terminate)

```python
self._thread = QThread(self)
self._worker = MigrationWorker(runner, tasks, app_by_id, account_by_id)
self._worker.moveToThread(self._thread)
self._thread.started.connect(self._worker.run)          # worker.run is a slot
self._worker.task_started.connect(self.panel.on_task_started)
self._worker.completed.connect(self._on_migration_completed)
self._thread.finished.connect(self._cleanup_thread)      # deleteLater both, clear refs
self._thread.start()
```

- Safe stop: `worker.request_stop()` sets a flag on the runner; runner checks it before starting each next task and cancels remaining pending tasks. Never `terminate()`.
- Keep strong refs to thread+worker on the window until `finished`, else "QThread: Destroyed while thread is still running".
- `closeEvent`: if migrating, prompt, `request_stop()`, `thread.quit()`, `thread.wait(5000)`, then accept.

## Worker signals (workers.py)

```python
class MigrationWorker(QObject):
    task_started = Signal(object, int, int)   # task, index, total
    stage_changed = Signal(object, object)    # task, TaskStage
    task_finished = Signal(object)            # TaskResult
    log_line = Signal(str)
    completed = Signal(object)                # list[TaskResult]
```
`AccountValidationWorker(QObject)` with `succeeded = Signal(object)` / `failed = Signal(str)`; the dialog owns its QThread and cleans up on `finished`.

## DPAPI credential protector (ctypes, no third-party dep)

```python
class WindowsDPAPI:
    def __init__(self, entropy: bytes): ...
    def protect(self, plaintext: str) -> bytes      # CryptProtectData
    def unprotect(self, blob: bytes) -> str         # CryptUnprotectData
```
- Use `CRYPTPROTECT_UI_FORBIDDEN (0x1)`; entropy = app-specific constant (`b"YingdaoMigrationAssistant:v1"`) so the blob binds to user+app.
- Always `LocalFree(out_blob.pbData)` in a finally block.
- Wrap failure in `CredentialUnavailable("本地凭据不可用，请重新输入密码")`.
- Verify the DB file bytes contain no plaintext password (test asserts `b"secret" not in db_path.read_bytes()`).

## AccountRepository (SQLite)

- `INSERT ... ON CONFLICT(username) DO UPDATE` with `COLLATE NOCASE UNIQUE` for case-insensitive upsert; after upsert re-select by username (lastrowid unreliable on conflict-update).
- Timestamps: `datetime.now(timezone.utc).isoformat()`.

## List widgets — selection state gotchas

- Each `QListWidgetItem` stores its stable id in `Qt.UserRole`; selection tracked in a `set[id]`, not by row order.
- `QCheckBox.stateChanged` emits **int** in PySide6: `checked = int(state) == 2`. `state == Qt.Checked` is False → "select all" silently no-ops.
- Disabled/unavailable accounts: `set_selected_ids` intersects with selectable ids; `selected_accounts()` re-filters `validation_state == "verified"`; rows get flags minus `Qt.ItemIsEnabled`.
- When mutating checkstates in bulk, `blockSignals(True)` then re-emit `selection_changed` once.

## Styling

- `theme.qss` loaded at app level (`app.setStyleSheet(_load_theme())`); objectNames drive specific widget styles (`QFrame#card`, `QPushButton#startBtn`, `QListWidget#appList`).
- Progress panel: `QProgressBar` range `0..total` for step ticks, or `0..100` for percent; log view `setMaximumBlockCount(500)` + `sanitize()` every line.

## Sliding toggle switch widget (账号名/显示名 example)

Self-drawn knob switch, no dependencies:

```python
class _TrackButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(48, 24)
        self.setCursor(Qt.PointingHandCursor)
    def paintEvent(self, event):
        super().paintEvent(event)              # QSS paints the track background
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        knob_w = 18
        x = self.rect().width() - knob_w - 3 if self.isChecked() else 3
        painter.setBrush(QColor("#ffffff")); painter.setPen(QPen(QColor("#ffffff"), 1))
        painter.drawEllipse(x, (self.rect().height() - knob_w) // 2, knob_w, knob_w)
```

- Track colors via QSS `QPushButton#modeSwitch` (off: `#cfd4e4`, `:checked`: purple `qlineargradient`).
- Side labels highlight by **property selector**: `QLabel#switchLabel[active="true"] { color:#665cf6; font-weight:700 }`. After `setProperty("active", ...)` you MUST `lbl.style().unpolish(lbl); lbl.style().polish(lbl)` or Qt never re-evaluates the selector — the highlight silently never appears.
- `clicked(bool)` delivers a plain int-like bool in PySide6; track mode in `self._mode` explicitly instead of trusting widget state. `set_mode()` should `blockSignals` while forcing `setChecked`.
- Parent widget (`NameModeSwitch`) lays out `[label_left][track][label_right]` with 6px spacing and emits `mode_changed(str)`.

## pytest (24 tests pass)

- Env: `QT_QPA_PLATFORM=offscreen python -m pytest -q`.
- `tests/__init__.py` and `tests/ui/__init__.py` required for collection.
- Fakes in `tests/fake_http.py` (NOT imported from conftest — `from tests.conftest import FakeResponse` collides with the interpreter's own `tests` package when pytest adds paths). FakeSession needs `headers = {}` attribute (MigrationClient updates UA only for real `requests.Session` via `isinstance` check).
- UI test fixture: build `MainWindow` with real `AppScanner` against a temp tree + real SQLite in tmp_path; assert titles, task-count math, enable/disable rules, unavailable-account filtering.
- Mock-based client tests assert request construction: URLs, headers, `isBot` string values, `packageMd5` source, PUT bodies, stage sequence — no real network.

## PyInstaller GUI spec notes

- Absolute paths everywhere in the spec (`Analysis(['D:/xobt/main.py'], pathex=['D:/xobt/src'], datas=[('D:/xobt/src/migration_assistant/resources/theme.qss', 'migration_assistant/resources')])`).
- `console=False`; exe name `影刀迁移助手`; single-file EXE with `a.binaries + a.datas` inline.
- Rebuild failure `PermissionError: [WinError 5] 拒绝访问` = a previous instance still runs; `taskkill /F /IM 影刀迁移助手.exe` first.
- Offscreen smoke: launch with `QT_QPA_PLATFORM=offscreen`, wait ~6s, assert process still alive, then terminate.
- Record SHA256 with `certutil -hashfile dist\影刀迁移助手.exe SHA256` for handoff.
