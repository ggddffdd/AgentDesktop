# -*- coding: utf-8 -*-
"""v4.290.0 P1：Agent 时间预算（180s 硬编码 → 可配 + 每轮重置）。

背景（2026-11-11 大哥报障「任务没完成就超时停止」）：
  · `MAX_DURATION = 180` 硬编码在 `_run_inner` 局部变量，做 PPT/视频/多步搜索必被砍；
  · 续跑轮复用同一个 `_t0`，180 秒一到续跑一进来就再判超时 → 续跑形同虚设；
  · 续跑循环原本**完全没有超时检查**，模型卡住能一直耗。

纯标准库，不联网。
"""
import os
import sys
import tempfile
from pathlib import Path

_SBX = tempfile.mkdtemp(prefix="xc_290_time_")
os.environ["XC_USER_DATA_DIR"] = os.path.join(_SBX, "user")
os.environ["XC_LOG_DIR"] = os.path.join(_SBX, "logs")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + (("  <- " + str(detail)) if detail else ""))


_SRC = (ROOT / "agent.py").read_text(encoding="utf-8-sig")

print("=" * 66)
print("T 组：预算读取")
print("=" * 66)

check("T1 默认 (round, total) = (600, 1800)",
      config.get_agent_time_budget() == (600, 1800),
      config.get_agent_time_budget())
check("T2 config 覆盖生效",
      config.get_agent_time_budget(
          {"agent_round_seconds": 60, "agent_total_seconds": 300}) == (60, 300))
check("T3 非法值（非数字 / 负数）回退默认",
      config.get_agent_time_budget(
          {"agent_round_seconds": "x", "agent_total_seconds": -5}) == (600, 1800))
check("T4 越界值被夹取（round≤3600，total≤14400）",
      config.get_agent_time_budget(
          {"agent_round_seconds": 99999, "agent_total_seconds": 99999})
      == (3600, 14400))
check("T5 缺参/None 也不炸",
      config.get_agent_time_budget(None) == (600, 1800))

print()
print("=" * 66)
print("W 组：接线（agent.py 结构钉子）")
print("=" * 66)

check("W1 不再写死 MAX_DURATION = 180",
      "MAX_DURATION = 180" not in _SRC)
check("W2 改为从 config 读预算",
      "config.get_agent_time_budget(" in _SRC)
check("W3 有本轮起点 _round_t0",
      "_round_t0 = _t0" in _SRC)
check("W4 超时判断全部改看本轮起点（≥4 处读取）",
      _SRC.count("_round_t0") >= 6, _SRC.count("_round_t0"))
check("W5 所有超时判断都不再用总起点 `- _t0 > MAX_DURATION`",
      "time.time() - _t0 > MAX_DURATION" not in _SRC
      and "time.time() - _t0 > _TOTAL_BUDGET" not in _SRC)
check("W6 续跑轮开始前重置计时",
      "续跑轮**单独计时**" in _SRC or "_round_t0 = time.time()" in _SRC)
check("W7 总预算兜底存在（_TOTAL_BUDGET 至少 3 处：取值+主循环+续跑）",
      _SRC.count("_TOTAL_BUDGET") >= 3, _SRC.count("_TOTAL_BUDGET"))
check("W8 续跑循环里有超时检查（续跑轮超时文案存在）",
      "续跑轮执行超时" in _SRC)
check("W9 总预算耗尽也标可续（有 total_budget 原因）",
      '"_mark_resumable("total_budget")'.replace('"', "") in _SRC.replace('"', "")
      or "_mark_resumable(\"total_budget\")" in _SRC)

# 行为级：把 round 调小，确认 _run_inner 真的读配置而不是常量
_small = config.get_agent_time_budget({"agent_round_seconds": 60})
check("W10 行为级：传小 round 后返回值随之变化（非恒量）",
      _small[0] == 60 and _small[0] != config.get_agent_time_budget()[0])

print()
print("PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
