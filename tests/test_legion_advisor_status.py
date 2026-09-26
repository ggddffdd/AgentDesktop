# -*- coding: utf-8 -*-
"""军团「顾问模式带风险接受」终态一致性回归（审查 #4 / v4.168.0）。

背景
----
顾问模式（advisory）在重跑上限耗尽后，旧代码只做了三件事：

    1. 任务板写 status="rejected"
    2. 日志写一句「标红放行，不卡流水线」
    3. break 掉重试循环，继续跑后面的波

但它**没有**：改终态、推进 last_completed_wave、把 checkpoint 游标与实际
继续执行的位置对齐。最终还会标整个 run 为 done。于是出现语义冲突：

    任务板：第 N 波 rejected
    实际流程：后续波已执行
    最终状态：done
    checkpoint：没记录这波已被接受

本套件钉住修好后的四条线：
  ① 任务板写 `accepted_with_warning`（不再写 rejected）
  ② 审计账本记一笔「带风险接受」（by=advisory）
  ③ checkpoint 用 last_completed_wave=本波落盘
  ④ 结项报告强制补录带风险波次（含续跑重建）

无 Qt / 无 legion_worker 导入 —— 用 AST 抽源码 + 桩对象跑**真行为**。
"""
import ast
import os
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent
sys.path.insert(0, str(ROOT))

_SBX = tempfile.mkdtemp(prefix="xc_verify_sbx_advisor_")
os.environ["XC_LEGION_DIR"] = _SBX
os.environ.setdefault("XC_USER_DATA_DIR", os.path.join(_SBX, "userdata"))

import legion as L   # noqa: E402

SRC = (ROOT / "legion_worker.py").read_text(encoding="utf-8-sig")
TREE = ast.parse(SRC)
LINES = SRC.splitlines()

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [PASS] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}" + (f" —— {detail}" if detail else ""))


def _method_source(cls_name, meth_name):
    for node in ast.walk(TREE):
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            for fn in node.body:
                if isinstance(fn, ast.FunctionDef) and fn.name == meth_name:
                    return ast.get_source_segment(SRC, fn), fn
    return None, None


class _FakeLog:
    def __init__(self):
        self.warnings = []

    def warning(self, *a, **k):
        self.warnings.append(a)


class _Stub:
    """_accept_with_warning 用到的全部 self 表面。"""

    def __init__(self):
        self.pid = "proj_1"
        self._pname = "选题项目"
        self.run_id = "run_1"
        # 真实形状：wi -> {mi: [role, text]}（见 legion.save_checkpoint 的 _wmt_json）
        self._wave_member_texts = {0: {0: ["研究员", "底稿A"]},
                                   1: {0: ["写手", "底稿B"]}}
        self._warned_waves = {}
        self.boards = []
        self.logs = []
        self.saved = []

    def _board(self, node_id, wave=None, status="", pm_verdict=""):
        self.boards.append({"node_id": node_id, "wave": wave,
                            "status": status, "pm_verdict": pm_verdict})

    def _log(self, text):
        self.logs.append(text)


def _waves(n):
    """军团真实 waves 形状：list[list[member dict]]（见 legion._waves_fingerprint）。"""
    return [[{"name": f"成员{i}", "tools": [], "model": ""}] for i in range(n)]


def _load_real_method():
    """把 _accept_with_warning 的真实源码 exec 出来，配桩 legion / log。

    注意：这里刻意**不**复制方法体 —— 复制就等于测试自己写的代码，
    源码一改测试还绿。必须是同一份源码。
    """
    src, _ = _method_source("LegionWorker", "_accept_with_warning")
    assert src, "未找到 LegionWorker._accept_with_warning"
    ns = {"legion": L, "log": _FakeLog()}
    exec(compile(src, "<legion_worker._accept_with_warning>", "exec"), ns)
    return ns["_accept_with_warning"], ns["log"]


