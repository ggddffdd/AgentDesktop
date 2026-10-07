# -*- coding: utf-8 -*-
"""扰动验证 v4.227：run_python 参数 schema。

手法：备份原字节 → 逐条退化 tool_contract.py 的 schema 登记 / tools.py 的接线 →
跑 test_run_python_schema_227.py → 期望对应判据翻红 → 恢复原字节。

验收标准是**哑弹清零**：每条变异都必须真的让判据翻红。

照妖镜（本轮配了两条，防「判据写成怎么改都红」的假红）：
  · V8 上限调到 0（全拒）→ A8 必须红 —— 否则 A 组可能压根没在真跑 exec_tool；
  · V9 顺手给 code 标 required（看起来是「更严更好」）→ B1 必须红 ——
    证明零破坏不是口号，「更严」在这里恰是行为变更。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TEST = os.path.join(ROOT, "tests", "test_run_python_schema_227.py")
FILES = ["tool_contract.py", "tools.py"]
_backup = {}
_crlf = {}

TC = "tool_contract.py"
TL = "tools.py"


def read_raw(fp):
    with open(os.path.join(ROOT, fp), "rb") as f:
        return f.read()


def write_raw(fp, blob):
    with open(os.path.join(ROOT, fp), "wb") as f:
        f.write(blob)


def is_crlf(fp):
    return b"\r\n" in read_raw(fp)


def clean_pycache():
    """清掉全仓 __pycache__（跑前跑后各一次）。

    被扰动的 tool_contract / tools 在判据里是 import 进来的，.pyc 比源码旧就
    直接用缓存 → 变异等于没发生 → 红 0 条 → 误判成「判据抓不住」。
    跑完也必须清：否则上一条用例最后写下的 .pyc 会成为下一条的起始状态，
    实测会让后一条读到**上一条的残留红点**。
    """
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
                       timeout=600, env=env, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    # ⚠️ 只取断言名（` —— ` 之前那段），detail 挂在同一行，
    #    带着 detail 匹配期望串就永远匹配不上 → 真 HIT 被判成 MISS。
    reds = [x.split(" —— ")[0].strip()
            for x in re.findall(r"\[FAIL\] ([^\n]+)", out)]
    clean_pycache()
    return reds


sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()

# ---- 原串全部从真实字节取（纪律：逐字节抄极易差一个字符 → 静默 SKIP）----
SCHEMA_OLD = (
    'register_tool_schema("run_python", [\n'
    '    {"key": "code", "type": "str", "max_len": ARG_MAX_LEN_CAP},\n'
    '])'
)
GATE_OLD = (
    '    if isinstance(args, dict):\n'
    '        _va, _ve = validate_for_tool(name, args)\n'
    '        if _ve:'
)
GATE_REASSIGN_OLD = '        args = _va'

CASES = [
    # ---- V1 整条登记删掉（退回「未登记原样放行」的 v4.223 前状态）----
    ("V1 run_python schema 整条删掉（退回原样放行）",
     TC, SCHEMA_OLD, "# 扰动：登记整条移除",
     ["A1 run_python 已登记 schema（不是未登记原样放行）",
      "A3 超上限 code 被拒",
      "A6 exec_tool 对超大 code 返回拒绝（ok=False）",
      "A7 ★副作用为零：code 里的哨兵没被执行（拦在副作用之前）"]),

    # ---- V2 只留键名，去掉 max_len（半吊子：登记了但没上限）----
    ("V2 去掉 max_len（登记了但没上限 = 形同虚设）",
     TC, SCHEMA_OLD,
     'register_tool_schema("run_python", [\n'
     '    {"key": "code", "type": "str"},\n'
     '])',
     ["A3 超上限 code 被拒",
      "A3b 错误信息含真实长度与上限（模型可据此调整）",
      "A6 exec_tool 对超大 code 返回拒绝（ok=False）",
      "A7 ★副作用为零：code 里的哨兵没被执行（拦在副作用之前）",
      "C1 run_python.code 与 run_command.command 上限同值"]),

    # ---- V3 去掉 type（list/dict 原样透传 → 落盘 TypeError）----
    ("V3 去掉 type 约束（list 原样透传给 f.write）",
     TC, SCHEMA_OLD,
     'register_tool_schema("run_python", [\n'
     '    {"key": "code", "max_len": ARG_MAX_LEN_CAP},\n'
     '])',
     ["A5 list code 被强转成 str（不再原样透传给 f.write）",
      "C2 两者都标 str（同类大字符串同处理）"]),

    # ---- V4 exec_tool 不再调 validate_for_tool（校验只存在于函数里 = 零防护）----
    ("V4 exec_tool 不再调 validate_for_tool（校验零防护）",
     TL, GATE_OLD,
     '    if False:  # 扰动\n'
     '        _va, _ve = validate_for_tool(name, args)\n'
     '        if _ve:',
     ["E1 exec_tool 体内真调用 validate_for_tool（AST，非找串）",
      "A6 exec_tool 对超大 code 返回拒绝（ok=False）",
      "A7 ★副作用为零：code 里的哨兵没被执行（拦在副作用之前）",
      "E1b 该调用不在 `if False` 之类恒假分支里（源码层判据防短路变异）"]),

    # ---- V5 校验结果不赋回 args（清洗结果被丢弃 = 校验形同虚设）----
    ("V5 清洗后的 args 不赋回（校验结果被丢弃）",
     TL, GATE_REASSIGN_OLD, '        args = args  # 扰动：丢弃清洗结果',
     ["E3 清洗后的 args 被赋回（args = _va），校验结果真的生效"]),

    # ---- V6 上限比 run_command 更严（无依据地收紧，会误拒正常长脚本）----
    ("V6 上限比 run_command 更严（无依据收紧）",
     TC, SCHEMA_OLD,
     'register_tool_schema("run_python", [\n'
     '    {"key": "code", "type": "str", "max_len": 1000},\n'
     '])',
     ["C1 run_python.code 与 run_command.command 上限同值",
      "C3 未比 run_command 更严（没把上限调到更小）"]),

    # ---- V7 多登记一个幽灵键（登记了却从不改变结论）----
    ("V7 多登记幽灵键 timeout（登记了却从不被消费）",
     TC, SCHEMA_OLD,
     'register_tool_schema("run_python", [\n'
     '    {"key": "code", "type": "str", "max_len": ARG_MAX_LEN_CAP},\n'
     '    {"key": "timeout", "type": "int", "min": 1},\n'
     '])',
     ["C5 run_python schema 只声明 code（无幽灵键）"]),

    # ---- V8 照妖镜①：上限调到 0（全拒），A8 必须红 ----
    #     若这条不红，说明 A8 压根没真跑 exec_tool —— A 组可能是假绿。
    ("V8 照妖镜：上限调到 0（全拒），证明 A8 在真跑 exec_tool",
     TC, SCHEMA_OLD,
     'register_tool_schema("run_python", [\n'
     '    {"key": "code", "type": "str", "max_len": 0},\n'
     '])',
     ["A8 合法小代码真跑通（上限不是形同虚设的全拒）"]),

    # ---- V9 照妖镜②：顺手标 required（「更严更好」在这里是行为变更）----
    ("V9 照妖镜：给 code 标 required（零破坏被破）",
     TC, SCHEMA_OLD,
     'register_tool_schema("run_python", [\n'
     '    {"key": "code", "type": "str", "required": True,\n'
     '     "max_len": ARG_MAX_LEN_CAP},\n'
     '])',
     ["B1 空/缺 code 不被校验层硬拒（保持友好返回）{'code': ''}",
      "C6 run_python.code 未标 required（零破坏）"]),
]


def main():
    for fp in FILES:
        _backup[fp] = read_raw(fp)
        _crlf[fp] = is_crlf(fp)

    passed = failed = 0
    try:
        for title, fp, old, new, expect in CASES:
            # ⚠️ 源必须每条都从 **_backup（原始字节）** 取，不是从磁盘当前状态。
            #    从磁盘取的话，上一条用例的变异会一直留着 → 原串再也匹配不上
            #    → 后面全部 SKIP（本次首跑就这么废掉的）。
            src = _backup[fp].decode("utf-8-sig").replace("\r\n", "\n")
            if old not in src:
                print("[SKIP] %s —— 原串未命中（源码已变，勿当跑过了）" % title)
                failed += 1
                continue
            out = src.replace(old, new, 1)
            write_raw(fp, (out.replace("\n", "\r\n") if _crlf[fp]
                           else out).encode("utf-8"))
            reds = run_test()
            ok = all(any(e in r for r in reds) for e in expect)
            print("[%s] %s → 红 %d 条" % ("HIT " if ok else "MISS", title, len(reds)))
            if not ok:
                print("       期望红：%s" % expect)
                print("       实际红：%s" % reds[:6])
            else:
                passed += 1
            # 每条用例后立刻还原（漏了这步 = 下一条在上一条的变异上跑）
            write_raw(fp, _backup[fp])
    finally:
        for fp in FILES:
            write_raw(fp, _backup[fp])
        clean_pycache()

    print("\nPERTURB PASS=%d FAIL=%d" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())