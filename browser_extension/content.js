// content.js — 在页面里提取可读正文 / 选中文字
// 轻量 Readability 思路：按文本密度给节点打分，取最高分区块。
(function () {
  "use strict";

  function getSelectionText() {
    var sel = (window.getSelection && window.getSelection().toString()) || "";
    return sel.trim();
  }

  function extractReadable() {
    // 直接取选中优先
    var sel = getSelectionText();
    if (sel) {
      return { text: sel, bySelection: true };
    }

    var clone = document.cloneNode(true);
    // 去掉明显非正文节点
    var junk = clone.querySelectorAll(
      "script,style,noscript,nav,footer,header,aside,form," +
      "button,input,select,textarea,svg,iframe,[aria-hidden='true']"
    );
    for (var i = 0; i < junk.length; i++) junk[i].remove();

    var candidates = clone.querySelectorAll("p,div,article,section,li,td,pre,blockquote");
    var best = null;
    var bestScore = 0;
    for (var c = 0; c < candidates.length; c++) {
      var node = candidates[c];
      var txt = (node.innerText || node.textContent || "").trim();
      if (txt.length < 25) continue;
      var score = txt.length;
      // 链接占比惩罚（导航/菜单链接多）
      var links = node.querySelectorAll("a");
      var linkChars = 0;
      for (var l = 0; l < links.length; l++) linkChars += (links[l].textContent || "").length;
      if (txt.length > 0) {
        var ratio = linkChars / txt.length;
        if (ratio > 0.5) score *= 0.3;
      }
      // 标点密度奖励（正文句子多标点）
      var punct = (txt.match(/[。.，,！!？?；;：:、]/g) || []).length;
      score += punct * 10;
      if (score > bestScore) {
        bestScore = score;
        best = node;
      }
    }

    var out;
    if (best) {
      out = (best.innerText || best.textContent || "").replace(/\s+\n/g, "\n").trim();
    } else {
      out = (document.body.innerText || "").replace(/\s+\n/g, "\n").trim();
    }
    if (out.length > 60000) out = out.slice(0, 60000);
    return { text: out, bySelection: false };
  }

  chrome.runtime.onMessage.addListener(function (msg, sender, sendResponse) {
    if (msg && msg.type === "extract") {
      try {
        var r = extractReadable();
        sendResponse({
          ok: true,
          title: document.title || "",
          url: location.href || "",
          text: r.text,
          bySelection: r.bySelection
        });
      } catch (e) {
        sendResponse({ ok: false, error: String(e) });
      }
    }
    return true; // 异步 sendResponse
  });
})();
