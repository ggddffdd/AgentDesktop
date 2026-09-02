# -*- coding: utf-8 -*-
"""director_web.py — 导演台预览区网页渲染引擎（v4.105）。

动机：原导演台分镜/关键帧/角色/合成预览用 QLabel(QPixmap)+QGridLayout 画卡片，
视频片段只能缩略图 + 弹外部播放器，图片死小看不清人物会不会崩。改为 QWebEngineView 渲染：
- 视频片段内嵌 <video controls> 直接播放（不再跳出系统播放器）
- 图片点击灯箱放大（看清三视图/关键帧细节）
- 响应式卡片网格（CSS 圆角/阴影，比 QFrame 直角卡片现代）

复用 chat_web._ChatPage 的 console 通信通道：
    JS act(kind,idx) → console.log('__xc__kind:idx')
    → _ChatPage.javaScriptConsoleMessage → anchorActivated(kind:idx)
    → DirectorWebView.actionRequested 信号 → director_panel._on_web_action
本地资源（图片/视频）走 localres:// scheme handler，绕开 file:// 的 CORS 限制
（setHtml 页面 origin 是 about:blank，直接 file:// 会被 Chromium 拦）。

注意：register_localres_scheme() 必须在 QApplication 创建前调用（Qt 硬性要求），
已在 main.py 顶部 import 后、QApplication 构造前执行。
"""
import os
import html as _html
import urllib.parse

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QUrl, Signal
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import (
    QWebEnginePage, QWebEngineProfile, QWebEngineUrlScheme,
    QWebEngineUrlSchemeHandler, QWebEngineUrlRequestJob,
)
from chat_web import _ChatPage

_SCHEME = b"localres"
_scheme_registered = False
_handler_installed = set()


def register_localres_scheme():
    """必须在 QApplication 创建前调用（Qt 要求 scheme 注册早于 QApplication）。
    幂等：重复调用安全。"""
    global _scheme_registered
    if _scheme_registered:
        return
    try:
        s = QWebEngineUrlScheme(_SCHEME)
        s.setFlags(QWebEngineUrlScheme.Flag.Secure
                   | QWebEngineUrlScheme.Flag.CorsEnabled
                   | QWebEngineUrlScheme.Flag.ContentSecurityPolicyIgnored)
        QWebEngineUrlScheme.registerScheme(s)
    except Exception:
        pass
    _scheme_registered = True


def _localres_url(path: str) -> str:
    """本地绝对路径 → localres:///... 形式（URL 编码空格/中文）。"""
    p = path.replace("\\", "/")
    enc = urllib.parse.quote(p, safe="/:")
    return "localres:///" + enc


class _LocalResHandler(QWebEngineUrlSchemeHandler):
    """按 localres:///C:/.../x.png 读本地文件字节返回。

    reply(QIODevice) 在 Qt6 无法显式传 content-type，默认 application/octet-stream；
    Chromium 对 <video>/<img> 会按字节 magic 自动 sniff 真实类型并播放/显示，无需担心。
    device 引用由 _devs 持有至请求完成（job.destroyed 时释放），避免异步读时 GC 崩溃。
    """
    _MIME = {
        ".png": b"image/png", ".jpg": b"image/jpeg", ".jpeg": b"image/jpeg",
        ".gif": b"image/gif", ".webp": b"image/webp", ".bmp": b"image/bmp",
        ".mp4": b"video/mp4", ".webm": b"video/webm", ".mov": b"video/quicktime",
        ".m4v": b"video/mp4", ".avi": b"video/x-msvideo",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._devs = []

    def requestStarted(self, job):
        u = job.requestUrl()
        p = urllib.parse.unquote(u.path())  # /C:/Users/.../x.png
        if p.startswith("/"):
            p = p[1:]
        if not p or not os.path.isfile(p):
            try:
                job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)
            except Exception:
                pass
            return
        ext = os.path.splitext(p)[1].lower()
        mime = self._MIME.get(ext, b"application/octet-stream")
        try:
            with open(p, "rb") as f:
                data = f.read()
        except Exception:
            try:
                job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)
            except Exception:
                pass
            return
        buf = QByteArray(data)
        dev = QBuffer(buf)
        dev.open(QIODevice.ReadOnly)
        self._devs.append((dev, buf))  # buf 随 dev 存活，防 GC
        job.destroyed.connect(lambda: self._drop(dev))
        try:
            job.reply(mime, dev)
        except Exception:
            self._drop(dev)

    def _drop(self, dev):
        for t in list(self._devs):
            if t[0] is dev:
                try:
                    self._devs.remove(t)
                except Exception:
                    pass
                break


