# -*- coding: utf-8 -*-
"""v4.217.0 判据：system_control 新增 clean_recycle_bin 真实工具 + 反编造硬约束。

被测对象：system_control_tools.tool_clean_recycle_bin / _count_recycle_items / _empty_recycle_bin

核心契约（针对「小臭撒谎」根因）：
  ① 工具真实注册（schema 声明 + 路由表 + 风险 EXEC）
  ② 成功路径必须带 before/after 计数证据（"清空前 N 项"）
  ③ 失败 / 复检失败路径不得谎报「已清空」，且必须带清空前计数
  ④ 计数是真实遍历（源码走 os.walk），不是硬编码常量

零副作用：本套件用 monkeypatch 把 _count_recycle_items / _empty_recycle_bin 换成受控桩，
绝不真的去清空任何回收站、绝不调用真实 PowerShell。
用法：python tests/test_clean_recycle_bin_217.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_p = 0
_f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [PASS] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}  {detail}")


import system_control_tools as sct                       # noqa: E402
from risk import RiskClass, RISK_MAP, tier_of            # noqa: E402


def _restore(name, fn):
    setattr(sct, name, fn)


# ===========================================================================
# ① 注册完整性
# ===========================================================================
def test_registration():
    names = [d["function"]["name"] for d in sct.SYSTEM_CONTROL_TOOL_DEFS]
    check("schema 声明含 clean_recycle_bin", "clean_recycle_bin" in names)
    check("路由表含 clean_recycle_bin", "clean_recycle_bin" in sct.SYSTEM_CONTROL_TOOL_TABLE)
    check("handler 可调用", callable(sct.SYSTEM_CONTROL_TOOL_TABLE.get("clean_recycle_bin")))
    check("handler 签名含 (cfg, app_dir, args)",
          "cfg" in __import__("inspect").signature(sct.tool_clean_recycle_bin).parameters)
    check("风险等级登记为 EXEC", RISK_MAP.get("clean_recycle_bin") == RiskClass.EXEC,
          "RISK_MAP=%r" % RISK_MAP.get("clean_recycle_bin"))
    check("EXEC 对应 manual 确认档", tier_of("clean_recycle_bin") == "manual",
          "tier=%r" % tier_of("clean_recycle_bin"))


# ===========================================================================
# ② 成功路径必须带 before/after 计数证据（反编造）
# ===========================================================================
def test_success_evidence():
    orig_c = sct._count_recycle_items
    orig_e = sct._empty_recycle_bin
    calls = {"n": 0}

    def fake_count():
        calls["n"] += 1
        return 0 if calls["n"] > 1 else 42      # 第一次 42，第二次 0 → 模拟成功删除

    try:
        sct._count_recycle_items = fake_count
        sct._empty_recycle_bin = lambda: None
        msg, _d, _s = sct.tool_clean_recycle_bin(cfg=None, app_dir=None, args={})
        check("成功消息含『清空前 42 项』计数证据", "清空前 42 项" in msg, msg)
        check("成功消息含『清空后』计数", "清空后" in msg, msg)
        check("成功消息含『已清空』字样", "已清空" in msg, msg)
    finally:
        _restore("_count_recycle_items", orig_c)
        _restore("_empty_recycle_bin", orig_e)


# ===========================================================================
# ③ 失败路径不得谎报「已清空」（反编造核心）
# ===========================================================================
def test_failure_no_lie():
    orig_c = sct._count_recycle_items
    orig_e = sct._empty_recycle_bin

    def fake_count():
        return 42                                  # 清空会抛错，after 不会被调用到

    try:
        sct._count_recycle_items = fake_count
        sct._empty_recycle_bin = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        msg, _d, _s = sct.tool_clean_recycle_bin(cfg=None, app_dir=None, args={})
        # 反编造：诚实失败必须明说「失败」（不是把"已清空"当成功宣称）；
        # 子串"未确认已清空"里虽含"已清空"，但"失败"二字是诚实失败的硬标志。
        check("失败路径明说『失败』而非谎称成功", "失败" in msg, msg)
        check("失败路径带清空前计数证据", "清空前计数=42" in msg, msg)
    finally:
        _restore("_count_recycle_items", orig_c)
        _restore("_empty_recycle_bin", orig_e)


# ===========================================================================
# ④ 计数是真实遍历（源码走 os.walk），非硬编码常量
# ===========================================================================
def test_count_is_real_traversal():
    src = open(os.path.join(ROOT, "system_control_tools.py"), encoding="utf-8").read()
    check("计数函数源码含 os.walk（真实遍历）",
          "_count_recycle_items" in src and "os.walk" in src)
    check("清空函数走 Clear-RecycleBin 命令",
          "_empty_recycle_bin" in src and "Clear-RecycleBin" in src)
    check("计数辅助函数存在且可调用", callable(sct._count_recycle_items))


if __name__ == "__main__":
    test_registration()
    test_success_evidence()
    test_failure_no_lie()
    test_count_is_real_traversal()
    print(f"\nPASS={_p} FAIL={_f}")
    sys.exit(1 if _f else 0)