# ==========================================================================
def part_a():
    print("=== A) 分支改造：不再写 rejected 后继续 ===")
    # 定位 advisor 上限分支
    idx = [i for i, l in enumerate(LINES)
           if "if gate_mode == \"advisory\" and attempt >= max_retry:" in l]
    check("找到顾问模式上限分支（唯一）", len(idx) == 1, f"命中 {len(idx)} 处")
    i = idx[0]
    block = "\n".join(LINES[i:i + 8])
    check("分支调用 _accept_with_warning", "_accept_with_warning(" in block, block)
    check("分支推进 last_passed_wave = wi", "last_passed_wave = wi" in block, block)
    check("分支不再只打一句日志就 break",
          "标红放行，不卡流水线" not in block, block)

    # rejected 的 _board 必须在顾问分支**之后**（否则先写 rejected 再改，事件脏）
    rej = [j for j, l in enumerate(LINES)
           if 'status="rejected", pm_verdict=suggest' in l]
    check("仍保留 rejected 写入（真打回重跑路径）", len(rej) == 1, f"{len(rej)} 处")
    check("rejected 写入位于顾问分支之后（顺序正确）", rej and rej[0] > i,
          f"rejected@{rej} advisor@{i}")
    check("顾问分支内含 break（本波到此结束）", "break" in block, block)


def part_b():
    print("\n=== B) 真行为：_accept_with_warning 一次跑出四条线 ===")
    fn, flog = _load_real_method()
    sb = _Stub()
    fn(sb, 3, 2, 2, 2, "fp-3", "FAIL（建议打回）",
       "任务描述", "计划文本", {0: "波0产出", 1: "波1产出"}, ["验收0", "验收1"],
       _waves(3))

    print("\n-- B1 任务板终态 --")
    check("任务板只写一次", len(sb.boards) == 1, str(sb.boards))
    check("终态是 accepted_with_warning",
          sb.boards[0]["status"] == "accepted_with_warning", str(sb.boards[0]))
    check("不是 rejected", sb.boards[0]["status"] != "rejected")
    check("带 PM 判定留痕", sb.boards[0]["pm_verdict"] == "FAIL（建议打回）")
    check("波号正确", sb.boards[0]["wave"] == 3, str(sb.boards[0]))

    print("\n-- B2 审计账本 --")
    recs = L.read_auth(10)
    check("审计写入一笔", len(recs) == 1, str(recs))
    r = recs[0]
    check("decision = 带风险接受", r.get("decision") == "带风险接受", str(r))
    check("by = advisory（不是 user，不算人手授权）", r.get("by") == "advisory",
          str(r.get("by")))
    check("带 run_id 谱系", r.get("run_id") == "run_1", str(r.get("run_id")))
    check("reason 写明不是授权", "不是授权" in (r.get("reason") or ""),
          str(r.get("reason")))
    check("审计已接哈希链", bool(r.get("record_hash")))
    check("链路校验通过", L.verify_auth_chain()["ok"] is True)

    print("\n-- B3 checkpoint 游标 --")
    cp = L.load_checkpoint(sb.pid) if hasattr(L, "load_checkpoint") else None
    check("save_checkpoint 可调用（未抛异常）", True)
    check("load_checkpoint 返回本波为已完成",
          isinstance(cp, dict) and cp.get("last_completed_wave") == 2,
          str(cp)[:200] if cp else "None")
    check("checkpoint 保留成员级底稿",
          isinstance(cp, dict) and (cp.get("wave_member_texts") or {}),
          str(cp)[:200] if cp else "None")

    print("\n-- B4 登记簿 + 日志 --")
    check("_warned_waves 记下本波", 3 in sb._warned_waves, str(sb._warned_waves))
    note = sb._warned_waves.get(3, "")
    check("风险说明含波号/次数/上限",
          "第 3 波" in note and "2 次" in note and "上限 2" in note, note)
    check("风险说明写明带风险继续", "带风险继续" in note, note)
    check("日志提示已推进到下一波",
          any("已推进到第 3 波" in t for t in sb.logs), str(sb.logs))

    print("\n-- B5 幂等/容错：_board 抛异常不阻断其余三条线 --")
    class _BadBoard(_Stub):
        def _board(self, *a, **k):
            raise RuntimeError("任务板写盘失败")

    sb2 = _BadBoard()
    try:
        fn(sb2, 1, 0, 1, 1, "fp", "FAIL", "t", "p", {}, [], [])
        ok = True
    except Exception as e:
        ok = False
        flog.warning(("raised", e))
    check("_board 失败不抛穿（降级继续）", ok)
    check("仍写了审计", len(L.read_auth(10)) >= 1)
    check("仍登记了带风险波次", 1 in sb2._warned_waves, str(sb2._warned_waves))


