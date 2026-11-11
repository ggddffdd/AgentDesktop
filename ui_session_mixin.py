# -*- coding: utf-8 -*-
"""ui_session_mixin.py —— ChatWindow 的会话管理（v4.290.0 外移）

为什么单独成文件：v4.290.0 修「自动化任务串台 / 全空会话堆积 / 会话列表混入
自动化会话」净增约 93 行，ui.py 冲到 **10082 行**，撞了 `tests/test_split_216.py`
的 D1 红线（`ui.py < 10000`）。按项目纪律「处理 = 外移，不抬阈值」，把整组
会话管理方法搬出来。

承接四个方法（ui.py 里只留调用点 / 继承）：
  · `_refresh_session_combo` —— 顶部会话下拉框（过滤 auto_、置顶+时间倒序）
  · `_on_session_combo`      —— 下拉框选择回调
  · `_new_session`           —— 新建/复用会话
  · `_close_session`         —— 关闭会话（含独立上下文摘要清理）

依赖宿主类提供：`store` / `session_combo` / `input_box` / `_busy` /
`_rendered_msg_count` / `_switch_session` 以及若干 `_refresh_*` 方法。
"""
import automation


class ChatSessionMixin(object):
    """会话列表、新建、关闭。"""

    def _refresh_session_combo(self):
        """刷新对话页顶部的会话下拉框。

        v4.290.0：三处修正 ——
          ① 过滤 `auto_` 自动化会话：那是任务专属会话，混进用户对话列表
             正是「自动化与普通对话串台」的视觉来源；
          ② 置顶优先 + 按创建时间倒序（原来按 dict 插入顺序，最新会话反而排最后）；
          ③ 标题截断口径不变，空标题统一显示「新会话」。
        """
        if not hasattr(self, "session_combo"):
            return
        self.session_combo.blockSignals(True)
        try:
            self.session_combo.clear()
            active_sid = self.store.active_sid
            pinned, normal = [], []
            for sid, s in self.store.sessions.items():
                if str(sid).startswith(automation.SESSION_SID_PREFIX):
                    continue
                title = s.title or "新会话"
                display = title if len(title) <= 24 else title[:23] + "…"
                row = (str(getattr(s, "created", "") or ""), display, sid)
                (pinned if getattr(s, "pinned", False) else normal).append(row)
            # ISO 时间字符串可直接比较；同秒并列用 sid 兜底，保证顺序稳定
            pinned.sort(key=lambda r: (r[0], r[2]), reverse=True)
            normal.sort(key=lambda r: (r[0], r[2]), reverse=True)
            idx = 0
            for i, (_t, display, sid) in enumerate(pinned + normal):
                self.session_combo.addItem(display, sid)
                if sid == active_sid:
                    idx = i
            self.session_combo.setCurrentIndex(idx)
        finally:
            self.session_combo.blockSignals(False)

    def _on_session_combo(self, index):
        sid = self.session_combo.itemData(index)
        if sid:
            self._switch_session(sid)

    def _new_session(self):
        # v4.290.0：当前会话还是空的就直接复用 —— 连点「新对话」不再堆一串同名
        # 空会话（列表里一眼全是「新会话」，找历史反而更难）。
        try:
            _cur = self.store.active()
            _cur_sid = str(getattr(_cur, "sid", "") or "")
            if (_cur is not None
                    and not _cur_sid.startswith(automation.SESSION_SID_PREFIX)
                    and not getattr(_cur, "messages", None)):
                self._render_messages(force_bottom=True)
                self.input_box.setFocus()
                return
        except Exception:
            pass
        self._reset_session_trust()   # v4.169.0 P0-6：信任不跨会话
        self.store.new_session()
        self._sync_product_context()   # v4.129
        self._rendered_msg_count = 0  # v4.60：新建会话重置增量渲染计数
        self._refresh_session_combo()
        self._render_messages(force_bottom=True)
        self._refresh_skill_buttons()
        self._update_skill_bar()
        self._refresh_deliverables()
        self._refresh_recent_on_welcome()
        self.input_box.setFocus()

    def _close_session(self, sid):
        if self._busy:
            self.status_label.setText("正在处理，稍后再关闭会话")
            return
        # v4.169.0 P0-6：关掉的是**当前**会话时才清信任（关别的会话不该影响手头的操作）
        _was_active = False
        try:
            _cur = self.store.active()
            _was_active = (_cur is not None and getattr(_cur, "sid", None) == sid)
        except Exception:
            pass
        self.store.remove(sid)
        if _was_active:
            self._reset_session_trust()
        # v4.73：删除会话时一并清理其独立的上下文摘要文件，避免残留串台
        # v4.290.0：路径改走 config.USER_DATA_DIR —— 原来硬编码
        # `~/Documents/小臭玩AI`，改道环境（测试/便携版）下根本删不到，
        # 「避免残留串台」这句话就是空的。与 session/context_manager 同源。
        try:
            import os as _os
            import config as _cfg
            _ud = _cfg.USER_DATA_DIR
            for _f in (f"context_summary_{sid}.json", f"key_info_{sid}.json"):
                _p = _os.path.join(_ud, _f)
                if _os.path.exists(_p):
                    _os.remove(_p)
        except Exception:
            pass
        self._refresh_session_combo()
        self._render_messages(force_bottom=True)
        self._refresh_skill_buttons()
        self._update_skill_bar()
        self._refresh_deliverables()
        self._refresh_recent_on_welcome()
        self.input_box.setFocus()
