# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: 影刀迁移助手 (GUI 单文件, 带图标).

可移植版本: 所有路径由本 spec 所在目录自动推导 —— 技能包 (shadowbot-rpa-migration)
拷到任何电脑后, 在该目录执行

    python -m PyInstaller --noconfirm --clean assistant.spec

即可重建 dist/影刀迁移助手.exe, 无需修改任何路径.
"""
import os
import sys

# PyInstaller 执行 spec 时没有 __file__, 用内置 SPECPATH (spec 所在目录)
SKILL_DIR = os.path.abspath(SPECPATH)
RES_DIR = os.path.join(SKILL_DIR, 'src', 'migration_assistant', 'resources')

a = Analysis(
    [os.path.join(SKILL_DIR, 'main.py')],
    pathex=[os.path.join(SKILL_DIR, 'src')],
    binaries=[],
    datas=[
        (os.path.join(RES_DIR, 'theme.qss'), 'migration_assistant/resources'),
        (os.path.join(RES_DIR, 'app_icon.png'), 'migration_assistant/resources'),
        # 指令块库: 「内联自定义指令集」功能靠它把代码块还原成依赖声明
        (os.path.join(RES_DIR, 'shadowbot_blocklib.json'), 'migration_assistant/resources'),
    ],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

# crypto 的 hook 会用 ctypes.util.find_library() 从系统目录抓
# libssl-3-x64.dll / libcrypto-3-x64.dll (可能是别的软件装的), 与 Python 自带
# _ssl.pyd 的 OpenSSL 版本不匹配 —— 打出来的 exe 里 import ssl 会失败, 登录时报
# 「Can't connect to HTTPS URL because the SSL module is not available」。
# 这里强制替换成 Python 自带的那两个 DLL (按当前解释器位置定位, 与安装路径无关)。
_SSL_DLLS = ("libssl-3-x64.dll", "libcrypto-3-x64.dll")
_py_dlls = os.path.join(sys.base_prefix, "DLLs")
_patched = []
for _name in _SSL_DLLS:
    _src = os.path.join(_py_dlls, _name)
    if os.path.isfile(_src):
        _patched.append(_name)
a.binaries = [b for b in a.binaries if b[0] not in _SSL_DLLS]
a.binaries += [(n, os.path.join(_py_dlls, n), 'BINARY') for n in _patched]
if len(_patched) != len(_SSL_DLLS):
    print(f"!! 警告: 未找到 Python 自带的 {_SSL_DLLS} (在 {_py_dlls}), "
          f"打包后 HTTPS 可能不可用")
else:
    print(f"== 已强制使用 Python 自带的 OpenSSL: {_patched} @ {_py_dlls}")

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='影刀迁移助手',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(SKILL_DIR, 'app_icon.ico'),
)
