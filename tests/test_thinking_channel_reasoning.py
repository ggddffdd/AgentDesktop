# -*- coding: utf-8 -*-
"""「思考模式下必须回传 reasoning_content」回归（v4.168.2 BUG 修）。

事故（2026-09-27，用户实测）
---------------------------
跑「每日GitHub热榜」自动化时，模型调用报 `HTTP Error 400: Bad Request`，
任务卡住不动。日志里只有这一句 —— **API 的真实报文被丢掉了**，无从定位。

定位过程（全部用真实接口实测）
------------------------------
1. 会话/参数/工具/消息形态逐项排除：单变量全 200，历史清洗后也全 200；
2. 读 `route_log.jsonl` 发现失败的那次被**升舱到 DeepSeek 官方**
   （`route | deepseek-flash | api.deepseek.com | tool_intent`），且其后**没有 usage 记录**；
3. 在真实通道上复现，拿到 API 报文：
   400 `The ``reasoning_content`` in the thinking mode must be passed back to the API.`
4. 规则实测（deepseek-flash）：
   · `[SYS, U, A(纯文本)]`                → **400**（assistant 缺该字段）
   · `[SYS, U, A(tool_calls), T]`         → **400**
   · `A(..., reasoning_content="…")`      → 200
   · `A(..., reasoning_content="")`       → **200（空串也算带上）**
   → **思考模式下，每条 assistant 消息都必须带 `reasoning_content`。**

根因：本项目的出站消息白名单只有 `role/content/tool_calls/tool_call_id` 四项，
      把 `reasoning_content` 筛掉了；`_stream_once` 也从不累积它。
      结果：**历史里一旦出现 assistant 消息，走 DeepSeek 通道的调用从第二轮起必然 400**
      （第一轮 messages 末尾是 user，天然没有 assistant 要回传 → 所以"第一轮能跑，第二轮挂"）。

纯标准库 + AST（不依赖 PySide6）。
"""

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_UI = (ROOT / "ui.py").read_text(encoding="utf-8-sig")
_AGENT = (ROOT / "agent.py").read_text(encoding="utf-8-sig")

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [PASS] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}" + (f" —— {detail}" if detail else ""))


def _extract(src, names, extra_ns=None):
    """按名抽取模块级函数并 exec（不导入 ui，避开 Qt）。"""
    tree = ast.parse(src)
    ns = dict(extra_ns or {})
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            exec(ast.get_source_segment(src, node), ns)
    return ns


# 线上那份实现（不复制逻辑）
_ns = _extract(_UI, {"_ensure_reasoning_content", "_is_thinking_channel",
                     "_api_error_text", "_flatten_text_content",
                     "_sanitize_msg_for_api", "_build_api_history",
                     "is_thinking_channel", "_repair_tool_pairs"},
               extra_ns={"re": re, "json": __import__("json")})
ensure_rc = _ns["_ensure_reasoning_content"]
is_think = _ns["_is_thinking_channel"]
api_err = _ns["_api_error_text"]


def validate_thinking(msgs):
    """本地校验：模拟 DeepSeek 思考模式的硬要求（每条 assistant 必须带该字段）。"""
    bad = []
    for i, m in enumerate(msgs or []):
        if isinstance(m, dict) and m.get("role") == "assistant" \
                and "reasoning_content" not in m:
            bad.append(f"#{i} assistant 缺 reasoning_content")
    return bad


def part_a():
    print("=== A) 通道判定：谁要求回传 ===")
    cases = [
        ("https://api.deepseek.com", "deepseek-flash", True),
        ("https://api.deepseek.com", "deepseek-chat", True),
        ("https://api.deepseek.com", "deepseek-reasoner", True),
        ("https://api.deepseek.com/v1", "deepseek-flash", True),
        ("https://apihub.agnes-ai.cn/v1", "agnes-3.0-flash", False),
        ("https://api.siliconflow.cn/v1", "deepseek-ai/DeepSeek-V3", False),
        ("https://api.siliconflow.cn/v1", "Qwen/Qwen3-8B", False),
        ("https://open.bigmodel.cn/api/paas/v4", "glm-4-flash", False),
    ]
    for base, model, exp in cases:
        got = is_think(base, model)
        check(f"{base[:30]} / {model} → {exp}", got is exp, f"实际 {got}")
    check("名字带 thinking 的模型也算",
          is_think("https://x.com/v1", "some-thinking-model") is True)
    check("空参数不炸", is_think("", "") is False and is_think(None, None) is False)


def part_b():
    print("\n=== B) 补齐函数行为 ===")
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        {"role": "assistant", "content": "带真思考", "reasoning_content": "真的想过"},
    ]
    out = ensure_rc(msgs, required=True)
    check("缺的补成空串", out[2].get("reasoning_content") == "", str(out[2]))
    check("已有的不动（保留真思考内容）",
          out[4].get("reasoning_content") == "真的想过", str(out[4]))
    check("user / tool / system 不被动",
          all("reasoning_content" not in out[i] for i in (0, 1, 3)),
          str([out[i] for i in (0, 1, 3)]))
    check("**原列表未被就地修改**（_seq 回写依赖对象身份）",
          "reasoning_content" not in msgs[2], str(msgs[2]))
    check("返回的是新列表对象", out is not msgs)
    # 幂等
    check("幂等（再补一次不变）", ensure_rc(out, required=True) == out)
    # required=False 时原样返回
    check("required=False 时原样返回", ensure_rc(msgs, required=False) is msgs)
    # 容错
    check("空输入不炸", ensure_rc([], required=True) == [])
    check("None 不炸", ensure_rc(None, required=True) == [])
    check("混入非 dict 不炸",
          len(ensure_rc(["x", None, {"role": "assistant", "content": ""}], True)) == 3)


