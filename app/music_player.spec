# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['music_player.py'],
    pathex=['..'],  # Path to the project root
    binaries=[
        ('C:/windows/system32/hidapi.dll', '.'),  # Include hidapi.dll for StreamDeck support
    ],
    datas=[
        ('../venv/Lib/site-packages/fleep/data.json', 'fleep'),  # Include fleep's data.json
        ('../Assets', 'Assets'),  # Include Assets directory for button icons
        ('../service_log', 'service_log'),  # Include service_log directory for logs
        ('../engine_tuning.json', '.'),  # Bundle default tuning (EXE-adjacent file still overrides)
    ],
    hiddenimports=[
        'StreamDeck',
        'StreamDeck.DeviceManager',
        'StreamDeck.Devices',
        'StreamDeck.Devices.StreamDeck',
        'StreamDeck.Devices.StreamDeckXL',
        'StreamDeck.Transport',
        'StreamDeck.Transport.LibUSBHIDAPI',
        'StreamDeck.ImageHelpers',
        'StreamDeck.ImageHelpers.PILHelper',
        'hid',
        'PIL',
        'PIL.Image',
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
    name='Step D Audio Player',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # Windowed app
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='../Assets/app_icon.ico',  # Icon for the executable and title bar
)