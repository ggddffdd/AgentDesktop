# -*- coding: utf-8 -*-
"""v4.213.0 判据：记忆准入元数据随长期记忆落库（外部审核 P1-1 修复）。

背景：`memory_gate.admit()` 判出 source/confidence/evidence_id/expires_at/
verified，但旧版 `agent.py` 落库只传 fact/type/topic —— 准入信息全部丢弃，
记忆落库后分不清「用户明确说的」和「模型推断的」（讽刺的是：被拦下来的
pending 条目反而存了 `[来源:xxx]`，收下的正经条目什么都没存）。

守的东西（每条都能被 `_perturb_memory_meta.py` 单独翻红）：

  A1  带元数据的 append_memory → 条目下紧跟 `[元数据]` 行（五字段齐全）；
  A2  不带元数据 → 无元数据行，与旧格式逐字节兼容（老调用方不受影响）；
  A3  topic 冲突替换路径（_replace_by_topic）同样保留元数据行；
  B1  过有效期条目不再被 recall_memory 当确定事实注入；
  B2  无有效期 / 未过期条目照常注入（不误伤存量记忆）；
  C   entry_is_expired 纯函数边界（无元数据/日期越界/恰好今天）；
  D   agent.py 落库调用点传齐 5 个治理字段（source/confidence/evidence_id/
      expires_at/verified）。

写盘全部经 ms._configure() 隔离到临时目录，绝不碰真实记忆文件。
扰动用环境变量 AGENT_PATH / MS_PATH 指向变异副本。
用法：python tests/test_memory_metadata.py
"""
import ast
import os
import sys
import tempfile
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_p = 0
_f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        print(f"  [PASS] {name}")
        _p += 1
    else:
        print(f"  [FAIL] {name}  {detail}")
        _f += 1


