# -*- coding: utf-8 -*-
"""
剪贴板自动监听模块 v1.1
主线程 QTimer 轮询剪贴板（每 N 秒），自动分类（URL/代码/图片路径/长文本），
通过 Qt Signal 跨线程安全地把结果推到主线程，由托盘 showMessage 通知用户。

相比文档模板 v1.0 的改进：
- 用 QApplication.clipboard() + QTimer（主线程），无需 pyperclip 新依赖，线程更安全
- 通知接真实 UI 托盘（非 print TODO）
- URL 标题抓取放后台线程，不阻塞 UI；图片路径不自动烧 token，仅提示可识别

使用方式（在 ChatWindow.__init__ 中）：
    from clipboard_monitor import ClipboardMonitor
    self.clipboard_monitor = ClipboardMonitor(self.cfg)
    self.clipboard_monitor.clipboard_event.connect(self._on_clipboard_event)
    if self.cfg.get("clipboard_enabled", True):
        self.clipboard_monitor.start()
"""
import re
import threading
from urllib.request import urlopen, Request
from html.parser import HTMLParser

from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtWidgets import QApplication


URL_RE = re.compile(r'https?://[^\s<>"\']+')
IMG_RE = re.compile(r'.*\.(?:jpg|jpeg|png|gif|bmp|webp)(?:\s|$)', re.IGNORECASE)


class _TitleExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data


class ClipboardMonitor(QObject):
    """剪贴板监听（主线程 QTimer 驱动）。"""

    # 跨线程安全：后台 fetch 线程 emit 也会被 Qt 排队到主线程
    clipboard_event = Signal(dict)

    def __init__(self, cfg=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg or {}
        self.interval = max(1, int(self.cfg.get("clipboard_interval", 2)))
        self.auto_fetch = self.cfg.get("clipboard_auto_fetch", True)
        self.auto_format = self.cfg.get("clipboard_auto_format", True)
        self.last = ""
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)

    def start(self):
        self.timer.start(int(self.interval * 1000))

    def stop(self):
        self.timer.stop()

    # ---- 主线程轮询 ----
    def _tick(self):
        try:
            text = QApplication.clipboard().text()
        except Exception:
            return
        if not text or text == self.last:
            return
        self.last = text
        result = self._classify(text)
        if not result:
            return
        if result["type"] == "url" and self.auto_fetch:
            # URL 标题抓取放后台线程，避免阻塞 UI
            threading.Thread(target=self._fetch_and_emit, args=(result,), daemon=True).start()
        else:
            self.clipboard_event.emit(result)

    # ---- 分类 ----
    def _classify(self, text):
        urls = URL_RE.findall(text)
        if urls:
            return {"type": "url", "urls": urls[:3]}
        if self.auto_format and self._looks_like_code(text):
            return {
                "type": "code",
                "language": self._detect_language(text),
                "lines": len(text.splitlines()),
            }
        if IMG_RE.match(text.strip()):
            return {"type": "image_path", "path": text.strip()}
        if len(text) > 100:
            return {"type": "text", "length": len(text), "hint": "可让小臭翻译/摘要"}
        return None

    def _looks_like_code(self, text):
        code_chars = sum(1 for c in text if c in '{}[]();:=+-*/<>!&|^~')
        ratio = code_chars / max(len(text), 1)
        has_indent = '\t' in text or re.match(r'^\s+\w', text, re.MULTILINE)
        return ratio > 0.05 or has_indent

    def _detect_language(self, code):
        low = code.lower()
        indicators = {
            'python': ['#', 'import ', 'def ', 'print('],
            'javascript': ['console.', 'document.', 'function(', '=>'],
            'java': ['public class', 'system.out', 'import java.'],
            'html': ['<html', '<div', '<span', '</'],
            'css': ['{', '}', ':', '.'],
            'sql': ['select ', 'from ', 'where ', 'insert '],
            'json': ['":', '": ', '{', '}'],
        }
        for lang, kws in indicators.items():
            if any(kw in low for kw in kws):
                return lang
        return "unknown"

    # ---- 后台抓取 URL 标题 ----
    def _fetch_and_emit(self, result):
        titles = []
        for u in result["urls"]:
            titles.append(self._fetch_title(u) or u)
        self.clipboard_event.emit({
            "type": "url",
            "urls": result["urls"],
            "titles": titles,
        })

    @staticmethod
    def _fetch_title(url):
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=5) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
            parser = _TitleExtractor()
            parser.feed(html[:50000])
            title = parser.title.strip()
            return title or None
        except Exception:
            return None
