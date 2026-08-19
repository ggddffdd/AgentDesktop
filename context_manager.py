"""上下文窗口智能管理模块 v1.1
自动压缩历史对话，保留关键信息（实体/决策/待办/偏好）。
超过阈值自动做启发式压缩；可调用 context_compress 工具用免费 LLM 生成真实摘要。

设计说明（相对规划文档 v4.5 的升级）：
- 文档里的 _generate_summary 是占位启发式；本实现保留启发式作为兜底，
  并在 context_compress 工具里接入真实 LLM（优先用配置模型，无 key 时回退 Agnes 免费档）。
- 为不破坏现有对话流，本模块独立维护一份上下文副本（单例），
  由 ui.py 在消息产生时轻量同步，不在 Session 内部做侵入式改造。
"""

import json
import re
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path


def _user_data_dir():
    p = Path.home() / "Documents" / "AgentDesktop"
    p.mkdir(parents=True, exist_ok=True)
    return p


class ContextManager:
    def __init__(self, max_window=20, compress_threshold=10,
                 summary_path=None, key_info_path=None):
        self.max_window = max_window        # 保留最近 N 轮完整上下文
        self.compress_threshold = compress_threshold  # 超过 N 条触发自动压缩
        ud = _user_data_dir()
        self.summary_path = summary_path or str(ud / "context_summary.json")
        self.key_info_path = key_info_path or str(ud / "key_info.json")
        self.messages = []
        self.summaries = []
        self.key_info = {
            "entities": [],     # 关键实体（人名/地名/项目名）
            "decisions": [],    # 关键决策
            "todos": [],        # 待办事项
            "preferences": [],  # 用户偏好
        }
        self._load()

    # ---------- 持久化 ----------
    def _load(self):
        try:
            if Path(self.summary_path).exists():
                self.summaries = json.loads(
                    Path(self.summary_path).read_text(encoding="utf-8"))
        except Exception:
            self.summaries = []
        try:
            if Path(self.key_info_path).exists():
                self.key_info = json.loads(
                    Path(self.key_info_path).read_text(encoding="utf-8"))
        except Exception:
            pass

    def _save_summaries(self):
        Path(self.summary_path).write_text(
            json.dumps(self.summaries, ensure_ascii=False, indent=2),
            encoding="utf-8")

    def _save_key_info(self):
        Path(self.key_info_path).write_text(
            json.dumps(self.key_info, ensure_ascii=False, indent=2),
            encoding="utf-8")

    # ---------- 核心 ----------
    def add_message(self, role, content, metadata=None):
        """添加消息到上下文，并在超过阈值时自动压缩"""
        self.messages.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "metadata": metadata or {},
        })
        self.extract_key_info(content)
        if len(self.messages) > self.compress_threshold:
            self._compress()

    def _compress(self):
        """自动压缩：保留最近 max_window 条，更早的历史做启发式摘要归档"""
        recent = self.messages[-self.max_window:]
        history = self.messages[:-self.max_window]
        if history:
            summary = self._heuristic_summary(history)
            self.summaries.append(summary)
            self._save_summaries()
        self.messages = recent

    def _heuristic_summary(self, history):
        summary = {
            "period": f"{history[0]['timestamp']} ~ {history[-1]['timestamp']}",
            "message_count": len(history),
            "key_points": [],
            "method": "heuristic",
        }
        for msg in history:
            if msg["role"] != "user":
                continue
            c = msg["content"]
            if any(k in c for k in ("TODO", "待办", "任务")):
                summary["key_points"].append({"type": "todo", "content": c[:200]})
            elif any(k in c for k in ("记住", "偏好", "我喜欢", "约定")):
                summary["key_points"].append({"type": "preference", "content": c[:200]})
        return summary

    def compress_with_llm(self, cfg=None):
        """调用免费 LLM 对超窗历史做真实摘要，返回摘要文本。失败回退启发式。"""
        history = (self.messages[:-self.max_window]
                   if len(self.messages) > self.max_window else self.messages)
        if not history:
            return "（没有可压缩的历史）"
        text = "\n".join(f"{m['role']}: {m['content']}" for m in history)
        prompt = (
            "你是一个对话压缩器。请把下面的历史对话压缩成简洁的中文要点，"
            "保留：1)关键决策 2)待办事项 3)关键实体(人名/项目名) 4)用户偏好。"
            "用编号列表输出，不要编造信息。\n\n" + text
        )
        llm_text = _call_summary_llm(prompt, cfg)
        if llm_text is None:
            s = self._heuristic_summary(history)
            self.summaries.append({**s, "method": "heuristic-fallback"})
            self._save_summaries()
            return "（LLM 不可用，已用启发式摘要）\n" + json.dumps(s, ensure_ascii=False)[:600]
        entry = {
            "period": f"{history[0]['timestamp']} ~ {history[-1]['timestamp']}",
            "message_count": len(history),
            "summary": llm_text,
            "method": "llm",
        }
        self.summaries.append(entry)
        self._save_summaries()
        # 真实摘要后，把超窗部分裁剪，仅留最近窗口
        self.messages = self.messages[-self.max_window:]
        return llm_text

    def get_compressed_context(self):
        """获取压缩后的上下文（供工具/调试查看）"""
        return {
            "recent_messages": self.messages,
            "key_info": self.key_info,
            "summaries": self.summaries,
        }

    def extract_key_info(self, text):
        """从文本中提取关键信息（轻量正则）"""
        todos = re.findall(r'(?:待办|TODO|任务)[:：]\s*(.+)', text)
        entities = re.findall(r'([A-Za-z]{2,})', text)
        self.key_info["todos"] = list(dict.fromkeys(self.key_info["todos"] + todos))
        self.key_info["entities"] = list(dict.fromkeys(self.key_info["entities"] + entities))
        self._save_key_info()

    def clear(self):
        self.messages = []
        self.summaries = []
        self._save_summaries()


