export function createAutowrite({
  api,
  notice,
  getContext,
  openDraft,
  onRuns,
  showView,
}) {
  const $ = (s) => document.querySelector(s);
  const node = (tag, text, cls = "") => {
    const n = document.createElement(tag);
    n.textContent = text;
    n.className = cls;
    return n;
  };
  let latestRuns = [];
  let branch,
    timer,
    generation = 0,
    signature = "",
    sending = false,
    configured = false;
  const prompts = new Map();
  const labels = {
    orchestrating: "正在创作",
    paused: "已暂停后续章节",
    succeeded: "初稿已完成",
    failed: "创作中断",
    cancelled: "已停止",
  };
  function button(text, action, cls = "secondary") {
    const b = node("button", text, cls);
    b.type = "button";
    b.onclick = async () => {
      b.disabled = true;
      try {
        await action();
      } catch (e) {
        notice(e.message);
      } finally {
        b.disabled = false;
      }
    };
    return b;
  }
  function detach() {
    if (branch) prompts.set(branch, $("#auto-prompt").value);
    branch = null;
    generation++;
    clearTimeout(timer);
    signature = "";
    latestRuns = [];
  }
  async function refresh() {
    if (!branch) return;
    const ticket = generation;
    try {
      const runs = await api("/branches/" + branch + "/autowrite");
      if (ticket !== generation) return;
      const active = runs.some((r) =>
        ["orchestrating", "paused"].includes(r.status),
      );
      $("#auto-start").disabled = sending || !configured || active;
      const next = JSON.stringify(runs);
      if (signature !== next) {
        signature = next;
        latestRuns = runs;
        render(runs);
        onRuns(runs);
      }
    } catch (e) {
      if (ticket === generation)
        $("#auto-config").textContent = "进度连接中断，正在重新连接。";
    }
    if (ticket === generation) timer = setTimeout(refresh, 2500);
  }
  function render(runs) {
    const panel = $("#auto-runs");
    panel.replaceChildren();
    if (!runs.length)
      panel.append(node("p", "第一份初稿，将从这里开始。", "auto-empty"));
    for (const r of runs) {
      const card = node("article", "", "auto-run");
      const head = node("div", "", "auto-run-head");
      head.append(
        node("strong", labels[r.status] || r.status),
        node(
          "span",
          r.chapters.length + " / " + r.chapter_count + " 章",
          "muted",
        ),
      );
      card.append(head, node("p", r.text, "auto-request"));
      const progress = document.createElement("progress");
      progress.max = r.chapter_count + 1;
      progress.value = r.chapters.length + (r.outline.length ? 1 : 0);
      progress.setAttribute("aria-label", "章纲与章节完成进度");
      card.append(progress);
      if (r.status === "orchestrating")
        card.append(
          node(
            "p",
            !r.outline.length
              ? "正在构思章纲与故事走向…"
              : "正在承接前文，写第 " +
                  Math.min(r.chapters.length + 1, r.chapter_count) +
                  " 章…",
            "muted",
          ),
        );
      if (r.status === "paused")
        card.append(
          node("p", "当前已发出的章节会保存完成；继续后再写下一章。", "muted"),
        );
      if (r.error)
        card.append(
          node(
            "p",
            "本轮已停止，已完成章节仍可阅读。错误：" +
              r.error +
              "。不会自动重复调用。",
            "auto-error",
          ),
        );
      const actions = node("div", "", "actions");
      for (const [action, label] of r.status === "orchestrating"
        ? [
            ["pause", "本章后暂停"],
            ["stop", "立即停止"],
          ]
        : r.status === "paused"
          ? [
              ["resume", "继续写作"],
              ["stop", "结束本轮"],
            ]
          : []) {
        actions.append(
          button(label, async () => {
            await api("/autowrite/" + r.id + "/" + action, "POST");
            clearTimeout(timer);
            await refresh();
          }),
        );
      }
      if (r.chapters.length)
        actions.append(
          button("导出本轮初稿", () => {
            const text =
              "# 全 AI 写作 · 待审阅初稿\n\n" +
              r.chapters
                .map(
                  (c, i) => "## " + (i + 1) + ". " + c.title + "\n\n" + c.body,
                )
                .join("\n\n");
            const url = URL.createObjectURL(
              new Blob([text], { type: "text/markdown;charset=utf-8" }),
            );
            const a = document.createElement("a");
            a.href = url;
            a.download = "未尽-初稿.md";
            a.click();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
          }),
        );
      card.append(actions);
      r.outline.forEach((p, i) => {
        const detail = node("details", "", "auto-chapter");
        const summary = node(
          "summary",
          `${String(i + 1).padStart(2, "0")}　${p.title}　${r.chapters[i] ? "已成文" : "章纲"}`,
        );
        detail.append(summary, node("p", p.summary, "auto-intent"));
        if (r.chapters[i]) {
          const c = r.chapters[i];
          detail.append(node("div", c.body, "auto-prose"));
          const edit = button(
            r.import_statuses?.[i] === "accepted"
              ? "阅读已定稿正文"
              : r.imports[i]
                ? "打开审阅稿"
                : "送入编辑器审阅",
            () => openChapter(r.id, i),
          );
          edit.disabled = i > r.imports.length;
          detail.append(edit);
        }
        card.append(detail);
      });
      card.append(
        node(
          "small",
          `${r.model} · 已调用 ${r.calls} / ${r.call_limit} 次 · 输入 ${r.usage.input_tokens} / 输出 ${r.usage.output_tokens} tokens。费用以供应商账单为准。`,
          "auto-usage",
        ),
      );
      panel.append(card);
    }
  }
  $("#auto-form").onsubmit = async (event) => {
    event.preventDefault();
    if (sending) return;
    const { current, dirty } = getContext();
    if (dirty) return notice("请先保存正文与设定，再开始全 AI 写作。");
    sending = true;
    $("#auto-start").disabled = true;
    try {
      await api("/branches/" + current.branch_id + "/autowrite", "POST", {
        base_revision_id: current.revision_id,
        text: $("#auto-prompt").value,
        chapter_count: Number($("#auto-count").value),
        target_words: Number($("#auto-words").value),
      });
      clearTimeout(timer);
      await refresh();
      notice("写作任务已保存，后台开始规划章纲。");
    } catch (e) {
      notice(e.message);
    } finally {
      sending = false;
      clearTimeout(timer);
      await refresh();
    }
  };
  async function attach() {
    detach();
    branch = getContext().current.branch_id;
    const ticket = generation;
    $("#auto-prompt").value = prompts.get(branch) || "";
    const status = await api("/ai/status");
    if (ticket !== generation) return;
    configured = status.configured;
    $("#auto-config").textContent = configured
      ? "模型：" + status.model + " · 开始后将向 DeepSeek 发送本轮创作上下文。"
      : "尚未接入模型。请在本机 backend/.env 配置 DeepSeek 密钥与模型后重启服务。";
    const budget = () => {
      const calls = Number($("#auto-count").value) + 1;
      $("#auto-budget").textContent =
        `本轮最多 ${calls} 次调用，输出上限合计 ${(calls * status.max_output_tokens).toLocaleString()} tokens；失败后停止。`;
    };
    $("#auto-count").onchange = budget;
    budget();
    await refresh();
  }
  async function openChapter(id, index) {
    const run = latestRuns.find((r) => r.id === id);
    if (!run?.chapters[index]) {
      showView("autowrite");
      return;
    }
    if (index > 0 && !run.imports[index - 1]) {
      showView("autowrite");
      notice("请从本轮第一章开始，依次审阅并定稿。");
      return;
    }
    if (getContext().dirty)
      throw Error("请先保存当前正文和设定，再打开 AI 初稿。");
    const res = await api(`/autowrite/${id}/chapters/${index}/draft`, "POST");
    await openDraft(res.draft_id);
    clearTimeout(timer);
    await refresh();
  }
  return { attach, detach, openChapter };
}
