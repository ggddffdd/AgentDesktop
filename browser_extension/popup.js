// popup.js — 弹窗逻辑：保存 token、触发抓取、显示状态（L1 增强版）
function $(id) { return document.getElementById(id); }
const PAIR_URL = "http://127.0.0.1:9100/pair";

chrome.storage.local.get(["bridge_token", "autosend"], function (o) {
  if (o.bridge_token) $("token").value = o.bridge_token;
  if (typeof o.autosend !== "undefined") $("autosend").checked = !!o.autosend;
});

$("token").addEventListener("change", function () {
  chrome.storage.local.set({ bridge_token: $("token").value.trim() });
});
$("autosend").addEventListener("change", function () {
  chrome.storage.local.set({ autosend: $("autosend").checked });
});

function setStatus(text, kind) {
  var el = $("status");
  el.textContent = text;
  el.className = "status " + (kind || "");
}

function sendCapture(note, mode, autosend) {
  var tok = $("token").value.trim();
  if (!tok) {
    setStatus("请先点「一键配对」或填配对码", "err");
    return;
  }
  chrome.storage.local.set({ bridge_token: tok });
  setStatus("抓取中…");
  chrome.runtime.sendMessage(
    { type: "capture", note: note || "", mode: mode || "article", autosend: !!autosend },
    function (resp) {
      if (chrome.runtime.lastError) {
        setStatus("出错：" + chrome.runtime.lastError.message, "err");
        return;
      }
      if (resp && resp.ok) {
        setStatus("✅ 已发送 " + resp.chars + " 字到小臭" +
          (resp.autosend ? "（自动处理）" : "（按发送键让 AI 处理）"), "ok");
      } else {
        setStatus("⚠️ " + (resp && resp.error || "失败"), "err");
      }
    });
}

$("capture").addEventListener("click", function () {
  sendCapture($("note").value.trim(), "article", $("autosend").checked);
});
$("captureSel").addEventListener("click", function () {
  sendCapture($("note").value.trim(), "selection", $("autosend").checked);
});

// ---- L1.7 一键配对（接上 /pair）----
$("pair").addEventListener("click", async function () {
  setStatus("配对中…");
  try {
    var oldTok = $("token").value.trim();
    var headers = { "Content-Type": "application/json" };
    if (oldTok) headers["X-Bridge-Token"] = oldTok;
    var r = await fetch(PAIR_URL, {
      method: "POST",
      headers: headers,
      body: "{}"
    });
    if (r.status === 401) {
      setStatus("⚠️ 旧配对码校验失败，无法重置（先在上面填旧码再点配对）", "err");
      return;
    }
    var j = await r.json();
    if (j && j.token) {
      $("token").value = j.token;
      chrome.storage.local.set({ bridge_token: j.token });
      setStatus("✅ 配对成功，已保存配对码" + (j.reset ? "（已重置）" : ""), "ok");
    } else {
      setStatus("⚠️ 配对失败：" + JSON.stringify(j), "err");
    }
  } catch (e) {
    setStatus("⚠️ 连不上小臭（确认小臭已打开）：" + e, "err");
  }
});
