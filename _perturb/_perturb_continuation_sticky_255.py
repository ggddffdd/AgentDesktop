# -*- coding: utf-8 -*-
"""v4.255.0 扰动脚本：续接指令 × 会话粘性（B 包）。

反向照妖镜：逐个把改动打回「未接」状态，跑 tests/test_continuation_sticky_255.py，
确认判据必红（改坏必红、零哑弹）。

V1 续接词表失效          → 「再来一个/再详细点…」重新掉回 needs_action=False（空转复活）
V2 事实判据断开          → 同上（粘性没接上，等于没改）
V3 事实闸跳过（恒真）    → COLD 会话「再来一个」也被派活（本包退化成"又补一批词"）
V4 kind 闸放宽           → 「太简略了，展开说说」这类讨论句被推去调工具
V5 长度闸失效            → 自动化任务说明书里的「失败再试」被当续接
V6 主观/疑问排除失效      → 「我觉得不够详细」被当成再写一派的指令
V7 agent.py 不传 messages → 判据写得再好也没接线（S9 专盯这条）
V8 不认 tool_log 口径    → 从 UI 落盘会话恢复的上下文「刚干完活」却判成没干过
V9 continuation 标记不置位 → 可观测字段成了摆设（状态栏/日志查不到）
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
IT = os.path.join(ROOT, "intent.py")
RJ = os.path.join(ROOT, "route_judge.py")
AG = os.path.join(ROOT, "agent.py")
JUDGE = os.path.join(ROOT, "tests", "test_continuation_sticky_255.py")

CASES = [
    # V1：词表失效 → S2 全部正例翻红（续接指令重新空转）
    ("V1 续接词表失效", IT,
     "        if not any(mk in t for mk in _CONTINUATION_MARKERS):\n"
     "            return False",
     "        if True:  # 扰动：词表失效\n"
     "            return False"),
    # V2：事实判据断开 → has_recent_tool_any 恒 False，粘性别想生效
    ("V2 事实判据断开", RJ,
     "    if _recent_tool_in(messages, k, _TOOL_ROLES_ANY):",
     "    return False  # 扰动：事实判据断开\n"
     "    if _recent_tool_in(messages, k, _TOOL_ROLES_ANY):"),
    # V3：跳过事实闸 → 只凭词就派活，COLD 会话反例翻红（B 包立论被抽掉）
    ("V3 事实闸跳过", IT,
     "        if not route_judge.has_recent_tool_any(messages, k):\n"
     "            return False",
     "        if False:  # 扰动：事实闸跳过\n"
     "            return False"),
    # V4：kind 闸放宽 → S4 讨论/疑问类反向用例翻红
    # ⚠ 续行符后不能跟注释（会造 SyntaxError，判据崩掉而非翻红 → 假哑弹），
    #   所以这里整段改写成单行。
    ("V4 kind 闸放宽", IT,
     "    if messages is not None and kind in (KIND_UNKNOWN, KIND_ACTION) \\\n"
     "            and not force and not needs_action:",
     "    if messages is not None and kind not in (KIND_NEGATED,) and not force and not needs_action:  # 扰动：闸放宽"),
    # V5：长度闸失效 → S6 长文本（任务说明书）翻红
    ("V5 长度闸失效", IT,
     "        if len(t) > _CONTINUATION_MAX_LEN:",
     "        if False:  # 扰动：长度闸失效"),
    # V6：主观/疑问排除失效 → S7 翻红
    ("V6 主观排除失效", IT,
     "        if any(w in t for w in _CONTINUATION_EXCLUDE_KW):\n"
     "            return False",
     "        if False:  # 扰动：主观排除失效\n"
     "            return False"),
    # V7：agent.py 不把会话喂进去 → S9 接线判据翻红
    ("V7 接线不传 messages", AG,
     "self._intent = intent.classify(self._last_user_text(), None, self.messages)",
     "self._intent = intent.classify(self._last_user_text(), None)  # 扰动：不传会话"),
    # V8：只认 tool → S5 的 tool_log / tool_calls 口径翻红
    ("V8 不认 tool_log 口径", RJ,
     '_TOOL_ROLES_ANY = ("tool", "tool_log", "function")',
     '_TOOL_ROLES_ANY = ("tool",)  # 扰动：不认 tool_log'),
    # V9：continuation 不置位 → 可观测字段摆设
    ("V9 continuation 不置位", IT,
     "            continuation = True",
     "            continuation = False  # 扰动：标记不置位"),
]


def run_judge():
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("PYTHONUTF8", "1")
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, p.stdout + p.stderr


def main():
    files = [IT, RJ, AG]
    G.arm(files)
    snaps = {}
    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            snaps[fp] = f.read()
    total = 0
    hits = 0
    misses = []
    try:
        for name, fp, old, new in CASES:
            total += 1
            src = snaps[fp]
            if old not in src:
                misses.append(name)
                print("SKIP %s: old 串未命中（源码已变，需更新脚本）" % name)
                continue
            cnt = src.count(old)
            assert cnt == 1, "old 串在 %s 出现 %d 次，非唯一" % (fp, cnt)
            mutated = src.replace(old, new, 1)
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(mutated)
            rc, out = run_judge()
            failed = (rc != 0) and ("FAIL" in out)
            if failed:
                hits += 1
                print("HIT  %s" % name)
            else:
                misses.append(name)
                print("MISS %s（哑弹！判据未翻红）" % name)
                print("---- judge output ----\n" + out[:1200])
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(src)
    finally:
        for fp, src in snaps.items():
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(src)
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    if misses:
        print("哑弹清单: %s" % ", ".join(misses))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
