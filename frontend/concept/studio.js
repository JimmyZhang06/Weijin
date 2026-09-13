const $ = (selector) => document.querySelector(selector);
function preview() {
  $("#original").hidden = false;
  $("#original").classList.add("removed");
  $("#replacement").hidden = false;
  $("#revision-actions").hidden = false;
  $("#version").textContent = "正在对照 · 尚未采用";
  $("#notice").textContent =
    "演示：修改只落在选中段落。请在正文中采用或保留原文。";
  $("#revision").scrollIntoView({ block: "center", behavior: "smooth" });
}
$("#proposal").addEventListener("click", preview);
$("#demo-send").addEventListener("click", preview);
$("#apply").addEventListener("click", () => {
  $("#original").hidden = true;
  $("#revision-actions").hidden = true;
  $("#version").textContent = "本页已采用 · 未写入数据库";
  $("#notice").textContent =
    "演示修改已采用。刷新恢复原稿，正式版本将记录为独立修订。";
});
$("#dismiss").addEventListener("click", () => {
  $("#original").hidden = false;
  $("#original").classList.remove("removed");
  $("#replacement").hidden = true;
  $("#revision-actions").hidden = true;
  $("#version").textContent = "原稿 · 示例";
  $("#notice").textContent = "已保留原文。可以继续讨论改法。";
});
$("#quote").addEventListener("click", () => {
  document.body.classList.remove("focus");
  $("#focus").textContent = "专注正文";
  $("#context-chip").textContent = "↳ 已引用示例第 4 段 · 仅演示，不发送";
  $("#instruction").focus();
});
$("#bible-toggle").addEventListener("click", () => {
  $("#bible").hidden = !$("#bible").hidden;
});
$("#focus").addEventListener("click", () => {
  const focused = document.body.classList.toggle("focus");
  $("#focus").textContent = focused ? "展开创作对话" : "专注正文";
});
