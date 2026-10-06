"""ui_audit_mixin.py —— ChatWindow 审计族 mixin（v4.216.0 从 ui.py 拆出）

反编造审计全部判据（引用审计/附件读取审计/部分读取审计/计数审计/
主张-证据审计/语气证据审计 + _ce_* 证据核验族 + _extract_read_path/
_resolve_read_path 路径回溯）与审计常量（_AUDIT_*/_READ_*/AUDIT_WARN_*）。

ChatWindow(ChatAuditMixin, QMainWindow) 继承接入，方法体逐行搬移零变化；
self.store / self._extract_text 等运行时属性仍由 ChatWindow 提供。
"""
import evidence
import html as html_mod
import json
import os
import re
import tools as tools_mod
import uncertainty
from theme_tokens import THEME  # v4.216.0：THEME 唯一真源
from config import (APP_DIR)

class ChatAuditMixin:
    # ---- v4.189 批③：回复自动回验器（机器执法，防编造引用） ----
    # 引用语境词：与版本断言同现才触发验证（防「Python 3.12」类一般知识误报）
    # v4.193 冲突修复：原词表只认「文件里/记录里」，实测漏报「整份读完 X，
    #   其中**记载了** v4.126」这类最常见的引用表述（0 张卡）。补「引用动词类」
    #   词——语义明确指向「某文件内有此内容」，非泛动词（不加「提到/显示」等
    #   可能被闲聊命中的词，宁窄勿宽，防误报）。
    _AUDIT_CITE_CTX = ("文件里", "文件中", "里面", "文中", "清单里", "记录里",
                       "changelog", "change log", "更新日志", "版本记录",
                       "版本节", "条目",
                       # v4.193 补充：引用动词类（明确「文件内存在」语义）
                       "记载了", "记录了", "写明了", "列出了", "写的是",
                       "里写着", "标注了", "标明了", "列的是")
    # 否定语境词：版本号邻近出现这些词 = 「如实报告某版本不存在」，豁免验证。
    # v4.189 实战补词：模型承认编造时说的是「这些版本号我全都没在这个文件里见到」
    # ——「没在」「没见到」等口语否定式原词表没覆盖，导致如实报告被标红（误报）。
    _AUDIT_NEG_KW = ("没有", "不存在", "不在", "没在", "查无", "找不到", "未找到",
                     "未收录", "无此", "并无", "不是从", "没见到", "没看到",
                     "没读到", "未见", "全都没", "都没", "没这", "超出",
                     "不在这个文件", "不在本文件", "文件里没有")
    # read_file 失败返回的开头标记（这些文件没真读到内容，不能作真值源）
    _READ_FAIL_PREFIX = ("未提供", "已阻止", "拒绝：", "文件不存在", "读取失败")
    def _audit_reply_citations(self):
        """v4.189 批③：回复自动回验器——把「人肉抓编造」自动化。

        背景（2026-09-30 小臭 CHANGELOG 编造事件）：模型对账时引用文件里
        不存在的版本号（v4.126 等），靠用户手里有真值才被拆穿。本方法把
        抓包三招自动化：提取回复中的 vN.N 版本断言 → grep 本轮真实
        read_file 读过的文件 → 所有候选文件均零命中 = 疑似编造 →
        追加 ⚠️ audit_warn 警示（落盘 + UI 直显），不改动原回复。

        防误报设计：
        - 只验本轮（最后一条真实 user 之后）read_file **成功读取**过的文件；
        - 回复须含引用语境词（文件里/清单里/更新日志…）才提取版本断言；
        - 版本号邻近含否定词（「文件里没有v4.126」）→ 豁免：如实报告不存在
          是正确行为，不能标红；
        - 候选文件读失败/被删 → 跳过该文件（fail-open，不当场误报）；
        - 任何异常整体吞掉（校验器绝不阻断 agent 完成流程）。
        """
        try:
            msgs = self.store.active().messages
            # 1) 本轮成功 read_file 过的文件（扫到本会话最后一条真实 user 为止）
            files = []
            for m in reversed(msgs):
                r = m.get("role")
                if r == "tool_log" and m.get("name") == "read_file":
                    if str(m.get("result", "")).startswith(self._READ_FAIL_PREFIX):
                        continue  # 读取失败 ≠ 读过，不能作真值源
                    p = self._resolve_read_path(self._extract_read_path(m.get("args", "")))
                    if p and p not in files:
                        files.append(p)
                elif r == "user" and not m.get("_internal"):
                    break  # 本轮边界：最后一条真实 user
            if not files:
                return
            # 2) 最后一条 assistant 回复（worker 收尾已回写 session）
            last_asst = ""
            for m in reversed(msgs):
                if m.get("role") == "assistant":
                    c = m.get("content", "")
                    if isinstance(c, list):
                        c = self._extract_text(m)
                    last_asst = str(c or "")
                    if last_asst.strip():
                        break
            if not last_asst.strip():
                return
            _low = last_asst.lower()
            if not any(k in _low for k in self._AUDIT_CITE_CTX):
                return  # 无引用语境 → 版本号可能是 一般知识/闲聊，不验
            # 3) 提取版本断言，逐个回验（否定语境豁免）
            vers = set(re.findall(r"v\d+\.\d+(?:\.\d+)?", last_asst))
            if not vers:
                return
            texts = []
            for p in files:
                try:
                    with open(p, "r", encoding="utf-8", errors="ignore") as f:
                        texts.append(f.read())
                except Exception:
                    continue
            if not texts:
                return
            missed = sorted(v for v in vers
                            if not self._negation_near(last_asst, v)
                            and not any(v in t for t in texts))
            if not missed:
                return  # 全部命中或全部豁免：真读真引，无事发生
            # 4) 追加警示（只落 store；UI 由 _on_agent_done 末尾的 _flush_render
            #    统一增量渲染，此处绝不手动 chat_view.append——否则渲染计数不推进，
            #    flush 会再渲染同一条 → 同一张警示卡出现两遍（v4.189 实测））
            flist = "、".join(os.path.basename(p) for p in files[:3])
            warn = (f"⚠️ 自动核验失败：回复中引用的 {'、'.join(missed[:8])} "
                    f"在本轮实际读取的文件（{flist}）中未找到，疑似编造。"
                    "采信前请要求给出【路径+行号+原文摘录】三件套。")
            self.store.active().messages.append({
                "role": "audit_warn", "content": warn,
            })
        except Exception:
            pass  # 校验器绝不阻断 agent 完成流程
    def _audit_warn_html(self, text):
        """v4.189：audit_warn 警示卡 HTML（配色走 THEME['warn'] 派生，
        过 UI 裸 hex 护栏 + 换肤跟随）。三处（实时直显 / 重放 / 导出）共用。"""
        return ('<div style="background:%s;'
                'border-left:3px solid %s;border-radius:8px;'
                'padding:8px 12px;margin:6px 0;font-size:12px;">'
                % (self.AUDIT_WARN_BG, self.AUDIT_WARN_BORDER)
                + html_mod.escape(str(text)) + "</div>")
    # 句分隔符（中英文句号/问号/叹号/分号/换行）
    _AUDIT_SENT_SPLIT_RE = re.compile(r"[。！？!?；;\n]")
    def _negation_near(self, text, ver, window=30):
        """版本号所在**句子**内含否定词 → 「如实报告不存在」，豁免验证。

        v4.189 实战修正：原实现按「版本号前后 window=30 字」取窗口，遇到枚举式
        承认（「v4.126、v4.113、v4.104、v4.128-130 这些版本号，我全都没在这个
        文件里见到」）会漏豁免——句首的版本号离句尾的「没在」远超 30 字，逐个
        判否 → 如实报告被误标红。改为**整句判定**：句子里任一处出现否定词，该句
        所有版本断言一并豁免（语义正确：整句在说「这些都不存在」）。
        """
        for m in re.finditer(re.escape(ver), text):
            # 该版本号所在句子的边界：向前找最近的句分隔符，向后同理
            start = 0
            for sm in self._AUDIT_SENT_SPLIT_RE.finditer(text):
                if sm.start() < m.start():
                    start = sm.end()
                else:
                    break
            end = len(text)
            for sm in self._AUDIT_SENT_SPLIT_RE.finditer(text, m.end()):
                end = sm.start()
                break
            sent = text[start:end]
            if any(n in sent for n in self._AUDIT_NEG_KW):
                return True
            # 兜底：句子过长（如无标点的长段）时仍保留原窗口判定
            if len(sent) > 200:
                s, e = max(0, m.start() - window), min(len(text), m.end() + window)
                if any(n in text[s:e] for n in self._AUDIT_NEG_KW):
                    return True
        return False
    # ---- v4.189 批④：附件行为回验（声称已读但没读 → 标红） ----
    # 附件标记正则（与 agent._ATTACH_MARK_RE_STR 同款；发送侧最终形态，
    # 图片标记已被换成 image_url，不在此列）
    _ATTACH_MARK_RE = re.compile(
        r"\[(?:非图片文件|文件|file):\s*([^\]\n]{1,200}?\.(?:[A-Za-z0-9]{1,8}))\]")
    # 「声称已读」词表（须与提及文件名同现）
    _READ_CLAIM_KW = ("已读", "读完", "读完了", "读过了", "看完了", "看过了",
                      "翻完了", "通读", "我读了", "阅读了")
    # v4.189：audit_warn 警示卡配色——从 THEME['warn'] 派生（不裸写 hex，
    # 过 UI 裸 hex 护栏；换肤自动跟随）
    _AW = THEME["warn"].lstrip("#")
    AUDIT_WARN_BORDER = THEME["warn"]
    AUDIT_WARN_BG = ("rgba(%d,%d,%d,0.12)" % (int(_AW[0:2], 16),
                                              int(_AW[2:4], 16),
                                              int(_AW[4:6], 16)))
    del _AW
    def _audit_attachment_reads(self):
        """v4.189 批④：附件行为回验——你说读了，机器查你读没读。

        背景（2026-09-30 实锤）：用户发 CHANGELOG.md，模型零工具调用直接
        「读完 CHANGELOG，给您交叉核验」开编。行为链对账：
          本轮 user 带附件标记 → 本轮是否真有 read_file/run_python 读它
          → 若「声称已读」却零调用 → audit_warn 标红。

        防误报设计：
        - 真读判定宽进：tool_log(read_file) 或 assistant.tool_calls 里
          read_file/run_python 的完整 arguments 含该文件名（basename 匹配，
          run_python open() 读也算真读）；
        - 只在「声称已读词 ∧ 提及未读文件名」同时命中才标红——
          没声称已读的（如「我无法读取」）不触发，留给批③版本回验兜底；
        - 任何异常整体吞掉，绝不阻断收尾。
        """
        try:
            msgs = self.store.active().messages
            # 1) 找最后一条真实 user，提取附件 basename 集合
            att = set()
            last_user_idx = -1
            for i in range(len(msgs) - 1, -1, -1):
                m = msgs[i]
                if m.get("role") == "user" and not m.get("_internal"):
                    c = m.get("content", "")
                    if not isinstance(c, str):
                        c = str(c)
                    for mm in self._ATTACH_MARK_RE.finditer(c):
                        att.add(os.path.basename(mm.group(1).strip().strip('"')))
                    last_user_idx = i
                    break
            if not att or last_user_idx < 0:
                return
            # 2) 本轮（该 user 之后）真读过的文件：tool_log / tool_calls 双通道
            read_hit = set()
            for m in msgs[last_user_idx + 1:]:
                if m.get("role") == "tool_log" and m.get("name") in ("read_file",
                                                                     "run_python"):
                    hay = str(m.get("args", ""))
                    read_hit |= {a for a in att if a and a in hay}
                elif m.get("role") == "assistant" and m.get("tool_calls"):
                    for tc in m.get("tool_calls") or []:
                        try:
                            fn = (tc.get("function", {}) or {})
                            if fn.get("name") in ("read_file", "run_python"):
                                hay = str(fn.get("arguments", ""))
                                read_hit |= {a for a in att if a and a in hay}
                        except Exception:
                            continue
            unread = att - read_hit
            if not unread:
                return  # 全部真读过
            # 3) 最后 assistant 回复「声称已读 ∧ 提及未读文件」→ 标红
            last_asst = ""
            for m in reversed(msgs):
                if m.get("role") == "assistant":
                    c = m.get("content", "")
                    if isinstance(c, list):
                        c = self._extract_text(m)
                    last_asst = str(c or "")
                    if last_asst.strip():
                        break
            if not last_asst.strip():
                return
            claimed = any(k in last_asst for k in self._READ_CLAIM_KW)
            mentioned = {u for u in unread
                         if u in last_asst
                         or os.path.splitext(u)[0] in last_asst}
            if not (claimed and mentioned):
                return
            warn = (f"⚠️ 附件核验失败：回复声称已读取 "
                    f"{'、'.join(sorted(mentioned)[:3])}，但本轮工具记录中没有"
                    "对应的 read_file/run_python 调用——疑似未读编造，"
                    "请要求它先真读再答。")
            self.store.active().messages.append({
                "role": "audit_warn", "content": warn,
            })
            # 同上：只落 store，UI 交给 _flush_render 统一渲染（防重复卡）
        except Exception:
            pass  # 校验器绝不阻断 agent 完成流程
    # ---- v4.190 批⑤：部分读取回验（读了 41% 却声称「整份读完」→ 标红） ----
    # 判据不看 read_file 的 result——tool_log 的 result 被 _clip 裁到 500 字符，
    # 而分段读取的截断提示在 8000 字段的**末尾**，必然被裁掉（实测永远看不到）。
    # 改为按 args 里的 offset + 实际文件长度自算覆盖率，顺带能在警示里报百分比。
    _READ_OFFSET_RE = re.compile(r'"offset"\s*:\s*(\d+)')
    _READ_LIMIT_RE = re.compile(r'"limit"\s*:\s*(\d+)')
    # 「读全」声称词（区别于「只读了一部分」）
    _READ_FULL_CLAIM_KW = ("整份读完", "整份已读", "全部读完", "全文读完",
                           "读完全文", "读完了整份", "已全部读完", "完整读完",
                           "读完整个文件", "通读完", "全部读完了", "已整份读完",
                           "整份读完了", "通读全文", "读全了")
    # 自我限定词：同句出现 = 明确声明只读了部分，如实报告 → 豁免
    _READ_PARTIAL_HINT_KW = ("只读", "仅读", "没读全", "未读全", "没读完",
                             "未读完", "还剩", "剩余", "部分读", "读到第",
                             "没读到最后", "分 8 段", "分段读", "还没读")
    @staticmethod
    def _merge_ranges(ranges):
        """把 [(a,b), ...] 合并成不重叠、升序的区间列表。"""
        if not ranges:
            return []
        rs = sorted((int(a), int(b)) for a, b in ranges if b > a)
        out = []
        for a, b in rs:
            if out and a <= out[-1][1]:
                if b > out[-1][1]:
                    out[-1] = (out[-1][0], b)
            else:
                out.append((a, b))
        return out
    @staticmethod
    def _find_gaps(ranges, total):
        """v4.192 批⑦：在合并后的区间列表中找 [0, total) 内的空洞。

        返回缺口列表 [(a,b), ...]（a<b），无缺口返回 []。
        """
        merged = ChatAuditMixin._merge_ranges(ranges)
        gaps = []
        cur = 0
        for a, b in merged:
            if a > cur:
                gaps.append((cur, a))
            cur = max(cur, b)
        if cur < total:
            gaps.append((cur, total))
        return gaps
    def _audit_partial_reads(self):
        """v4.190 批⑤ + v4.192 批⑦：你说读全了，机器算你「有没有读全」。

        背景（2026-10-01 实锤）：
        - 批⑤原始版本：模型第一次只读到 20000/49050 字符就声称「整份读完」并
          据此列清单、下结论。批④只查「有没有调 read_file」（它确实调了），
          查不出「读了 41% 却说 100%」。
        - 批⑦升级（新实锤）：模型采用**乱序多窗口**读取（0~20000、11300~16200、
          8900~10400 …），窗口之间留缝且自身未对齐，结果漏掉头部两整节
          （v4.190.0 / v4.189.1），还下了「文件退化、记录丢失」的反向强结论。
          批⑤旧算法 `covered = max(offset+limit)` 只记「最大到达位置」，
          中间有洞也看不见（因为最大位置确实到了 55151），故漏报。

        批⑦算法：本轮每个 read_file 的 args 提取 (offset, offset+limit) 记成
        区间 → 合并 → 对 [0, total) 求缺口。声称读全但存在缺口即标红，
        并在警示里**指出未覆盖区间**（而非只给百分比）。

        触发：最后 assistant 同一句里「提及该文件（或本轮只读了这一个文件）∧
        出现读全声称词 ∧ 无自我限定词」→ audit_warn 标红。

        防误报：文件读不到/超大（>2MB）fail-open；结果被裁导致 offset 缺失时
        按 0 计（只会漏报不会误报）；缺口小于 200 字符视为「边缘未读」不报
        （避免因压缩/对齐误差误伤）。
        """
        try:
            msgs = self.store.active().messages
            last_user_idx = -1
            for i in range(len(msgs) - 1, -1, -1):
                m = msgs[i]
                if m.get("role") == "user" and not m.get("_internal"):
                    last_user_idx = i
                    break
            if last_user_idx < 0:
                return
            # 1) 本轮每个文件的已覆盖区间列表（v4.192 批⑦：不再是单一 max）
            covered = {}  # path -> [(a, b), ...]
            for m in msgs[last_user_idx + 1:]:
                if m.get("role") != "tool_log" or m.get("name") != "read_file":
                    continue
                p = self._extract_read_path(m.get("args", ""))
                if not p:
                    continue
                s = str(m.get("args", ""))
                _o = self._READ_OFFSET_RE.search(s)
                off = int(_o.group(1)) if _o else 0
                _l = self._READ_LIMIT_RE.search(s)
                lim = int(_l.group(1)) if _l else getattr(
                    tools_mod, "TOOL_READ_LIMIT", 8000)
                covered.setdefault(p, []).append((off, off + lim))
            if not covered:
                return
            # 2) 与文件实际长度比 → 存在「覆盖空洞」的集合
            unfin = {}  # path -> (gaps, total)
            for p, rngs in covered.items():
                try:
                    if os.path.getsize(p) > 2 * 1024 * 1024:
                        continue  # 超大文件不读，fail-open
                    with open(p, "r", encoding="utf-8", errors="replace") as f:
                        total = len(f.read())
                except Exception:
                    continue  # 读不到/已删除：无法判定，跳过
                if not total:
                    continue
                gaps = self._find_gaps(rngs, total)
                # 单缺口 < 200 字符视为边缘未读/对齐误差，不报（防误伤）
                real = [(a, b) for a, b in gaps if (b - a) >= 200]
                if real or (gaps and sum(b - a for a, b in gaps) >= 200):
                    unfin[p] = (real if real else gaps, total,
                                sum(b - a for a, b in gaps))
            if not unfin:
                return
            # 3) 最后 assistant 回复：同句「读全声称 ∧ 提及该文件」→ 标红
            last_asst = ""
            for m in reversed(msgs):
                if m.get("role") == "assistant":
                    c = m.get("content", "")
                    if isinstance(c, list):
                        c = self._extract_text(m)
                    last_asst = str(c or "")
                    if last_asst.strip():
                        break
            if not last_asst.strip():
                return
            hits = []
            for p, (gaps, total, miss) in unfin.items():
                base = os.path.basename(p)
                stem = os.path.splitext(base)[0]
                # 本轮只这一个文件时，允许「这份文件」式指代（不要求出现文件名）
                named = (base in last_asst) or (stem in last_asst)
                for sent in self._AUDIT_SENT_SPLIT_RE.split(last_asst):
                    if not named and not (len(unfin) == 1 and sent.strip()):
                        continue
                    if not any(k in sent for k in self._READ_FULL_CLAIM_KW):
                        continue
                    if any(k in sent for k in self._READ_PARTIAL_HINT_KW):
                        continue  # 自己声明只读了一部分 → 如实报告，豁免
                    hits.append((base, gaps, total, miss))
                    break
            if not hits:
                return
            _d = []
            for base, gaps, total, miss in hits[:3]:
                _g = "、".join(f"{a}~{b}" for a, b in gaps[:3])
                if len(gaps) > 3:
                    _g += " 等"
                _d.append(f"{base} 有 {len(gaps)} 处未覆盖区间（{_g}，"
                          f"共缺 {miss} 字符 / 全文 {total}）")
            warn = (f"⚠️ 读取核验失败：回复声称整份读完，但本轮实际"
                    f"：{'；'.join(_d)}——疑似**漏读整段却下结论**（不是只差百分之几，"
                    "是中间有洞）。请要求它用 read_file offset=缺口起点 补读后重新回答。")
            self.store.active().messages.append({
                "role": "audit_warn", "content": warn,
            })
            # 同上：只落 store，UI 交给 _flush_render 统一渲染（防重复卡）
        except Exception:
            pass  # 校验器绝不阻断 agent 完成流程
    @staticmethod
    def _count_units(text, unit, alt_null=False):
        """v4.194 批⑧：对文件正文**确定性计数** unit 类单元。

        只处理可正则判定的单元；无法可靠计数的返回 None（该单元放弃回验）。

        口径（关键，曾因口径不一致误报）：
          - 文件里若有**编号式节**（`## 第N节`/`## 第N章`）→ 真值取**编号的个数**，
            **不含**「附则/附录/说明」这类非编号标题（它们不是"节"）。
            例：137 个 `第N节` + 1 个 `## 附则` → 真值 **137**，不是 138。
          - 若无编号式标题 → 退化为「二级标题数」（`## `~`###### `，不含一级 `# `）。
          - 行 = 行数；页/条/项/段 = 不可确定性计数 → None（放弃回验）。
        alt_null=True 时返回 (主口径, 备选口径)，供"两种答法都算对"的宽容判定。
        """
        if text is None:
            return None
        if unit in ("节", "章", "篇"):
            # 主口径：编号式节数（第N节）
            numbered = len(re.findall(r"^\s{0,3}#{2,6}\s+[^\n]{0,20}?第\s*\d+\s*[节章篇]",
                                      text, re.M))
            if not numbered:
                numbered = len(re.findall(r"第\s*\d+\s*[节章篇]", text))
            # 备选口径：所有二级及以下标题数
            allheads = len(re.findall(r"^\s{0,3}#{2,6}\s+\S", text, re.M))
            main = numbered if numbered else allheads
            if alt_null:
                return (main, allheads)
            return main
        if unit == "行":
            if not text:
                return 0
            body = text[:-1] if text.endswith("\n") else text
            return body.count("\n") + 1
        # 页：不可由文本推断；条/项/段：语义过泛不可确定性计数 → 放弃
        return None
    @classmethod
    def _extract_count_claims(cls, text):
        """v4.194 批⑧：从回复里提取「可数事实」断言。

        返回 [(数字, 单位词, 是否确定表述)]。
        第三个元素 False = 模糊表述（含约/大概/左右…），不参与回验。

        排除项（关键，否则误报泛滥）：
          - 「第N节」「第003节~第081节」这类是**章节编号引用**，不是「共 N」计数
            断言。判据：数字紧前是「第」→ 跳过（`第081节` 的 81 是编号不是总数）。
          - 编号范围式的真实总数在「（共 80 个）」里，由常规模式捕捉。
        """
        out = []
        if not text:
            return out
        for m in cls._COUNT_RE.finditer(text):
            # 紧跟数字前的字符是「第」→ 这是章节编号（第81节），不是计数断言
            pre = text[max(0, m.start() - 1):m.start()]
            if pre == "第":
                continue
            num = int(m.group(1))
            unit = m.group(2)
            # 该数字周围（整句）含模糊词 → 标为不确定
            s = max(0, m.start() - 12)
            e = min(len(text), m.end() + 12)
            ctx = text[s:e]
            certain = not any(k in ctx for k in cls._COUNT_VAGUE_KW)
            out.append((num, unit, certain))
        return out
    @classmethod
    def _count_negation_near(cls, text, num):
        """数字所在**句子**含否定词 → 「如实报告不是这个数」，豁免。"""
        for m in re.finditer(r"(?<!\d)%d(?!\d)" % num, text):
            start = 0
            for sm in ChatAuditMixin._AUDIT_SENT_SPLIT_RE.finditer(text):
                if sm.start() < m.start():
                    start = sm.end()
                else:
                    break
            end = len(text)
            for sm in ChatAuditMixin._AUDIT_SENT_SPLIT_RE.finditer(text, m.end()):
                end = sm.start()
                break
            if any(n in text[start:end] for n in ChatAuditMixin._AUDIT_NEG_KW):
                return True
        return False
    def _audit_count_claims(self):
        """v4.194 批⑧：你说「共 N 节」，机器对文件数一遍。

        触发（全部满足才判）：
          1) 本轮有成功 read_file 的文件，且该文件**已读全**（无覆盖空洞）；
          2) 最后 assistant 回复里出现「数字 + 单位词」的计数断言；
          3) 该断言能绑定到某个已读全的文件（文件名/stem/「这份文件」指代）；
          4) 断言附近无模糊词、无否定词。
        判据：模型给的数字 ≠ 对文件真数的结果 → audit_warn 标红（附真值）。

        防误报：
          - 只处理可确定性计数的单位（节/章/篇/行），条/项/个/段/页 语义过泛
            → 放弃回验（宁漏勿错）；
          - 文件读取有空洞 → 本层让位批⑦（不越权）；
          - 无法读取/超大（>2MB）→ fail-open；
          - 任何异常整体吞掉，绝不阻断 agent 完成流程。
        """
        try:
            msgs = self.store.active().messages
            last_user_idx = -1
            for i in range(len(msgs) - 1, -1, -1):
                m = msgs[i]
                if m.get("role") == "user" and not m.get("_internal"):
                    last_user_idx = i
                    break
            if last_user_idx < 0:
                return
            # 1) 本轮每个文件的覆盖区间（复用批⑦ 的采集口径）
            covered = {}
            for m in msgs[last_user_idx + 1:]:
                if m.get("role") != "tool_log" or m.get("name") != "read_file":
                    continue
                p = self._extract_read_path(m.get("args", ""))
                if not p:
                    continue
                s = str(m.get("args", ""))
                _o = self._READ_OFFSET_RE.search(s)
                off = int(_o.group(1)) if _o else 0
                _l = self._READ_LIMIT_RE.search(s)
                lim = int(_l.group(1)) if _l else getattr(
                    tools_mod, "TOOL_READ_LIMIT", 8000)
                covered.setdefault(p, []).append((off, off + lim))
            if not covered:
                return
            # 2) 只保留**读全**（无空洞）的文件 + 其正文
            full = {}  # path -> text
            for p, rngs in covered.items():
                try:
                    if os.path.getsize(p) > 2 * 1024 * 1024:
                        continue  # 超大文件 fail-open
                    with open(p, "r", encoding="utf-8", errors="replace") as f:
                        total_txt = f.read()
                except Exception:
                    continue  # 读不到/已删除：跳过
                if not total_txt:
                    continue
                gaps = self._find_gaps(rngs, len(total_txt))
                real = [(a, b) for a, b in gaps if (b - a) >= 200]
                if real or (gaps and sum(b - a for a, b in gaps) >= 200):
                    continue  # 没读全 → 让位批⑦（本层不越权）
                full[p] = total_txt
            if not full:
                return
            # 3) 最后 assistant 回复 → 提取计数断言
            last_asst = ""
            for m in reversed(msgs):
                if m.get("role") == "assistant":
                    c = m.get("content", "")
                    if isinstance(c, list):
                        c = self._extract_text(m)
                    last_asst = str(c or "")
                    if last_asst.strip():
                        break
            if not last_asst.strip():
                return
            claims = self._extract_count_claims(last_asst)
            if not claims:
                return
            # 4) 逐个断言回验（只处理可绑定的文件 + 可确定性计数的单位）
            hits = []
            for num, unit, certain in claims:
                if not certain:
                    continue  # 模糊表述不验
                if self._count_negation_near(last_asst, num):
                    continue  # 否定语境（如实报告不是这个数）豁免
                # 绑定文件：回复里出现该文件名/stem；或本轮只有一个读全文件
                #   **且回复在谈论该文件**（有指代词）时放宽。
                #   注意：不能只凭「本轮只有一个文件」就绑定——否则闲聊里的
                #   「这个项目共 80 个节要写」会被误判为该文件的计数（实测误报）。
                _REFER_KW = ("这份文件", "该文件", "这个文件", "此文件",
                             "全文", "整份", "文件里", "文件中", "本文档",
                             "这份文档", "该文档")
                cands = []
                for p, txt in full.items():
                    base = os.path.basename(p)
                    stem = os.path.splitext(base)[0]
                    if base in last_asst or stem in last_asst:
                        cands.append((p, base, txt))
                if not cands and len(full) == 1 and any(
                        k in last_asst for k in _REFER_KW):
                    p, txt = next(iter(full.items()))
                    cands = [(p, os.path.basename(p), txt)]
                if not cands:
                    continue  # 无法绑定 → 不验（防闲聊误报）
                for p, base, txt in cands[:1]:
                    truth = self._count_units(txt, unit)
                    if truth is None:
                        continue  # 该单位不可确定性计数 → 放弃
                    # 宽容判定：文件的"节数"常有两种合理口径（编号节数 vs 全部标题数）。
                    # 例：137 个「第N节」+ 1 个「附则」→ 137 和 138 都该算对
                    # （后者把附则也算作一节，语义上说得通）。只有两种口径都对不上才标红。
                    accepts = {truth}
                    alt = self._count_units(txt, unit, alt_null=True)
                    if isinstance(alt, tuple):
                        accepts |= {x for x in alt if x is not None}
                    if num not in accepts:
                        hits.append((base, unit, num, truth))
                    break
            if not hits:
                return
            _d = "；".join(f"{b} 的「{u}」实际是 {t}（回复写 {n}）"
                           for b, u, n, t in hits[:3])
            warn = (f"⚠️ 计数核验失败：{_d}——**读全了不等于数得对**。"
                    "这类数字没有原文出处可对照，采信前请要求它逐个数一遍"
                    "（或用脚本对文件做确定性计数）。")
            self.store.active().messages.append({
                "role": "audit_warn", "content": warn,
            })
            # 同上：只落 store，UI 交给 _flush_render 统一渲染（防重复卡）
        except Exception:
            pass  # 校验器绝不阻断 agent 完成流程
    @classmethod
    def _ce_extract_tokens(cls, sent):
        """抽取句中**确定性可回验**的事实 token → [(类别, token)]。

        抽不出来就不验 —— 与「抽出来但对不上」是两回事，
        前者是护栏的能力边界（宁漏勿错），后者才是风险。
        """
        out = []
        s = str(sent or "")
        if not s:
            return out
        # 先剥掉 ⟦EV#n⟧ / ⟦EV#n:L12-30⟧ 引用标记：
        #   标记本身不是断言内容。实测不剥会把行号片 **L1-10** 当成「标识符」
        #   类事实 token 抽出（匹配 [A-Z][A-Z0-9]*[-_][A-Z0-9]+），
        #   于是本来完全被支持的断言被降级成 partial —— 现在 partial 也判 Funk，
        #   直接变成误报。「引用标记」与「被断言的事实」必须分开。
        try:
            import evidence as _ev0
            s = _ev0.strip_ev_tags(s)
        except Exception:
            s = re.sub(r"⟦EV#\d+(?::L\d+-\d+)?⟧", "", s)
        for kind, rx in cls._CE_TOKEN_RES:
            for m in rx.finditer(s):
                t = (m.group(1) if m.groups() else m.group(0)).strip()
                if t and (kind, t) not in out:
                    out.append((kind, t))
        return out
    @classmethod
    def _ce_is_claim(cls, sent):
        """这句是不是「需要证据支撑的事实断言」。"""
        s = str(sent or "").strip()
        if not s or len(s) > 400 or "```" in s:
            return False
        if any(k in s for k in cls._CE_HEDGE_KW):
            return False
        if any(s.startswith(k) for k in cls._CE_META_PREFIX):
            return False
        if any(k in s for k in cls._CE_HONEST_KW):
            return False
        return bool(cls._ce_extract_tokens(s))
    @staticmethod
    def _ce_collect_evidence_map(msgs):
        """本轮 tool_log 里登记过的证据 → {eid: {raw, ok, tool}}。"""
        out = {}
        try:
            import evidence as _ev
        except Exception:
            return out
        for m in msgs or []:
            if m.get("role") != "tool_log":
                continue
            eid = m.get("evidence_id")
            if eid is None:
                continue
            try:
                eid = int(eid)
            except Exception:
                continue
            if eid in out:
                continue
            rec = None
            try:
                rec = _ev.get(eid)
            except Exception:
                rec = None
            if not rec:
                continue
            out[eid] = {"raw": rec.get("raw") or "",
                        "ok": bool(rec.get("ok", 1)),
                        "tool": rec.get("tool") or ""}
        return out
    @classmethod
    def _ce_evidence_text(cls, ev, start_line=None, end_line=None):
        """按行区间取证据原文；无区间取全文。"""
        raw = (ev or {}).get("raw") or ""
        if not (start_line or end_line):
            return raw
        ls = raw.split("\n")
        a = max(1, int(start_line or 1))
        b = min(len(ls), int(end_line or len(ls)))
        if b < a:
            return ""
        return "\n".join(ls[a - 1:b])
    @classmethod
    def _ce_verify_sentence(cls, sent, tags, evmap):
        """回验一句断言 → (level, detail)。

        level 由好到坏：direct > partial > no_token > unsupported
                        > failed_evidence > hallucinated
        一句引了多个证据时取**最坏**的那个（伪造编号比「对不上」更严重）。
        """
        toks = cls._ce_extract_tokens(sent)
        if not toks:
            return ("no_token", "无可回验 token")
        _ORDER = {"direct": 0, "partial": 1, "no_token": 2,
                  "unsupported": 3, "failed_evidence": 4, "hallucinated": 5}
        best, best_detail = None, ""
        for eid, ls, le in (tags or []):
            ev = (evmap or {}).get(eid)
            if ev is None:
                lv = "hallucinated"
                detail = "证据 EV#%s 本轮从未登记（编号是编的？）" % eid
            elif not ev.get("ok", True):
                lv = "failed_evidence"
                detail = ("EV#%s 是一次**失败**的调用 —— 工具失败禁止据此补全事实"
                          % eid)
            else:
                txt = cls._ce_evidence_text(ev, ls, le)
                miss = [t for k, t in toks if t not in txt]
                if not miss:
                    lv = "direct"
                    detail = ""
                elif len(miss) < len(toks):
                    # partial 也算不通过：每个 token 都是「确定性可回验的事实单元」，
                    # 一句里只要有一个查不到，就意味着**有东西没核实到**。
                    # （原本 partial 放行 → 实测漏报「一句两个事实、一个对一个编的」）
                    lv = "partial"
                    detail = ("[EV#%d] 部分要素在原文里查无：" % eid
                              + "、".join(miss[:3]))
                else:
                    lv = "unsupported"
                    detail = ("[EV#%d] 原文里查无：" % eid
                              + "、".join(miss[:3]))
            if best is None or _ORDER[lv] > _ORDER[best]:
                best, best_detail = lv, detail
        return (best or "unsupported", best_detail)
    def _audit_claim_evidence(self):
        """v4.195 批⑩：断言-证据绑定回验。

        把判据从「附近有没有 URL」换成「这条被引用的证据原文支不支持这句断言」。

        触发（全满足才判）：
          1) 本轮 tool_log 至少登记了 1 条 Evidence（否则让位批③）；
          2) 最后 assistant 回复里**用过** ⟦EV#n⟧ 协议（渐进生效，
             模型还不会用时不做裸断言扫描，避免一夜之间全站标红）；
          3) 句子被判为事实断言（有确定性 token、非模糊/元话语/诚实否定）。

        判红：
          · hallucinated    —— 引用了本轮不存在的证据编号
          · failed_evidence —— 拿失败调用当支撑
          · unsupported     —— 断言的事实要素在被引证据原文里一个都对不上
          · naked           —— 同一回复里别人都标了，这句没标（标注不一致）

        任何异常整体吞掉，绝不阻断 agent 完成流程。
        """
        try:
            try:
                import evidence as _ev_mod
            except Exception:
                return
            msgs = self.store.active().messages
            last_user_idx = -1
            for i in range(len(msgs) - 1, -1, -1):
                m = msgs[i]
                if m.get("role") == "user" and not m.get("_internal"):
                    last_user_idx = i
                    break
            if last_user_idx < 0:
                return
            evmap = self._ce_collect_evidence_map(msgs[last_user_idx + 1:])
            if not evmap:
                return  # 让位批③（本轮没登记证据 = 批⑨ 未启用）
            last_asst = ""
            for m in reversed(msgs):
                if m.get("role") == "assistant":
                    c = m.get("content", "")
                    if isinstance(c, list):
                        c = self._extract_text(m)
                    last_asst = str(c or "")
                    if last_asst.strip():
                        break
            if not last_asst.strip():
                return
            protocol_used = bool(_ev_mod.parse_ev_tags(last_asst))
            if not protocol_used:
                return  # 渐进：模型还没用新协议 → 不扫裸断言
            # 逐句回验
            segs, prev = [], 0
            for m in self._AUDIT_SENT_SPLIT_RE.finditer(last_asst):
                s = last_asst[prev:m.end()]
                if s.strip():
                    segs.append(s)
                prev = m.end()
            tail_s = last_asst[prev:]
            if tail_s.strip():
                segs.append(tail_s)

            n_claim, bad = 0, []
            for seg in segs:
                if not self._ce_is_claim(seg):
                    continue
                n_claim += 1
                tags = _ev_mod.parse_ev_tags(seg)
                if tags:
                    lv, detail = self._ce_verify_sentence(seg, tags, evmap)
                    if lv in ("hallucinated", "failed_evidence", "unsupported",
                              "partial"):
                        clean = _ev_mod.strip_ev_tags(seg).strip()
                        bad.append((lv, clean, detail))
                else:
                    clean = _ev_mod.strip_ev_tags(seg).strip()
                    bad.append(("naked", clean, "未绑定任何 ⟦EV#n⟧"))
            if not bad:
                return
            _KIND_TXT = {
                "hallucinated": "引用了不存在的证据编号",
                "failed_evidence": "拿失败调用当支撑",
                "unsupported": "被引证据原文里查无实据",
                "partial": "只有部分要素能在被引证据里查到",
                "naked": "事实断言未绑定证据",
            }
            lines = []
            for lv, clean, detail in bad[:3]:
                _snip = clean[:56].replace("\n", " ")
                lines.append("· 「%s」→ %s%s" % (
                    _snip, _KIND_TXT.get(lv, lv),
                    ("（%s）" % detail) if detail else ""))
            warn = ("⚠️ 证据绑定核验（批⑩）：本回复 %d 条事实断言里有 %d 条"
                    "**无法在其引用的证据里核实**，采信前请让它给出原文位置：\n"
                    % (n_claim, len(bad)) + "\n".join(lines)
                    + ("\n（判据不是「有没有 URL」，而是「被引证据原文里有没有这个事实」）"
                       if any(b[0] == "unsupported" for b in bad) else ""))
            self.store.active().messages.append({
                "role": "audit_warn", "content": warn,
            })
            # 只落 store，UI 交给 _flush_render 统一渲染（防重复卡）
        except Exception:
            pass  # 校验器绝不阻断 agent 完成流程
    # ---- v4.196 批⑬：有关证语气的第 11 层 ----
    def _audit_tone_evidence(self):
        """v4.196 批⑬：语气—证据等级匹配检查（第 11 层）。

        前十层全是「有没有编」，这一层管另一半：**没编，但说得比证据允许的程度更满**。
        这类句子查不出假话（每个字都可能在某处出现过），但读者的**确定性感知**
        超过了实际证据水平 —— 效果等同于错。

        判定链（uncertainty.decision table）：
          零证据 / 用了失败调用 / 需实时数据却没调工具 → refuse（应该说不知道）
          单一来源                                   → hedged（只能「初步判断」）
          多来源互相打架                             → conflict（必须两边都摆）
          ≥2 条独立成功证据                          → answer（可以下结论）
        然后看回复里有没有**强肯定句**（「确定是/答案就是/百分之百…」）
        越过它应属的等级 → 提醒改写。

        防误报三条（沿用批⑩ 的教训，判据收紧会放大此前的抽取缺陷）：
          ① 本轮没登记证据 → 直接退出（让位批③，新层不抢旧层的活）
          ② 句子已带保留词（可能/待核实/似乎…）或已在承认不知道 → 放行
          ③ 全回复找不到强肯定句 → 不提示（不必没事找事）

        任何异常整体吞掉，绝不阻断 agent 完成流程。
        """
        try:
            try:
                import evidence as _ev_mod
                import uncertainty as _unc
            except Exception:
                return
            msgs = self.store.active().messages
            last_user_idx, query = -1, ""
            for i in range(len(msgs) - 1, -1, -1):
                m = msgs[i]
                if m.get("role") == "user" and not m.get("_internal"):
                    last_user_idx = i
                    c = m.get("content", "")
                    if isinstance(c, list):
                        c = self._extract_text(m)
                    query = str(c or "")
                    break
            if last_user_idx < 0:
                return
            tail = msgs[last_user_idx + 1:]
            evmap = self._ce_collect_evidence_map(tail)
            if not evmap:
                return  # ① 本轮无登记证据 → 让位批③
            tools_used = [m.get("name") for m in tail if m.get("role") == "tool_log"]
            last_asst = ""
            for m in reversed(msgs):
                if m.get("role") == "assistant":
                    c = m.get("content", "")
                    if isinstance(c, list):
                        c = self._extract_text(m)
                    last_asst = str(c or "")
                    if last_asst.strip():
                        break
            if not last_asst.strip():
                return
            rows = [{"id": k, "ok": v.get("ok"), "raw": (v.get("raw") or "")[:4000],
                     "tool": v.get("tool")} for k, v in evmap.items()]
            cited = [int(e) for (e, _s, _t) in _ev_mod.parse_ev_tags(last_asst)]
            vd = _unc.assess(query, rows, tools_used=tools_used, cited_ids=cited)
            if vd["level"] == _unc.LEVEL_ANSWER:
                return
            body = _ev_mod.strip_ev_tags(last_asst)
            segs, prev = [], 0
            for m in self._AUDIT_SENT_SPLIT_RE.finditer(body):
                s = body[prev:m.end()]
                if s.strip():
                    segs.append(s)
                prev = m.end()
            if body[prev:].strip():
                segs.append(body[prev:])
            bad = []
            for seg in segs:
                okflag, why = _unc.tone_allowed(seg, vd["level"])
                if not okflag:
                    bad.append((seg.strip()[:56].replace("\n", " "), why))
            if not bad:
                return  # ③ 没有越界的强肯定句 → 不打扰
            lines = ["· 「%s」→ %s" % (s, w) for s, w in bad[:2]]
            warn = ("⚠️ 语气核验（批⑬）：本轮证据只到「%s」级（%s），"
                    "但回复里有句子说得太满：\n%s\n应改为：%s"
                    % (vd["level"], vd["reason"], "\n".join(lines),
                       _unc.system_hint(vd["level"])))
            self.store.active().messages.append({
                "role": "audit_warn", "content": warn,
            })
        except Exception:
            pass  # 校验器绝不阻断 agent 完成流程
    def _extract_read_path(self, args):
        """从 tool_log 的 read_file args（JSON 字符串，可能被 _clip 截断）提取路径。"""
        s = str(args or "").strip()
        if not s:
            return ""
        try:
            d = json.loads(s)
            if isinstance(d, dict):
                for k in ("path", "file", "file_path", "filename", "name"):
                    v = d.get(k)
                    if isinstance(v, str) and v.strip():
                        return v.strip()
        except Exception:
            pass
        # 兜底：args 是裸路径，或 JSON 截断后开头残留的 "path" 值
        m = re.search(r'[\'"]?(path|file|file_path|filename)[\'"]?\s*[:=]\s*[\'"]([^\'"]+)',
                      s)
        if m:
            return m.group(2).strip()
        m = re.match(r'^[\'"]?([A-Za-z]:[/\\][^\'"]+|[/~/][^\'"]+'
                     r'|\w[^\'":,]{2,}\.[A-Za-z0-9]{1,5})', s)
        return m.group(1).strip() if m else ""
    def _resolve_read_path(self, path):
        """按 tool_read_file 同款规则解析路径（v4.189 批③回验用）：
        WORKSPACE_DIR 优先 → app_dir 回退 → 纯文件名去 incoming/ 找。
        解析不到存在的文件返回 ""（跳过，不误报）。"""
        if not path:
            return ""
        try:
            p = os.path.abspath(os.path.join(tools_mod.WORKSPACE_DIR, path))
            if not os.path.isfile(p):
                _alt = os.path.abspath(os.path.join(APP_DIR, path))
                if os.path.isfile(_alt):
                    p = _alt
            if not os.path.isfile(p) and "/" not in path and "\\" not in path:
                for _r in tools_mod._tool_roots(APP_DIR):
                    cand = os.path.abspath(
                        os.path.join(_r, "incoming", os.path.basename(path)))
                    if os.path.isfile(cand):
                        p = cand
                        break
            return p if os.path.isfile(p) else ""
        except Exception:
            return ""