def part_c():
    print("\n=== C) 出站白名单必须保住 reasoning_content ===")
    sanitize = _ns["_sanitize_msg_for_api"]
    build = _ns["_build_api_history"]

    m = {"role": "assistant", "content": "答案", "reasoning_content": "推理过程",
         "_seq": 7, "_internal": True}
    s = sanitize(m, vision_ok=False)
    check("_sanitize_msg_for_api 保住该字段",
          s and s.get("reasoning_content") == "推理过程", str(s))
    check("_sanitize 仍然滤掉 _seq", s and "_seq" not in s, str(s))

    m0 = {"role": "assistant", "content": "答案", "reasoning_content": ""}
    check("空串也保住（空串合法，实测通过）",
          sanitize(m0).get("reasoning_content") == "", str(sanitize(m0)))

    plain = {"role": "assistant", "content": "老消息（session 里没有该字段）"}
    check("没有该字段时不凭空造（由出站门统一补）",
          "reasoning_content" not in sanitize(plain), str(sanitize(plain)))

    hist = build([
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "回话", "reasoning_content": "想过了"},
    ], vision_ok=False, max_history=20)
    a = next((x for x in hist if x.get("role") == "assistant"), None)
    check("_build_api_history 保住该字段",
          a and a.get("reasoning_content") == "想过了", str(a))
    check("_build_api_history 对缺失的补空串",
          all("reasoning_content" in x for x in hist if x.get("role") == "assistant"),
          str(hist))
    check("白名单里没有 _seq 之类存储字段",
          all(set(x) <= {"role", "content", "tool_calls", "tool_call_id",
                         "reasoning_content"} for x in hist), str(hist))


def part_d():
    print("\n=== D) 源码契约：链路各处都接上了 ===")
    check("_stream_once 累积 reasoning_content",
          "_reasoning += delta[\"reasoning_content\"]" in _UI)
    check("_stream_once 返回四元组（含 reasoning）",
          "return _content, _acc, _u, _reasoning" in _UI)
    check("调用处解包四元组",
          "full_content, tool_acc, _usage, _reasoning = _attempt_model(" in _UI)
    check("响应字典带 reasoning_content",
          '"reasoning_content": _reasoning,' in _UI)
    check("出站门在 body 构造前补字段",
          "_ensure_reasoning_content(messages, required=True)" in _UI
          and _UI.index("_is_thinking_channel(_base_url, _model)")
          < _UI.index("body = {\n            \"model\""))
    check("agent.py 主循环 asst 带该字段",
          'asst["reasoning_content"] = resp.get("reasoning_content") or ""' in _AGENT)
    check("agent.py 两处 asst 都覆盖",
          _AGENT.count('asst["reasoning_content"] = resp.get("reasoning_content") or ""') == 2,
          str(_AGENT.count('asst["reasoning_content"]')))

    print("\n-- D2 400 必须能自证（读真实报文）--")
    check("存在 _api_error_text", "_api_error_text" in _UI)
    check("_attempt_model 用它取报文", "_estr = _api_error_text(e) or str(e)" in _UI)
    check("拿到报文就写日志", "模型接口报错（HTTP %s）：%s" in _UI)
    check("报文点名 reasoning_content 时补齐重试",
          'if "reasoning_content" in _estr:' in _UI
          and "_ensure_reasoning_content(\n                                body.get(\"messages\"), required=True)" in _UI)
    check("400/401/403/404/422 都进入修复分支",
          "if _code in (400, 401, 403, 404, 422)" in _UI)
    check("最终失败也带上报文（不再只有 Bad Request）",
          "接口报文：%s" in _UI)


def part_e():
    print("\n=== E) _api_error_text 行为 ===")
    import io as _io
    import urllib.error

    class _Resp:
        def __init__(self, data):
            self._d = data

        def read(self):
            return self._d

    e = urllib.error.HTTPError("http://x", 400, "Bad Request", {}, _io.BytesIO(
        b'{"error":{"message":"The `reasoning_content` in the thinking mode must be passed back"}}'))
    txt = api_err(e)
    check("能从 HTTPError 读出报文体", "reasoning_content" in txt, txt[:80])
    check("能抓到真实原因（不再只有 Bad Request）", "Bad Request" not in txt, txt[:80])

    e2 = urllib.error.HTTPError("http://x", 500, "Server Error", {}, None)
    check("无 body 时返回空串不炸", api_err(e2) == "")
    check("非 HTTP 异常返回空串", api_err(ValueError("boom")) == "")

    class _Boom:
        def read(self):
            raise RuntimeError("已读")

    check("read 抛错时返回空串", api_err(_Boom()) == "")


def part_f():
    print("\n=== F) 负面验证：不补齐就必然违反接口要求 ===")
    raw = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "跑一下"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ]
    bad_before = validate_thinking(raw)
    check("原样发送 → 违反思考模式要求（这就是 400 现场）",
          len(bad_before) == 1, str(bad_before))
    ok_after = validate_thinking(ensure_rc(raw, required=True))
    check("过出站门后 → 0 违规", ok_after == [], str(ok_after))
    check("修复确实改变了行为（不是同义重写）", bad_before != ok_after)

    # 多轮历史：每一条 assistant 都要带
    multi = [{"role": "user", "content": "a"},
             {"role": "assistant", "content": "1"},
             {"role": "user", "content": "b"},
             {"role": "assistant", "content": "2"},
             {"role": "user", "content": "c"}]
    check("多轮纯文本历史同样会被违反（不止工具回环）",
          len(validate_thinking(multi)) == 2)
    check("补齐后多轮也干净",
          validate_thinking(ensure_rc(multi, required=True)) == [])


def main():
    part_a()
    part_b()
    part_c()
    part_d()
    part_e()
    part_f()
    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
