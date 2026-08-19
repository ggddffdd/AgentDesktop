"""SQLite 数据库操作模块 v1.1
预置笔记(notes)/待办(todos)/素材库(assets) 三张表，提供 CRUD 工具。
数据库落用户目录（避开重建覆盖 dist）。

使用方式：
    db = DatabaseTools()
    db.insert("notes", {"title": "测试笔记", "content": "内容"})
    results = db.query("notes", where={"title": "测试笔记"})
"""

import sqlite3
import json
from pathlib import Path
from datetime import datetime


def _user_data_dir():
    p = Path.home() / "Documents" / "AgentDesktop"
    p.mkdir(parents=True, exist_ok=True)
    return p


# 数据库 schema 版本（迁移用，避免旧库缺新字段/CHECK）
SCHEMA_VER = "2"
# assets 表最新 DDL（type 含 link，支持存网址）
ASSETS_DDL = '''
    CREATE TABLE assets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        type TEXT CHECK(type IN ('image', 'video', 'audio', 'document', 'other', 'link')),
        file_path TEXT,
        url TEXT,
        tags TEXT,
        description TEXT,
        created_at TEXT
    )
'''


class DatabaseTools:
    def __init__(self, db_path=None):
        self.db_path = db_path or str(_user_data_dir() / "xiaochou.db")
        self._init_tables()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self):
        conn = self._get_connection()
        cursor = conn.cursor()

        # 版本标记，避免重复迁移
        cursor.execute('CREATE TABLE IF NOT EXISTS _meta (k TEXT PRIMARY KEY, v TEXT)')
        cursor.execute("SELECT v FROM _meta WHERE k='schema_ver'")
        row = cursor.fetchone()
        if row is None or row[0] != SCHEMA_VER:
            self._migrate(cursor)
            cursor.execute("INSERT OR REPLACE INTO _meta (k, v) VALUES ('schema_ver', ?)",
                           (SCHEMA_VER,))

        conn.commit()
        conn.close()

    def _migrate(self, cursor):
        """一次性迁移：notes/todos 保持原样，assets 强制新 schema（保留数据）。"""
        # 笔记表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT,
                tags TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        ''')
        # 待办表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'in_progress', 'completed', 'cancelled')),
                priority TEXT DEFAULT 'medium' CHECK(priority IN ('low', 'medium', 'high', 'urgent')),
                due_date TEXT,
                created_at TEXT,
                completed_at TEXT
            )
        ''')
        # 素材库表：若已存在则温和迁移到含 link 的新 schema（保留数据）
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='assets'")
        if cursor.fetchone():
            cursor.execute("ALTER TABLE assets RENAME TO _assets_old")
            cursor.execute(ASSETS_DDL)
            cols = "id, name, type, file_path, url, tags, description, created_at"
            cursor.execute(f"INSERT INTO assets ({cols}) SELECT {cols} FROM _assets_old")
            cursor.execute("DROP TABLE _assets_old")
        else:
            cursor.execute(ASSETS_DDL)

    # 每张表的字段白名单 + 必填项（与 config.py DB_TOOL_DEFS 描述保持一致）
    _SCHEMA = {
        "notes":  {"columns": ["title", "content", "tags"], "required": ["title"]},
        "todos":  {"columns": ["title", "description", "status", "priority", "due_date"],
                   "required": ["title"]},
        "assets": {"columns": ["name", "type", "file_path", "url", "tags", "description"],
                   "required": ["name"]},
    }

    def insert(self, table, data):
        """插入记录（自动过滤未知列 + 校验必填）"""
        if table not in self._SCHEMA:
            return {"status": "error", "message": f"未知表: {table}（仅支持 notes/todos/assets）"}
        schema = self._SCHEMA[table]
        cols = schema["columns"]
        required = schema["required"]

        # 过滤未知列（避免 'no column named X'）
        data = {k: v for k, v in (data or {}).items() if k in cols}

        # 校验必填
        missing = [c for c in required if not str(data.get(c, "")).strip()]
        if missing:
            return {
                "status": "error",
                "message": f"{table} 表缺必填字段: {', '.join(missing)}。{table} 必填: {', '.join(required)}；可选: {', '.join(c for c in cols if c not in required)}",
                "missing_fields": missing,
            }

        # 默认值
        if table == "todos" and "status" not in data:
            data["status"] = "pending"
        if table == "todos" and "priority" not in data:
            data["priority"] = "medium"
        if table == "assets" and "type" not in data and "url" in data:
            data["type"] = "link"  # 有 url 没 type 默认为 link

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data["created_at"] = now
        if table == "notes":
            data["updated_at"] = now

        columns = list(data.keys())
        placeholders = ','.join(['?' for _ in columns])
        column_str = ','.join(columns)

        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            f"INSERT INTO {table} ({column_str}) VALUES ({placeholders})",
            [data[c] for c in columns])
        conn.commit()
        record_id = cursor.lastrowid
        conn.close()
        return {"status": "success", "id": record_id}

    def query(self, table, where=None, order_by=None, limit=50, offset=0):
        """查询记录（where 字段白名单过滤）"""
        if table not in self._SCHEMA:
            return {"status": "error", "message": f"未知表: {table}（仅支持 notes/todos/assets）"}
        cols = self._SCHEMA[table]["columns"]
        conn = self._get_connection()
        cursor = conn.cursor()

        query = f"SELECT * FROM {table} WHERE 1=1"
        params = []
        if where:
            for key, value in where.items():
                if key not in cols:
                    return {"status": "error",
                            "message": f"{table} 表无字段: {key}（合法: {', '.join(cols)}）"}
                query += f" AND {key} = ?"
                params.append(value)
        if order_by:
            query += f" ORDER BY {order_by}"
        query += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def update(self, table, record_id, data):
        """更新记录（自动过滤未知列）"""
        if table not in self._SCHEMA:
            return {"status": "error", "message": f"未知表: {table}（仅支持 notes/todos/assets）"}
        cols = self._SCHEMA[table]["columns"]
        # 过滤未知列 + 不允许改 id/created_at
        data = {k: v for k, v in (data or {}).items() if k in cols and k not in ("id", "created_at")}

        if not data:
            return {"status": "error", "message": "没有可更新的有效字段"}

        if table == "notes":
            data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        set_clause = ','.join([f"{key} = ?" for key in data.keys()])
        values = list(data.values())
        values.append(record_id)
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE {table} SET {set_clause} WHERE id = ?", values)
        conn.commit()
        affected = cursor.rowcount
        conn.close()
        return {"status": "success" if affected > 0 else "no_change",
                "affected": affected}

    def delete(self, table, record_id):
        """删除记录"""
        if table not in ("notes", "todos", "assets"):
            return {"status": "error", "message": f"未知表: {table}"}
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(f"DELETE FROM {table} WHERE id = ?", (record_id,))
        conn.commit()
        affected = cursor.rowcount
        conn.close()
        return {"status": "success" if affected > 0 else "not_found",
                "affected": affected}

    def search(self, table, keyword, fields=None):
        """全文搜索（模糊匹配）"""
        if table not in ("notes", "todos", "assets"):
            return {"status": "error", "message": f"未知表: {table}"}
        if not fields:
            fields = ["title", "content", "description", "tags", "name"]
        conn = self._get_connection()
        cursor = conn.cursor()
        conditions = " OR ".join([f"{field} LIKE ?" for field in fields])
        query = f"SELECT * FROM {table} WHERE {conditions}"
        params = [f"%{keyword}%" for _ in fields]
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
