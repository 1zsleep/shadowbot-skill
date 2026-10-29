"""影刀迁移助手 — GUI 入口.

支持 `--selftest`: 自检 SSL / HTTPS / 加密库是否可用 (打包后的 exe 排障用).
结果同时写入 %TEMP%\\影刀迁移助手_selftest.txt 并弹窗显示。
"""
import sys


def selftest() -> int:
    import io
    import os
    import tempfile
    import traceback

    lines = [f"frozen={getattr(sys, 'frozen', False)}", f"exe={sys.executable}"]

    def check(label, fn):
        try:
            lines.append(f"{label}: OK  {fn()}")
        except Exception as exc:  # noqa: BLE001 - 自检要把所有失败都记下来
            lines.append(f"{label}: FAIL  {type(exc).__name__}: {exc}")
            lines.append("  " + traceback.format_exc().strip().splitlines()[-1])

    check("ssl", lambda: __import__("ssl").OPENSSL_VERSION)
    check("https", lambda: __import__("requests").get(
        "https://api.winrobot360.com/", timeout=15).status_code)

    def crypto():
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import padding  # noqa: F401
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        AESGCM.generate_key(128)
        return serialization.__name__

    check("cryptography", crypto)
    text = "\n".join(lines) + "\n"
    path = os.path.join(tempfile.gettempdir(), "影刀迁移助手_selftest.txt")
    try:
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError:
        pass
    print(text)
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication(sys.argv[:1])
        QMessageBox.information(None, "自检结果", f"{text}\n\n已写入:\n{path}")
        del app
    except Exception:  # noqa: BLE001 - 弹窗失败不影响结果落地
        pass
    return 0 if all("FAIL" not in line for line in lines) else 1


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    from migration_assistant.app import run
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
