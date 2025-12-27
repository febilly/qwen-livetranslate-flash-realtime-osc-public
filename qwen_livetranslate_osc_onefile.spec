# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

# 单文件（onefile）打包入口：start_server.py
# 说明：
# - 资源文件：打包 static/ 目录（用于 FastAPI 返回的 index.html）
# - 控制台程序：console=True，便于看到日志/报错
# - exe 名称：QwenLiveTranslateOSC（可按需改名）


a = Analysis(
    ["start_server.py"],
    pathex=[],
    binaries=[],
    datas=[("static/index.html", "static")],
    hiddenimports=[
        # uvicorn 运行时会动态选择实现，这里做保守兜底
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="QwenLiveTranslateOSC",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
