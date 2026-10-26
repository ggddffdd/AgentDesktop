# -*- coding: utf-8 -*-
"""v4.289.2：记忆清空、LLM 压缩竞态、Legion 信任并发。"""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
from unittest import mock

_SBX = tempfile.mkdtemp(prefix="xc_292_state_")
os.environ["XC_LEGION_DIR"] = os.path.join(_SBX, "legion")
os.environ["XC_USER_DATA_DIR"] = os.path.join(_SBX, "user")
os.environ["XC_LOG_DIR"] = os.path.join(_SBX, "logs")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import context_manager as cm
import legion
import legion_feedback
import memory_store as ms

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + (("  <- " + str(detail)) if detail else ""))


def _memory_clear():
    print("\n[A] 清空全部记忆不残留、不被自愈复活")
    d = os.path.join(_SBX, "memory")
    os.makedirs(d, exist_ok=True)
    ms._configure(d)
    ms._write_text_file(ms.MEMORY_PATH, "## 2026-10-01 [偏好]\n旧记忆\n")
    ms._write_text_file(ms.PINNED_PATH, "核心画像\n")
    ms._write_text_file(ms.PENDING_PATH, "待确认\n")
    with open(ms.MEMORY_DB_PATH, "wb") as f:
        f.write(b"sqlite-placeholder")
    ms._snapshot_memory("seed")
    bd = ms._backup_dir()
    check("A0 前置：主/画像/待确认/备份都存在",
          all(os.path.exists(ms._physical(p))
              for p in (ms.MEMORY_PATH, ms.PINNED_PATH, ms.PENDING_PATH))
          and any(n.startswith(("memory_", "core_")) for n in os.listdir(bd)))
    ok = ms.clear_memory()
    check("A1 clear_memory 返回成功", ok is True)
    check("A2 主记忆与加密态均清除",
          not os.path.exists(ms._physical(ms.MEMORY_PATH))
          and not os.path.exists(ms.MEMORY_PATH + ".enc"))
    check("A3 核心画像/待确认区/数据库均清除",
          not os.path.exists(ms._physical(ms.PINNED_PATH))
          and not os.path.exists(ms._physical(ms.PENDING_PATH))
          and not os.path.exists(ms._physical(ms.MEMORY_DB_PATH)))
    check("A4 旧备份清零（自愈无源可复活）",
          os.path.isdir(bd) and not [n for n in os.listdir(bd)
                                     if n.startswith(("memory_", "core_", "db_"))],
          os.listdir(bd) if os.path.isdir(bd) else "no dir")
    check("A5 repair 不再恢复已清空记忆", ms.repair_memory() == "healthy")
    check("A6 清空后读取为空", ms.load_memory() == "" and ms.load_pinned() == "")
    ms.append_memory("清空后的新记忆", type="用户偏好", topic="新")
    check("A7 清空后仍可正常写入新记忆", "清空后的新记忆" in ms.load_memory())


def _context_race():
    print("\n[B] LLM 压缩期间新增消息不被裁掉")
    d = tempfile.mkdtemp(prefix="ctx292_")
    mgr = cm.ContextManager(max_window=5, compress_threshold=2,
                            summary_path=os.path.join(d, "s.json"),
                            key_info_path=os.path.join(d, "k.json"))
    for i in range(8):
        mgr.add_message("user", f"待办：旧任务{i}")
    before_sum = len(mgr.summaries)

    def _slow(prompt, cfg):
        mgr.add_message("user", "网络等待期间的新消息")
        return "陈旧摘要"

    with mock.patch.object(cm, "_call_summary_llm", side_effect=_slow):
        out = mgr.compress_with_llm({})
    check("B1 版本变化时丢弃陈旧摘要", "丢弃" in out, out)
    check("B2 新消息仍保留在上下文",
          any(m["content"] == "网络等待期间的新消息" for m in mgr.messages),
          [m["content"] for m in mgr.messages])
    check("B3 陈旧摘要没有写进 summaries", len(mgr.summaries) == before_sum)

    tails = [m["content"] for m in mgr.messages[-5:]]
    with mock.patch.object(cm, "_call_summary_llm", return_value="有效摘要"):
        out2 = mgr.compress_with_llm({})
    check("B4 无并发变化时正常生成摘要", "有效摘要" in out2)
    check("B5 只裁掉快照前缀，窗口尾部原样保留",
          [m["content"] for m in mgr.messages] == tails,
          ([m["content"] for m in mgr.messages], tails))


def _trust_concurrency():
    print("\n[C] Legion 信任读改写与额度消费原子化")
    check("C1 consume_auto_pass 存在", hasattr(legion, "consume_auto_pass"))
    key = "atomic-project"
    fp = "same-fp"
    n_threads, per_thread = 12, 20
    errs = []

    def _grant():
        try:
            for _ in range(per_thread):
                legion.grant_trust(key, fp, 1)
        except Exception as e:
            errs.append(repr(e))

    ths = [threading.Thread(target=_grant, daemon=True) for _ in range(n_threads)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(10)
    item = legion._trust_load().get(legion.trust_key(key, fp), {})
    check("C2 并发人工批准无丢失", item.get("count") == n_threads * per_thread,
          item.get("count"))
    check("C3 并发 grant 无异常", not errs, errs)

    # 额度过 1：并发抢额度只能成功一次
    legion.revoke_trust(key)
    legion.grant_trust(key, fp, 1)
    legion.grant_trust(key, fp, 1)
    allowed = []
    lock = threading.Lock()

    def _take():
        ok, _reason = legion.consume_auto_pass(key, fp, threshold=2, max_auto=1)
        if ok:
            with lock:
                allowed.append(1)

    ths2 = [threading.Thread(target=_take, daemon=True) for _ in range(20)]
    for t in ths2:
        t.start()
    for t in ths2:
        t.join(10)
    item2 = legion._trust_load().get(legion.trust_key(key, fp), {})
    check("C4 并发抢额度只成功一次", len(allowed) == 1, allowed)
    check("C5 自动放行用量不超过上限", item2.get("auto_used") == 1, item2)


def _legion_sentence():
    print("\n[D] 军团状态句：超时不是已通过")
    nodes = {
        "w1_m1": {"status": "done", "role": "写手", "wave": 1},
        "gate_w1": {"status": "timeout", "wave": 1},
    }
    s = legion_feedback.progress_sentence(nodes, 1, 1)
    check("D1 验收超时显式说明未授权", "超时" in s and "已通过" not in s, s)
    nodes2 = {
        "w1_m1": {"status": "incomplete", "role": "写手", "wave": 1},
        "gate_w1": {"status": "approved", "wave": 1},
    }
    s2 = legion_feedback.progress_sentence(nodes2, 1, 1)
    check("D2 无产出成员显示没交稿", "没交上稿" in s2, s2)


def main():
    try:
        _memory_clear()
        _context_race()
        _trust_concurrency()
        _legion_sentence()
    finally:
        shutil.rmtree(_SBX, ignore_errors=True)
    print("\nPASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