_MGRS = {}


def get_context_manager(sid=None):
    """返回与指定会话绑定的上下文管理器（按 sid 隔离，避免跨会话记忆/摘要串台）。

    v4.73 修复：原实现是全局单例，所有会话共用同一份 context_summary/key_info，
    导致不同对话的上下文摘要、提取的关键信息互相污染（对话A聊小说、对话B聊电力会混在一起）。
    现在按 sid 缓存独立实例，各自读写 context_summary_{sid}.json / key_info_{sid}.json。

    sid 为 None 时回退全局实例（兼容旧调用），但正常路径必须由调用方传入会话 sid。
    """
    if sid is None:
        sid = "_global"
    key = str(sid)
    mgr = _MGRS.get(key)
    if mgr is None:
        ud = _user_data_dir()
        summary_path = str(ud / f"context_summary_{key}.json")
        key_info_path = str(ud / f"key_info_{key}.json")
        mgr = ContextManager(summary_path=summary_path, key_info_path=key_info_path)
        _MGRS[key] = mgr
    return mgr


def _call_summary_llm(prompt, cfg=None):
    """调用配置的 LLM（无 key 时回退 Agnes 免费档）。返回文本或 None。"""
    try:
        import config as _cfg_mod
    except Exception:
        return None
    cfg = cfg or _cfg_mod.load_config()
    base_url = cfg.get("base_url") or "https://api.deepseek.com"
    model = cfg.get("model") or "deepseek-chat"
    api_key = cfg.get("api_key") or ""
    if not api_key:
        profiles = cfg.get("model_profiles", {})
        agnes = profiles.get("Agnes")
        if agnes:
            base_url = agnes.get("base_url")
            model = agnes.get("model")
            api_key = agnes.get("api_key", "")
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 800,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            obj = json.loads(resp.read().decode("utf-8"))
        return obj["choices"][0]["message"]["content"].strip()
    except Exception as e:
        try:
            _cfg_mod.log.warning("context LLM 摘要失败: %s", e)
        except Exception:
            pass
        return None
