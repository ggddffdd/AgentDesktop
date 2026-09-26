// content.js — 在页面里提取可读正文 / 选中文字
// L1.9 (2026-09-21) 修复清单：
//   D1 游离节点 innerText 退化 → 新增 toPlainText()，段落不再粘连
//   D2 findContentRoot 只会上爬 → 改为「先向下收敛包裹层，再受限向上扩张」
//   D3 外层包裹 div 必胜 → 收敛步剥离持有 ≥90% 文本的唯一子节点
//   D4 6 万字静默截断 → 上限提到 20 万，并回传 truncated / warnings
//   D5 cleanText 吃掉空行 → 只归一化空格与行尾空白，保留空行
//   D7 removeJunk 误删长文本 header/footer/aside → 加「长文本+标点」白名单保护
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

  // ---- D2/D3 修正：先向下收敛包裹层，再受限向上扩张合并兄弟 ----
  // v4.161 D8：站点级正文选择器 —— 命中即锁定，禁止上爬
  // 实测：知乎 .RichText=3781字(纯正文)，而 article=3987字，
  // 评论区 .Comments-container=699字 与热搜榜都在 article 之外 —— 旧版上爬爬到公共祖先，全吞
  var PRIORITY_SEL = [".RichText", ".Post-RichTextContainer", "#js_content",
    "[itemprop='articleBody']", ".rich_media_content", "article",
    ".article-content", ".post-content", ".entry-content"];

  function pickPriority() {
    for (var i = 0; i < PRIORITY_SEL.length; i++) {
      var els = document.querySelectorAll(PRIORITY_SEL[i]);
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
    var candidates = clone.querySelectorAll("p,div,article,section,li,td,pre,blockquote");
    var best = null, bestScore = 0;
    for (var c = 0; c < candidates.length; c++) {
      var node = candidates[c];
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
      if (score > bestScore) { bestScore = score; best = node; }
    }

    var root;
    if (best) {
      root = findContentRoot(best);
    } else {
      root = clone.body || clone;
    }

    // 容器内再清一遍导航/广告类（同样走白名单保护）
    var sub = root.querySelectorAll(
      "nav,aside,header,footer,form,.ad,.ads,[role='navigation'],[role='complementary']");
    for (var s = 0; s < sub.length; s++) {
      var e = sub[s];
      if (e === root) continue;
      if (looksLikeContent(e)) continue;
      if (e.parentNode) e.parentNode.removeChild(e);
    }

    var md = postProcessMarkdown(toMarkdown(root));
    var plain = cleanText(toPlainText(root));

    var warnings = [];
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

  chrome.runtime.onMessage.addListener(function (msg, sender, sendResponse) {
    if (msg && msg.type === "extract") {
      try {
        var r = extractReadable(msg.mode || "article");
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
      } catch (e) {
        sendResponse({ ok: false, error: String(e) });
      }
    }
    return true;
  });
})();
