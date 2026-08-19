# -*- coding: utf-8 -*-
"""
结构化日志模块 v1.1
SQLite 存储，替代纯文本 debug.log，支持按级别/模块/时间查询。
落用户目录（~Documents/AgentDesktop/agent_log.db），避开重建覆盖 dist。

单例用法：
    from structured_logger import get_logger
    logger = get_logger()
    logger.info("用户打开应用", module="ui")
    logger.error("工具执行失败", module="tools", extra={"tool": "web_search", "error": "timeout"})
    rows = logger.query(level="ERROR", limit=10)
"""
import sqlite3
import json
import os
from datetime import datetime
from pathlib import Path


def _default_db_path():
    d = os.path.join(os.path.expanduser("~"), "Documents", "AgentDesktop")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "agent_log.db")


class StructuredLogger:
    def __init__(self, db_path=None):
        self.db_path = db_path or _default_db_path()
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS agent_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                level TEXT NOT NULL CHECK(level IN ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')),
                module TEXT,
                message TEXT,
                extra_data TEXT,
                session_id TEXT
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_ts ON agent_logs(timestamp)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_lv ON agent_logs(level)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_md ON agent_logs(module)')
        conn.commit()
        conn.close()

    def _log(self, level, message, module=None, extra=None, session_id=None):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute(
            'INSERT INTO agent_logs (timestamp, level, module, message, extra_data, session_id) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"), level, module, message,
             json.dumps(extra, ensure_ascii=False) if extra else None, session_id))
        conn.commit()
        conn.close()
        # 兼容旧 debug.log
        try:
            with open(os.path.join(os.path.dirname(self.db_path), "debug.log"), "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{level}] {message}\n")
        except Exception:
            pass

    def debug(self, message, module=None, extra=None, session_id=None):
        self._log("DEBUG", message, module, extra, session_id)

    def info(self, message, module=None, extra=None, session_id=None):
        self._log("INFO", message, module, extra, session_id)

    def warning(self, message, module=None, extra=None, session_id=None):
        self._log("WARNING", message, module, extra, session_id)

    def error(self, message, module=None, extra=None, session_id=None):
        self._log("ERROR", message, module, extra, session_id)

    def critical(self, message, module=None, extra=None, session_id=None):
        self._log("CRITICAL", message, module, extra, session_id)

    def query(self, level=None, module=None, start_time=None, end_time=None, limit=50, offset=0):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        q = "SELECT * FROM agent_logs WHERE 1=1"
        p = []
        if level:
            q += " AND level = ?"
            p.append(level)
        if module:
            q += " AND module = ?"
            p.append(module)
        if start_time:
            q += " AND timestamp >= ?"
            p.append(start_time)
        if end_time:
            q += " AND timestamp <= ?"
            p.append(end_time)
        q += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        p.extend([limit, offset])
        c.execute(q, p)
        rows = c.fetchall()
        conn.close()
        res = []
        for r in rows:
            it = dict(r)
            if it.get("extra_data"):
                try:
                    it["extra_data"] = json.loads(it["extra_data"])
                except Exception:
                    pass
            res.append(it)
        return res

    def get_stats(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('SELECT level, COUNT(*) FROM agent_logs GROUP BY level')
        stats = dict(c.fetchall())
        c.execute("SELECT COUNT(*) FROM agent_logs")
        stats["total"] = c.fetchone()[0]
        conn.close()
        return stats


_logger = None


def get_logger():
    global _logger
    if _logger is None:
        _logger = StructuredLogger()
    return _logger
