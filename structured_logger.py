# -*- coding: utf-8 -*-
"""
结构化日志模块 v1.1
SQLite 存储，替代纯文本 debug.log，支持按级别/模块/时间查询。
落用户目录（~Documents/小臭玩AI/agent_log.db），避开重建覆盖 dist。

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
import sys
import traceback
from datetime import datetime
from pathlib import Path


def _default_db_path():
    # v4.134.4：跟随军团数据根的改道（XC_LEGION_DIR）—— 测试子进程注入该变量后，
    # 日志库也落临时目录，不再写真实 ~/Documents/小臭玩AI/agent_log.db。
    # （此前用 expanduser("~") 硬算，只有骗 HOME/USERPROFILE 才拦得住，
    #  跑手做不到对每个 import 都生效。）
    d = os.environ.get("XC_LEGION_DIR") or os.path.join(
        os.path.expanduser("~"), "Documents", "小臭玩AI")
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

    def _log(self, level, message, module=None, extra=None, session_id=None,
             exc_info=False):
        # v4.257.0（P0-5）：接住 `exc_info` —— 与 std logging 同名的那个参数。
        #
        # 病根：本类签名此前**没有** exc_info，而 tools.py 两处（964/978）按
        #   std logging 的习惯传了 `exc_info=True` → TypeError → 外层
        #   `except Exception: pass` 吞掉。**v4.155 fix1 号称补的「工具失败
        #   ERROR 级落库」，自那以后一次都没成功过** —— 工具失败在日志里是空白。
        #
        # 口径（对齐 std logging，避免第二套语义）：
        #   · exc_info 默认 False —— 老调用点行为**逐字不变**（只加不减）；
        #   · True：取当前 sys.exc_info()（必须在 except 块里调，否则没有异常）；
        #   · 也可直接传异常实例或 (type, value, tb) 三元组；
        #   · message 尾部追加「[异常] 类型: 信息」，完整栈进 extra["traceback"]。
        #     两者都给：message 供人看，traceback 供排障；日志查看器只展示
        #     message 时也不至于只剩一句「执行异常」。
        if exc_info:
            message, extra = self._append_exc(message, exc_info, extra)
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute(
            'INSERT INTO agent_logs (timestamp, level, module, message, extra_data, session_id) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"), level, module, message,
             json.dumps(extra, ensure_ascii=False) if extra else None, session_id))
        conn.commit()
        conn.close()

    @staticmethod
    def _append_exc(message, exc_info, extra):
        """把异常信息并进 message / extra。纯函数，失败一律退回原样（fail-open）。"""
        try:
            if exc_info is True:
                exc_info = sys.exc_info()
            if isinstance(exc_info, BaseException):
                exc_info = (type(exc_info), exc_info, exc_info.__traceback__)
            et, ev, tb = exc_info if isinstance(exc_info, tuple) else (None, None, None)
            if et is None and ev is None:
                return message, extra
            head = "%s: %s" % (getattr(et, "__name__", str(et)), ev) if et is not None else str(ev)
            stack = "".join(traceback.format_exception(et, ev, tb)) if tb is not None else ""
        except Exception:
            return message, extra
        msg = "%s\n[异常] %s" % (message, head)
        ex = dict(extra) if isinstance(extra, dict) else {}
        ex["traceback"] = stack or head
        return msg, ex
        # v4.134.5：**移除**「兼容旧 debug.log」的文本双写。
        # 病根：这条双写自 v1.1 起就没有任何读取方 —— 全仓唯一读 `debug.log` 的
        #   diagnostic_export 读的是 APP_DIR 下那份（即 dist 里由 config.py 的
        #   logging.basicConfig 写的那份），与这里 `os.path.dirname(self.db_path)`
        #   指向的用户数据目录那份**不是同一个文件**。
        #   于是它成了「无人消费却无限增长」的纯负担：2026-07-22 ~ 09-10 七周
        #   堆到 246KB / 4394 行，且被测试子进程反复追加（污染真实数据目录的写点之一）。
        # SQLite（agent_log.db）本身已含 timestamp/level/module/message，
        #   文本副本零信息增量，纯属历史遗留。

    # v4.257.0（P0-5）：五个级别**全族**都接 exc_info —— 只修 error 会在
    # warning/critical 上留下同一个坑（工具失败不止走 error 一条路）。
    def debug(self, message, module=None, extra=None, session_id=None,
              exc_info=False):
        self._log("DEBUG", message, module, extra, session_id, exc_info)

    def info(self, message, module=None, extra=None, session_id=None,
             exc_info=False):
        self._log("INFO", message, module, extra, session_id, exc_info)

    def warning(self, message, module=None, extra=None, session_id=None,
                exc_info=False):
        self._log("WARNING", message, module, extra, session_id, exc_info)

    def error(self, message, module=None, extra=None, session_id=None,
              exc_info=False):
        self._log("ERROR", message, module, extra, session_id, exc_info)

    def critical(self, message, module=None, extra=None, session_id=None,
                 exc_info=False):
        self._log("CRITICAL", message, module, extra, session_id, exc_info)

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
