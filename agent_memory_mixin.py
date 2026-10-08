# -*- coding: utf-8 -*-
"""agent_memory_mixin.py —— v4.236 自动记忆接线（从 agent.py 抽出）

为什么要有这个文件
------------------
v4.235 收尾时 agent.py 已到 **2393 行**，距 v4.216 拆分时立的红线
（`tests/test_split_216.py` 的 `D2 agent.py < 2400 行`）只剩 **7 行**余量 ——
等于下一次任何改动都会撞线。

处理方式沿用 v4.225 / v4.226 抽 AgentTaskMixin / AgentLoopMixin /
AgentResultMixin 的同一思路：**成块逻辑搬出 agent.py，主类只留继承**。
判据 `D2` 的阈值**不动**（那是红线，不是可以商量的参数）——
抬阈值等于把红线挪到自己脚下，测不出真回弹。

本 Mixin 承接「对话结束后的自动记忆」这一整件事（原来在 agent.py 尾部 164 行）：
  · AUTO_REMEMBER_PROMPT       —— 提取提示词（v4.196 批⑫ 起要求自报来源）
  · _auto_remember(mw)         —— 本轮有工具调用才触发；逐条过准入关后落库，
                                  被拦下的落待确认区，不无声丢弃
  · _parse_remember_facts(raw) —— 解析 LLM 回的条目（兼容对象/字符串/代码块包裹）

⚠️ `AUTO_REMEMBER_PROMPT` 必须从 agent 模块继续可取：
   tests/test_memory_gate_196.py 走的是 `agent.AUTO_REMEMBER_PROMPT`。
   所以 agent.py 里写成
   `from agent_memory_mixin import AgentMemoryMixin, AUTO_REMEMBER_PROMPT`
   —— **搬走本体、保留名字**，不是复制两份（复制两份必然漂移）。
"""
import json
import logging

import memory_store

log = logging.getLogger("dsdesktop")

# 自动记忆提取：对话结束后 LLM 自检是否产生了值得跨对话保留的信息
#
# v4.196 批⑫：新增 **来源申报** —— 这是记忆准入关的地基。
# 旧提示词只问「值不值得记」，不问「凭什么这么认为」，于是推断与用户原话
# 混在同一份介质里，越攒越脏。现在每条必须自报：
#   source     = user（用户亲口说） / tool（工具结果，必须绑 ⟦EV#n⟧）
#                / inference（你推断的） / ephemeral（本次任务临时状态）
#   confidence = 0~1，你自己有多大把握
# 机器据此验证，**不采信任何自称**（tool 来源要回验证据原文，inference 不入库）。
AUTO_REMEMBER_PROMPT = """
你是一个对话归档助手。请从以上对话中提取值得长期记忆的信息，以 JSON 数组格式输出。

每条是一个对象，包含以下字段：
- "topic"：该记忆的主题关键词（如"工作区路径""常用网址""偏好设置"），用于后续去重与覆盖；
- "category"：类别，取值为 "能力进化" / "用户偏好与约定" / "重要决策" 之一；
- "content"：1-2 句话的具体记忆内容，包含必要上下文（路径、数值、原因）；
- "source"：**信息来源，必须如实申报**，取值为：
    "user"      —— 用户在对话里亲口明确说过的事实（如"我姓张""我喜欢X"）；
    "tool"      —— 来自工具返回的结果（必须同时在 "ev" 字段给出证据编号，如 3 或 "EV#3"）；
    "inference" —— 你自己推断/总结的结论，用户没明说、工具也没直接给出；
    "ephemeral" —— 只对本次任务有效的临时状态（如"当前正在处理 Y 文件"），需要 "expires" 字段；
- "ev"：仅 source="tool" 时必填，本轮工具结果的证据编号（写在 ⟦EV#n⟧ 里的那个数字）；
- "confidence"：0~1，你对这条记忆真实性的把握程度（拿不准就写 0.5 以下）；
- "expires"：仅 source="ephemeral" 时给过期时间（如 "7天"、"30天"）。

铁律：
1. **不许把推断伪装成用户陈述**——用户没说过的，source 只能写 inference。
2. source="tool" 时 content 里的事实要素必须真能在该证据原文里找到，
   机器会逐字回验，对不上会被整条驳回。
3. 记不住就别记：没有相关信息输出空数组 []。

输出格式（纯 JSON 数组，不要 markdown 包裹）：
[{"topic":"工作区路径","category":"用户偏好与约定","source":"user","confidence":0.95,"content":"用户工作区路径为 ~/Documents 下对应项目目录"}]
"""


