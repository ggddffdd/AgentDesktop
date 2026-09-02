# -*- coding: utf-8 -*-
"""DeepSeek 桌面助手 — 会话管理模块"""

import os
import json
import logging
from datetime import datetime

from config import APP_DIR

log = logging.getLogger("dsdesktop")


class Session:
    def __init__(self, sid, title="新会话", messages=None, created=None, skill=None, deliverables=None, goal="", pinned=False, folder=""):
        self.sid = sid
        self.title = title or "新会话"
        self.messages = messages or []          # [{"role":"user"/"assistant","content":str}]
        self.created = created or datetime.now().isoformat(timespec="seconds")
        self.skill = skill                      # 技能 id，None=通用模式
        self.deliverables = deliverables or []  # 工具生成的交付物 [{rel,kind,name,time}]
        self.goal = goal or ""                  # 本会话最初目标（首条用户消息），用于长对话中防止「中途忘了要干什么」
        self.pinned = bool(pinned)              # v4.79：置顶
        self.folder = folder or ""              # v4.79：分组/文件夹（空=未分组）

    def to_dict(self):
        return {"sid": self.sid, "title": self.title,
                "messages": self.messages, "created": self.created,
                "skill": self.skill, "deliverables": self.deliverables,
                "goal": self.goal, "pinned": self.pinned, "folder": self.folder}

    @classmethod
    def from_dict(cls, d):
        return cls(d.get("sid"), d.get("title", "新会话"),
                   d.get("messages", []), d.get("created"), d.get("skill"),
                   d.get("deliverables", []), d.get("goal", ""),
                   d.get("pinned", False), d.get("folder", ""))


class SessionStore:
    PATH = os.path.join(os.path.expanduser("~/Documents/小臭玩AI"), "sessions.json")

    def __init__(self):
        self.sessions = {}
        self.active_sid = None
        os.makedirs(os.path.dirname(self.PATH), exist_ok=True)
        self._load()
        if not self.sessions:
            self.new_session()

    def _load(self):
        if not os.path.exists(self.PATH):
            return
        try:
            with open(self.PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            for d in data.get("sessions", []):
                s = Session.from_dict(d)
                self.sessions[s.sid] = s
            self.active_sid = data.get("active")
            if self.active_sid not in self.sessions:
                self.active_sid = next(iter(self.sessions), None)
        except Exception as e:
            log.error("加载 sessions.json 失败: %s", e)

    def save(self):
        """v4.58：原子写入——先写临时文件再 os.replace，防止崩溃时损坏 sessions.json。"""
        data = {
            "active": self.active_sid,
            "sessions": [s.to_dict() for s in self.sessions.values()],
        }
        try:
            os.makedirs(os.path.dirname(self.PATH), exist_ok=True)
            tmp_path = self.PATH + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.PATH)  # 原子重命名
        except Exception as e:
            log.error("保存 sessions.json 失败: %s", e)

    def active(self):
        return self.sessions.get(self.active_sid) or self.new_session()

    def new_session(self, title=None, goal=None):
        sid = datetime.now().strftime("%Y%m%d%H%M%S") + str(len(self.sessions))
        s = Session(sid, title=title or "新会话", goal=goal or "")
        self.sessions[sid] = s
        self.active_sid = sid
        self.save()
        return s

    def switch(self, sid):
        if sid in self.sessions:
            self.active_sid = sid
            self.save()

    def remove(self, sid):
        if sid not in self.sessions:
            return
        if len(self.sessions) <= 1:
            # 至少保留一个会话，清空它而不是删除
            s = self.sessions[sid]
            s.messages = []
            s.title = "新会话"
            self.save()
            return
        del self.sessions[sid]
        if self.active_sid == sid:
            self.active_sid = next(iter(self.sessions))
        self.save()

    # ---- v4.79：会话管理增强（置顶/分组/批量删除/筛选）----
    def set_pinned(self, sid, val):
        s = self.sessions.get(sid)
        if not s:
            return
        s.pinned = bool(val)
        self.save()

    def set_folder(self, sid, folder):
        s = self.sessions.get(sid)
        if not s:
            return
        s.folder = (folder or "").strip()
        self.save()

    def remove_many(self, sids):
        """批量删除（跳过当前激活会话与最后一个残留会话，保证至少留一个）。"""
        sids = [s for s in sids if s in self.sessions]
        if not sids:
            return 0
        # 保底：至少留一个会话
        remaining_after = len(self.sessions) - len(sids)
        if remaining_after <= 0:
            sids = sids[:-1]  # 留一个不删
        deleted = 0
        for sid in sids:
            if sid == self.active_sid and len(self.sessions) > 1:
                # 删激活会话时，先切到另一个
                other = next((x for x in self.sessions if x != sid), None)
                if other:
                    self.active_sid = other
            del self.sessions[sid]
            deleted += 1
        if self.active_sid not in self.sessions:
            self.active_sid = next(iter(self.sessions), None)
        self.save()
        return deleted

    def list_folders(self):
        """返回所有出现过的分组名（去重、排序），不含空串。"""
        folders = sorted({s.folder for s in self.sessions.values() if s.folder})
        return folders

    def all_sorted(self, query="", folder=""):
        """返回会话列表，按 置顶优先 + 创建时间倒序。
        query: 标题子串筛选（忽略大小写）；folder: 仅该分组（''=全部含未分组）。"""
        items = list(self.sessions.values())
        if folder:
            items = [s for s in items if s.folder == folder]
        if query:
            q = query.strip().lower()
            items = [s for s in items if q in (s.title or "").lower()]
        items.sort(key=lambda s: (not s.pinned, s.created), reverse=True)
        return items
