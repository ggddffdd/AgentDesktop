# -*- coding: utf-8 -*-
"""v4.284.0 P0 扰动：_sync_to_session 合并逻辑改坏必红。

三条分别打在三处修复点上，跑 tests/test_sync_tool_calls_284.py：
  P1 主路径合并分支改成「判重跳过」（退回 v4.284.0 前旧行为）
  P2 四维指纹退回三元组（_sync_sig 不再含 tool_calls）
  P3 兜底路径边界合并摘除

SKIP / MISS 都算哑弹。原串从真实文件取，且必须唯一。
IO 用纯 text mode（agent.py 磁盘为 CRLF：读时 universal newlines 归一、
写时 Windows text mode 自动转回 CRLF，原样 round-trip）。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
JUDGE = os.path.join(ROOT, "tests", "test_sync_tool_calls_284.py")
F_AGENT = os.path.join(ROOT, "agent_task_mixin.py")  # v4.284.0：_sync_to_session 已外移至此

OLD_P1 = """                _tgt = _exist_by_base.get(_sig[:3])
                if _tgt is not None and m.get("tool_calls") \\
                        and not _tgt.get("tool_calls"):
                    # 同文、session 侧缺 tool_calls → 合并进既有消息（不重复正文）
                    _tgt["tool_calls"] = [dict(tc) for tc in m["tool_calls"]]
                    _exist_sigs.add(_sig)
                    continue
"""
NEW_P1 = """                _tgt = _exist_by_base.get(_sig[:3])
                if _tgt is not None and m.get("tool_calls") \\
                        and not _tgt.get("tool_calls"):
                    continue  # 扰动：退回旧行为——判重跳过，不合并
"""

OLD_P2 = """        return (m.get("role"), str(m.get("content") or ""),
                str(m.get("tool_call_id", "")), _tc_sig)"""
NEW_P2 = """        return (m.get("role"), str(m.get("content") or ""),
                str(m.get("tool_call_id", "")))"""

OLD_P3 = """        _bm = self.messages[start_idx - 1]
        _le = existing[-1]
        if (isinstance(_bm, dict) and _bm.get("tool_calls")
                and isinstance(_le, dict) and not _le.get("tool_calls")):
            _le["tool_calls"] = [dict(tc) for tc in _bm["tool_calls"]]"""
NEW_P3 = """        _bm = self.messages[start_idx - 1]
        _le = existing[-1]
        if False:  # 扰动：边界合并摘除
            _le["tool_calls"] = [dict(tc) for tc in _bm["tool_calls"]]"""

CASES = [
    ("P1 主路径合并改回跳过", F_AGENT, OLD_P1, NEW_P1,
     ["A2 主路径同文 assistant 合并进 tool_calls", "A5 主路径孤儿复活"]),
    ("P2 四维指纹退回三元组", F_AGENT, OLD_P2, NEW_P2,
     ["E1 指纹含 tool_calls 维度", "A2 主路径同文 assistant 合并进 tool_calls"]),
    ("P3 兜底边界合并摘除", F_AGENT, OLD_P3, NEW_P3,
     ["C2 兜底路径边界消息合并", "C3 兜底路径孤儿同样复活"]),
]


def run_judge():
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"
    p = subprocess.run(
        [PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    G.arm([F_AGENT])
    with open(F_AGENT, "r") as fh:
        orig = fh.read()
    total = 0
    hits = 0
    try:
        for name, target, old, new, expect in CASES:
            total += 1
            if old not in orig:
                print("SKIP %s: old 串未命中（可能已改）" % name)
                continue
            n = orig.count(old)
            if n != 1:
                print("SKIP %s: old 串出现 %d 次，非唯一" % (name, n))
                continue
            with open(target, "w") as fh:
                fh.write(orig.replace(old, new, 1))
            rc, out = run_judge()
            failed = (rc != 0) and all(t in out for t in expect)
            if failed:
                hits += 1
                print("HIT  %s" % name)
            else:
                print("MISS %s（哑弹！判据未翻红）" % name)
                print("---- judge output ----\n" + out[-2000:])
            with open(target, "w") as fh:
                fh.write(orig)
    finally:
        with open(F_AGENT, "w") as fh:
            fh.write(orig)
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
