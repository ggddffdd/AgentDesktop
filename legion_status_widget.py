"""军团内部可观测控件（v4.183.0）。

复用现有真源，不引入事件溯源架构 / 单写者锁 / sig 哈希 / 独立 taskboard：

  - LegionStatusStrip（实时状态条）：
      订阅 worker.board_update 信号，从 board 节点聚合
      「波次 / 成员运行态 / 闸门待批」—— 真源是 legion.board_update 已落地的
      legion_board/<pid>.json（node_key: run / w{w}_m{s} / gate_w{w}）。

  - LegionAuditFeed（授权审计流）：
      读 legion_auth.jsonl（record_auth / record_message）+ legion_tool_audit.jsonl
      （LegionPermission._audit），按 actor / wave / 项目过滤展示
      批准 / 拒绝 / 收回 / 工具判定 / 用户指令。两文件同落 LEGION_DIR。

样式严守 DESIGN.md：色全用 THEME[key]，字号 token（11/12/13px），圆角 6/8/10。
"""
from __future__ import annotations

import os
import json

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QGroupBox, QComboBox, QVBoxLayout,
)
from PySide6.QtCore import Qt


def _theme() -> dict:
    """延迟取 THEME，避免模块级循环导入（ui <- legion_ui <- 本模块）。

    走不通时回退到与 DESIGN.md 对齐的浅色基底（仅离线/测试场景命中）。
    """
    try:
        from ui import THEME
        return THEME
    except Exception:
        return {
            "bg": "#F7F8FC", "panel": "#FFFFFF", "card": "#FFFFFF",
            "border": "#E5E7EB", "text": "#202124", "dim": "#5F6368",
            "faint": "#9AA0A6", "ok": "#34A853", "warn": "#FBBC04",
            "danger": "#EA4335", "accent": "#1A73E8", "tool_running": "#1A73E8",
            "font_micro": "11px", "font_second": "12px", "font_body": "13px",
        }


# ---------------------------------------------------------------------------
# 纯函数：审计数据采集与格式化（可单测，无需 QApplication）
# ---------------------------------------------------------------------------

def _read_jsonl(path):
    """逐行读 JSONL，坏行跳过；文件不存在返回 []。"""
    out = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return out
    return out


def _label_decision(d):
    return {"allow": "✅ 批准", "pass": "✅ 批准",
            "deny": "⛔ 拒绝"}.get(d, f"·{d}·")


def _fmt(rec):
    """把一条审计记录渲染成一行可读文本（纯展示，含脱敏原文/摘要）。"""
    t = rec.get("time") or rec.get("ts") or ""
    src = rec.get("_src")
    if src == "tool":
        d = rec.get("decision", "")
        tool = rec.get("tool", "?")
        wave = rec.get("wave", "")
        by = rec.get("by", "")
        return (f"{t} {_label_decision(d)} · 工具 {tool} · 波{wave} · {by}")
    # auth 账本：用户指令 或 授权/收回
    if rec.get("event") == "user_message":
        txt = (rec.get("text") or "")
        if len(txt) > 36:
            txt = txt[:36] + "…"
        wave = rec.get("wave", "")
        return f"{t} 💬 指令 · 波{wave} · {txt}"
    dec = rec.get("decision", "")
    by = rec.get("by", "")
    wave = rec.get("wave", "")
    role = rec.get("role") or rec.get("pm_verdict") or rec.get("fingerprint") or ""
    return f"{t} {_label_decision(dec)} · 波{wave} · {by} · {role}"


def collect_audit(auth_path, tool_path, project_id=None, actor="全部"):
    """合并两账本并按 项目 / actor 过滤，按时间升序返回。"""
    rows = []
    for r in _read_jsonl(auth_path):
        d = dict(r)
        d["_src"] = "auth"
        rows.append(d)
    for r in _read_jsonl(tool_path):
        d = dict(r)
        d["_src"] = "tool"
        rows.append(d)
    rows.sort(key=lambda r: r.get("ts", 0))
    out = []
    for r in rows:
        if project_id and r.get("project_id") and r["project_id"] != project_id:
            continue
        by = r.get("by")
        if actor != "全部" and by != actor:
            continue
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# 实时状态条
# ---------------------------------------------------------------------------

