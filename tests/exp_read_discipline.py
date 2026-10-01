"""v4.191.0 对照实验 · 机制实测
直接 import 当前 tools.py 的 tool_read_file，跑三场景，证明 ①② 已真实落入 tool result。
仅打印「前 220 字符 + 末尾范围标记/RESULT NOT FOUND」，避免刷屏。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tools

PROJ = r"D:\小臭玩AI\deepseek-desktop"
tools.WORKSPACE_DIR = PROJ  # 让 CHANGELOG.md 落在 roots 内


def show(title, s, head=220):
    print("=" * 70)
    print(title)
    print("-" * 70)
    print("[前 %d 字符] %s" % (head, s[:head].replace("\n", "\\n")))
    print("...")
    print("[末尾 %d 字符] %s" % (200, s[-200:].replace("\n", "\\n")))


# 场景 A：部分读 CHANGELOG（v4.189/v4.190 编造事件真发生的场景）
rA = tools.tool_read_file(PROJ, "CHANGELOG.md", 0, 8000)
show("【A】部分读 CHANGELOG.md 前 8000 字符（共 %d 字符）" % len(rA), rA)

# 场景 B：一次读全的小文件（oneshot 标记）
tmp = os.path.join(PROJ, "_exp_tmp_small.txt")
with open(tmp, "w", encoding="utf-8") as f:
    f.write("小臭玩AI v4.191.0 实验临时文件。\n仅一行，用于验证 oneshot 标记。\n")
rB = tools.tool_read_file(PROJ, "_exp_tmp_small.txt", 0, 999999)
show("【B】一次读全的小文件", rB)
os.remove(tmp)

# 场景 C：扑空（文件不存在）→ RESULT NOT FOUND
rC = tools.tool_read_file(PROJ, "不存在的文件_xyz.md", 0, 8000)
print("=" * 70)
print("【C】扑空（文件不存在）— 完整返回")
print("-" * 70)
print(rC)

# 断言：三场景关键标记必须出现
assert "[读取纪律]" in rA, "A 缺少纪律头"
assert "禁止声称已读完整个文件" in rA, "A 缺少未读全禁令"
assert "本次已全部读入" in rB, "B 缺少 oneshot 标记"
assert "[RESULT NOT FOUND]" in rC, "C 缺少 RESULT NOT FOUND"
print("\n✅ 三场景关键标记全部命中（机制实测通过）")
