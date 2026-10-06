"""ui_workers.py —— 后台 QThread worker 层（v4.216.0 从 ui.py 拆出）

内容：_GenThread（通用子线程）、PerfWorker（性能探测）、GuiAsyncJob、
OrchestrateWorker（小说一条龙编排）、_ASRWorker/_TTSWorker（语音链路）。

零行为变化：代码逐行搬移；ui.py 顶部 re-export 全部名字保兼容。
"""
from PySide6.QtCore import (QMutex, QThread, QWaitCondition, Signal)
import os
import perf_baseline
import re
import task_resume
import time
import trace_log
import voice as voice_mod  # v4.216.0：worker 内引用 voice_mod
from config import (WORKSPACE_DIR)
from datetime import (datetime)

class _GenThread(QThread):
    """后台跑阻塞型生成（生图/生视频），结果经信号回主线程。"""
    result = Signal(object)   # (rel, kind, name) 或 错误字符串

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            self.result.emit(self.fn(*self.args, **self.kwargs))
        except Exception as e:
            self.result.emit(f"异常：{e}")
class PerfWorker(QThread):
    """v4.78：性能基线后台跑批（不阻塞 UI），结果经 done 信号回主线程。"""
    done = Signal(object)  # 结果 dict 或 {"error": str}

    def __init__(self, save_baseline=False):
        super().__init__()
        self.save_baseline = save_baseline

    def run(self):
        try:
            import perf_baseline as pb
            metrics = pb.run_benchmarks()
            if self.save_baseline:
                pb.save_baseline(metrics)
            cmp, verdict = pb.compare_with_baseline(metrics)
            run_path = pb.save_run(metrics, cmp, verdict)
            self.done.emit({
                "metrics": metrics, "comparison": cmp, "verdict": verdict,
                "run_path": run_path, "saved_baseline": self.save_baseline,
            })
        except Exception as e:
            self.done.emit({"error": str(e)})
class GuiAsyncJob(QThread):
    """审计修复 D1：把阻塞型 subprocess/网络调用丢到后台线程跑，
    结果（或异常信息）经 result 信号回投主线程处理，GUI 不再假死。
    约定 res = {"ok": True, "data": fn返回值} 或 {"ok": False, "error": str}。"""
    result = Signal(object)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            self.result.emit({"ok": True, "data": self._fn()})
        except Exception as e:
            self.result.emit({"ok": False, "error": str(e)})
