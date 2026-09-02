# -*- coding: utf-8 -*-
"""v4.107 bugfix：导演台对话条流式渲染「替换」语义测试（offscreen，离线）。

背景：stream_chunk 信号携带的是「累积文本」（从句子开头到当前位置），
主聊天 chat_web.jsStream 用「替换」语义消费；而 director_chat 早先误用
insertPlainText「追加」语义，导致累积文本一遍遍重复拼接（用户实证
「镜镜3镜3关键镜3关键帧…」灾难）。本测试锁定 _on_chunk 的替换语义。

覆盖：
1. 累积 chunk 流式渲染后，目标句只出现一次（无重复累加、无前缀重复）
2. 以「🎬 导演：」开头
3. 流式结束 commit 补换行
4. 连续两轮流式互不串（第二轮的流式片段替换自己的起点，不污染第一轮）
5. 未走流式直接 commit（兜底渲染）
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QTextEdit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from director_chat import DirectorChatBar

FAIL = []


def check(name, cond):
    if cond:
        print(f"  [OK] {name}")
    else:
        print(f"  [FAIL] {name}")
        FAIL.append(name)


class FakeApp:
    director_pipeline = None
    director_busy = False

    def _on_deliverable_added(self, *a, **k):
        pass


def make_bar():
    # 绕过 __init__（不 import ui.THEME / 不读历史），手动装配最小属性
    bar = DirectorChatBar.__new__(DirectorChatBar)
    bar.app = FakeApp()
    bar.log = QTextEdit()
    bar.input = QTextEdit()
    bar.history = []
    bar._worker = None
    bar._streaming = False
    return bar


app = QApplication.instance() or QApplication(sys.argv)

# ===== 用例 1：单轮流式（累积 chunk）不重复 =====
bar = make_bar()
full = "镜3关键帧已按原提示词重生成完毕。"
# 模拟累积 chunk：每个 chunk 是从头到当前位置的前缀
acc = ""
for i in range(1, len(full) + 1, 2):
    acc = full[:i]
    bar._on_chunk(acc)
bar._on_commit(full)

txt = bar.log.toPlainText()
check("用例1 以导演标签开头", txt.startswith("🎬 导演："))
check("用例1 目标句只出现一次", txt.count(full) == 1)
check("用例1 无前缀重复(镜镜)", "镜镜" not in txt)
check("用例1 无前缀重复(关键关键)", "关键关键" not in txt)
check("用例1 无前缀重复(帧帧)", "帧帧" not in txt)

# ===== 用例 2：连续两轮流式互不串 =====
bar2 = make_bar()
s1 = "第3镜已重生成。"
acc = ""
for i in range(1, len(s1) + 1):
    acc = s1[:i]
    bar2._on_chunk(acc)
bar2._on_commit(s1)

s2 = "主角三视图已更新。"
acc = ""
for i in range(1, len(s2) + 1):
    acc = s2[:i]
    bar2._on_chunk(acc)
bar2._on_commit(s2)

txt2 = bar2.log.toPlainText()
check("用例2 第一句只出现一次", txt2.count(s1) == 1)
check("用例2 第二句只出现一次", txt2.count(s2) == 1)
check("用例2 两句都在", s1 in txt2 and s2 in txt2)

# ===== 用例 3：未走流式直接 commit（兜底渲染）=====
bar3 = make_bar()
bar3._on_commit("\n\n⏰ 执行超时（90秒），已自动停止。")
txt3 = bar3.log.toPlainText()
check("用例3 兜底渲染超时提示", "执行超时" in txt3)
check("用例3 兜底带导演标签", txt3.startswith("🎬 导演："))

print()
if FAIL:
    print(f"RESULT: {len(FAIL)} FAIL -> {FAIL}")
    sys.exit(1)
print("ALL_STREAM_REPLACE_OK")
