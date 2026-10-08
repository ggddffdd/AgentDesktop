# -*- coding: utf-8 -*-
"""防空转三闸门修复（v4.234）扰动脚本：改坏必红、零哑弹。

反向照妖镜：分别变异 agent.py（A 去死条件）/ agent_text.py（B 词表、C agnes 豁免），
跑 test_anti_stall_234.py，确认对应判据必红。三块各自独立变异+还原，互不干扰。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = "C:/Users/xyb/AppData/Local/Programs/Python/Python312/python.exe"
AGENT = os.path.join(ROOT, "agent.py")
AGENT_TEXT = os.path.join(ROOT, "agent_text.py")
JUDGE = os.path.join(ROOT, "tests", "test_anti_stall_234.py")

# ---- A：把「去死条件」改回带 not _any_tool_executed 死条件 ----
OLD_A = """        if not content or getattr(self, "_nudged", False):
            return False
        if self._nudge_count >= MAX_FORCE_RETRIES:
            return False
        return agent_text._looks_like_promise(content)"""
NEW_A = """        if not content or getattr(self, "_nudged", False):
            return False
        if self._nudge_count >= MAX_FORCE_RETRIES:
            return False
        if getattr(self, "_any_tool_executed", False):  # 扰动：恢复死条件
            return False
        return agent_text._looks_like_promise(content)"""

# ---- B：把 _looks_like_promise 词表回退到无新增（删「再补/直接/联网/搜/查」） ----
OLD_B = """    promise = ("我来", "我先", "我这就", "我马上", "现在开始", "马上开始",
               "这就去", "去搜索", "去查", "去写", "去执行", "去生成", "去排查",
               "开始自检", "开始检查", "开始排查", "开始诊断", "开始扫描",
               "继续自检", "继续检查", "继续排查", "继续诊断", "继续扫描",
               "先检查", "先排查", "先诊断", "先自检", "先扫一遍", "先查一下",
               "我检查", "我排查", "我诊断", "我扫描", "我核验", "我复核",
               # v4.234（B 修复）：截断截图真实空转语料的承诺/意图特征
               "再补", "接着", "继续补", "现在联网", "联网", "准备去",
               "我去", "直接", "我准备", "打算去", "我这就去")
    action = ("搜索", "排查", "检查", "诊断", "巡检", "自检", "核验", "扫描",
              "复核", "抓取", "写入", "写文件", "执行", "读取", "调用工具",
              "跑一下", "跑个",
              # v4.234（B 修复）：覆盖「再补一次搜」「直接查」等单字动作
              "搜", "查")"""
NEW_B = """    promise = ("我来", "我先", "我这就", "我马上", "现在开始", "马上开始",
               "这就去", "去搜索", "去查", "去写", "去执行", "去生成", "去排查",
               "开始自检", "开始检查", "开始排查", "开始诊断", "开始扫描",
               "继续自检", "继续检查", "继续排查", "继续诊断", "继续扫描",
               "先检查", "先排查", "先诊断", "先自检", "先扫一遍", "先查一下",
               "我检查", "我排查", "我诊断", "我扫描", "我核验", "我复核")
    action = ("搜索", "排查", "检查", "诊断", "巡检", "自检", "核验", "扫描",
              "复核", "抓取", "写入", "写文件", "执行", "读取", "调用工具",
              "跑一下", "跑个")"""

# ---- C：在 model_rejects_tool_required 恢复 agnes-3.x 误判豁免 ----
OLD_C = """    m = (model or "").lower()
    b = (base_url or "").lower()
    if any(k in m for k in ("think", "reason", "-r1", "reasoning", "thinking")):
        return True
    if "api.deepseek.com" in b:
        return True
    return False"""
NEW_C = """    m = (model or "").lower()
    b = (base_url or "").lower()
    if any(k in m for k in ("think", "reason", "-r1", "reasoning", "thinking")):
        return True
    if "agnes" in b and "-3" in m:  # 扰动：恢复误判豁免
        return True
    if "api.deepseek.com" in b:
        return True
    return False"""

CASES = [
    ("A 恢复死条件 not _any_tool_executed", AGENT, OLD_A, NEW_A, ["A1"]),
    ("B 回退词表(删再补/直接/联网/搜/查)", AGENT_TEXT, OLD_B, NEW_B, ["B1", "B2", "B3"]),
    ("C 恢复 agnes-3.x 豁免", AGENT_TEXT, OLD_C, NEW_C, ["C1", "C2"]),
]


def run_judge():
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True, cwd=ROOT)
    return p.returncode, p.stdout + p.stderr


def main():
    G.arm([AGENT, AGENT_TEXT])  # 三重还原 + 残留预检
    total = 0
    hits = 0
    try:
        for name, target, old, new, expect in CASES:
            total += 1
            src = open(target, "r", encoding="utf-8").read()
            if old not in src:
                print("SKIP %s: old 串未命中（可能已改）" % name)
                continue
            assert src.count(old) == 1, "%s: old 串出现 %d 次，非唯一" % (name, src.count(old))
            open(target, "w", encoding="utf-8").write(src.replace(old, new, 1))
            rc, out = run_judge()
            failed = (rc != 0) and ("FAIL" in out or any(t in out for t in expect))
            if failed:
                hits += 1
                print("HIT  %s" % name)
            else:
                print("MISS %s（哑弹！判据未翻红）" % name)
                print("---- judge output ----\n" + out[:1500])
            open(target, "w", encoding="utf-8").write(src)  # 还原本文件
    finally:
        # 最终兜底：两个文件都还原（arm 也会在 atexit 再还原一次）
        for t in (AGENT, AGENT_TEXT):
            open(t, "w", encoding="utf-8").write(open(t, "r", encoding="utf-8").read())
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
