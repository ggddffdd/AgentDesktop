# -*- coding: utf-8 -*-
"""v4.289.6：记忆清理细节 + 上下文压缩策略。

三项均为实测确认的缺陷（2026-10-27）：
  ① `context_manager._user_data_dir()` 硬编码 `~/Documents/小臭玩AI`，不认
     `config.USER_DATA_DIR` / `XC_USER_DATA_DIR` —— 判据改道后仍读写真实用户目录。
  ② `_prune_backups` 把 memory_/core_/db_ 混排按文件名全局裁剪，字典序上
     `core_` 最先被删（实测 25 组裁完剩 memory 25 / core 10 / db 25）。
  ③ `compress_threshold(10) < max_window(20)`：压缩后仍超阈值 → 每来一条消息
     就再压一次并落盘；且单段 `key_points` 只去重不裁，长会话无界增长。

纯标准库，不联网；所有落盘改道临时目录，并额外断言真实目录零写入。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_SBX = tempfile.mkdtemp(prefix="xc_296_mem_")
os.environ["XC_USER_DATA_DIR"] = os.path.join(_SBX, "user")
os.environ["XC_LOG_DIR"] = os.path.join(_SBX, "logs")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import context_manager as cm
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


# 记录真实目录状态（用于「零写入」断言），必须在任何 ContextManager 构造之前
_REAL = Path.home() / "Documents" / "小臭玩AI"
def _real_state():
    out = {}
    for n in ("context_summary.json", "key_info.json"):
        p = _REAL / n
        out[n] = (p.stat().st_size, p.stat().st_mtime_ns) if p.exists() else None
    return out


_BEFORE = _real_state()

print("=" * 66)
print("M 组：数据目录改道（防测试污染真实用户数据）")
print("=" * 66)

mgr = cm.ContextManager()
_ud = os.path.normcase(os.environ["XC_USER_DATA_DIR"])
check("M1 摘要路径跟随 XC_USER_DATA_DIR",
      os.path.normcase(mgr.summary_path).startswith(_ud), mgr.summary_path)
check("M2 关键信息路径跟随 XC_USER_DATA_DIR",
      os.path.normcase(mgr.key_info_path).startswith(_ud), mgr.key_info_path)
check("M3 启动不读取真实目录的旧摘要", len(mgr.summaries) == 0,
      len(mgr.summaries))

print()
print("=" * 66)
print("M 组：记忆快照轮转公平性")
print("=" * 66)

bd = os.path.join(_SBX, "bk")
os.makedirs(bd, exist_ok=True)
for i in range(25):
    ts = "20260101_%06d" % i
    for nm, ext in (("memory", ".md"), ("core", ".md"), ("db", ".db")):
        with open(os.path.join(bd, "%s_%s%s" % (nm, ts, ext)), "w") as f:
            f.write("x")
with open(os.path.join(bd, "notes.txt"), "w") as f:
    f.write("不是快照，别删")

ms._prune_backups(bd)
_left = {p: len([n for n in os.listdir(bd) if n.startswith(p)])
         for p in ("memory_", "core_", "db_")}
check("M4 三类快照各自保留 MAX_BACKUPS 份", all(v == ms.MAX_BACKUPS
                                                 for v in _left.values()), _left)
check("M5 核心画像快照不再被优先删光（原实测 25/10/25）",
      _left["core_"] == ms.MAX_BACKUPS, _left)
check("M6 非快照文件不受影响",
      os.path.isfile(os.path.join(bd, "notes.txt")))

# 保留的是最新一批（最旧的被删）
_names = sorted(n for n in os.listdir(bd) if n.startswith("core_"))
check("M7 淘汰的是最旧快照（保留末 20 个时间戳）",
      "20260101_000005" in _names[0] and "20260101_000024" in _names[-1],
      (_names[0], _names[-1]))

print()
print("=" * 66)
print("M 组：上下文压缩策略")
print("=" * 66)

check("M8 默认阈值大于窗口", cm.ContextManager().compress_threshold
      > cm.ContextManager().max_window,
      (cm.ContextManager().compress_threshold, cm.ContextManager().max_window))
_bad = cm.ContextManager(compress_threshold=5)
check("M9 误配（阈值<=窗口）自动纠正，不退化逐条压缩",
      _bad.compress_threshold > _bad.max_window, _bad.compress_threshold)

def _iso(tag):
    """每个用例独立摘要/关键信息文件，避免用例间数据串。"""
    return cm.ContextManager(
        summary_path=os.path.join(_SBX, "sum_%s.json" % tag),
        key_info_path=os.path.join(_SBX, "ki_%s.json" % tag))


_m = _iso("m10")
_n = {"c": 0}
_orig = _m._save_summaries
def _count():
    _n["c"] += 1
    return _orig()
_m._save_summaries = _count
for i in range(60):
    _m.add_message("user", "普通消息 %d" % i)
check("M10 连加 60 条消息的落盘次数受控（原为 ~40 次）", _n["c"] <= 5, _n["c"])
check("M11 消息窗口仍被压在 max_window 附近（不无限增长）",
      len(_m.messages) <= _m.compress_threshold + 1, len(_m.messages))

_m2 = _iso("m12")
for i in range(150):
    _m2.add_message("user", "待办：第 %d 项任务必须完成" % i)
_pts = sum(len(s.get("key_points", [])) for s in _m2.summaries)
check("M12 单段 key_points 有上限（原实测 80 条→60+ 无界）",
      _pts <= cm._MAX_KEY_POINTS, _pts)
check("M13 摘要段数保持有界（增量并入同一段）", len(_m2.summaries) <= 2,
      len(_m2.summaries))

# 去重仍然有效：重复内容不重复记账
_m3 = _iso("m14")
for _ in range(30):
    _m3.add_message("user", "待办：同一件事重复说 30 遍")
_p3 = sum(len(s.get("key_points", [])) for s in _m3.summaries)
check("M14 重复内容仍被去重（不按条数膨胀）", _p3 <= 2, _p3)

print()
print("=" * 66)
print("M 组：真实用户目录零写入")
print("=" * 66)

_AFTER = _real_state()
check("M15 本次判据未改动真实 context_summary.json / key_info.json",
      _BEFORE == _AFTER, (_BEFORE, _AFTER))

print()
print("PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
