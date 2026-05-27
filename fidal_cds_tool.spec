# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['fidal_cds_tool.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('Cadette.json', '.'),
    ],
    hiddenimports=[
        'flask',
        'werkzeug',
        'werkzeug.serving',
        'werkzeug.debug',
        'requests',
        'bs4',
        'beautifulsoup4',
    ],
    hookspath=[],
    hooksconfig={},
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
    name='FIDAL_CDS_Tool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,          # mostra finestra terminale con log del server
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    icon=None,             # sostituire con 'icon.ico' se disponibile
)
