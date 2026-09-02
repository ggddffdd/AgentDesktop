# -*- coding: utf-8 -*-
"""导演台对话指令路由测试（离线，不联网不调工具）。

v4.106 让导演指令从主对话框进 Agent（调 director_* 工具）；
v4.107 按用户要求把导演对话独立到导演台底部，主对话框不再处理导演指令——
故「命中 needs_agent/tool_intent」的旧断言已反转：导演指令在主路由必须 False，
但仍被 _is_director_command 识别（send() 靠它渲染引导语、不入主会话）。
完整零交集断言见 _test_v4107_route.py，本测试只锁「误伤排除」部分。
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

# ============ 导演指令：主路由不再进 Agent（v4.107 反转），但仍被识别供引导 ============
director_cmds = [
    "把第3镜的关键帧改成夜晚",
    "第2镜重新生成，人物表情再惊讶一点",
    "把主角换成短发",
    "角色的三视图重画一下，衣服换成红色",
    "把成片合成出来",
    "合成成片",
    "第4镜重做，背景去掉文字",
    "导演台现在进度怎么样",
    "分镜跑到哪一步了",
    "第几镜还没生成完？",
    "把这一镜调亮一点",
]
for t in director_cmds:
    check(f"不进主路由 needs_agent: {t}", w._message_needs_agent(t), False)
    check(f"仍被识别(引导用): {t}", w._is_director_command(t), True)

# ============ 误伤排除（应 False）============
miss_tests = [
    "帮我列几个分镜创意",          # 列方向型，无动作词
    "关键帧是什么意思",            # 学习问句
    "换个头像行不行",              # 头像不在导演对象词
    "把文章改一下语气",            # 无导演对象词
    "视频号好难做啊，唉",          # 纯陈述
    "今天天气怎么样",              # 闲聊
    "推荐几个短视频平台",          # 咨询
]
for t in miss_tests:
    msgs = [{"role": "user", "content": t}]
    check(f"不误伤 needs_agent: {t}", w._message_needs_agent(t), False)
    check(f"不误伤 tool_intent: {t}", w._needs_tool_intent(msgs), False)

print()
if fails:
    print(f"FAIL {len(fails)} 项：", fails)
    sys.exit(1)
print("ALL_OK")
