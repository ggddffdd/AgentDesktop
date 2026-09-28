"""v4.177.0 回归：历史注入的「字符预算」闸（第二道闸，按体量而非条数）。

为什么需要：既有的 `max_history` 按**条数**截断，条数相同、体量能差几十倍 ——
30 条纯文本 ≈ 几十 KB，30 条带图/带工具结果可以到几百 KB
（实测见过单次 payload 264KB）。条数闸管不住"条数不多但每条巨大"。

设计取舍（本套件把这些取舍钉住）：
  · 单位是**字符数**不是 token（不引 tokenizer：依赖/体积/版本漂移都不划算）
  · 丢整条之后**交给既有 `_repair_tool_pairs`**，不自己写第二份配对逻辑
  · 从**最旧**丢，永远保住最后一条（本轮提问）
  · `budget <= 0` = 关闭，行为与旧版完全一致（可逆）
"""
import ast
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ui  # noqa: E402

_p = _f = 0


def check(label, got, exp=True, extra=""):
    global _p, _f
    ok = (got == exp)
    _p += ok
    _f += (not ok)
    print(f"  {'✓' if ok else '✗'} {label:<56} got={got!s:<6} exp={exp}  {extra}")


def size(msgs):
    return sum(len(json.dumps(m, ensure_ascii=False)) for m in msgs)


def orphan_tools(msgs):
    """返回孤儿 tool 消息数（前面没有配对的 assistant.tool_calls）。"""
    pending, bad = set(), 0
    for m in msgs:
        if m.get("role") == "assistant":
            pending = {tc.get("id") for tc in (m.get("tool_calls") or []) if tc.get("id")}
        elif m.get("role") == "tool":
            if m.get("tool_call_id") not in pending:
                bad += 1
            else:
                pending.discard(m.get("tool_call_id"))
    return bad


def big_msg(chars, role="user", text="X"):
    return {"role": role, "content": text * chars}


