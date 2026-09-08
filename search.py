# -*- coding: utf-8 -*-
"""DeepSeek 桌面助手 — 联网搜索模块（全部为模块级函数）"""

import re
import html as html_mod
import json
import logging
import urllib.parse
import urllib.request
import urllib.error

log = logging.getLogger("dsdesktop")


def _is_english_query(query):
    """判定 query 是否为英文（v4.124.4）：纯英文/数字/标点的查询走英文优先链。

    中文默认走 ['bing', 'baidu', 'sogou', 'wikipedia'] 全中文 geo bias，
    对 'pet hair roller' 这种英文查询 100% 返回零碎中文无关页（百度百科/百度知道/抖音）——
    兄长实战印证（详见 legion_runs/manual_pet_hair_search_failed_examples.md 或
    pm 第 3 次 FAIL 的报告）。修路由是治本，比「重试 N 次」便宜得多。
    """
    s = (query or "").strip()
    if not s:
        return False
    # 全 ASCII（字母数字空格标点）→ 视为英文
    return all(ord(c) < 128 for c in s)


def provider_chain(search_provider="auto", query=""):
    """根据 search_provider 配置 + query 语言返回搜索后端优先级列表（v4.124.4）。

    策略：
    - 显式 provider（如 "bing"）→ 严格按用户配置走，无脑分流
    - auto + 英文 query → 英文优先链：Brave(无)/Serper(无)→ DuckDuckGo(en)→ Bing(en)→ Wikipedia(en)
    - auto + 中文/混合 query → 中文默认链：Brave → Serper → Baidu → Bing → Sogou → Wikipedia(zh)
    """
    if search_provider != "auto":
        if search_provider == "baidu":
            return ["baidu"]
        if search_provider == "bing":
            return ["bing"]
        if search_provider == "sogou":
            return ["sogou"]
        if search_provider == "baidu_sogou":
            return ["baidu", "sogou"]
        if search_provider == "wikipedia":
            return ["wikipedia"]
        if search_provider == "duckduckgo":
            return ["duckduckgo"]
        if search_provider == "bing_en":
            return ["bing_en"]
        if search_provider == "wikipedia_en":
            return ["wikipedia_en"]
        if search_provider == "duckduckgo_en":
            return ["duckduckgo_en"]
        return ["baidu", "bing", "sogou", "wikipedia"]

    # auto 模式：按 query 语言分流
    if _is_english_query(query):
        # 英文优先：把已经配过 Brave/Serper 的留口子（main 路径优先级
        # 在 tools.py:tool_web_search 处理，这里只列免费/无 key 后端）
        return ["duckduckgo_en", "bing_en", "wikipedia_en"]
    return ["bing", "baidu", "sogou", "wikipedia"]


def search_serpapi(query, api_key, search_top_k=5):
    """付费搜索兜底：SerpAPI（稳定、抗反爬，对小红书/Bing/Google 都返回结构化结果）。

    返回与 parse_search 一致的结构化列表；失败返回空列表（调用方回落免费引擎）。
    """
    if not api_key:
        return []
    try:
        from urllib.parse import urlencode
        params = {
            "engine": "google",
            "q": query,
            "num": search_top_k,
            "hl": "zh-cn",
            "gl": "cn",
            "api_key": api_key,
        }
        url = "https://serpapi.com/search.json?" + urlencode(params)
        req = urllib.request.Request(url, headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
        })
        with urllib.request.urlopen(req, timeout=20) as resp:
            obj = json.loads(resp.read().decode("utf-8", "ignore"))
        out = []
        for r in obj.get("organic_results", [])[:search_top_k]:
            out.append({
                "title": r.get("title", ""),
                "url": r.get("link", ""),
                "snippet": re.sub(r"<[^>]+>", "", r.get("snippet", "")).strip(),
            })
        return out
    except Exception as e:
        log.warning("SerpAPI 搜索失败（回落免费引擎）: %s", e)
        return []