def attach_localres_handler(profile=None):
    """把 localres handler 装到指定 profile（默认 profile），幂等。"""
    profile = profile or QWebEngineProfile.defaultProfile()
    if profile in _handler_installed:
        return
    h = _LocalResHandler(profile)
    profile.installUrlSchemeHandler(_SCHEME, h)
    _handler_installed.add(profile)


_SKELETON = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
:root{--bg:__BG__;--card:__CARD__;--border:__BORDER__;--text:__TEXT__;--dim:__DIM__;--accent:__ACCENT__;}
*{box-sizing:border-box;}
html,body{margin:0;padding:0;background:var(--bg);
  font-family:"Microsoft YaHei","Segoe UI",sans-serif;color:var(--text);}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));
  gap:12px;padding:6px 2px;}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;
  overflow:hidden;display:flex;flex-direction:column;box-shadow:0 1px 2px rgba(0,0,0,.05);}
.thumb{width:100%;aspect-ratio:16/9;background:#000;display:flex;align-items:center;
  justify-content:center;cursor:zoom-in;overflow:hidden;position:relative;}
.play-overlay{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
  pointer-events:none;z-index:2;}
.play-overlay::before{content:'▶';font-size:28px;color:rgba(255,255,255,.85);
  background:rgba(0,0,0,.4);width:52px;height:52px;border-radius:50%;display:flex;
  align-items:center;justify-content:center;text-indent:4px;}
