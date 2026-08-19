---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 84d238f462fc4d456bf3c50cce5a6de4_3a378c3e840011f180b3525400bff409
    ReservedCode1: YyNtaWqacDatAPtcSv1nblbCNX0vcSGh5A2bDRYAbCDViOV01KoXdRuXusaGFyrAJ5XQQXYXK/VnpU99DZkjWBWUDIb/WTOC/pgpvGN68BS2id/D6CJdCvDTU39bZxMZwI/OqQT370QDRT9pSauu0fNKEqxTOzCTFxavxCF/WPUN7ciW1JyV3nwwwGk=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 84d238f462fc4d456bf3c50cce5a6de4_3a378c3e840011f180b3525400bff409
    ReservedCode2: YyNtaWqacDatAPtcSv1nblbCNX0vcSGh5A2bDRYAbCDViOV01KoXdRuXusaGFyrAJ5XQQXYXK/VnpU99DZkjWBWUDIb/WTOC/pgpvGN68BS2id/D6CJdCvDTU39bZxMZwI/OqQT370QDRT9pSauu0fNKEqxTOzCTFxavxCF/WPUN7ciW1JyV3nwwwGk=
---

# 系统操控 & 软件操控 Tool 对接指南
#
# 本文档说明如何将 system_control_tools.py / software_control_tools.py
# 接入现有的 tools.py → config.py → agent.py 三层架构。
#
# 总修改量：3 个文件，每个文件 2~3 处改动。

# ============================================================================
# 1. config.py 改动
#    在 TOOL_DEFS 后面合并 SYSTEM_CONTROL_TOOL_DEFS 和 SOFTWARE_CONTROL_TOOL_DEFS
# ============================================================================

# 文件头部新增 import：
import system_control_tools
import software_control_tools

# TOOL_DEFS 行（原来大概是 TOOL_DEFS = [...]），在后面追加：
TOOL_DEFS = [
    # ... 原有的内置工具 defs ...
] + system_control_tools.SYSTEM_CONTROL_TOOL_DEFS \
  + software_control_tools.SOFTWARE_CONTROL_TOOL_DEFS


# ============================================================================
# 2. tools.py 改动
#    在 exec_tool() 的 if/elif 链中增加两段分发
# ============================================================================

# 文件头部新增 import：
from system_control_tools import SYSTEM_CONTROL_TOOL_TABLE
from software_control_tools import SOFTWARE_CONTROL_TOOL_TABLE

# exec_tool() 函数尾部，在 _try_mcp_tool() 之前插入：
def exec_tool(cfg, app_dir, name, args):
    """返回 (result_str, deliverables, schedule)"""

    # === 系统操控工具 ===
    if name in SYSTEM_CONTROL_TOOL_TABLE:
        return SYSTEM_CONTROL_TOOL_TABLE[name](cfg, app_dir, args)

    # === 软件操控工具 ===
    if name in SOFTWARE_CONTROL_TOOL_TABLE:
        return SOFTWARE_CONTROL_TOOL_TABLE[name](cfg, app_dir, args)

    # === 原有工具（保持在中间位置不变）===
    # if name == "read_file":  return tool_read_file(cfg, app_dir, args)
    # ...

    # === MCP 兜底（保持在最后）===
    # return _try_mcp_tool(cfg, app_dir, name, args)


# ============================================================================
# 3. spec 文件改动（如果需要打包进去）
#    DeepSeekDesktop.spec 的 hiddenimports 列表追加：
# ============================================================================

# hiddenimports=[
#     ...
#     'system_control_tools',
#     'software_control_tools',
#     'pyautogui',
#     'pynput',
#     'pygetwindow',
#     'pyperclip',
#     'pywinauto',
# ],


# ============================================================================
# 4. 依赖安装（开发环境）
# ============================================================================

# pip install pyautogui pynput pygetwindow pyperclip pywinauto
# 注：pywinauto 推荐版本 >= 0.6.8


# ============================================================================
# 工具清单速查
# ============================================================================

# 【系统操控】14 个工具：
#   screenshot       — 截屏（全屏/区域/窗口）
#   mouse_move       — 鼠标移动
#   mouse_click      — 鼠标点击（左/右/中，单击/双击）
#   mouse_scroll     — 鼠标滚轮
#   keyboard_type    — 键盘输入文本
#   keyboard_press   — 按键/组合键
#   clipboard_read   — 读剪贴板
#   clipboard_write  — 写剪贴板
#   window_list      — 列出窗口
#   window_focus     — 窗口切前台
#   window_get_info  — 窗口详情（位置/大小/状态）
#   process_list     — 列出进程
#   process_kill     — 终止进程
#   process_start    — 启动程序
#
# 【软件操控】10 个工具：
#   app_launch       — 启动应用（exe/UWP）
#   app_kill         — 强杀应用
#   app_focus        — 窗口获取焦点
#   app_window_state — 最大化/最小化/关闭/置顶
#   app_list_controls— 枚举控件树
#   app_click        — 点击控件
#   app_type         — 输入文本到控件
#   app_get_text     — 读取控件/窗口文本
#   app_wait_for     — 等待控件出现/消失
#   app_screenshot   — 应用窗口截图


# ============================================================================
# 设计原则
# ============================================================================

# 1. 零侵入 — 不改动现有 tool_xxx() 函数签名和 execute_tool_chain 逻辑
# 2. 声明式 — TOOL_DEFS 保持 list 拼接，路由表用 dict 展开
# 3. 无异常 — 所有实现用 try/except 包裹，错误以自然语言 str 返回 LLM
# 4. 可降级 — 缺少依赖时返回安装提示，不阻塞其他工具
# 5. 主线程安全 — 截图/窗口操作标注 Qt 主线程依赖；其他操作可在 ThreadPoolExecutor 中运行
*（内容由AI生成，仅供参考）*