PAIR_HISTORY = [
    {"role": "user", "content": "最早的提问"},
    {"role": "assistant", "content": "",
     "tool_calls": [{"id": "c1", "type": "function",
                     "function": {"name": "f", "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "c1", "content": "结果一" * 20},
    {"role": "assistant", "content": "中间答复"},
    {"role": "user", "content": "当前提问（必须保住）"},
]


# ---------------------------------------------------------------------------
def part_a_off_is_noop():
    print("\n-- A) 关闭语义：budget <= 0 时行为与旧版完全一致（可逆） --")
    for b in (0, None, -1):
        kept, dropped, chars = ui._fit_history_to_budget(PAIR_HISTORY, b)
        check(f"A1 budget={b!r} → 一条不丢", dropped, 0)
        check(f"A2 budget={b!r} → 内容逐条不变",
              json.dumps(kept, ensure_ascii=False) == json.dumps(PAIR_HISTORY, ensure_ascii=False))
    check("A3 空列表不炸", ui._fit_history_to_budget([], 100), ([], 0, 0))


def part_b_within_budget():
    print("\n-- B) 预算内 → 原样不动 --")
    kept, dropped, chars = ui._fit_history_to_budget(PAIR_HISTORY, 10 ** 7)
    check("B1 不丢任何一条", dropped, 0)
    check("B2 返回的字符数 = 实际体量", chars, size(PAIR_HISTORY))


def part_c_drop_oldest():
    print("\n-- C) 超预算 → 从最旧开始丢，丢到装得下 --")
    msgs = [big_msg(2000, text=f"第{i}条") for i in range(6)]
    full = size(msgs)
    budget = max(1200, full // 3)
    # 保证 last 不超预算，否则 min_keep 会兜住它
    kept, dropped, chars = ui._fit_history_to_budget(msgs, budget)
    check("C1 确实丢了", dropped >= 1, True, f"丢 {dropped} 条")
    check("C2 丢下来后 ≤ 预算", chars <= budget, True, f"{chars} ≤ {budget}")
    check("C3 丢的是**最旧**的（留下的更靠后）",
          kept[-1]["content"].startswith("第5条"), True)
    check("C4 字符数与实际一致", chars, size(kept))
    check("C5 丢掉的条数 + 留下的条数 = 总数", dropped + len(kept), len(msgs))


def part_d_last_always_kept():
    print("\n-- D) 最后一条（本轮提问）永远保住，哪怕它自己就超预算 --")
    msgs = [big_msg(500, text="旧的"), big_msg(99999, text="当前提问超长")]
    kept, dropped, chars = ui._fit_history_to_budget(msgs, 1000)
    check("D1 最后一条仍在", kept and kept[-1]["content"].startswith("当前提问"), True)
    check("D2 超预算也认了（chars 可 > budget）", chars > 1000, True)
    check("D3 只丢掉了旧的那条", dropped, 1)


def part_e_tool_pairs_intact():
    print("\n-- E) 丢完整条后不产生孤儿 tool（复用既有配对修复，不写第二份） --")
    kept, dropped, _ = ui._fit_history_to_budget(PAIR_HISTORY, 200)
    # 原始裁剪结果**可能**留下孤儿 tool（取决于切在哪）→ 这正是必须复用修复函数的原因
    check("E1 复用既有 _repair_tool_pairs 即可清零孤儿",
          orphan_tools(ui._repair_tool_pairs(list(kept))), 0,
          f"丢 {dropped} 条，修复前孤儿 {orphan_tools(kept)}")
    # 真实链路：_build_api_history 会自己修
    out = ui._build_api_history(PAIR_HISTORY, vision_ok=False,
                               max_history=30, char_budget=200)
    check("E2 走 _build_api_history 后无孤儿 tool", orphan_tools(out), 0)
    check("E3 最后一条仍是当前提问",
          out and str(out[-1].get("content")).startswith("当前提问"), True)


def part_f_big_image_dropped_first():
    print("\n-- F) 大图消息按体量计入（会被优先丢掉） --")
    big_img = {"role": "user", "content": [
        {"type": "text", "text": "这张图"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + "A" * 60000}},
    ]}
    msgs = [big_img, {"role": "assistant", "content": "已看图"},
            {"role": "user", "content": "当前提问"}]
    kept, dropped, chars = ui._fit_history_to_budget(msgs, 5000)
    check("F1 大图那条被丢掉", dropped >= 1, True)
    check("F2 留下的不含大图",
          all("base64" not in json.dumps(m, ensure_ascii=False) for m in kept), True)
    check("F3 当前提问保住", str(kept[-1].get("content")).startswith("当前提问"), True)


def part_g_pure_function():
    print("\n-- G) 纯函数：不改传入的 list（调用方靠对象身份对齐） --")
    msgs = [big_msg(2000, text=f"第{i}条") for i in range(5)]
    snapshot = json.dumps(msgs, ensure_ascii=False)
    before_len = len(msgs)
    ui._fit_history_to_budget(msgs, 500)
    check("G1 入参列表长度没变", len(msgs), before_len)
    # ⚠️ 别把整个快照塞进 extra —— 会把套件输出撑到上百 KB（run_all 捕获时会淹掉别的）
    check("G2 入参内容没被改", json.dumps(msgs, ensure_ascii=False) == snapshot)


def part_h_source_contract():
    print("\n-- H) 源码契约 --")
    src = open(os.path.join(ROOT, "ui.py"), encoding="utf-8-sig").read()
    check("H1 闸在**条数闸之后**（先粗后细）",
          src.index("len(cleaned) > int(max_history)") < src.index("_fit_history_to_budget(cleaned"))
    check("H2 裁剪后又修了一遍配对",
          "cleaned = _repair_tool_pairs(cleaned)" in src)
    check("H3 两个调用点都接上了配置键",
          src.count('char_budget=self.cfg.get("history_char_budget", 0)'), 2)
    check("H4 _build_api_history 签名含 char_budget",
          "char_budget=None" in src)
    import config
    check("H5 config 有默认键（load_config 会补，旧配置不会 KeyError）",
          "history_char_budget" in config.DEFAULT_CONFIG, True)
    check("H6 默认值 > 0（默认开启兜底）",
          int(config.DEFAULT_CONFIG["history_char_budget"]) > 0, True)
    check("H7 默认值不至于误伤正常会话（≥60KB）",
          int(config.DEFAULT_CONFIG["history_char_budget"]) >= 60000, True)


def part_i_negative_direction():
    print("\n-- I) 负面验证：把「从最旧丢」改成「从最新丢」→ 当前提问会被丢掉 --")
    src = open(os.path.join(ROOT, "ui.py"), encoding="utf-8-sig").read()
    tree = ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "_fit_history_to_budget")
    # 用 ast.get_source_segment 取**原始源码片段**再改 —— 不能用 ast.unparse：
    # 它会重排格式（例如给 return 的元组加括号），字符串锚点就失配了（第一版踩过）。
    seg = ast.get_source_segment(src, fn)
    broken = seg.replace("return msgs[i:], i, total",
                         "return msgs[:len(msgs) - i], i, total")
    check("I1 锚点命中（方向可被改坏）", broken != seg)
    ns = {"json": json}
    exec(compile(broken, "<broken>", "exec"), ns)
    msgs = [big_msg(2000, text=f"第{i}条") for i in range(6)]
    kept_bad, dropped_bad, _ = ns["_fit_history_to_budget"](msgs, 1200)
    check("I2 方向改坏后：当前提问（最后一条）被丢掉（证明方向必需）",
          kept_bad[-1]["content"].startswith("第5条"), False,
          f"留下的是 {kept_bad[-1]['content'][:12]!r}")
    kept_ok, _, _ = ui._fit_history_to_budget(msgs, 1200)
    check("I3 正确方向：最后一条仍在", kept_ok[-1]["content"].startswith("第5条"), True)


def main():
    part_a_off_is_noop()
    part_b_within_budget()
    part_c_drop_oldest()
    part_d_last_always_kept()
    part_e_tool_pairs_intact()
    part_f_big_image_dropped_first()
    part_g_pure_function()
    part_h_source_contract()
    part_i_negative_direction()
    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