def search_serper(query, api_key, search_top_k=10):
    """主搜索路径（2026-07-28 起）：Serper.dev —— Google 结果 API。

    稳定抗反爬、中文源覆盖好（知乎/公众号/行业报告都能出），近乎免费（~$1/2500 次）。
    返回与 parse_search 一致的结构化列表；失败返回空列表（调用方回落免费刮搜引擎）。
    """
    if not api_key:
        return []
    try:
        url = "https://google.serper.dev/search"
        payload = json.dumps({
            "q": query,
            "gl": "cn",                       # 地区：中国
            "hl": "zh-cn",                    # 语言：中文
            "num": max(int(search_top_k), 10),
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("X-API-KEY", api_key)
        with urllib.request.urlopen(req, timeout=15) as resp:
            obj = json.loads(resp.read().decode("utf-8", "ignore"))
        out = []
        for r in (obj.get("organic") or [])[:search_top_k]:
            out.append({
                "title": r.get("title", ""),
                "url": r.get("link", ""),
                "snippet": re.sub(r"<[^>]+>", "", r.get("snippet", "")).strip(),
            })
        return out
    except Exception as e:
        log.warning("Serper 搜索失败（回落免费引擎）: %s", e)
        return []


def search_brave(query, api_key, search_top_k=10):
    """主搜索路径备选（2026-07-28 起）：Brave Search API。

    只需邮箱注册、免费 2000 次/月、稳定 JSON、零反爬、无需手机验证
    （避开 SerpApi/Serper 的短信/注册坑）。中文覆盖中上，做国内平台调研够用。
    返回与 parse_search 一致的结构化列表；失败返回空列表（调用方回落）。
    """
    if not api_key:
        return []
    try:
        url = "https://api.search.brave.com/res/v1/web/search"
        params = urllib.parse.urlencode({
            "q": query,
            "country": "cn",                 # 地区：中国
            "search_lang": "zh-hans",        # 语言：简体中文
            "count": max(int(search_top_k), 10),
            "safesearch": "moderate",
        })
        req = urllib.request.Request(url + "?" + params, method="GET")
        req.add_header("Accept", "application/json")
        req.add_header("X-Subscription-Token", api_key)
        with urllib.request.urlopen(req, timeout=15) as resp:
            obj = json.loads(resp.read().decode("utf-8", "ignore"))
        out = []
        for r in (obj.get("web", {}).get("results") or [])[:search_top_k]:
            out.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": re.sub(r"<[^>]+>", "", r.get("description", "")).strip(),
            })
        return out
    except Exception as e:
        log.warning("Brave 搜索失败（回落免费引擎）: %s", e)
        return []


def search_ddg(query, search_top_k=10, lang="zh"):
    """零注册主搜索路径（2026-07-29 起）：DuckDuckGo HTML 端点。

    v4.124.4 起加 lang 参数：英文 query 时传 "en" → Accept-Language 强制英文，
    避免此前无条件 zh-CN 让英文 query 返回"中文站翻译/对接到百度"的无关页。
    完全免 key、免注册；POST html.duckduckgo.com/html 取结果，用 _parse_ddg 解析。
    失败返回空列表（调用方回落免费刮搜链）。
    """
    try:
        url = "https://html.duckduckgo.com/html/"
        data = urllib.parse.urlencode({"q": query}).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.add_header("User-Agent", ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                                       "Chrome/124.0.0.0 Safari/537.36"))
        if lang == "en":
            req.add_header("Accept-Language", "en-US,en;q=0.9")
            # DDG 英文端点（html 版原本就英文，无 geo bias）
        else:
            req.add_header("Accept-Language", "zh-CN,zh;q=0.9")
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read().decode("utf-8", "ignore")
        return _parse_ddg(raw, search_top_k)
    except Exception as e:
        log.warning("DuckDuckGo 搜索失败（回落免费引擎）: %s", e)
        return []