class LegionStatusStrip(QWidget):
    """军团运行实时状态条：波次 / 成员运行态 / 闸门待批。常驻军团所有页顶部。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._t = _theme()
        self._nodes = {}            # node_key -> {role, wave, status}
        self._cur_wave = 0
        self._total_waves = 0
        self._build()

    def _build(self):
        t = self._t
        self.setStyleSheet(
            f"background:{t['card']};border:1px solid {t['border']};"
            f"border-radius:8px;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(14)
        self._lbl_wave = QLabel("波 —/—")
        self._lbl_members = QLabel("成员 运行中0 完成0 异常0")
        self._lbl_gate = QLabel("闸门 —")
        for w in (self._lbl_wave, self._lbl_members, self._lbl_gate):
            w.setStyleSheet(f"font-size:{t['font_second']};color:{t['dim']};")
            w.setAlignment(Qt.AlignVCenter)
            lay.addWidget(w)
        lay.addStretch(1)
        self._dot_host = QWidget()
        self._dot_lay = QHBoxLayout(self._dot_host)
        self._dot_lay.setContentsMargins(0, 0, 0, 0)
        self._dot_lay.setSpacing(4)
        lay.addWidget(self._dot_host)

    def set_total_waves(self, n):
        self._total_waves = int(n) if n else 0
        self._refresh()

    def reset(self):
        self._nodes = {}
        self._cur_wave = 0
        self._total_waves = 0
        self._refresh()

    def on_board(self, pid, node_key, role, wave, status):
        """worker.board_update 信号槽：(pid, node_key, role, wave, status)。"""
        try:
            wave_i = int(wave)
        except (TypeError, ValueError):
            wave_i = 0
        if node_key == "run":
            pass  # 生命周期节点不直接计成员
        else:
            self._nodes[node_key] = {
                "role": role or "", "wave": wave_i, "status": status or ""}
            if wave_i and node_key not in ("pm_plan",) \
                    and not node_key.startswith("gate_w"):
                self._cur_wave = max(self._cur_wave, wave_i)
        self._refresh()

    def _refresh(self):
        t = self._t
        if self._total_waves:
            self._lbl_wave.setText(f"波 {self._cur_wave}/{self._total_waves}")
        else:
            self._lbl_wave.setText("波 —/—")
        running = done = err = 0
        for k, n in self._nodes.items():
            if k.startswith("gate_w") or k in ("run", "pm_plan"):
                continue
            s = n.get("status")
            if s in ("running",):
                running += 1
            elif s in ("done", "approved", "incomplete"):
                done += 1
            elif s in ("error",):
                err += 1
        self._lbl_members.setText(
            f"成员 运行中{running} 完成{done} 异常{err}")
        gate = "—"
        gcolor = t["dim"]
        # P3 修：原版后遍历的 gate 节点覆盖先遍历的（字典序最后一个说了算）——
        # gate_w1 待批、gate_w2 已批时会错显「已批」。改为优先级汇总：
        # 任一 gate 待批 → 待批；否则任一已批 → 已批。
        _gate_pending = False
        _gate_done = False
        for k, n in self._nodes.items():
            if k.startswith("gate_w"):
                s = n.get("status")
                if s == "running":
                    _gate_pending = True
                elif s in ("done", "approved", "timeout"):
                    _gate_done = True
        if _gate_pending:
            gate = "待批"
            gcolor = t["warn"]
        elif _gate_done:
            gate = "已批"
            gcolor = t["ok"]
        self._lbl_gate.setText(f"闸门 {gate}")
        self._lbl_gate.setStyleSheet(
            f"font-size:{t['font_second']};color:{gcolor};")
        self._render_dots()

    def _render_dots(self):
        t = self._t
        while self._dot_lay.count():
            it = self._dot_lay.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        cmap = {
            "running": t["tool_running"],
            "done": t["ok"], "approved": t["ok"], "incomplete": t["ok"],
            "error": t["danger"], "pending": t["warn"],
        }
        for k, n in sorted(self._nodes.items()):
            if k.startswith("gate_w") or k in ("run", "pm_plan"):
                continue
            c = cmap.get(n.get("status"), t["faint"])
            dot = QLabel("●")
            dot.setStyleSheet(f"color:{c};font-size:{t['font_micro']};")
            dot.setToolTip(f"{k} · {n.get('role', '')} · {n.get('status')}")
            self._dot_lay.addWidget(dot)


# ---------------------------------------------------------------------------
# 授权审计流
# ---------------------------------------------------------------------------

class LegionAuditFeed(QGroupBox):
    """授权审计流：读两账本，按 actor 过滤展示批准/拒绝/收回/工具判定/用户指令。"""

    def __init__(self, parent=None):
        super().__init__("授权审计流", parent)
        self._t = _theme()
        self._project_id = None
        self._actor = "全部"
        self._build()

    def _build(self):
        t = self._t
        self.setStyleSheet(
            f"QGroupBox{{background:{t['panel']};border:1px solid {t['border']};"
            f"border-radius:10px;margin-top:8px;font-size:{t['font_second']};"
            f"color:{t['text']};}}"
            f"QGroupBox::title{{subcontrol-origin:margin;left:10px;padding:0 4px;"
            f"color:{t['dim']};}}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 18, 10, 10)
        lay.setSpacing(6)
        top = QHBoxLayout()
        top.addWidget(QLabel("按审批人"))
        self._actor_cb = QComboBox()
        self._actor_cb.addItems(["全部", "user", "auto", "engine", "timeout"])
        self._actor_cb.setStyleSheet(
            f"QComboBox{{font-size:{t['font_second']};color:{t['text']};"
            f"border:1px solid {t['border']};border-radius:6px;padding:2px 6px;}}")
        self._actor_cb.currentTextChanged.connect(self._on_actor)
        top.addWidget(self._actor_cb, 1)
        top.addStretch(1)
        lay.addLayout(top)
        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget{{background:{t['card']};border:1px solid {t['border']};"
            f"border-radius:8px;font-size:{t['font_second']};color:{t['text']};}}")
        self._list.setFixedHeight(150)
        lay.addWidget(self._list, 1)

    def _on_actor(self, txt):
        self._actor = txt
        self.load(self._project_id)

    def load(self, project_id=None, limit=300):
        self._project_id = project_id
        try:
            import legion as _lg
            auth_path = _lg.AUTH_LOG
            tool_path = os.path.join(_lg.LEGION_DIR, "legion_tool_audit.jsonl")
        except Exception:
            auth_path = tool_path = ""
        rows = collect_audit(auth_path, tool_path, project_id, self._actor)
        self._list.clear()
        for r in rows[-limit:]:
            item = QListWidgetItem(_fmt(r))
            dec = r.get("decision")
            if dec == "deny":
                item.setForeground(_qcolor(self._t["danger"]))
            elif dec in ("allow", "pass"):
                item.setForeground(_qcolor(self._t["ok"]))
            self._list.addItem(item)


def _qcolor(hexstr):
    try:
        from PySide6.QtGui import QColor
        return QColor(hexstr)
    except Exception:
        return None
