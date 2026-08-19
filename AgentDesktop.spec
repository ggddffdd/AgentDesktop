# -*- mode: python ; coding: utf-8 -*-

datas = [('config.json', '.'), ('agent_rules.md', '.'), ('skills', 'skills'), ('images', 'images'), ('icon.ico', '.')]
binaries = []
hiddenimports = ['PySide6', 'PySide6.QtPrintSupport', 'requests', 'psutil', 'watchdog', 'skill_manager_ui', 'clipboard_monitor', 'mcp_client', 'rag', 'browser_runner', 'ui', 'agent', 'agent_node', 'tools', 'config', 'skill_loader', 'memory_store', 'context_manager', 'session', 'browser_control_tools', 'chart_generator', 'database_tools', 'sandbox', 'search', 'skill_installer_tools', 'software_control_tools', 'step_tracer', 'structured_logger', 'system_control_tools', 'task_graph', 'token_compressor', 'voice', 'webhook_server', 'permissions', 'risk', 'cryptography', 'docx', 'perf_baseline', 'onboarding', 'harness', 'task_resume', 'trace_log', 'skill_review', 'digital_twin_panel', 'director_panel', 'video_pipeline', 'automation', 'automation_panel']


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 以下重型 ML 库 app 运行时从不 import（源码无 torch/transformers/sklearn 引用），
        # 仅是 scipy→array_api_compat 的传递依赖。PyInstaller 在「找二进制依赖」的隔离子进程里
        # import torch 会 0xC0000005 崩溃（3221225477）。排除后 scipy/numpy/pandas/matplotlib 不受影响。
        'torch', 'torchvision', 'torchaudio',
        'tensorflow', 'keras',
        'scipy._lib.array_api_compat.torch',
        'scipy._lib.array_api_compat.cupy',
        'scipy._lib.array_api_compat.dask',
        'cupy', 'dask', 'sympy',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AgentDesktop',
    icon='icon.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
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
    name='AgentDesktop',
)
