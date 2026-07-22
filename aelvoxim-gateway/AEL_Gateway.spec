# -*- mode: python ; coding: utf-8 -*-
"""
Aelvoxim Desktop Gateway — PyInstaller spec

Build:
    cd C:\Aelvoxim\aelvoxim-gateway
    pyinstaller --clean AEL_Gateway.spec

Output:
    dist/AEL Gateway/  (directory with AEL Gateway.exe)
"""
import os
from pathlib import Path

_PROJ = Path(os.getcwd())

# ── Block heavy modules ──
_EXCLUDES = [
    'paddleocr', 'paddle', 'paddlepaddle', 'paddlex',
    'easyocr',
    'torch', 'torchvision', 'torchaudio',
    'onnxruntime', 'onnxruntime_training',
    'transformers', 'tokenizers', 'safetensors',
    'modelscope', 'datasets',
    'scikit_image', 'scikit_learn',
    'pytest', 'black', 'mypy', 'ruff', 'coverage',
    'setuptools', 'pip', 'wheel', 'twine',
    'psycopg2', 'SQLAlchemy',
    'flask', 'werkzeug', 'jinja2',
    'bce_python_sdk',
    'openai',
]

# ── Data files to bundle ──
_DATAS = [
    (str(_PROJ / 'config.yaml'), '.'),
    (str(_PROJ / 'ocr_worker.py'), '.'),
]

# ── Hidden imports ──
_HIDDEN = [
    'uvicorn',
    'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto',
    'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
    'uvicorn.middleware',
    'websockets',
    'websockets.legacy',
    'websockets.legacy.client',
    'websockets.legacy.server',
]

a = Analysis(
    [str(_PROJ / 'main.py')],
    pathex=[str(_PROJ)],
    binaries=[],
    datas=_DATAS,
    hiddenimports=_HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_EXCLUDES,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='AEL Gateway',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AEL Gateway',
)
