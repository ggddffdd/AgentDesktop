# -*- mode: python ; coding: utf-8 -*-

datas = [('config.example.json', '.'), ('agent_rules.md', '.'), ('skills', 'skills'), ('images', 'images'), ('icon.ico', '.'), ('browser_runner.py', '.'), ('browser_extension', 'browser_extension'),
          # 统一视频内核：video-agent/core 整包随附为 exe 同目录下的 `core` 包
          ('../video-agent/core', 'core')]
binaries = []
hiddenimports = ['PySide6', 'PySide6.QtPrintSupport', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineCore', 'chat_web', 'requests', 'psutil', 'watchdog', 'skill_manager_ui', 'clipboard_monitor', 'mcp_client', 'rag', 'ui', 'agent', 'agent_node', 'tools', 'config', 'skill_loader', 'memory_store', 'context_manager', 'session', 'browser_control_tools', 'chart_generator', 'database_tools', 'sandbox', 'search', 'skill_installer_tools', 'software_control_tools', 'step_tracer', 'structured_logger', 'system_control_tools', 'task_graph', 'token_compressor', 'voice', 'webhook_server', 'permissions', 'risk', 'cryptography', 'docx', 'perf_baseline', 'onboarding', 'harness', 'task_resume', 'trace_log', 'skill_review', 'digital_twin_panel', 'director_panel', 'video_pipeline', 'automation', 'automation_panel', 'browser_bridge', 'route_log',
               # v4.111 工具管理器：与 skill_manager_ui 同为函数内延迟导入，
               # 静态分析扫不到，必须显式登记，否则打包后点菜单报 ModuleNotFoundError。
               'tool_manager_ui',
               # 统一内核桥接 + video-agent/core 包（让 PyInstaller 静态收集 core.*）
               'core_agnes', 'core', 'core.agnes', 'core.media', 'core.config', 'core.models', 'core.pipeline', 'core.script', 'core.zhipu', 'core.__init__',
               # v4.120.1 补漏：数字人分身 + 视频导演台共用的 VLM QC 模块，打包前缺失导致启动崩溃
               'vision_qc',
               # v4.121 Agent 军团：数据层 + 波次执行器 + 面板界面。
               # legion_ui 在 ui.py 里是函数内延迟导入（防御式），静态分析扫不到，必须显式登记，
               # 否则打包后点「⚔️ Agent 军团」按钮会报 ModuleNotFoundError。
               'legion', 'legion_worker', 'legion_ui',
               # v4.128 Agnes 文本模型调用层：在 ui._agent_call / video_pipeline 里
               # 均为函数内延迟导入，静态分析扫不到，必须显式登记，否则打包后
               # 军团与导演台的文本环节会 ModuleNotFoundError。
               'agnes_text',
               # v4.129 产物分层落盘：tools._products_dir / ui 归档 / video_pipeline
               # 全是函数内延迟导入，不登记则打包后新产物退回旧平铺路径（静默降级，最难查）。
               'product_layout',
               # v4.195 批⑨ 证据登记处：agent._handle_tool_result 内为延迟导入
               # （与本清单里 tool_manager_ui / legion / agnes_text 同一个坑：
               # 函数内 import 静态分析扫不到）。漏登记的表现是打包后工具调用
               # 照常执行但**没有任何证据入账**，回验全线静默降级 —— 难查且无声。
               'evidence']


a = Analysis(
    ['main.py'],
    pathex=['../video-agent'],
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

        # ---------- v4.152 打包面瘦身 ----------
        # 这四个包在**全部 349 个源码文件（含 core/）里零 import**，却出现在包里，
        # 实测占 _internal 约 298.6 MB / 1076 MB（28%）：
        #     cv2 138.1 MB | pyarrow 77.3 MB | scipy 67.1 MB(含 libs) | pandas 16.1 MB
        # 反向依赖查证：requires 它们的只有 modelscope / paddleocr / streamlit / altair /
        # scikit-learn / easyocr / sentence-transformers 等 —— **这些包同样没被打进包**，
        # 与本程序无运行关系（推测是某条 hook 的传递收集）。
        #
        # ⚠️ 以下三个虽然也"体积可疑"，但是**真传递依赖**，绝不能一起排：
        #     lxml         ← python-docx (docx) 依赖
        #     pdfminer.six ← pdfplumber 依赖
        #     numpy        ← matplotlib / sounddevice / rag 依赖
        #
        # 排除后必须验证：启动冒烟 + 全量回归（尤其 chart_generator→matplotlib、
        # rag→docx/pdfminer、voice→sounddevice/numpy 这几条链）。
        'cv2', 'pyarrow', 'scipy', 'pandas',
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
    name='小臭玩AI',
    icon='icon.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # v4.147: 关 UPX 压缩——Qt DLL 巨大，UPX 既拖长构建又吃内存峰值（易触发后台 Job 回收），还可能和 Qt6Core 运行时加载不兼容导致 0xc0000409 崩溃
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
    upx=False,  # v4.147: 关 UPX 压缩——Qt DLL 巨大，UPX 既拖长构建又吃内存峰值（易触发后台 Job 回收），还可能和 Qt6Core 运行时加载不兼容导致 0xc0000409 崩溃
    upx_exclude=[],
    name='小臭玩AI',
)