class AgentMemoryMixin:
    """自动记忆接线。

    方法体与迁出前**逐字一致** —— 这是搬运，不是重写；任何顺手改动都会让
    「行为是否回归」这个问题失去对照基线。
    """

    def _auto_remember(self, mw):
        """对话结束后自动提取值得长期记忆的信息，写入 MEMORY.md。
        
        仅在对话中实际执行过工具调用时才触发，避免纯闲聊污染记忆库。
        """
        # v4.107：隔离会话（导演台对话）不写长期记忆——导演闲聊（"这镜太暗了"）
        # 不该被提炼成用户画像事实污染全局记忆。
        if getattr(self, "_isolated", False):
            return
        # 快速判断：**本轮**对话是否有实质性操作（v4.169.0 审查：只看本轮）。
        #
        # 原来写的是 `any(msg.get("role") == "tool" for msg in self.messages)` ——
        # 扫的是**整个历史**（含 baseline 里带进来的旧工具消息）。
        # 于是只要历史上曾经调过工具，本轮**什么都没干**也会触发自动记忆提炼，
        # 属于"没有明确下令却产生持久副作用"：用户只是闲聊两句，
        # 记忆库里却多了一条推断出来的"事实"。
        #
        # "本轮"的界定直接复用既有的 _seq 机制：__init__ 给 baseline 打 _seq=0，
        # 运行内新生成的消息 _seq 单调递增（见 __init__ 注释），> 0 即本轮新增。
        has_tool_msg = any(
            isinstance(msg, dict) and msg.get("role") == "tool"
            and isinstance(msg.get("_seq"), int) and msg["_seq"] > 0
            for msg in self.messages
        )
        if not has_tool_msg:
            return

        self._emit_status("正在提取长期记忆…")

        # 构造提取请求：复用已有对话历史 + 追加归档指令
        extraction_msgs = list(self.messages)
        extraction_msgs.append({"role": "user", "content": AUTO_REMEMBER_PROMPT})

        try:
            resp = mw._agent_call(
                extraction_msgs,
                [],  # 无须工具，纯文本回答
                on_delta=lambda d: None,
            )
            content = (resp.get("content") or "").strip()
        except Exception as e:
            log.warning("自动记忆 LLM 调用失败: %s", e)
            return

        facts = self._parse_remember_facts(content)
        if not facts:
            return

        # v4.196 批⑫：每条先过准入关（memory_gate），只有 admit 才落库，
        # pending 落待确认区，reject 当场驳回。
        results = []
        for item in facts:
            try:
                it = item if isinstance(item, dict) else {"content": str(item or "")}
                fact_txt = (it.get("content") or "").strip()
                if not fact_txt:
                    continue
                # 该主题已有的旧记忆（冲突判定的比对基线；缺失不算问题）
                old = ""
                try:
                    _topic = it.get("topic")
                    if _topic:
                        hits = memory_store.search_memory(str(_topic), limit=3)
                        old = "\n".join(str(h.get("text") or "") for h in (hits or []))
                except Exception:
                    old = ""
                try:
                    import memory_gate
                    verdict = memory_gate.admit(it, old_text=old)
                except Exception as e:
                    log.warning("记忆准入关异常（本条按待确认处理）: %s", e)
                    verdict = {"decision": "pending", "reason": f"准入关异常：{e}",
                               "fact": fact_txt, "topic": it.get("topic"),
                               "source": None, "confidence": None,
                               "expires_at": None, "evidence_id": None}
                verdict.setdefault("category", it.get("category"))
                verdict.setdefault("topic", it.get("topic"))
                results.append(verdict)
            except Exception as e:
                log.warning("自动记忆条目处理失败: %s", e)

        if not results:
            return

        count = 0
        for v in results:
            decision = v.get("decision")
            if decision == "admit":
                try:
                    result = memory_store.append_memory(
                        v.get("fact", ""), type=v.get("category"), topic=v.get("topic"),
                        source=v.get("source"), confidence=v.get("confidence"),
                        evidence_id=v.get("evidence_id"),
                        expires_at=v.get("expires_at"),
                        verified=bool(v.get("verified")))
                    if "已写入" in result or "已更新" in result:
                        count += 1
                except Exception as e:
                    log.warning("自动记忆写入失败: %s", e)
            else:
                # 拦下来的不能无声无息——落到待确认区，注明来源与原因，等人点头
                try:
                    memory_store.append_pending(v)
                except Exception as e:
                    log.warning("写入待确认区失败: %s", e)

        try:
            import memory_gate
            self._emit_status(memory_gate.summarize(results))
        except Exception:
            pass

    @staticmethod
    def _parse_remember_facts(raw):
        """从 LLM 回复中解析记忆条目列表，兼容对象/字符串/各种格式污染。

        v4.73：支持结构化对象 {"topic","category","content"}，topic 用于冲突合并。
        纯字符串条目 topic/category 记为 None（仅去重追加）。

        v4.196 批⑫：透传 source / ev / evidence / confidence / expires
        —— 这些是记忆准入关的判据字段，**丢一个整条就降级为推断**
        （工具来源没了证据编号，机器无从回验，只能按 inference 处理）。
        """
        raw = (raw or "").strip()
        if not raw:
            return []
        # 移除可能的 markdown 代码块包裹
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        # 尝试解析 JSON
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                out = []
                for f in parsed:
                    if isinstance(f, str):
                        out.append({"topic": None, "category": None, "content": f})
                    elif isinstance(f, dict):
                        content = (f.get("content") or f.get("fact") or "").strip()
                        if content:
                            out.append({
                                "topic": (f.get("topic") or f.get("subject") or None),
                                "category": (f.get("category") or f.get("type") or None),
                                "content": content,
                                # v4.196 批⑫：准入判据字段透传
                                "source": (f.get("source") or f.get("origin") or None),
                                "ev": (f.get("ev") or f.get("evidence")
                                       or f.get("evidence_id") or None),
                                "confidence": f.get("confidence"),
                                "expires": (f.get("expires") or f.get("expires_at") or None),
                            })
                return out
            if isinstance(parsed, str):
                return [{"topic": None, "category": None, "content": parsed}]
        except json.JSONDecodeError:
            pass
        # 兜底：按行分割，清理编号前缀
        lines = []
        for line in raw.split("\n"):
            line = line.strip().lstrip("-*•123456789. ").strip()
            if line and not line.startswith("```") and not line.startswith("["):
                lines.append({"topic": None, "category": None, "content": line})
        return lines
