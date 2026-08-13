# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: 影刀迁移助手 (GUI 单文件, 带图标).

可移植版本: 所有路径由本 spec 所在目录自动推导 —— 技能包 (shadowbot-rpa-migration)
拷到任何电脑后, 在该目录执行 `python -m PyInstaller --noconfirm --clean assistant.spec`
即可重建 dist/影刀迁移助手.exe, 无需修改任何路径.
"""
import os

# PyInstaller 执行 spec 时没有 __file__, 用内置 SPECPATH (spec 所在目录)
SKILL_DIR = os.path.abspath(SPECPATH)

a = Analysis(
    [os.path.join(SKILL_DIR, 'main.py')],
    pathex=[os.path.join(SKILL_DIR, 'src')],
    binaries=[],
    datas=[(os.path.join(SKILL_DIR, 'src', 'migration_assistant', 'resources', 'theme.qss'),
            'migration_assistant/resources'),
           (os.path.join(SKILL_DIR, 'src', 'migration_assistant', 'resources', 'app_icon.png'),
            'migration_assistant/resources')],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

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