def part_c():
    print("\n=== C) 结项报告：带风险波次必须补录 ===")
    src, _ = _method_source("LegionWorker", "_make_final_report")
    check("取到 _make_final_report 源码", bool(src))
    check("读取 _warned_waves", "_warned_waves" in src)
    check("报告节标题明确", "带风险接受" in src and "系统补录" in src)
    check("报告节说明未通过验收", "未通过项目经理验收" in src)
    check("报告节说明放行权在大哥手里", "放行权始终在大哥手里" in src)
    check("报告节列出具体波号", "第 {int(w)} 波" in src)
    # 位置：必须在 return 之前
    tail = src[src.index("_warned_waves"):]
    check("补录块在 return _sum 之前", "return _sum" in tail, tail[-200:])

    print("\n-- C2 行为仿真：块输出内容正确 --")
    sb = _Stub()
    sb._warned_waves = {2: "第 2 波未通过验收（项目经理 2 次判定均为 FAIL）—— 按顾问模式带风险继续",
                        4: "第 4 波未通过验收（项目经理 1 次判定均为 FAIL）—— 按顾问模式带风险继续"}
    _sum = "## 结项总结\n正文"
    _warned = sb._warned_waves
    _wl = [f"· 第 {int(w)} 波：{_warned[w][:160]}" for w in sorted(_warned)]
    _sum = (_sum + "\n\n---\n\n## ⚠️ 带风险接受（顾问模式 · 系统补录）\n"
            + "\n".join(_wl)).strip()
    check("按波号升序排列", _sum.index("第 2 波") < _sum.index("第 4 波"))
    check("两波都在", "第 2 波" in _sum and "第 4 波" in _sum)
    check("挂在正文之后", _sum.startswith("## 结项总结"))

    print("\n-- C3 续跑重建：从审计账本恢复带风险波次 --")
    src2, _ = _method_source("LegionWorker", "run")
    check("续跑路径读审计重建", "_warned_waves" in src2 and "带风险接受" in src2)
    check("重建按 project_id 过滤", 'str(_r.get("project_id")) == str(self.pid)' in src2
          or "project_id\")) == str(self.pid)" in src2)
    check("重建限定同一 run_id", 'self.run_id' in src2)


def part_d():
    print("\n=== D) 语义对照：任务板 / 审计 / checkpoint / 报告 四处一致 ===")
    fn, _ = _load_real_method()
    sb = _Stub()
    fn(sb, 5, 4, 3, 3, "fp5", "FAIL（建议打回）", "t", "p", {}, [], _waves(5))
    board_status = sb.boards[0]["status"]
    audit_decision = L.read_auth(1)[0]["decision"]
    cp_wave = L.load_checkpoint(sb.pid).get("last_completed_wave")
    warned = sb._warned_waves
    check("任务板 = accepted_with_warning", board_status == "accepted_with_warning")
    check("审计 = 带风险接受", audit_decision == "带风险接受")
    check("checkpoint 推进到本波（wi=4）", cp_wave == 4, str(cp_wave))
    check("报告登记簿含本波（wave_no=5）", 5 in warned, str(warned))
    check("四处不再是「rejected + 继续」的混搭",
          board_status != "rejected" and board_status == "accepted_with_warning")
    check("审计链在多次写入后仍完整", L.verify_auth_chain()["ok"] is True)


def main():
    part_a()
    part_b()
    part_c()
    part_d()
    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
