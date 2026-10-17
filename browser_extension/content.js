// content.js — 在页面里提取可读正文 / 选中文字
// L1.9 (2026-09-21) 修复清单：
//   D1 游离节点 innerText 退化 → 新增 toPlainText()，段落不再粘连
//   D2 findContentRoot 只会上爬 → 改为「先向下收敛包裹层，再受限向上扩张」
//   D3 外层包裹 div 必胜 → 收敛步剥离持有 ≥90% 文本的唯一子节点
//   D4 6 万字静默截断 → 上限提到 20 万，并回传 truncated / warnings
//   D5 cleanText 吃掉空行 → 只归一化空格与行尾空白，保留空行
//   D7 removeJunk 误删长文本 header/footer/aside → 加「长文本+标点」白名单保护
// v1.5（2026-10-17）智能抓取四招：
//   S1 抓取前唤醒：滚到底再回顶 + 点「继续阅读/展开全文」类按钮 + 沉降等待
//      （治懒加载/点击展开型壳页；仅在用户点抓取时触发，不后台自动跑）
//   S2 短正文延迟重抽：<100 字等 1.5s 再抽（v1.4 已有，保留）
//   S3 结构化兜底擂台：JSON-LD articleBody（权威）> og:description（摘要）>
//      同源 iframe 正文（跨域摸不到，跳过）；启发式结果 <100 字时启用
//   S4 shadow DOM 穿透：querySelectorAll 不过 shadow 边界，深搜补齐候选池
//      （注意 cloneNode 不含 shadowRoot，shadow 候选只能从 live DOM 取、
//       且不得对 live 节点做 removeJunk 式Mutation——只读）
(function () {
  "use strict";

  var MAXLEN = 200000;

  function textLen(n) {
    if (!n) return 0;
    return (n.textContent || "").replace(/\s/g, "").length;
  }

  function getSelectionText() {
    var sel = (window.getSelection && window.getSelection().toString()) || "";
    return sel.trim();
  }

  // 硬垃圾：删掉一定与正文无关
  // v4.161 D8：评论区 / 热搜 / 推荐 / 互动栏 / 站内广告
  var JUNK_EXTRA_GUARD = ".Comments-container,[class*='Comments-'],[class*='CommentList']," +
    "[class*='HotSearch'],[class*='Recommendations'],[class*='CornerButtons']," +
    "[class*='Post-SideActions'],[class*='Advertisement'],[class*='Advertisement'],[class*='advert']";
  var JUNK_SEL = "script,style,noscript,nav,form,button,input,select,textarea," +
    "svg,iframe,[aria-hidden='true']," + JUNK_EXTRA_GUARD;
  // 软垃圾：可能是正文容器，需按内容特征判断
  var SOFT_JUNK_SEL = "header,footer,aside";

  // 像正文吗？长文本 + 有标点 → 不动它
  function looksLikeContent(el) {
    if (!el || !el.textContent) return false;
    var t = el.textContent.trim();
    if (t.length < 150) return false;
    var punct = (t.match(/[。.，,！!？?；;：:、]/g) || []).length;
    return punct >= 5;
  }

  function removeJunk(root) {
    var junk = root.querySelectorAll(JUNK_SEL);
    for (var i = 0; i < junk.length; i++) {
      if (junk[i].parentNode) junk[i].parentNode.removeChild(junk[i]);
    }
    var soft = root.querySelectorAll(SOFT_JUNK_SEL);
    for (var j = 0; j < soft.length; j++) {
      if (looksLikeContent(soft[j])) continue;   // D7 白名单保护
      if (soft[j].parentNode) soft[j].parentNode.removeChild(soft[j]);
    }
  }

  // D5 修正：不再把 \n\n 压成 \n
  function cleanText(s) {
    s = (s || "").replace(/\r/g, "");
    s = s.replace(/\u00a0/g, " ");
    s = s.replace(/[\u200b\u200c\u200d\ufeff\u2060]/g, "");
    s = s.replace(/[ \t]+/g, " ");
    s = s.replace(/[ \t]+\n/g, "\n");
    s = s.replace(/\n[ \t]+/g, "\n");
    s = s.replace(/\n{3,}/g, "\n\n");
    return s.trim();
  }

  // ---- D1 新增：纯文本序列化，块级元素补换行，绕开游离节点 innerText 退化 ----
  var BLOCK_TAGS = ",p,div,section,article,main,ul,ol,li,tr,table,blockquote," +
    "pre,figure,figcaption,h1,h2,h3,h4,h5,h6,dd,dt,dl,hr,";

  function toPlainText(node) {
    if (!node) return "";
    var tag = (node.nodeName || "").toLowerCase();
    if (tag === "#text") return node.nodeValue || "";
    // 只跳过"一定不是正文"的。nav/header/footer/aside 不在此列：
    // removeJunk 已按白名单筛过，此处若再跳过，被保住的正文会被二次丢弃。
    if (tag === "script" || tag === "style" || tag === "noscript" ||
        tag === "form" || tag === "button" || tag === "textarea" || tag === "select" ||
        tag === "svg" || tag === "iframe") {
      return "";
    }
    if (tag === "br") return "\n";
    if (tag === "img") return "";
    var children = node.childNodes;
    var inner = "";
    for (var i = 0; i < children.length; i++) inner += toPlainText(children[i]);
    if (tag === "li") return "\n- " + inner.trim();
    if (BLOCK_TAGS.indexOf("," + tag + ",") >= 0) {
      var t = inner.trim();
      return t ? "\n\n" + t + "\n\n" : "";
    }
    return inner;
  }

  // ---- L1.4 toMarkdown ----
  function toMarkdown(node) {
    if (!node) return "";
    var tag = (node.nodeName || "").toLowerCase();
    if (tag === "#text") return node.nodeValue || "";
    // 同上：nav/header/footer/aside 交给 removeJunk 判断，这里不重复丢弃
    if (tag === "script" || tag === "style" || tag === "noscript" ||
        tag === "form" || tag === "button" || tag === "textarea" || tag === "select" ||
        tag === "svg" || tag === "iframe") {
      return "";
    }
    if (tag === "br") return "\n";
    if (tag === "img") {
      var alt = node.getAttribute("alt") || "";
      return alt ? "[" + alt + "]" : "";
    }
    var children = node.childNodes;
    var inner = "";
    for (var i = 0; i < children.length; i++) {
      inner += toMarkdown(children[i]);
    }
    if (tag === "a") {
      var href = node.getAttribute("href") || "";
      if (href && href.indexOf("#") !== 0 && /^https?:/i.test(href)) {
        return "[" + inner.trim() + "](" + href + ")";
      }
      return inner;
    }
    if (tag === "strong" || tag === "b") return "**" + inner.trim() + "**";
    if (tag === "em" || tag === "i") return "*" + inner.trim() + "*";
    if (tag === "code") return "`" + inner.trim() + "`";
    if (tag === "td" || tag === "th") return inner.trim() + " | ";
    if (tag === "h1") return "\n# " + inner.trim() + "\n";
    if (tag === "h2") return "\n## " + inner.trim() + "\n";
    if (tag === "h3") return "\n### " + inner.trim() + "\n";
    if (tag === "h4") return "\n#### " + inner.trim() + "\n";
    if (tag === "h5") return "\n##### " + inner.trim() + "\n";
    if (tag === "h6") return "\n###### " + inner.trim() + "\n";
    if (tag === "blockquote") return "\n> " + inner.trim().replace(/\n/g, "\n> ") + "\n";
    if (tag === "pre") return "\n```\n" + (node.textContent || "").trim() + "\n```\n";
    if (tag === "li") return "\n- " + inner.trim();
    if (tag === "p" || tag === "div" || tag === "section" || tag === "article" ||
        tag === "main" || tag === "ul" || tag === "ol" || tag === "tr" ||
        tag === "table" || tag === "figure") {
      return "\n" + inner.trim() + "\n";
    }
    return inner;
  }

  function postProcessMarkdown(md) {
    md = (md || "").replace(/\r/g, "");
    md = md.replace(/\u00a0/g, " ");
    md = md.replace(/[\u200b\u200c\u200d\ufeff\u2060]/g, "");
    md = md.replace(/[ \t]+\n/g, "\n");
    md = md.replace(/\n{3,}/g, "\n\n");
    return md.trim();
  }

  function getMeta() {
    var siteName = "";
    var og = document.querySelector("meta[property='og:site_name']");
    if (og && og.getAttribute("content")) siteName = og.getAttribute("content").trim();
    if (!siteName) siteName = location.hostname || "";
    return {
      siteName: siteName,
      capturedAt: new Date().toISOString(),
      wordCount: 0
    };
  }

  // ============================================================
  // v1.5 S4：shadow DOM 深搜 —— querySelectorAll 不过 shadow 边界
  // ============================================================
  function deepQuerySelectorAll(root, sel) {
    var res = [];
    if (!root || !root.querySelectorAll) return res;
    var list = root.querySelectorAll(sel);
    for (var i = 0; i < list.length; i++) res.push(list[i]);
    var all = root.querySelectorAll("*");
    for (var j = 0; j < all.length; j++) {
      var sr = all[j].shadowRoot;
      if (sr) {
        var sub = deepQuerySelectorAll(sr, sel);
        for (var k = 0; k < sub.length; k++) res.push(sub[k]);
      }
    }
    return res;
  }

  // ============================================================
  // v1.5 S1：抓取前唤醒页面 —— 滚动 + 点展开 + 沉降
  // 只在用户点抓取时跑（extract 消息触发），不后台自动执行。
  // 副作用说明：视口位置会还原；点「展开」类按钮会改变页面展开状态
  // （本就是用户要读的内容，视为可接受）。
  // ============================================================
  function sleep(ms) {
    return new Promise(function (res) { setTimeout(res, ms); });
  }

  // 白名单精确匹配（短文案才点，避免误点导航/广告；不点「加载更多」类列表按钮）
  var EXPAND_TEXTS = ["继续阅读", "阅读全文", "展开全文", "查看全文", "展开全文"];
  var EXPAND_MAX = 3;

  function clickExpanders() {
    var clicked = 0;
    var els = deepQuerySelectorAll(document, "a,button,[role='button']");
    for (var i = 0; i < els.length && clicked < EXPAND_MAX; i++) {
      var el = els[i];
      var t = (el.textContent || "").trim();
      if (!t || t.length > 10) continue;
      var hit = false;
      for (var j = 0; j < EXPAND_TEXTS.length; j++) {
        if (t === EXPAND_TEXTS[j]) { hit = true; break; }
      }
      if (!hit) continue;
      try {
        var r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) continue;   // 不可见不点
        el.click();
        clicked++;
      } catch (e) { /* 忽略单个点击失败 */ }
    }
    return clicked;
  }

  async function wakePage() {
    try {
      var startY = window.scrollY || 0;
      var h = document.documentElement;
      window.scrollTo(0, h.scrollHeight);      // 滚到底触发懒加载
      await sleep(600);
      clickExpanders();                        // 点「继续阅读」类
      await sleep(400);
      window.scrollTo(0, startY);              // 还原视口
      await sleep(200);
    } catch (e) { /* 唤醒失败不阻断抓取，照常抽 */ }
  }

  // ============================================================
  // v1.5 S3：结构化兜底 —— 站点自报正文/摘要，权威度高于启发式
  // ============================================================
  function fromJsonLd() {
    var scripts = document.querySelectorAll('script[type="application/ld+json"]');
    for (var i = 0; i < scripts.length; i++) {
      try {
        var data = JSON.parse(scripts[i].textContent);
        var items = Array.isArray(data) ? data.slice() : [data];
        for (var g = 0; g < items.length; g++) {
          if (items[g] && items[g]["@graph"] && Array.isArray(items[g]["@graph"])) {
            items = items.concat(items[g]["@graph"]);
          }
        }
        for (var k = 0; k < items.length; k++) {
          var o = items[k];
          if (!o) continue;
          var body = o.articleBody || o.text || o.description;
          if (body && String(body).replace(/\s/g, "").length >= 100) {
            return String(body);
          }
        }
      } catch (e) { /* 坏 JSON 跳过 */ }
    }
    return "";
  }

  function fromOgDescription() {
    var m = document.querySelector('meta[property="og:description"],meta[name="description"]');
    if (m) {
      var c = (m.getAttribute("content") || "").trim();
      if (c.replace(/\s/g, "").length >= 100) return c;
    }
    return "";
  }

  function fromSameOriginIframes() {
    var out = [];
    var iframes = document.querySelectorAll("iframe");
    for (var i = 0; i < iframes.length; i++) {
      var doc;
      try { doc = iframes[i].contentDocument; } catch (e) { continue; }  // 跨域 → 跳过
      if (!doc || !doc.body) continue;
      var t = (doc.body.textContent || "").trim();
      if (t.replace(/\s/g, "").length >= 200) out.push(t);
    }
    return out.join("\n\n");
  }

  // 分级：JSON-LD 权威 > og 摘要 > 同源 iframe
  function fallbackBody() {
    var jl = fromJsonLd();
    if (jl) return { text: jl, src: "JSON-LD articleBody" };
    var og = fromOgDescription();
    if (og) return { text: og, src: "og:description" };
    var ifr = fromSameOriginIframes();
    if (ifr) return { text: ifr, src: "同源 iframe" };
    return null;
  }

  // ---- D2/D3 修正：先向下收敛包裹层，再受限向上扩张合并兄弟 ----
  // v4.161 D8：站点级正文选择器 —— 命中即锁定，禁止上爬
  // 实测：知乎 .RichText=3781字(纯正文)，而 article=3987字，
  // 评论区 .Comments-container=699字 与热搜榜都在 article 之外 —— 旧版上爬爬到公共祖先，全吞
  var PRIORITY_SEL = [".RichText", ".Post-RichTextContainer", "#js_content",
    "[itemprop='articleBody']", ".rich_media_content", "article",
    ".article-content", ".post-content", ".entry-content"];

  function pickPriority() {
    for (var i = 0; i < PRIORITY_SEL.length; i++) {
      var els = deepQuerySelectorAll(document, PRIORITY_SEL[i]);  // v1.5 S4：穿透 shadow
      for (var j = 0; j < els.length; j++) {
        var el = els[j], t = textLen(el);
        if (t < 250) continue;                       // 太短不像正文
        var links = el.querySelectorAll("a"), linkLen = 0;
        for (var k = 0; k < links.length; k++) linkLen += textLen(links[k]);
        if (linkLen > t * 0.5) continue;             // 链接密度过高 = 导航/列表
        return el;                                   // 命中最高优先级即返回
      }
    }
    return null;
  }

  function findContentRoot(best) {
    var cur = best;
    var pri = pickPriority();
    if (pri) cur = pri;

    // ① 向下收敛：若某父节点文本有 ≥90% 集中在唯一一个子节点里，那它只是包裹层
    for (var s = 0; s < 4; s++) {
      var curLen = textLen(cur);
      if (!curLen || !cur.children || !cur.children.length) break;
      var only = null, hit = 0;
      for (var i = 0; i < cur.children.length; i++) {
        if (textLen(cur.children[i]) >= curLen * 0.9) { only = cur.children[i]; hit++; }
      }
      if (hit === 1 && only) { cur = only; } else { break; }
    }

    if (pri) return cur;   // v4.161 D8：站点级选择器命中 → 不许再上爬

    // ② 向上扩张：只在「父节点体量不爆炸」的前提下上爬，避免把整页吞进来
    var base = textLen(cur);
    var up = cur;
    for (var d = 0; d < 4; d++) {
      var parent = up.parentElement;
      if (!parent) break;
      var pLen = textLen(parent);
      if (base > 0 && pLen > base * 1.25) break;  // v4.161 收紧：新增文本超 25% → 停手
      if (parent.querySelector(JUNK_EXTRA_GUARD)) break;   // 父节点已含评论/热搜 → 停手
      up = parent;
      var blocks = up.querySelectorAll("p,li,blockquote,pre,h1,h2,h3,h4,h5,h6");
      if (blocks.length >= 3) break;
    }
    return up;
  }

  function extractArticle() {
    var clone = document.cloneNode(true);
    removeJunk(clone);
    // v1.5 S4：候选池 = clone  light DOM（已清垃圾）+ live DOM shadow 深搜
    // （cloneNode 不含 shadowRoot，shadow 候选只能从 live 取；live 节点只读，
    //  下面的容器二清不得 Mutation 它们）
    var pool = [];
    var light = clone.querySelectorAll("p,div,article,section,li,td,pre,blockquote");
    for (var li = 0; li < light.length; li++) pool.push({ el: light[li], live: false });
    var shadow = deepQuerySelectorAll(document, "p,div,article,section,li,td,pre,blockquote");
    for (var si = 0; si < shadow.length; si++) {
      if (clone.contains(shadow[si])) continue;   // 已在 light 池
      pool.push({ el: shadow[si], live: true });
    }
    var best = null, bestLive = false, bestScore = 0;
    for (var c = 0; c < pool.length; c++) {
      var node = pool[c].el;
      var txt = (node.textContent || "").trim();   // 统一用 textContent，游离节点 innerText 不可靠
      if (txt.length < 25) continue;
      var score = txt.length;
      var links = node.querySelectorAll("a");
      var linkChars = 0;
      for (var l = 0; l < links.length; l++) linkChars += (links[l].textContent || "").length;
      var ratio = txt.length ? linkChars / txt.length : 1;
      if (ratio > 0.5) score *= 0.3;
      else if (ratio > 0.3) score *= 0.7;
      var punct = (txt.match(/[。.，,！!？?；;：:、]/g) || []).length;
      score += punct * 10;
      if (score > bestScore) { bestScore = score; best = node; bestLive = pool[c].live; }
    }

    var root, rootLive = false;
    if (best) {
      root = findContentRoot(best);
      rootLive = bestLive;   // findContentRoot 上爬可能换容器，保守仍按 live 处理
    } else {
      root = clone.body || clone;
    }

    // 容器内再清一遍导航/广告类（同样走白名单保护）—— live 节点只读，跳过
    if (!rootLive) {
      var sub = root.querySelectorAll(
        "nav,aside,header,footer,form,.ad,.ads,[role='navigation'],[role='complementary']");
      for (var s = 0; s < sub.length; s++) {
        var e = sub[s];
        if (e === root) continue;
        if (looksLikeContent(e)) continue;
        if (e.parentNode) e.parentNode.removeChild(e);
      }
    }

    var md = postProcessMarkdown(toMarkdown(root));
    var plain = cleanText(toPlainText(root));

    // v1.5 S3：启发式结果过短（<100 字）→ 结构化兜底擂台
    var fallbackUsed = "";
    if (plain.replace(/\s/g, "").length < 100) {
      var fb = fallbackBody();
      if (fb) {
        var fbClean = cleanText(fb.text);
        if (fbClean.replace(/\s/g, "").length > plain.replace(/\s/g, "").length) {
          plain = fbClean;
          md = postProcessMarkdown(fb.text);
          fallbackUsed = fb.src;
        }
      }
    }

    var warnings = [];
    if (fallbackUsed) warnings.push("启发式抽取过短，已启用结构化兜底（" + fallbackUsed + "）");
    var truncated = false;
    if (md.length > MAXLEN) { md = md.slice(0, MAXLEN); truncated = true; }
    if (plain.length > MAXLEN) { plain = plain.slice(0, MAXLEN); truncated = true; }
    if (truncated) warnings.push("正文超过 " + MAXLEN + " 字，已截断");

    var meta = getMeta();
    meta.wordCount = plain.replace(/\s/g, "").length;
    meta.truncated = truncated;
    meta.warnings = warnings;
    meta.mode = "article";
    meta.plainLen = plain.length;
    meta.markdownLen = md.length;

    return {
      text: plain,
      markdown: md,
      bySelection: false,
      truncated: truncated,
      warnings: warnings,
      meta: meta
    };
  }

  function extractSelection() {
    var sel = getSelectionText();
    if (!sel) {
      return extractArticle();   // 没选中则回退整页抓取
    }
    var meta = getMeta();
    meta.wordCount = sel.replace(/\s/g, "").length;
    meta.truncated = false;
    meta.warnings = [];
    meta.mode = "selection";
    return {
      text: cleanText(sel),
      markdown: postProcessMarkdown(sel),
      bySelection: true,
      truncated: false,
      warnings: [],
      meta: meta
    };
  }

  // v4.161 D9：正文结束锚点截尾
  // 实测知乎：正文止于「编辑于 2026-08-17 11:42・广东」，其后「副业/跨境」标签+维达广告+评论全是尾巴
  function trimArticleTail(s) {
    if (!s) return s;
    var lines = String(s).split("\n");
    for (var i = 0; i < lines.length; i++) {
      if (/^\s*(编辑于|发布于|修改于)\s/.test(lines[i])) {
        return lines.slice(0, i + 1).join("\n").replace(/\s+$/, "");
      }
    }
    return s;
  }

  function extractReadable(mode) {
    if (mode === "selection") return extractSelection();
    var r = extractArticle();
    if (r && r.text) r.text = trimArticleTail(r.text);
    if (r && r.markdown) r.markdown = trimArticleTail(r.markdown);
    return r;
  }

  // v1.4：短正文延迟重抽 —— 动态渲染页（客户端 JS 拉正文）首抽可能只拿到壳。
  // 实测 MSN 壳页首抽仅 25 字（只剩"为你精选更多内容"推荐流）：等 1.5s 让
  // 异步正文渲染完再抽一次，取更长结果。治不了「需点击展开 / 跨域 iframe」
  // 的页（那类只能靠选中模式），但对懒渲染新闻站有效。
  // selection 模式不重抽——选中即所读，没有"壳"的概念。
  var RETRY_MIN_LEN = 100;   // 正文低于此长度视为可疑壳页
  var RETRY_WAIT_MS = 1500;

  function bodyLen(r) {
    return r && r.text ? r.text.replace(/\s/g, "").length : 0;
  }

  chrome.runtime.onMessage.addListener(function (msg, sender, sendResponse) {
    if (msg && msg.type === "extract") {
      var mode = msg.mode || "article";
      function reply(r) {
        sendResponse({
          ok: true,
          title: document.title || "",
          url: location.href || "",
          text: r.text,
          markdown: r.markdown || r.text,
          bySelection: r.bySelection,
          truncated: !!r.truncated,
          warnings: r.warnings || [],
          meta: r.meta || {}
        });
      }
      // v1.5 S1：article 模式先唤醒页面（滚动+点展开+沉降）再首抽；
      // selection 模式不唤醒（选中即所读，无需等渲染）
      (async function () {
        var first;
        try {
          if (mode !== "selection") await wakePage();
          first = extractReadable(mode);
        } catch (e) {
          sendResponse({ ok: false, error: String(e) });
          return;
        }
        if (mode !== "selection" && bodyLen(first) < RETRY_MIN_LEN) {
          setTimeout(function () {
            try {
              var second = extractReadable("article");
              if (bodyLen(second) > bodyLen(first)) {
                (second.warnings = second.warnings || []).push(
                  "首抽正文过短（" + bodyLen(first) + " 字），已延迟重抽取更长结果");
                reply(second);
                return;
              }
            } catch (e2) { /* 重抽失败 → 落回首抽结果，不升级错误 */ }
            reply(first);
          }, RETRY_WAIT_MS);
        } else {
          reply(first);
        }
      })();
    }
    return true;
  });
})();