def search_url(provider, text, search_top_k=5):
    """根据搜索后端构建搜索 URL（v4.124.4 增英文分支）。"""
    q = urllib.parse.quote(text)
    if provider == "duckduckgo" or provider == "duckduckgo_en":
        return "https://html.duckduckgo.com/html/?q=" + q
    if provider == "bing_en":
        # cc.bing.com + ?setLang=en-US 让英文 query 出英文结果（无 geo bias）
        return "https://www.bing.com/search?setLang=en-US&q=" + q
    if provider == "wikipedia_en":
        # 英文维基 API（en.wikipedia.org）— 维基是英文 query 最后的精确参考
        return ("https://en.wikipedia.org/w/api.php?action=query&list=search"
                "&srsearch=" + q + "&format=json&srlimit=" + str(search_top_k))
    if provider == "baidu":
        return "https://www.baidu.com/s?wd=" + q
    if provider == "bing":
        return "https://www.bing.com/search?q=" + q
    if provider == "sogou":
        return "https://www.sogou.com/web?query=" + q
    return ("https://zh.wikipedia.org/w/api.php?action=query&list=search"
            "&srsearch=" + q + "&format=json&srlimit=" + str(search_top_k))


def parse_search(raw, provider, search_top_k=5):
    """解析搜索结果页原始 HTML/JSON；raw 为空直接返回空列表。"""
    if not raw:
        return []
    if provider == "duckduckgo" or provider == "duckduckgo_en":
        return _parse_ddg(raw, search_top_k)
    if provider == "baidu":
        return _parse_baidu(raw, search_top_k)
    if provider == "bing" or provider == "bing_en":
        # bing_en 用同一页面格式（同 _parse_bing 解析），只是 URL 里 setLang=en-US
        return _parse_bing(raw, search_top_k)
    if provider == "wikipedia":
        return _parse_wiki(raw, search_top_k)
    if provider == "wikipedia_en":
        # v4.124.4：英文维基 URL 重写到 en.wikipedia.org
        results = _parse_wiki(raw, search_top_k)
        for r in results:
            r["url"] = r["url"].replace("zh.wikipedia.org", "en.wikipedia.org")
        return results
    if provider == "sogou":
        return _parse_sogou(raw, search_top_k)
    return _parse_wiki(raw, search_top_k)


