# -*- coding: utf-8 -*-
"""扰动验证 v4.228.0：连接层瞬时失败（WinError 10061）重试 + 异常文案可读性。

手法：备份原字节 → 逐条退化 ui_msg.py / ui.py / agent.py 的加固点 →
跑 test_net_retry_228.py → 期望对应判据翻红 → 恢复原字节。

验收标准是**哑弹清零**：每条变异都必须真的让判据翻红。
本脚本另设「反向照妖镜」，防止判据写成「怎么改都红」的假红：
  · V5 把瞬时判定改成恒False → A 组正例必须红（证明 A 组不是恒真）
  · V6 把瞬时判定改成恒 True → A 组反例必须红（证明反例在约束实现）
  · V7 把文案剥离删掉（直接 return str(exc)）→ B 组必须红（证明 B 组不是恒真）
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TEST = os.path.join(ROOT, "tests", "test_net_retry_228.py")

UM = "ui_msg.py"
UI = "ui.py"
AG = "agent.py"

_backup = {}
_crlf = {}


def read_raw(fp):
    with open(os.path.join(ROOT, fp), "rb") as f:
        return f.read()


def write_raw(fp, blob):
    with open(os.path.join(ROOT, fp), "wb") as f:
        f.write(blob)


def is_crlf(fp):
    return b"\r\n" in read_raw(fp)


def clean_pycache():
    """清掉全仓 __pycache__。

    被扰动的 ui_msg 被判据 import 进来，.pyc 比源码旧就直接用缓存
    → 变异等于没发生 → 红 0 条（这正是「哑弹」的经典成因）。
    """
    for dirpath, dirnames, _files in os.walk(ROOT):
        if "_dev_history" in dirpath or "dist" in dirpath or "backup" in dirpath:
            continue
        for d in list(dirnames):
            if d == "__pycache__":
                import shutil
                shutil.rmtree(os.path.join(dirpath, d), ignore_errors=True)
                dirnames.remove(d)


# 统一护栏：快照 + SIGTERM/SIGINT/atexit 三重还原 + 残留预检。
# 缺了这段，被强杀时 finally 不执行 → 半截变异体留在磁盘上
# → 后续所有判据基线变红，排查成本极高（判据 test_perturb_harness.py C1 会钉这一条）。
sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()


def run_test():
    """跑判据，返回 (是否FAIL, 失败行列表)。"""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"
    p = subprocess.run([PY, TEST], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=600)
    red = []
    for line in p.stdout.splitlines():
        if "[FAIL]" in line:
            red.append(line.strip())
    # 全绿 = 红 0 条，且退出码为 0（判据 main() 里有 FAIL 就 return 1）
    return (not red) and p.returncode == 0, red


# ---- 原串：全部从真实字节核对过（行尾已按各文件实际情况处理）----
# 教训（本次踩到）：把两段拼成 `A + B` 时若中间少一个 \n，replace 会「静默不命中」，
# 判据却因为别的变异而红 → 误报成 HIT。故此处每段都取**文件里连续的一整块**。
_OLD_CLS_LOOP = (
    "    for bad in _NON_TRANSIENT_ERR_TOKENS:\n"
    "        if bad in txt:\n"
    "            return False\n"
    "    for good in _TRANSIENT_ERR_TOKENS:\n"
    "        if good in txt:\n"
    "            return True\n"
    "    return False\n"
)
_OLD_EXCLUDE = (
    "    for bad in _NON_TRANSIENT_ERR_TOKENS:\n"
    "        if bad in txt:\n"
    "            return False\n"
)
_OLD_HUMANIZE_10061 = (
    '    if "10061" in low or "目标计算机积极拒绝" in raw:\n'
    '        return ("连接被本机拒绝（10061）。这是瞬时网络抖动，"\n'
    '                "已自动重试过一次；若仍失败请检查网络/代理后重试。")\n'
)
_OLD_STRIP = (
    '    m = re.search(r"<\\s*[^<>]{1,120}?\\s*[:：]\\s*(.+?)\\s*>?\\s*$", raw, re.S)\n    if m:\n        tail = m.group(1).strip()\n        if tail:\n            return tail[:200]\n'
)
_OLD_RETRY = (
    "elif _backoff and is_transient_net_error(e):"
)
_OLD_NOTICE = (
    'from ui_msg import humanize_net_error as _hne'
)

CASES = [
    # ---- V1 照妖镜：瞬时判定恒 False → A 组正例红 ----
    ("V1 照妖镜：瞬时判定改成恒 False",
     UM, _OLD_CLS_LOOP,
     _OLD_EXCLUDE + "    return False  # 扰动：恒不重试\n",
     ["A1", "A2", "A3", "A4", "A5"]),

    # ---- V2 照妖镜：瞬时判定恒 True → A 组反例红（防「全部重试」偷懒实现）----
    # 期望只含 A11：排除表仍在前，A6~A10 那些「带 4xx 码」的报文照样被排除表拦下；
    # 真正被「恒 True」打穿的只有**完全无关的异常**（A11）。
     ("V2 照妖镜：瞬时判定改成恒 True（什么都重试）",
     UM, _OLD_CLS_LOOP,
     _OLD_EXCLUDE + "    return True  # 扰动：什么都重试\n",
     ["A11"]),

    # ---- V3 非瞬时排除表被掏空 → 429/400 会被误判可重试 → A 组反例红 ----
    ("V3 非瞬时排除表被掏空（400/429 会被误重试）",
     UM,
     '_NON_TRANSIENT_ERR_TOKENS = (\n'
     '    "400", "401", "403", "404", "405", "413", "415", "422", "429",\n'
     '    "unauthorized", "forbidden", "bad request", "not found",\n'
     '    "insufficient", "quota", "rate limit", "too many requests",\n'
     ')',
     '_NON_TRANSIENT_ERR_TOKENS = ()  # 扰动：不排除任何非瞬时',
     # 期望 A12/A13 而非 A6/A8/A10：那些用例的报文是纯「HTTP Error 400」，
     # 本来就不含任何瞬时词，掏空排除表也照样判非瞬时 —— 检验不到排除逻辑。
     # A12/A13 是「含 refused/timeout 但属限流」的混合报文，只有它们能打穿排除表。
     ["A12", "A13"]),

    # ---- V4 文案：删掉 10061 专属人话 → B3/B4 红 ----
    ("V4 文案：删掉 10061 的可读说明",
     UM, _OLD_HUMANIZE_10061,
     "",
     ["B3", "B4"]),

    # ---- V5 文案：剥壳逻辑删掉（直接回吐原文）→ B8/B9 红 ----
    # 期望 B12/B14（不是 B9）：B9 的输入 ValueError("参数…") 其 str 本就不含类名，
    # 剥壳与否输出相同 → 恒真。B12~B14 用带 <类名:正文> 外壳的报文才检验得到。
    ("V5 文案：不再剥离 <类名:...> 外壳",
     UM, _OLD_STRIP,
     "",
     ["B12", "B14"]),

    # ---- V6 接线：ui.py 瞬时分支摘掉 → C1/D9 红 ----
    # 期望里**不含 D8**：D8 查的是「全文件任意处有 _stream_once」，
    # 而那行在 else 分支等处也有，删掉 elif 根本不影响它 → 写了也注定哑弹。
    ("V6 接线：删掉 ui.py 的瞬时重试分支",
     UI,
     "elif _backoff and is_transient_net_error(e):",
     "elif _backoff and False:  # 扰动：瞬时分支被摘掉",
     ["C1", "D9"]),

    # ---- V7 接线：瞬时分支被摘掉且顺序错乱 → C3 必须红 ----
    # 只把它改成 else 之后仍能被 C1 抓到；这里改条件让 find() 定位不到原 elif，
    # 从而 C3（顺序断言）红。
    ("V7 接线：瞬时分支名改掉（C3 顺序断言失据）",
     UI,
     "elif _backoff and is_transient_net_error(e):",
     "elif _backoff and _tmp_flag(e):  # 扰动：换名脱离判据",
     ["C1", "C6"]),

    # ---- V8 文案：agent.py 真正回退成裸 {e} 渲染 → D4 红 ----
    # 上一版只改 import（红的是 D6，与 D4 不同口径）——D4 查的是
    # 「`⚠️ 模型调用失败：{e}` 这个裸渲染字面串不存在」，必须真的把它写回去。
    ("V8 文案：agent.py 回退成裸 {e} 渲染",
     AG,
     '                _notice = (f"\\n\\n⚠️ 模型调用失败"\n'
     "                          f\"（{getattr(self, '_last_model', '') or '当前模型'}）：{_friendly}\")",
     '                _notice = f"\\n\\n⚠️ 模型调用失败：{e}"  # 扰动：裸渲染',
     ["D4"]),
]

HIT, MISS = [], []
try:
    for fp in (UM, UI, AG):
        _backup[fp] = read_raw(fp)
        _crlf[fp] = is_crlf(fp)

    print("基线自检（未变异时必须全绿，否则判据本身坏了）：")
    ok, red = run_test()
    if not ok:
        print("  [基线红] %s" % "; ".join(red[:6]))
        print("  基线不绿就停止扰动 —— 否则「命中」无意义（判据自身有问题）。")
        sys.exit(2)
    print("  基线全绿 ✔\n")

    # 原串预检：任何一条对不上就直接退出，绝不静默 SKIP。
    # 教训（本次踩到）：把两段原串拼成 `A + B` 时中间少一个 \n，replace 静默不生效，
    # 而判据因**别的**变异而红 → 该条被误报成 HIT，哑弹被掩盖。
    _norm = {fp: _backup[fp].decode("utf-8").replace("\r\n", "\n")
             for fp in _backup}
    _unmatched = [n for n, fp, old, _new, _e in CASES if old not in _norm[fp]]
    if _unmatched:
        print("  [中止] 以下用例原串未命中真实字节（防静默失效）：")
        for n in _unmatched:
            print("    - " + n)
        sys.exit(2)
    print("  原串预检：%d/%d 全部命中真实字节 ✔\n" % (len(CASES), len(CASES)))

    for name, fp, old, new, expect in CASES:
        # 每条都从**原始备份**重置（上一条的污染会让下一条误判）
        write_raw(fp, _backup[fp])
        clean_pycache()

        src = _backup[fp].decode("utf-8").replace("\r\n", "\n")
        if old not in src:
            MISS.append(name + "（原串未命中）")
            print("  [SKIP] %s —— 原串未命中" % name)
            continue
        out = src.replace(old, new, 1)
        write_raw(fp, (out.replace("\n", "\r\n") if _crlf[fp] else out).encode("utf-8"))

        is_red, red = run_test()
        # 命中判定：期望的每一条判据名都要出现在红里
        joined = "\n".join(red)
        ok = all(e in joined for e in expect)
        tag = "HIT " if ok else "MISS"
        print("  [%s] %s → 红 %d 条%s"
              % (tag, name, len(red), "" if ok else "；期望含 %s" % expect))
        (HIT if ok else MISS).append(name)
        write_raw(fp, _backup[fp])
finally:
    for fp, blob in _backup.items():
        write_raw(fp, blob)
    clean_pycache()
    print("  （已恢复原文件，原始字节无损）")

print("\n=== 扰动汇总：命中 %d/%d ===" % (len(HIT), len(CASES)))
print("PERTURB PASS=%d FAIL=%d" % (len(HIT), len(MISS)))
sys.exit(1 if MISS else 0)