class OrchestrateWorker(QThread):
    """小说一条龙（3Phase+2检查）。短篇按目标字数一次性写满；长篇按章，用「续写」出下一章。
    交互：爆款雷达后弹出选项让你选定（模型不自决）；运行中可随时暂停并插入修改意见。"""
    stage = Signal(int, str)
    node_status = Signal(int, str)   # running / done / error
    log = Signal(str)
    done = Signal(str)
    need_choice = Signal(list, str)  # 爆款雷达后：[(label,text)...], raw
    paused = Signal(str)             # 暂停等待插入意见时：当前阶段标签

    STAGES = [
        ("爆款雷达", "blue",   "Phase1 选题：雷达扫描给出 3 个结构化爆款切入点，交给你选定"),
        ("选题验证", "orange", "检查1：用『选题三问』深化你选定的方向，给出核心设定"),
        ("写手成稿", "green",  "Phase2 写作：按选定方向产出成稿（短篇循环凑足目标字数 / 长篇写一章）"),
        ("虚拟编辑 审稿", "purple", "Phase3 审稿：按责编 rubric 挑必须改的问题"),
        ("终稿定稿", "red",    "检查2+定稿：按问题修改并终稿复审，输出可投递版本（含标题）"),
    ]

    def __init__(self, mw, topic, platform, length_type="短篇", target_words=2000,
                 prev_state=None, task_id=None, start_stage=0):
        super().__init__()
        self.mw = mw
        self.topic = topic
        self.platform = platform
        self.length_type = length_type if length_type in ("短篇", "长篇") else "短篇"
        try:
            self.target_words = int(target_words) if target_words else 2000
        except Exception:
            self.target_words = 2000
        self.prev_state = prev_state
        self.task_id = task_id or task_resume.new_task_id()
        self.start_stage = max(0, int(start_stage))
        self._stop_requested = False
        self._cancelled = False  # v4.101：用户取消编排（保留检查点可续跑）
        # 续写时：长篇章节号 +1，短篇仍视为第 1 段（扩写）；断点恢复(start_stage>0)则沿用原章节号
        if prev_state and self.length_type == "长篇":
            self.chapter = prev_state.get("chapter", 0) + (1 if self.start_stage == 0 else 0)
        else:
            self.chapter = 1
        self.full_draft = ""
        # 断点恢复：还原上一阶段已写好的正文，避免终稿拼装时丢失
        self.stage2_draft = (prev_state.get("stage2_draft", "") if prev_state else "")
        self.final_state = None
        self.messages = None  # run() 中赋值，检查点使用
        # 交互状态（选题闸门 + 中途暂停）
        self._pause_requested = False
        self._pending_feedback = None
        self._chosen_direction = None
        self._choice_options = []
        self._pause_mutex = QMutex()
        self._pause_cond = QWaitCondition()
        self._choice_mutex = QMutex()
        self._choice_cond = QWaitCondition()
        # D 项（轨迹记忆）：阶段计时 + 重试计数，供成功时采集轨迹
        self._stage_start = None
        self._stage_durations = {}
        self._retry_count = 0

    def request_stop(self):
        """主线程调用，请求取消（下一节点前生效）。同时释放挂起的等待，避免卡死。"""
        self._stop_requested = True
        self._choice_cond.wakeOne()
        self._pause_cond.wakeOne()

    def release_locks(self):
        """取消时由 UI 调用：唤醒所有挂起等待，让 run() 回到 stop 检查。"""
        self._stop_requested = True
        self._choice_cond.wakeOne()
        self._pause_cond.wakeOne()

    def request_pause(self):
        """主线程调用：请求在下一个阶段边界（或写手下一段）暂停，等待插入意见。"""
        self._pause_requested = True

    def resume_with_feedback(self, feedback):
        """主线程调用：带着作者意见恢复运行（feedback 为空则仅恢复）。"""
        self._pending_feedback = (feedback or "").strip() or None
        self._pause_cond.wakeOne()

    def choose(self, idx):
        """主线程调用：用户从爆款雷达选项中选定第 idx 个。"""
        try:
            idx = int(idx)
        except Exception:
            idx = -1
        if 0 <= idx < len(self._choice_options):
            self._chosen_direction = self._choice_options[idx]["text"]
        self._choice_cond.wakeOne()

    def choose_custom(self, text):
        """主线程调用：用户自填切入点方向。"""
        t = (text or "").strip()
        if t:
            self._chosen_direction = t
        self._choice_cond.wakeOne()

    @staticmethod
    def _build_novelist_system_prompt():
        """专用小说写作系统提示：网文主编/写作教练人设，注入作者方法论，禁止把真实家人写进角色。"""
        return (
            "你是一位资深网络小说主编兼写作教练，负责『小说一条龙』流水线"
            "（爆款雷达→选题验证→写手成稿→虚拟编辑审稿→终稿定稿）。\n"
            "作者的方法论（必须贯穿全流水线，不要违背）：\n"
            "1. 选题三问：①我想写什么（核心设定）②读者为什么看（爽点/情绪钩子）"
            "③凭什么我能写好（差异化卖点）。每个选题都要过这三问。\n"
            "2. 叙事：第一人称「我」；每约 500 字必须有一个钩子"
            "（悬念/反转/信息差/情绪爆发）；番茄小说节奏快、章末必须留悬念。\n"
            "3. 交付：番茄一稿一投（一次性成稿、不反复折腾）；场景化叙事、show-don't-tell。\n"
            "4. 纯虚构：严禁把作者的真实家庭成员或本人写进角色"
            "（例如作者的真实称呼、家人昵称等——这些只是作者的私人信息，与小说无关；"
            "除非作者明确要求，否则不得作为角色名、原型或背景人物出现）。角色、地名、机构一律原创。\n"
            "5. 各司其职：严格按当前流水线环节的要求输出，不越界、不提前做后续环节的事。\n"
            "6. 短篇必须写完整：开头→发展→高潮→结局四段齐备，收尾干净、人物命运有交代，"
            "严禁停在半路或留『待续』悬念。字数是软目标，完结优先于凑字数。\n"
        )

    def _platform_hint(self):
        """平台差异化提示。"""
        hints = {
            "番茄小说": "适配番茄小说：快节奏网文，开头强钩子，每章留悬念，第一人称沉浸。",
            "知乎": "适配知乎：盐选故事风格，文笔细腻，逻辑严密，适度知识性。",
            "公众号": "适配公众号：短段落，金句加粗，情感共鸣，不出现具体城市/行业。",
            "抖音": "适配抖音：极短句，强情绪，画面感强，适合口播节奏。",
            "头条": "适配头条：信息密度高，标题党，争议性切入点。",
        }
        return hints.get(self.platform, "")

    def _prompt(self, i, name, brief):
        base = (f"你正在参与『小说一条龙』流水线，当前环节：{name}（{brief}）。\n"
                f"主题：{self.topic}\n平台：{self.platform}\n{self._platform_hint()}\n")
        if i == 0:
            return base + (
                "你是题材趋势分析师。围绕主题做爆款切入点雷达扫描，输出 3 个结构化切入点，"
                "每个用『选项N：』开头（N=1/2/3），格式如下：\n"
                "选项1：[一句话切入点]\n"
                "·为什么爆：（目标人群 + 情绪钩子 + 市场空白）\n"
                "·风险：（同质化 / 违规 / 难以持续）\n"
                "·与番茄同类爆款的差异点\n"
                "只输出这 3 个选项，不要替作者做决定，也不要写正文。")
        if i == 1:
            cd = self._chosen_direction or "（作者尚未明确，请基于主题给出最稳妥的推荐方向）"
            return base + (
                f"作者已选定切入点：\n{cd}\n"
                "你是选题验证专家，不要另选方向。请用『选题三问』检验该方向的可行性："
                "①我想写什么（核心设定）②读者为什么看（爽点/情绪钩子）③凭什么我能写好（差异化）。"
                "指出 1-2 个风险与强化点，并给出落地核心设定：主角人设、核心冲突、开局强钩子。"
                "输出可直接交给写手的方向书。")
        if i == 2:
            if self.length_type == "长篇":
                return base + (f"本次写第 {self.chapter} 章，本章目标约 {self.target_words} 字。"
                               "你是写作教练：第一人称「我」，场景化叙事、show-don't-tell；"
                               "每约 500 字一个钩子（悬念/反转/信息差）；章末必须留强悬念钩子，本章内不写完结。")
            return base + (f"请按选定方向写小说，总体目标约 {self.target_words} 字。"
                           "你是写作教练：第一人称「我」，场景化叙事、show-don't-tell；"
                           "每约 500 字一个钩子；开头 50 字内强钩子，节奏快、爽点密集。"
                           "本次先写开头 800-1000 字；若已有正文务必接着写、不重复开头。")
        if i == 3:
            return base + ("你是责编。通读当前成稿（短篇=全文 / 长篇=最新一章），"
                           "按审稿 rubric 挑 3 条必须改的问题：①开头钩子是否够强 ②爽点/情绪密度 ③人设一致性 ④节奏与违规词。"
                           "每条给具体位置与可执行的改法。")
        if i == 4:
            if self.length_type == "长篇":
                return base + (f"基于审稿意见把第 {self.chapter} 章定稿："
                               f"输出『标题』一行，然后『完整本章正文（保留全部内容、约 {self.target_words} 字）』。")
            # 短篇：正文已在写手阶段写满，终稿只补标题+定稿说明，避免重写截断
            return base + ("不要重写正文。基于审稿意见只做两件事："
                           "1) 给全文拟一个吸睛标题；2) 用一两句话说明你做了哪些定稿处理。"
                           "（正文已在上一步生成，此处不要重复输出大段正文）")

    def _save_node(self, name, text):
        """落盘：每节点产出存 md 到工作区 orchestrate/ 目录。
        v4.164.0：由 APP_DIR（= dist = 分发源）改为 WORKSPACE_DIR，避免随包分发。"""
        try:
            d = os.path.join(WORKSPACE_DIR, "orchestrate")
            os.makedirs(d, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe = name.replace(" ", "_").replace("/", "_")
            if self.length_type == "长篇":
                safe = f"第{self.chapter}章_{safe}"
            fp = os.path.join(d, f"{ts}_{safe}.md")
            with open(fp, "w", encoding="utf-8") as f:
                f.write(f"# {name}\n\n主题：{self.topic}  平台：{self.platform}  "
                        f"类型：{self.length_type}"
                        f"（第{self.chapter}章）\n\n{text}\n")
            self.log.emit(f"💾 已保存：{os.path.basename(fp)}\n")
        except Exception as e:
            self.log.emit(f"⚠️ 落盘失败：{e}\n")

    def _save_checkpoint(self, stage_idx, extra=None):
        """阶段边界存档：把可恢复状态（累积 messages + 阶段号 + 草稿）落到磁盘。"""
        if self.messages is None:
            return
        state = {
            "task_id": self.task_id,
            "task_type": "orchestrate",
            "stage": stage_idx,                    # 已完成的最高阶段索引
            "topic": self.topic,
            "platform": self.platform,
            "length_type": self.length_type,
            "target_words": self.target_words,
            "chapter": self.chapter,
            "messages": self.messages,            # 含已完成各阶段的输入/输出
            "stage2_draft": getattr(self, "stage2_draft", ""),
            "full_draft": getattr(self, "full_draft", ""),
        }
        if extra:
            state.update(extra)
        try:
            task_resume.save_checkpoint(self.mw.cfg, state)
        except Exception:
            pass

    def _recover_chosen_direction(self):
        """断点恢复：从 messages 还原作者已选定的切入点，供选题验证/写手阶段使用。"""
        if not self.messages:
            return
        for m in reversed(self.messages):
            c = m.get("content", "") if isinstance(m, dict) else ""
            if m.get("role") == "user" and "作者选定的切入点" in c:
                try:
                    self._chosen_direction = c.split("作者选定的切入点：\n", 1)[-1]
                except Exception:
                    pass
                return

    def _record_stage_duration(self, name):
        """D 项：记录本阶段耗时（秒），供成功时采集进轨迹。"""
        try:
            if self._stage_start is not None:
                self._stage_durations[name] = round(time.time() - self._stage_start, 1)
        except Exception:
            pass

    def _call(self, messages):
        resp = self.mw._agent_call(messages, [], None)
        return resp.get("content") or ""

    def _call_with_retry(self, messages, retries=3, backoff=3, note=None):
        """C 项（借鉴 Prime-Agent 策略重试）：LLM 调用失败（超时/503/网络）自动换思路重试。

        - 仅重试「抛异常」的失败（网络/API 错误）；空内容不算失败，不重试。
        - 每次重试前在消息副本上追加「换一种写法」提示，改变策略而非单纯重发；
          用副本不改 caller 的 messages，保持检查点/上下文干净。
        - 退避 sleep 用 QThread.msleep（不阻塞主界面，因 worker 在后台线程）。
        - 全部失败则抛出最后一次异常，由调用方干净退出（保留上一节点检查点，可断点续跑）。
        """
        try:
            return self._call(messages)
        except Exception as e:
            last = e
        note = note or ("⚠️ 上一次生成调用失败或超时，请换一种方式重试"
                        "（控制篇幅、避免特殊符号），直接输出正文。")
        for k in range(1, retries + 1):
            self._retry_count += 1  # D 项：累计真实重试次数，供轨迹采集
            self.log.emit(f"🔁 重试（第 {k}/{retries} 次，{backoff*k}s 后）…\n")
            self.msleep(backoff * 1000 * k)
            msgs = list(messages)
            msgs.append({"role": "user", "content": note})
            try:
                return self._call(msgs)
            except Exception as e:
                last = e
        raise last if last is not None else RuntimeError("LLM 调用失败")

    def _parse_options(self, text):
        """从爆款雷达输出中解析出结构化选项，返回 [(label, full_text), ...]（最多 3 个）。"""
        import re
        pat = re.compile(r"选项\s*(\d+)\s*[：:]\s*(.*)")
        blocks = {}
        cur = None
        buf = []
        for line in text.split("\n"):
            m = pat.match(line.strip())
            if m:
                if cur is not None:
                    blocks[cur] = "\n".join(buf).strip()
                cur = int(m.group(1))
                buf = [m.group(2)]
            elif cur is not None:
                buf.append(line)
        if cur is not None:
            blocks[cur] = "\n".join(buf).strip()
        opts = []
        for k in sorted(blocks):
            full = blocks[k]
            first_line = full.split("\n", 1)[0].strip()
            label = (first_line[:30] + "…") if len(first_line) > 30 else first_line
            opts.append({"label": label, "text": full})
        if not opts:
            # 兜底：模型未按格式输出，把整段作为唯一选项
            opts = [{"label": "模型给出的方案", "text": text.strip()}]
        return opts[:3]

    def _maybe_pause(self, messages, label):
        """阶段边界检查暂停请求：若用户点了暂停，则挂起等待插入意见后再继续。"""
        if not self._pause_requested:
            return
        self._pause_requested = False
        self._pending_feedback = None
        self.paused.emit(label)
        self._pause_mutex.lock()
        self._pause_cond.wait(self._pause_mutex)
        self._pause_mutex.unlock()
        if self._pending_feedback:
            self.log.emit(f"\n💬 你插入的修改意见：{self._pending_feedback}\n")
            messages.append({"role": "user", "content":
                f"【作者对上一阶段的修改意见，请在后续环节采纳】{self._pending_feedback}"})
            self._pending_feedback = None

    def _run_writing_stage(self, messages, brief):
        """写手成稿：短篇循环续写直到达到目标字数；长篇单章。返回最终成稿文本（同时存 self.stage2_draft）。"""
        if self.length_type == "长篇":
            p = self._prompt(2, self.STAGES[2][0], brief)
            messages.append({"role": "user", "content": p})
            text = self._call(messages)
            messages.append({"role": "assistant", "content": text})
            self.log.emit(f"（第 {self.chapter} 章，约 {len(text)} 字）\n")
            self.stage2_draft = text
            try:
                task_resume.update_heartbeat(self.mw.cfg, self.task_id)
            except Exception:
                pass
            return text
        # 短篇：循环写主体，接近目标后明确写「完整结局」，确保不半路截断、不烂尾
        target = self.target_words
        draft = ""
        max_iter = min(30, max(3, target // 700 + 3))
        ending_emitted = False
        for it in range(max_iter):
            if self._stop_requested:
                break
            # 写手中也可随时暂停并插入修改意见
            self._maybe_pause(messages, "写手成稿（可在此插入修改意见）")
            if self._stop_requested:
                break
            if it == 0:
                p = self._prompt(2, self.STAGES[2][0], brief)
            else:
                tail = draft[-700:] if len(draft) > 700 else draft
                if len(draft) >= target * 0.6:
                    # 主体已铺垫够，转而写完整结局收尾（只写一次）
                    ending_emitted = True
                    p = (f"当前已写约 {len(draft)} 字（全文目标约 {target} 字）：\n……{tail}\n\n"
                         f"现在写【完整结局】：把前面的铺垫推向高潮并干净收尾，"
                         f"人物命运要有交代、情绪要有落点。约 {max(target - len(draft), 300)} 字左右，"
                         f"必须完结、不要留『待续』悬念。只输出结局正文，不重复前文。")
                else:
                    p = (f"当前已写约 {len(draft)} 字：\n……{tail}\n\n"
                         f"接着上文继续写下一节，约 800-1000 字，推进剧情、保持钩子密度，"
                         f"不要重复开头、此时不要写结局、继续铺垫发展。")
            messages.append({"role": "user", "content": p})
            try:
                chunk = self._call_with_retry(messages, retries=3, backoff=4)
            except Exception as e:
                self.log.emit(f"⚠️ 写手阶段调用失败：{e}（已重试，保留已写约 {len(draft)} 字）\n")
                messages.pop()  # 移除本次未完成的 user 轮，保持上下文干净
                break
            messages.append({"role": "assistant", "content": chunk})
            draft += chunk
            try:
                task_resume.update_heartbeat(self.mw.cfg, self.task_id)
            except Exception:
                pass
            # 一旦写完结局立即收尾；或已到目标且结局已写，停止（避免双结尾）
            if ending_emitted or (len(draft) >= target and ending_emitted):
                break
        self.log.emit(f"（已累计约 {len(draft)} 字）\n")
        self.stage2_draft = draft
        return draft

    def run(self):
        # 续写：复用上一次的底稿上下文（选题已完成，跳过前两个节点）
        if self.prev_state:
            messages = list(self.prev_state["messages"])
            for idx in range(0, max(self.start_stage, 2) if self.start_stage > 0 else 2):
                # 断点恢复：把已完成阶段节点标为 done（含 0/1；start_stage>0 时覆盖到 k-1）
                if idx < self.start_stage:
                    self.node_status.emit(idx, "done")
            if self.start_stage >= 1:
                self._recover_chosen_direction()
        else:
            sys_prompt = self._build_novelist_system_prompt()
            # D 项（轨迹记忆）：新鲜启动检索相似成功轨迹，拼成 few-shot 参考注入系统提示
            try:
                import trace_log
                if self.mw.cfg.get("orch_trace_enabled", True):
                    few = trace_log.build_fewshot_for(
                        self.mw.cfg, self.topic, self.platform, self.length_type)
                    if few:
                        sys_prompt += "\n\n" + few
            except Exception:
                pass
            messages = [{"role": "system", "content": sys_prompt}]
        self.messages = messages
        for i, (name, color, brief) in enumerate(self.STAGES):
            if i < self.start_stage:
                continue
            if i in (0, 1) and self.prev_state and self.start_stage == 0:
                continue
            # 中途暂停 + 插入意见（阶段边界，选题外的每个节点前都允许）
            if i >= 1:
                self._maybe_pause(messages, f"准备进入【{name}】前")
            if self._stop_requested:
                self.node_status.emit(i, "error")
                self.log.emit(f"⏹ 已取消（{name} 前）\n")
                self.done.emit("已取消")
                return
            self._stage_start = time.time()  # D 项：阶段计时起点
            self.stage.emit(i, name)
            self.node_status.emit(i, "running")
            self.log.emit(f"\n【{name}】{brief}\n")
            if i == 0:
                # 爆款雷达：产出 3 个结构化切入点，交作者选定（模型不自决）
                messages.append({"role": "user", "content": self._prompt(0, name, brief)})
                try:
                    text = self._call_with_retry(messages, retries=3)
                except Exception as e:
                    self.node_status.emit(i, "error")
                    self.log.emit(f"❌ {name} 多次重试仍失败：{e}\n")
                    self.done.emit(f"生成失败：{name}")
                    return
                messages.append({"role": "assistant", "content": text})
                self.log.emit(text + "\n")
                self._save_node(name, text)
                self._record_stage_duration(name)  # D 项：记录阶段耗时
                self.node_status.emit(i, "done")
                self._save_checkpoint(i)
                # —— 选题闸门：暂停，等作者从 3 个切入点中选定 ——
                self._choice_options = self._parse_options(text)
                self.need_choice.emit(self._choice_options, text)
                self._choice_mutex.lock()
                self._choice_cond.wait(self._choice_mutex)
                self._choice_mutex.unlock()
                if self._stop_requested:
                    self.log.emit("⏹ 已取消（选题阶段）\n")
                    self.done.emit("已取消")
                    return
                chosen = self._chosen_direction or self._choice_options[0]["text"]
                self.log.emit(f"\n✅ 你选定的切入点：\n{chosen}\n")
                messages.append({"role": "user", "content": f"作者选定的切入点：\n{chosen}"})
                messages.append({"role": "assistant", "content": "明白，将以该方向进入选题验证与写作。"})
                continue
            if i == 2:
                try:
                    text = self._run_writing_stage(messages, brief)
                except Exception as e:
                    self.node_status.emit(i, "error")
                    self.log.emit(f"❌ {name} 多次重试仍失败：{e}\n")
                    self.done.emit(f"生成失败：{name}")
                    return
            else:
                messages.append({"role": "user", "content": self._prompt(i, name, brief)})
                try:
                    text = self._call_with_retry(messages, retries=3)
                except Exception as e:
                    self.node_status.emit(i, "error")
                    self.log.emit(f"❌ {name} 多次重试仍失败：{e}\n")
                    self.done.emit(f"生成失败：{name}")
                    return
                messages.append({"role": "assistant", "content": text})
            if i == 4:  # 终稿定稿
                # 短篇：正文已在写手阶段写满，终稿只补标题+说明，拼到正文前，避免重写截断
                if self.length_type == "短篇" and getattr(self, "stage2_draft", ""):
                    self.full_draft = f"{text}\n\n{self.stage2_draft}"
                else:
                    self.full_draft = text
                # 终稿节点必须展示并保存完整可投递文本（标题+全部正文），而非仅标题
                self.log.emit("\n📜 终稿（可投递完整版，含全部正文）\n" + "-" * 24 + "\n" + self.full_draft + "\n")
                self._save_node(name, self.full_draft)
                self._record_stage_duration(name)  # D 项：记录阶段耗时
                self.node_status.emit(i, "done")
                self._save_checkpoint(i)
            else:
                self.log.emit(text + "\n")
                self._save_node(name, text)
                self._record_stage_duration(name)  # D 项：记录阶段耗时
                self.node_status.emit(i, "done")
                self._save_checkpoint(i)
        # 兜底：极少数情况下终稿被跳过，短篇优先用写手正文，否则取最后一段 assistant
        if not self.full_draft:
            if self.length_type == "短篇" and self.stage2_draft:
                self.full_draft = self.stage2_draft
            else:
                for m in reversed(messages):
                    if m.get("role") == "assistant":
                        self.full_draft = m.get("content") or ""
                        break
        self.final_state = {
            "topic": self.topic,
            "platform": self.platform,
            "length_type": self.length_type,
            "target_words": self.target_words,
            "messages": messages,
            "chapter": self.chapter,
            "draft": self.full_draft,
        }
        self.done.emit("小说一条龙生成完成")
class _ASRWorker(QThread):
    """后台语音识别：wav -> 文本（硅基流动 ASR）。"""
    sig_text = Signal(str)
    sig_error = Signal(str)

    def __init__(self, wav_path, sf):
        super().__init__()
        self.wav_path = wav_path
        self.sf = sf

    def run(self):
        try:
            txt = voice_mod.transcribe(self.wav_path, self.sf)
            self.sig_text.emit(txt)
        except Exception as e:
            self.sig_error.emit(str(e))
class _TTSWorker(QThread):
    """后台语音合成：文本 -> mp3（edge-tts）。"""
    sig_mp3 = Signal(str)

    def __init__(self, text, tts):
        super().__init__()
        self.text = text
        self.tts = tts

    def run(self):
        try:
            mp3 = voice_mod.synthesize(self.text, self.tts)
            self.sig_mp3.emit(mp3)
        except Exception as e:
            log.error("TTS 失败: %s", e)