def _parse_ddg(html_text, top_k):
    titles = re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                        html_text, re.S)
    snippets = re.findall(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', html_text, re.S)
    out = []
    for i, (href, title) in enumerate(titles[:top_k]):
        url = _ddg_real_url(href)
        snippet = re.sub(r'<[^>]+>', '', snippets[i]) if i < len(snippets) else ""
        snippet = html_mod.unescape(snippet).strip()
        title = html_mod.unescape(re.sub(r'<[^>]+>', '', title)).strip()
        if url and title:
            out.append({"title": title, "url": url, "snippet": snippet})
    return out


def _ddg_real_url(href):
    m = re.search(r'uddg=([^&]+)', href)
    if m:
        return urllib.parse.unquote(m.group(1))
    if href.startswith("//"):
        return "https:" + href
    return href


def _parse_wiki(raw, top_k):
    try:
        obj = json.loads(raw)
        items = obj.get("query", {}).get("search", [])
    except Exception:
        return []
    out = []
    for it in items[:top_k]:
        title = it.get("title", "")
        snippet = html_mod.unescape(re.sub(r'<[^>]+>', '', it.get("snippet", ""))).strip()
        url = "https://zh.wikipedia.org/wiki/" + urllib.parse.quote(title)
        if title:
            out.append({"title": title, "url": url, "snippet": snippet})
    return out


def _parse_baidu(html_text, top_k):
    """解析百度搜索结果页（免费、无需 key）。"""
    out = []
    parts = re.split(r'<h3 class="t">', html_text)
    for part in parts[1:top_k + 1]:
        m = re.search(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', part, re.S)
        if not m:
            continue
        href, title = m.group(1), m.group(2)
        title = html_mod.unescape(re.sub(r'<[^>]+>', '', title)).strip()
        sm = re.search(r'<div class="c-abstract[^"]*">(.*?)</div>', part, re.S)
        snippet = html_mod.unescape(re.sub(r'<[^>]+>', '', sm.group(1))).strip() if sm else ""
        url = href
        if url and title:
            out.append({"title": title, "url": url, "snippet": snippet})
    if out:
        return out
    # Fallback
    for m in re.finditer(r'<h3[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>\s*</h3>', html_text, re.S):
        href, title = m.group(1), m.group(2)
        title = html_mod.unescape(re.sub(r'<[^>]+>', '', title)).strip()
        if not href or not title:
            continue
        tail = html_text[m.end():m.end() + 1500]
        sm = re.search(r'<div[^>]*class="[^"]*abstract[^"]*"[^>]*>(.*?)</div>', tail, re.S)
        snippet = html_mod.unescape(re.sub(r'<[^>]+>', '', sm.group(1))).strip() if sm else ""
        out.append({"title": title, "url": href, "snippet": snippet})
        if len(out) >= top_k:
            break
    return out


def _parse_bing(html_text, top_k):
    """解析 Bing 搜索结果页。"""
    out = []
    for li in re.findall(r'<li class="b_algo"[^>]*>(.*?)</li>', html_text, re.S)[:top_k]:
        m = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>\s*</h2>', li, re.S)
        if not m:
            continue
        href, title = m.group(1), m.group(2)
        title = html_mod.unescape(re.sub(r'<[^>]+>', '', title)).strip()
        sm = re.search(r'<p[^>]*>(.*?)</p>', li, re.S)
        snippet = html_mod.unescape(re.sub(r'<[^>]+>', '', sm.group(1))).strip() if sm else ""
        if href and title:
            out.append({"title": title, "url": href, "snippet": snippet})
    return out


def _parse_sogou(html_text, top_k):
    """解析搜狗搜索结果页（免费、无需 key）。"""
    out = []
    parts = re.split(r'<h3[^>]*>', html_text)
    for part in parts[1:top_k + 1]:
        m = re.search(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', part, re.S)
        if not m:
            continue
        href, title = m.group(1), m.group(2)
        title = html_mod.unescape(re.sub(r'<[^>]+>', '', title)).strip()
        sm = (re.search(r'<div class="[^"]*text-layout[^"]*">(.*?)</div>', part, re.S)
              or re.search(r'<div class="[^"]*fz-mid[^"]*">(.*?)</div>', part, re.S))
        snippet = html_mod.unescape(re.sub(r'<[^>]+>', '', sm.group(1))).strip() if sm else ""
        url = href
        if url and title:
            out.append({"title": title, "url": url, "snippet": snippet})
    if out:
        return out
    # Fallback
    for m in re.finditer(r'<h3[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>\s*</h3>', html_text, re.S):
        href, title = m.group(1), m.group(2)
        title = html_mod.unescape(re.sub(r'<[^>]+>', '', title)).strip()
        if not href or not title:
            continue
        tail = html_text[m.end():m.end() + 1500]
        sm = re.search(r'<p[^>]*>(.*?)</p>', tail, re.S)
        snippet = html_mod.unescape(re.sub(r'<[^>]+>', '', sm.group(1))).strip() if sm else ""
        out.append({"title": title, "url": href, "snippet": snippet})
        if len(out) >= top_k:
            break
    return out


def format_context(results):
    """将搜索结果格式化为模型上下文文本。"""
    lines = ["【联网搜索结果，仅供参考，可能含不准确信息】"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}\n   {r['snippet']}\n   来源：{r['url']}")
    lines.append("\n请根据以上资料回答用户问题；若资料与问题无关可忽略。"
                 "回答末尾用『来源：』列出你实际引用的链接。")
    return "\n".join(lines)


def http_get(url, timeout=10):
    """同步 HTTP GET，返回解码后的文本。"""
    import gzip
    req = urllib.request.Request(
        url,
        headers={
            # 必须用完整 Chrome UA，否则搜狗会 403（精简 UA 被当爬虫拦截）
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                       "image/avif,image/webp,*/*;q=0.8"),
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        enc = resp.headers.get("Content-Encoding", "")
        if "gzip" in enc:
            data = gzip.decompress(data)
        charset = resp.headers.get_content_charset() or "utf-8"
        return data.decode(charset, "ignore")


def download_bytes(url, timeout=120):
    """下载二进制（如图片）返回原始字节。"""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()
