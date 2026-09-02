# -*- coding: utf-8 -*-
"""v4.106：导演台对话指令路由测试（离线，不联网不调工具）。

用户拿 Pavo 截图提出「对话框指挥导演台」后，v4.106 加了 5 个 director_* 工具；
但若普通模式下「把第3镜的关键帧改成夜晚」进不了 Agent（_ACTION_HINTS 无导演词），
工具永远没机会被调用。本测试锁定 _is_director_command 的接入效果。
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

# ============ 命中：导演台指令（应 True）============
hit_tests = [
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
for t in hit_tests:
    check(f"命中 needs_agent: {t}", w._message_needs_agent(t), True)
    check(f"命中 tool_intent: {t}", w._needs_tool_intent([{"role": "user", "content": t}]), True)

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
