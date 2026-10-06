# -*- coding: utf-8 -*-
"""单条消息预算硬上限（审查报告 P2，v4.224）验收判据。

v4.218 报告点名缺口：历史预算只有「整条丢弃」一道闸（`_fit_history_to_budget`），
而它为了不丢掉本轮提问，**刻意保住最后 `min_keep` 条** —— 于是只要那条巨消息
是最后一条，就**绕过全部预算**（实测见过单条 264KB）。报告要求的
「文本 / 工具结果 / 图片数 / 单图体积 / args 长度上限 + 截断标记」全部没有。

本轮补「单条内部」上限层：
  MB1 正文超长 → 截到上限，且**留可见截断标记**（模型得知道内容被砍过）。
  MB2 工具结果超长 → 按 tool_result_max_chars 截。
  MB3 assistant.tool_calls 的 arguments 超长 → 按 args_max_chars 截。
  MB4 单条图片数超上限 → 多余图被丢，并插入「已省略 N 张图」标记。
  MB5 单张图体积超上限 → 整张丢（截断的图是废字节，不如丢）。
  MB6 **最后一条同样受上限** —— 这是本轮要补的核心洞（旧行为对它完全免疫）。
  MB7 可逆：未超限/空 caps → 原样返回；且纯函数不改传入对象。
  MB8 只削内容不删消息 → assistant.tool_calls ↔ tool 配对不被破坏。

扰动脚本（豁免最后一条 / 去掉截断标记 / 放开图片数上限）跑完对应判据必须翻红（哑弹 0）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ui_msg  # noqa: E402

_p = 0
_f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [PASS] %s" % name)
    else:
        _f += 1
        print("  [FAIL] %s  %s" % (name, detail))


CAPS = dict(ui_msg.MSG_BUDGET_DEFAULTS)


def _img(n):
    return {"type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64," + ("A" * n)}}


def main():
    # ---------- MB1 正文上限 + 截断标记 ----------
    print("\n-- MB1 正文超长 → 截断 + 可见标记 --")
    cap = CAPS["text_max_chars"]
    m = {"role": "user", "content": "x" * (cap + 500)}
    nm, ch = ui_msg._cap_message_to_budget(m, CAPS)
    check("MB1-1 正文被截到上限长度（含标记）",
          isinstance(nm.get("content"), str) and len(nm["content"]) <= cap + 40,
          "len=%s" % len(nm.get("content") or ""))
    check("MB1-2 截断后带可见标记（模型知道被砍过）",
          "已截断" in (nm.get("content") or ""),
          "tail=%s" % (nm.get("content") or "")[-30:])
    check("MB1-3 changed=True（确曾裁剪）", ch is True, "changed=%s" % ch)
    check("MB1-4 标记里写明被截字符数",
          "500 字符" in (nm.get("content") or ""),
          "tail=%s" % (nm.get("content") or "")[-30:])

    # ---------- MB2 工具结果上限 ----------
    print("\n-- MB2 工具结果超长 → 按 tool_result_max_chars 截 --")
    tcap = CAPS["tool_result_max_chars"]
    m = {"role": "tool", "tool_call_id": "c1", "content": "y" * (tcap + 900)}
    nm, ch = ui_msg._cap_message_to_budget(m, CAPS)
    check("MB2-1 工具结果被截", ch is True and len(nm["content"]) <= tcap + 40,
          "len=%s changed=%s" % (len(nm.get("content") or ""), ch))
    check("MB2-2 工具结果截断也带标记", "已截断" in nm["content"], "")
    check("MB2-3 tool_call_id 保留（配对字段不动）",
          nm.get("tool_call_id") == "c1", "id=%s" % nm.get("tool_call_id"))

    # ---------- MB3 args 上限 ----------
    print("\n-- MB3 tool_calls 的 arguments 超长 → 按 args_max_chars 截 --")
    acap = CAPS["args_max_chars"]
    m = {"role": "assistant", "content": "",
         "tool_calls": [{"id": "t1", "type": "function",
                         "function": {"name": "write_file",
                                      "arguments": '{"content":"' + "z" * (acap + 700) + '"}'}}]}
    nm, ch = ui_msg._cap_message_to_budget(m, CAPS)
    got = nm["tool_calls"][0]["function"]["arguments"]
    check("MB3-1 arguments 被截", ch is True and len(got) <= acap + 40,
          "len=%s changed=%s" % (len(got), ch))
    check("MB3-2 arguments 截断带标记", "已截断" in got, "tail=%s" % got[-30:])
    check("MB3-3 tool_call id/name 保留",
          nm["tool_calls"][0].get("id") == "t1"
          and nm["tool_calls"][0]["function"].get("name") == "write_file", "")

    # ---------- MB4 图片张数上限 ----------
    print("\n-- MB4 单条图片数超上限 → 丢多余的图 + 省略标记 --")
    max_imgs = CAPS["max_images_per_msg"]
    parts = [_img(100) for _ in range(max_imgs + 3)]
    parts.insert(0, {"type": "text", "text": "看图"})
    m = {"role": "user", "content": parts}
    nm, ch = ui_msg._cap_message_to_budget(m, CAPS)
    imgs = [p for p in nm["content"]
            if isinstance(p, dict) and p.get("type") == "image_url"]
    check("MB4-1 图片数被压到上限", len(imgs) == max_imgs,
          "kept=%s max=%s" % (len(imgs), max_imgs))
    check("MB4-2 丢图后插入「已省略 N 张图」标记",
          any(isinstance(p, dict) and p.get("type") == "text"
              and "已省略" in (p.get("text") or "")
              for p in nm["content"]),
          "parts=%s" % [ (p.get("type") if isinstance(p, dict) else "str") for p in nm["content"]])
    check("MB4-3 正文 part 保留", any(isinstance(p, dict) and p.get("text") == "看图"
                                      for p in nm["content"]), "")

    # ---------- MB5 单图体积上限 ----------
    print("\n-- MB5 单张图体积超上限 → 整张丢（不截断）--")
    big = CAPS["max_image_chars"]
    m = {"role": "user", "content": [_img(big + 1000), _img(100)]}
    nm, ch = ui_msg._cap_message_to_budget(m, CAPS)
    imgs = [p for p in nm["content"]
            if isinstance(p, dict) and p.get("type") == "image_url"]
    check("MB5-1 超大图被整张丢弃", len(imgs) == 1, "kept=%s" % len(imgs))
    check("MB5-2 留下的是那张合规的小图",
          len(((imgs[0].get("image_url") or {}).get("url") or "")) < big,
          "len=%s" % len(((imgs[0].get("image_url") or {}).get("url") or "")))
    check("MB5-3 丢图标注数量", any(isinstance(p, dict) and p.get("type") == "text"
                                    and "已省略 1 张图" in (p.get("text") or "")
                                    for p in nm["content"]), "")

    # ---------- MB6 最后一条同样受上限（本轮核心洞） ----------
    print("\n-- MB6 最后一条不再免疫：照削内容，但整条保留 --")
    huge = "Q" * (CAPS["text_max_chars"] + 3000)
    msgs = [
        {"role": "user", "content": "早先的问题"},
        {"role": "assistant", "content": "早先的回答"},
        {"role": "user", "content": huge},
    ]
    out = ui_msg._build_api_history(msgs, msg_budget=CAPS)
    check("MB6-1 条数不变（只削内容不删消息）", len(out) == 3, "len=%s" % len(out))
    check("MB6-2 最后一条内容被削到上限内",
          len(out[-1].get("content") or "") <= CAPS["text_max_chars"] + 40,
          "len=%s" % len(out[-1].get("content") or ""))
    check("MB6-3 最后一条带截断标记",
          "已截断" in (out[-1].get("content") or ""), "")
    check("MB6-4 前面的短消息未被误伤",
          out[0].get("content") == "早先的问题"
          and out[1].get("content") == "早先的回答",
          "%s / %s" % (out[0].get("content"), out[1].get("content")))
    # 对照：不传 msg_budget（旧行为）时最后一条确实完全不受限
    out_old = ui_msg._build_api_history(msgs)
    check("MB6-5 对照：不传 msg_budget 时最后一条原样（旧行为可复现）",
          out_old[-1].get("content") == huge,
          "len=%s" % len(out_old[-1].get("content") or ""))

    # ---------- MB7 可逆 + 纯函数 ----------
    print("\n-- MB7 可逆（未超限/空 caps 原样）与纯函数性 --")
    m = {"role": "user", "content": "短消息"}
    nm, ch = ui_msg._cap_message_to_budget(m, CAPS)
    check("MB7-1 未超限 → changed=False", ch is False, "changed=%s" % ch)
    check("MB7-2 未超限 → 内容原样", nm["content"] == "短消息", "")
    m2 = {"role": "user", "content": "x" * 5000}
    nm2, ch2 = ui_msg._cap_message_to_budget(m2, {})
    check("MB7-3 空 caps → 一律不截（可逆，等于关闭）",
          ch2 is False and nm2["content"] == m2["content"], "changed=%s" % ch2)
    m3 = {"role": "user", "content": "x" * (CAPS["text_max_chars"] + 100)}
    _before = m3["content"]
    ui_msg._cap_message_to_budget(m3, CAPS)
    check("MB7-4 纯函数：不改传入对象", m3["content"] == _before, "")

    # ---------- MB8 不破坏配对 ----------
    print("\n-- MB8 只削内容不删消息 → tool_calls ↔ tool 配对完整 --")
    msgs = [
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "call_1", "type": "function",
                         "function": {"name": "read_file",
                                      "arguments": '{"path":"a"}'}}]},
        {"role": "tool", "tool_call_id": "call_1",
         "content": "R" * (CAPS["tool_result_max_chars"] + 400)},
        {"role": "user", "content": "w" * (CAPS["text_max_chars"] + 400)},
    ]
    out = ui_msg._build_api_history(msgs, msg_budget=CAPS)
    check("MB8-1 三条消息都在（没被删）", len(out) == 3, "len=%s" % len(out))
    check("MB8-2 assistant.tool_calls 保留",
          any(m.get("tool_calls") for m in out), "")
    check("MB8-3 tool 消息保留且 tool_call_id 对得上",
          any(m.get("role") == "tool" and m.get("tool_call_id") == "call_1"
              for m in out), "")
    check("MB8-4 超长工具结果确实被削",
          all(len(m.get("content") or "") <= CAPS["tool_result_max_chars"] + 40
              for m in out if m.get("role") == "tool"), "")

    print("\n" + "=" * 60)
    print("PASS=%d FAIL=%d" % (_p, _f))
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
