# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['fidal_cds_tool.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('data/Cadette.json', 'data'),
        ('data/Cadetti.json', 'data'),
        ('data/Ragazze.json', 'data'),
        ('data/Ragazzi.json', 'data'),
        ('core/cds_utils.py', 'core'),
        ('core/cds_optimizer.py', 'core'),
        ('templates', 'templates'),
        ('static', 'static'),
    ],
    hiddenimports=[
        'flask',
        'werkzeug',
        'werkzeug.serving',
        'werkzeug.debug',
        'werkzeug.routing',
        'werkzeug.exceptions',
        'requests',
        'bs4',
        'html.parser',
        'core.cds_utils',
        'core.cds_optimizer',
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
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    icon=None,
)