.thumb img,.thumb video{width:100%;height:100%;object-fit:contain;background:#000;display:block;cursor:pointer;}
.thumb3{height:120px;border-radius:6px;cursor:zoom-in;object-fit:cover;background:#000;}
.ph{color:var(--dim);font-size:12px;padding:8px;text-align:center;}
.info{padding:6px 8px 2px;font-size:12px;color:var(--text);word-break:break-word;}
.desc{padding:0 8px 6px;font-size:11px;color:var(--dim);}
.btns{display:flex;gap:6px;padding:6px 8px 8px;flex-wrap:wrap;}
.btns a{font-size:12px;color:var(--accent);cursor:pointer;user-select:none;
  border:1px solid var(--border);border-radius:6px;padding:2px 8px;background:var(--bg);}
.btns a:hover{background:var(--accent);color:#fff;border-color:var(--accent);}
.qc{font-size:11px;padding:0 8px 8px;}
.qc.fail{color:#d98c3f;}
.qclink{color:var(--accent);cursor:pointer;}
.empty{padding:24px;text-align:center;color:var(--dim);font-size:13px;}
/* lightbox */
#lb{position:fixed;inset:0;background:rgba(0,0,0,.88);display:none;
  align-items:center;justify-content:center;z-index:99;cursor:zoom-out;}
#lb img{max-width:92vw;max-height:92vh;border-radius:8px;}
</style></head><body>
<div class="grid" id="grid">__CARDS__</div>
<div id="lb" onclick="this.style.display='none'"><img id="lbimg" src=""></div>
<script>
function zoom(src){document.getElementById('lbimg').src=src;
  document.getElementById('lb').style.display='flex';}
function act(kind,idx){console.log('__xc__'+kind+':'+idx);}
</script>
</body></html>"""


def _esc(s):
    return _html.escape(str(s))


class DirectorWebView(QWebEngineView):
    """导演台单个预览区（角色/关键帧/分镜/合成）的网页渲染视图。"""

    actionRequested = Signal(str)

    def __init__(self, theme, on_action=None, parent=None):
        super().__init__(parent)
        self._theme = theme
        page = _ChatPage(self)
        self.setPage(page)
        attach_localres_handler(page.profile())
        if on_action is not None:
            self.actionRequested.connect(on_action)
        page.anchorActivated.connect(self.actionRequested.emit)
        self.render_cards("")

    def _skeleton(self, cards_html):
        t = {k: v for k, v in self._theme.items() if isinstance(v, str)}
        return (_SKELETON
                .replace("__BG__", t.get("bg", "#F7F8FC"))
                .replace("__CARD__", t.get("card", "#FFFFFF"))
                .replace("__BORDER__", t.get("border", "#E5E7EB"))
                .replace("__TEXT__", t.get("text", "#202124"))
                .replace("__DIM__", t.get("dim", "#5F6368"))
                .replace("__ACCENT__", t.get("accent", "#1A73E8"))
                .replace("__CARDS__", cards_html))

    def render_cards(self, cards_html):
        """整段替换网格卡片 HTML。"""
        self.setHtml(self._skeleton(cards_html))

    def render_empty(self, text="尚未生成"):
        self.render_cards(f'<div class="empty">{_esc(text)}</div>')


# ---------- 卡片模板 ----------

def clip_card_html(i, status, path=None, kf=None, error="", info_text=None):
    """分镜卡：视频内嵌播放（status=done）、排队/失败占位。idx 从 0 计。"""
    if status == "done" and path and os.path.isfile(path):
        poster = f' poster="{_localres_url(kf)}"' if (kf and os.path.isfile(kf)) else ""
        thumb = (f'<div class="thumb"><video src="{_localres_url(path)}" '
                 f'controls preload="metadata"{poster} onclick="act(\'play\',{i})"></video>'
                 f'<div class="play-overlay"></div></div>')
        info = info_text or f"镜{i+1} · ✅ 完成"
        btns = (f'<div class="btns">'
                f'<a onclick="act(\'mod\',{i})">✎改</a>'
                f'<a onclick="act(\'regen\',{i})">↻</a>'
                f'<a onclick="act(\'view\',{i})">🔍</a>'
                f'</div>')
        return f'<div class="card">{thumb}<div class="info">{_esc(info)}</div>{btns}</div>'
    if status == "fail":
        thumb = '<div class="thumb"><div class="ph">❌ 生成失败</div></div>'
        info = info_text or f"镜{i+1} · ❌ 失败"
        btns = (f'<div class="btns"><a onclick="act(\'view\',{i})">🔍看原因</a>'
                f'<a onclick="act(\'regen\',{i})">↻重生成</a></div>')
        return f'<div class="card">{thumb}<div class="info">{_esc(info)}</div>{btns}</div>'
    # queued / generating
    thumb = '<div class="thumb"><div class="ph">⏳ 排队中…</div></div>'
    info = info_text or f"镜{i+1} · ⏳ 排队中"
    return f'<div class="card">{thumb}<div class="info">{_esc(info)}</div></div>'


def keyframe_card_html(i, kf, note=""):
    """关键帧卡：首帧图片 + 质检状态；图片可点击灯箱放大。"""
    if kf and os.path.isfile(kf):
        thumb = (f'<div class="thumb"><img src="{_localres_url(kf)}" '
                 f'onclick="zoom(this.src)" alt="镜{i+1} 关键帧"></div>')
        info = f"镜{i+1} · 关键帧"
    else:
        thumb = '<div class="thumb"><div class="ph">无关键帧</div></div>'
        info = f"镜{i+1} · 生成失败"
    qc = ""
    if note:
        failed = "VERDICT: FAIL" in note.upper()
        label = "⚠️ 质检未通过" if failed else "✅ 质检通过"
        cls = "qc fail" if failed else "qc"
        qc = f'<div class="{cls}">{_esc(label)}</div>'
    return f'<div class="card">{thumb}<div class="info">{_esc(info)}</div>{qc}</div>'


def character_card_html(c):
    """角色卡：三视图横排（可灯箱）+ 名称 + 描述。"""
    name = _esc(c.get("name", "角色"))
    desc = _esc(c.get("desc", ""))
    views = c.get("views") or []
    imgs = ""
    for v in views:
        if v and os.path.isfile(v):
            imgs += (f'<img class="thumb3" src="{_localres_url(v)}" '
                     f'onclick="zoom(this.src)" alt="{_esc(name)} 视图">')
        else:
            imgs += '<div class="ph" style="height:120px;width:84px;">无</div>'
    if not imgs:
        imgs = '<div class="ph" style="height:120px;">无三视图</div>'
    return (f'<div class="card">'
            f'<div class="info"><b>{name}</b></div>'
            f'<div class="desc">{desc}</div>'
            f'<div style="display:flex;gap:6px;padding:8px;flex-wrap:wrap;">{imgs}</div>'
            f'</div>')


def merge_card_html(path, kf=None):
    """合成预览卡：成片视频内嵌播放（或首帧图）。"""
    if path and os.path.isfile(path):
        poster = f' poster="{_localres_url(kf)}"' if (kf and os.path.isfile(kf)) else ""
        thumb = (f'<div class="thumb" style="aspect-ratio:auto;">'
                 f'<video src="{_localres_url(path)}" controls preload="metadata"{poster} '
                 f'style="width:100%;max-height:360px;cursor:pointer;" onclick="act(\'play\',-1)"></video>'
                 f'<div class="play-overlay"></div></div>')
        info = "成片预览（可内嵌播放）"
        return f'<div class="card">{thumb}<div class="info">{_esc(info)}</div></div>'
    return '<div class="empty">尚未合成</div>'
