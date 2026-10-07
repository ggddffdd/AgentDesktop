# -*- coding: utf-8 -*-
"""扰动验证 v4.227 P2-3：任务状态机「只认字面点名」这条设计决定的钉子。

本脚本模拟「日后有人把这条刻意设计当成缺陷顺手改掉」——也就是把
`required_from_text` 改成关键词/意图命中（v4.218 报告建议的那种做法）。
期望：A2/A3（反例判据）必须翻红。若不红，说明钉子没钉住。

照妖镜两条：
  · V4 词边界改成裸 in → B1 必须红（证明 B 组非恒真）
  · V5 登记的否决理由被清空 → C3 必须红（防「登记了等于没登记」）
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TEST = os.path.join(ROOT, "tests", "test_task_state_design_227.py")
FILES = ["task_state.py"]
_backup = {}
_crlf = {}


def read_raw(fp):
    with open(os.path.join(ROOT, fp), "rb") as f:
        return f.read()


def write_raw(fp, blob):
    with open(os.path.join(ROOT, fp), "wb") as f:
        f.write(blob)


def clean_pycache():
    for _root, _dirs, _files in os.walk(ROOT):
        if os.path.basename(_root) == "__pycache__":
            for _f in _files:
                if _f.endswith(".pyc"):
                    try:
                        os.remove(os.path.join(_root, _f))
                    except Exception:
                        pass


def run_test():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
               PYTHONDONTWRITEBYTECODE="1")
    clean_pycache()
    r = subprocess.run([PY, TEST], capture_output=True, text=True, cwd=ROOT,
                       timeout=300, env=env, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    reds = re.findall(r"\[FAIL\] ([^\n]+)", out)
    clean_pycache()
    return reds


sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()

TS = "task_state.py"

# 实现里那圈 for 循环（从真实字节抄；改源码插段后必须同步核原串）
_LOOP_OLD = (
    '        pat = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(nm.lower())\n'
    '                         + r"(?![A-Za-z0-9_])")\n'
    '        m = pat.search(low)\n'
    '        if m:\n'
    '            out.append((nm, t[m.start():m.end()]))'
)
# 坏实现：改成「关键词 in 全文」命中（报告建议的做法）
_LOOP_KEYWORD = (
    '        if False:  # 扰动\n'
    '            pass\n'
    '        for _kw in ("口播", "视频", "文案", "查", "搜", "存", "保存"):\n'
    '            if _kw in low:\n'
    '                out.append((nm, _kw))\n'
    '                break'
)

CASE_BOUNDARY_OLD = (
    '        pat = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(nm.lower())\n'
    '                         + r"(?![A-Za-z0-9_])")\n'
    '        m = pat.search(low)'
)
CASE_BOUNDARY_NEW = (
    '        pat = re.compile(re.escape(nm.lower()))  # 扰动：去掉词边界\n'
    '        m = pat.search(low)'
)

CASE_REASON_OLD = (
    '    "intent_keyword": "关键词命中会把「写口播文案」误判成硬要求 video_gen，"\n'
    '                      "逼模型生成用户没要的产物（宁可漏，不可错）",'
)
CASE_REASON_NEW = (
    '    "intent_keyword": "",'
)

CASE_POLICY_OLD = 'HARD_REQUIREMENT_POLICY = "literal_tool_name_only"'
CASE_POLICY_NEW = 'HARD_REQUIREMENT_POLICY = "intent_keyword"   # 扰动：改成关键词命中'

CASES = [
    # ---- V1 真把实现改成关键词命中（报告建议的做法）----
    ("V1 实现改成关键词命中（报告建议的做法）",
     TS, _LOOP_OLD, _LOOP_KEYWORD,
     ["A2 反例：「口播/文案」类文本请求绝不命中 video_gen"]),

    # ---- V2 只删动作留条件：把命中判定整个去掉 ----
    ("V2 只删动作留条件（删命中判定）",
     TS, _LOOP_OLD,
     '        pass  # 扰动：什么都不判',
     ["A1 字面点名 → 认定为硬要求（3 例）"]),

    # ---- V3 词边界改成裸 in（前缀粘连会误伤）----
    ("V3 词边界改成裸 in（前缀粘连误伤）",
     TS, CASE_BOUNDARY_OLD, CASE_BOUNDARY_NEW,
     ["B1 前后缀粘连的标识符不误命中"]),

    # ---- V4 照妖镜：登记口径被改成关键词命中 ----
    #    期望里**不含** C6：本变异只改登记常量，实现一行没动，
    #    「实现与登记一致」当然仍成立、保持绿（首跑把 C6 写进期望 → 误判 MISS）。
    #    真正被打穿的是 C1（登记值变了）与 C5（源码里那行字面量不再匹配）。
    ("V4 照妖镜：登记口径改成关键词命中（登记与实现脱节）",
     TS, CASE_POLICY_OLD, CASE_POLICY_NEW,
     ["C1 当前口径已登记",
      "C5 HARD_REQUIREMENT_POLICY 落在源码里（可 grep）"]),

    # ---- V5 照妖镜：否决理由被清空（登记了等于没登记）----
    ("V5 照妖镜：否决理由被清空（登记了等于没登记）",
     TS, CASE_REASON_OLD, CASE_REASON_NEW,
     ["C3 每条否决理由都是实质说明"]),
]

HIT, MISS = [], []
try:
    for fp in FILES:
        _backup[fp] = read_raw(fp)
        _crlf[fp] = b"\r\n" in _backup[fp]

    for name, fp, old, new, expect in CASES:
        src = _backup[fp].decode("utf-8").replace("\r\n", "\n")
        if old not in src:
            MISS.append(name + "（原串未命中）")
            print("  [SKIP] " + name)
            continue
        out = src.replace(old, new, 1)
        write_raw(fp, (out.replace("\n", "\r\n") if _crlf[fp] else out).encode("utf-8"))
        red = run_test()
        ok = all(any(e in r for r in red) for e in expect)
        print("  [%s] %s → 红 %d 条" % ("HIT " if ok else "MISS", name, len(red))
              + ("" if ok else ("；期望 %s" % expect)))
        (HIT if ok else MISS).append(name)
        write_raw(fp, _backup[fp])
finally:
    for fp, blob in _backup.items():
        write_raw(fp, blob)
    print("  （已恢复原文件，原始字节无损）")

print("\n=== 扰动汇总：命中 %d/%d ===" % (len(HIT), len(CASES)))
print("PERTURB PASS=%d FAIL=%d" % (len(HIT), len(MISS)))
sys.exit(1 if MISS else 0)
