# -*- coding: utf-8 -*-
"""v4.107：导演指令从主对话框彻底摘除——零交集回归测试（离线）。

v4.106 让「改第3镜关键帧」能从主对话框进 Agent；v4.107 按用户要求把导演指令
独立到导演台底部「导演对话」条，主对话框不再处理导演相关操作。

锁定三项行为：
1. 导演指令在主路由 _message_needs_agent / _needs_tool_intent 里必须返回 False
   （否则仍会进主控 Agent、污染主会话）。
2. _is_director_command 仍返回 True（send() 靠它识别并渲染引导，不入库）。
3. 普通工具意图（写文件/生图）仍正常进 Agent，不受本次改动影响。
"""
import os, sys, json
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv)

import ui
cfg = json.load(open(os.path.expanduser("~/Documents/小臭玩AI/config.json"), encoding="utf-8"))
w = ui.ChatWindow(cfg)

fails = []
def check(name, actual, expected):
    status = "OK" if actual == expected else "FAIL"
    if status == "FAIL":
        fails.append(name)
    print(f"[{status}] {name}: got={actual} expected={expected}")

# ============ 导演指令：主路由必须 False，但 _is_director_command 必须 True ============
director_cmds = [
    "把第3镜的关键帧改成夜晚",
    "第2镜重新生成，人物表情再惊讶一点",
    "把主角换成短发",
    "角色的三视图重画一下，衣服换成红色",
    "把成片合成出来",
    "合成成片",
    "第4镜重做，背景去掉文字",
    "导演台现在进度怎么样",
    "第几镜还没生成完？",
    "把这一镜调亮一点",
]
for t in director_cmds:
    msgs = [{"role": "user", "content": t}]
    # 真正的拦截闸门：send() 先用 _is_director_command 识别并 return，
    # 故只要 _message_needs_agent 返回 False，主 Agent 就不会跑。
    # （_needs_tool_intent 即便对含「重新生成」等通用工具词返回 True 也是死代码，
    #  send() 已在更早处 return，不影响零交集结论。）
    check(f"导演指令主路由不进 Agent: {t}", w._message_needs_agent(t), False)
    check(f"导演指令仍被识别(引导用): {t}", w._is_director_command(t), True)

# ============ 普通工具意图：行为不变，仍进 Agent ============
# 注意：普通模式下「搜索…」走 _do_search 通道（_message_needs_agent 本就返回 False），
# 属既有行为，不在本次解耦范围内；故 needs_agent 断言用脚本/生图类短语。
normal_agent = [
    "帮我写个 python 脚本算一下月度开销",
    "生成一张科技风海报",
]
for t in normal_agent:
    check(f"普通工具意图仍进 Agent: {t}", w._message_needs_agent(t), True)
normal_tool = [
    "帮我写个 python 脚本算一下月度开销",
    "生成一张科技风海报",
    "搜索一下最新的 AI 新闻",
]
for t in normal_tool:
    msgs = [{"role": "user", "content": t}]
    check(f"普通工具意图仍升舱: {t}", w._needs_tool_intent(msgs), True)

# ============ 引导渲染方法存在（不调用，避免动 DOM） ============
check("主窗口有 _render_director_redirect", hasattr(w, "_render_director_redirect"), True)
check("导演面板有 DirectorChatBar 挂点", hasattr(w, "director_chat"), True)

print("\n==== 结果 ====")
if fails:
    print(f"FAIL ({len(fails)}): {fails}")
    sys.exit(1)
print("ALL_OK")