def _load_ms():
    """加载 memory_store —— MS_PATH 指向变异副本时按路径加载（扰动注入用）。"""
    p = os.environ.get("MS_PATH")
    if not p:
        import memory_store as ms
        return ms
    import importlib.util
    spec = importlib.util.spec_from_file_location("ms_mut", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def group_a_persist():
    print("== A 组：元数据落盘 ==")
    ms = _load_ms()
    tmp = tempfile.mkdtemp(prefix="ms_meta_judge_")
    ms._configure(tmp)
    try:
        r = ms.append_memory(
            "用户不吃香菜。", type="偏好", topic="饮食",
            source="用户陈述", confidence=0.95, evidence_id="EV#21",
            expires_at="2027-01-01", verified=True)
        check("A0a 带元数据写入成功", r.startswith("已写入"), f"返回: {r[:40]}")
        txt = ms.load_memory()
        check("A1a 元数据行存在且五字段齐全",
              "[元数据]" in txt and "来源:用户陈述" in txt
              and "置信:0.95" in txt and "证据:EV#21" in txt
              and "有效期至:2027-01-01" in txt and "已验证" in txt,
              f"实际文件：{txt[:200]!r}")
        check("A1b 元数据行紧跟结构化头（第 2 行）",
              txt.strip().splitlines()[1].startswith("[元数据]"))
        # A2：不带元数据 → 无元数据行（老调用方/老格式兼容）
        r2 = ms.append_memory("老式调用没有元数据。")
        txt2 = ms.load_memory()
        check("A2  不带元数据 → 无 [元数据] 行（逐字节兼容旧格式）",
              r2.startswith("已写入")
              and "[元数据]" not in txt2.split("老式调用没有元数据")[0].split("## ")[-1])
        # A3：topic 替换路径保留元数据
        r3 = ms.append_memory(
            "用户不吃香菜，改为不吃芹菜。", topic="饮食",
            source="用户陈述", confidence=0.88)
        txt3 = ms.load_memory()
        seg = [s for s in txt3.split("\n## ") if "芹菜" in s]
        check("A3  topic 替换路径保留元数据行",
              bool(seg) and "[元数据]" in seg[0] and "置信:0.88" in seg[0]
              and "不吃香菜，改为" in seg[0],
              f"替换段：{seg[:1]!r}")
    finally:
        ms._configure(os.path.join(tempfile.gettempdir(), "ms_meta_judge_cleanup"))


def group_b_recall_expiry():
    print("== B 组：过期事实不当确定事实注入 ==")
    ms = _load_ms()
    tmp = tempfile.mkdtemp(prefix="ms_meta_judge_")
    ms._configure(tmp)
    try:
        expired_date = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
        with open(ms.MEMORY_PATH, "w", encoding="utf-8") as f:
            f.write(f"## 2026-01-01 09:00 [事实] #股价\n"
                    f"[元数据] 来源:网页查询 | 置信:0.72 | 有效期至:{expired_date}\n"
                    f"某股票今日收盘价 12.34 元\n")
            f.write(f"\n## 2026-01-02 09:00 [事实] #城市\n"
                    f"[元数据] 来源:用户陈述 | 置信:0.95\n"
                    f"用户住在春城昆明\n")
            f.write(f"\n## 2026-01-03 09:00 [事实] #常量\n"
                    f"光速约每秒 30 万公里\n")
        out = ms.recall_memory("股价 收盘 昆明 光速", limit=8)
        check("B1  过期条目（股价）不被注入", "12.34" not in out)
        check("B2a 未过期条目（昆明）照常注入", "昆明" in out)
        check("B2b 无有效期条目（光速）照常注入", "光速" in out)
    finally:
        ms._configure(os.path.join(tempfile.gettempdir(), "ms_meta_judge_cleanup"))


def group_c_pure():
    print("== C 组：entry_is_expired 纯函数边界 ==")
    ms = _load_ms()
    check("C1  无元数据 → False（默认永久）",
          ms.entry_is_expired("光速约每秒 30 万公里") is False)
    check("C2  日期越界（2026-13-40）→ False（当没写）",
          ms.entry_is_expired("[元数据] 有效期至:2026-13-40\nx") is False)
    today = datetime.now().strftime("%Y-%m-%d")
    check("C3  有效期恰好今天 → False（含当天）",
          ms.entry_is_expired(f"[元数据] 有效期至:{today}\nx") is False)
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    check("C4  有效期昨天 → True",
          ms.entry_is_expired(f"[元数据] 有效期至:{yesterday}\nx") is True)
    check("C5  中文冒号也认", ms.entry_is_expired(
        f"[元数据] 有效期至：{yesterday}\nx") is True)


def group_d_agent_call():
    print("== D 组：记忆落库调用点传齐治理字段 ==")
    # v4.236.0：自动记忆块从 agent.py 尾部外移到 agent_memory_mixin.py（红线减压）。
    # 判据跟随新家 —— 否则「本体还在、只是换了文件」会被误报成真实回归。
    path = os.environ.get("AGENT_PATH") or os.path.join(ROOT, "agent_memory_mixin.py")
    with open(path, "r", encoding="utf-8-sig") as f:
        tree = ast.parse(f.read())
    call = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append_memory"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "memory_store"):
            call = node
    check("D0  找到 memory_store.append_memory 调用", call is not None)
    if call:
        kw = {k.arg for k in call.keywords}
        need = {"source", "confidence", "evidence_id", "expires_at", "verified"}
        check("D1  五个治理字段全部传入",
              need <= kw, f"缺: {sorted(need - kw)}")

    # D2：光「本体在新家」不够 —— 还得确认主类真的继承了它。
    # 否则搬运漏了接线，自动记忆静默停摆（不报错、不落库），判据却全绿。
    ag_path = os.path.join(ROOT, "agent.py")
    with open(ag_path, "r", encoding="utf-8-sig") as f:
        ag_src = f.read()
    check("D2  agent.py 继承 AgentMemoryMixin（搬运后接线在位）",
          "AgentMemoryMixin" in ag_src,
          "主类没继承 → 记忆块成了孤儿模块，自动记忆静默停摆")


def main():
    print("==== 记忆元数据落库：判据开始 ====")
    group_a_persist()
    group_b_recall_expiry()
    group_c_pure()
    group_d_agent_call()
    print(f"\n==== 记忆元数据落库结果：PASS={_p}  FAIL={_f} ====")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
