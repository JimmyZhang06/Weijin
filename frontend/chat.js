import { icon } from "/components.js";
export function createChat({
  api,
  notice,
  getContext,
  openDraft,
  showPanel,
  prepare,
}) {
  const $ = (s) => document.querySelector(s);
  const node = (tag, text, cls = "") => {
    const n = document.createElement(tag);
    n.textContent = text;
    if (text === "✧") n.replaceChildren(icon("spark"));
    n.className = cls;
    return n;
  };
  let branch = null,
    timer = null,
    source = null,
    running = null,
    sequence = 0,
    selection = "",
    sending = false;
  const buffers = new Map(),
    prompts = new Map();
  let status = { configured: false };
  const errors = {
    AI_NOT_CONFIGURED: "尚未配置模型。",
    DEEPSEEK_REQUEST_FAILED:
      "DeepSeek 请求失败，请检查密钥、模型权限、网络与额度。",
    OPENAI_REQUEST_FAILED:
      "OpenAI 请求失败。请检查模型权限、密钥、网络或额度；本次用量可能无法确认。",
    CALL_OUTCOME_UNKNOWN: "上次调用结果不明，已停止自动重试，避免重复计费。",
    MODEL_INCOMPLETE: "模型未完整生成，可调整输出上限后重新发送。",
    AI_INTERRUPTED: "任务超时、取消或失去租约。",
    MODEL_REFUSED_OR_ERROR: "模型拒绝或无法完成此请求。",
    MODEL_FORMAT_INVALID: "模型稿件格式不符合要求，未生成草稿。",
  };
  function clearSelection() {
    selection = "";
    $("#chat-selection").hidden = true;
  }
  function targetChanged() {
    clearSelection();
    const { doc } = getContext();
    $("#chat-target").textContent =
      "↳ " +
      (doc?.scene?.title ||
        doc?.draft?.content.title ||
        doc?.plan?.title ||
        "当前作品 · 尚未指定章节");
  }
  function detach() {
    if (branch) prompts.set(branch, $("#chat-input").value);
    branch = null;
    sequence++;
    clearTimeout(timer);
    source?.close();
    source = null;
    running = null;
    clearSelection();
  }
  function events(id) {
    if (running === id && source) return;
    source?.close();
    running = id;
    buffers.set(id, "");
    source = new EventSource("/api/v1/jobs/" + id + "/events");
    source.addEventListener("assistant.delta", (event) => {
      const value = JSON.parse(event.data).text;
      buffers.set(id, (buffers.get(id) || "") + value);
      const target = document.getElementById("reply-" + id);
      if (target) target.textContent = buffers.get(id);
    });
    for (const name of ["assistant.completed", "job.failed", "job.cancelled"])
      source.addEventListener(name, () => {
        source?.close();
        source = null;
      });
  }
  async function refresh() {
    if (!branch) return;
    const ticket = sequence,
      bid = branch;
    try {
      const rows = await api("/branches/" + bid + "/conversation");
      if (ticket !== sequence) return;
      const list = $("#chat-messages"),
        atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 65;
      list.replaceChildren();
      if (!rows.length) {
        const empty = node("div", "", "chat-empty");
        empty.append(
          node("span", "✧", "empty-emblem"),
          node("span", "故事，正等你开口。"),
          node(
            "p",
            "一个尚未想清楚的转折，一句不够准确的对白。都可以从这里开始。",
          ),
        );
        const starters = node("div", "", "chat-starters");
        for (const [label, prompt] of [
          [
            "推敲人物的动机",
            "结合目前的人物设定，帮我分析这一章的人物动机与冲突。",
          ],
          [
            "寻找下一幕的方向",
            "结合现有正文，给我三个下一幕的走向，说明各自对人物关系的影响。",
          ],
          [
            "让对白更有潜台词",
            "请分析当前章节的对白，指出哪里可以用潜台词代替直接解释，先给建议。",
          ],
        ]) {
          const b = node("button", label);
          b.type = "button";
          b.append(node("span", "↗"));
          b.onclick = () => {
            $("#chat-input").value = prompt;
            $("#chat-mode").value = "discuss";
            $("#chat-input").focus();
          };
          starters.append(b);
        }
        empty.append(starters);
        list.append(empty);
      }
      for (const row of rows) {
        const user = node("div", "", "chat-user");
        const userLabel = node("div", "", "message-label");
        userLabel.append(
          node("span", "你", "message-avatar"),
          node(
            "span",
            { discuss: "讨论剧情", write: "起草章节", revise: "修改稿件" }[
              row.mode
            ],
          ),
        );
        user.append(userLabel, node("p", row.text));
        const assistant = node("div", "", "chat-assistant");
        const assistantLabel = node("div", "", "message-label");
        assistantLabel.append(
          node("span", "✧", "message-avatar"),
          node("strong", "未尽"),
        );
        assistant.append(assistantLabel);
        const message = node(
          "p",
          row.reply ||
            buffers.get(row.job_id) ||
            (["queued", "running"].includes(row.status)
              ? row.status === "queued"
                ? "等待任务进程…"
                : "正在整理上下文并生成回复…"
              : "此次任务没有完成回复。"),
        );
        message.id = "reply-" + row.job_id;
        assistant.append(message);
        if (row.base_revision_id !== getContext().current?.revision_id)
          assistant.append(node("small", "基于较早作品版本 · 历史对话"));
        if (row.error)
          assistant.append(
            node(
              "p",
              errors[row.error] || "任务未完成：" + row.error,
              "chat-error",
            ),
          );
        if (row.status === "cancelled")
          assistant.append(
            node("p", "已停止。已发出的调用可能产生费用。", "chat-error"),
          );
        if (row.draft_id) {
          const card = node("div", "", "chat-proposal-card");
          const heading = node("div", "", "proposal-heading");
          heading.append(
            node("strong", row.draft_preview?.title || "章节提案"),
            node("span", row.draft_status === "pending" ? "待审阅" : "已处理"),
          );
          card.append(heading);
          if (row.draft_preview?.body)
            card.append(node("p", row.draft_preview.body));
          const b = node(
            "button",
            row.draft_status === "pending"
              ? "打开草稿审阅 ↗"
              : "稿件已" + (row.draft_status === "accepted" ? "定稿" : "搁置"),
            "chat-proposal",
          );
          b.disabled = row.draft_status !== "pending";
          b.addEventListener("click", () =>
            openDraft(row.draft_id).catch((e) => notice(e.message)),
          );
          card.append(b);
          assistant.append(card);
        }
        list.append(user, assistant);
      }
      if (atBottom) list.scrollTop = list.scrollHeight;
      const active = rows.find((r) => ["queued", "running"].includes(r.status));
      $("#chat-stop").hidden = !active;
      $("#chat-send").disabled = !!active || sending || !status.configured;
      if (active) {
        events(active.job_id);
        timer = setTimeout(refresh, 1200);
      } else {
        running = null;
        source?.close();
        source = null;
      }
    } catch (e) {
      if (ticket === sequence) {
        notice(e.message);
        timer = setTimeout(refresh, 4000);
      }
    }
  }
  async function attach() {
    branch = getContext().current.branch_id;
    $("#chat-input").value = prompts.get(branch) || "";
    targetChanged();
    await refresh();
  }
  $("#chat-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (sending || !branch) return;
    const text = $("#chat-input").value.trim();
    if (!text || running || !status.configured) return;
    const quoted = selection;
    sending = true;
    $("#chat-send").disabled = true;
    try {
      await prepare();
    } catch (e) {
      notice(e.message);
      sending = false;
      $("#chat-send").disabled = !!running || !status.configured;
      return;
    }
    sending = false;
    const { current, doc } = getContext();
    const bid = branch,
      ticket = sequence;
    const payload = {
      base_revision_id: current.revision_id,
      mode:
        $("#chat-mode").value === "write" &&
        ["draft", "scene"].includes(doc?.type)
          ? "revise"
          : $("#chat-mode").value,
      text,
      selected_text: quoted,
    };
    if (doc?.type === "scene") payload.scene_id = doc.scene.id;
    else if (doc?.type === "draft") payload.draft_id = doc.draft.id;
    else if (doc?.replaceScene) {
      notice("修订模式中的本地正文请先存成草稿。");
      $("#chat-send").disabled = false;
      return;
    }
    if (doc?.plan) payload.plan_id = doc.plan.id;
    sending = true;
    $("#chat-send").disabled = true;
    try {
      await api("/branches/" + bid + "/conversation", "POST", payload);
      if (ticket === sequence) {
        $("#chat-input").value = "";
        prompts.delete(bid);
        clearSelection();
        await refresh();
      }
    } catch (e) {
      notice(e.message);
    } finally {
      sending = false;
      if (ticket === sequence)
        $("#chat-send").disabled = !!running || !status.configured;
    }
  });
  $("#chat-stop").addEventListener("click", async () => {
    if (running) {
      try {
        await api("/jobs/" + running + "/cancel", "POST");
        await refresh();
      } catch (e) {
        notice(e.message);
      }
    }
  });
  $("#quote-selection").addEventListener("click", () => {
    const editor = $("#editor-body");
    const text = editor.value.slice(editor.selectionStart, editor.selectionEnd);
    if (!text) {
      notice("先在正文中选中一段文字，再点击引用。");
      return;
    }
    if (text.length > 10000) {
      notice("引用不能超过 10000 字符。");
      return;
    }
    selection = text;
    $("#chat-selection").hidden = false;
    $("#chat-selection-text").textContent =
      text.slice(0, 90) + (text.length > 90 ? "…" : "");
    $("#chat-mode").value = "revise";
    showPanel();
    $("#chat-input").focus();
  });
  $("#clear-selection").addEventListener("click", clearSelection);
  $("#chat-input").addEventListener("keydown", (event) => {
    if (
      (event.ctrlKey || event.metaKey) &&
      event.key === "Enter" &&
      !event.isComposing
    ) {
      event.preventDefault();
      $("#chat-form").requestSubmit();
    }
  });
  api("/ai/status")
    .then((data) => {
      status = data;
      $("#chat-model").textContent = data.configured
        ? data.model
        : "待配置 DeepSeek";
      $("#ai-config-message").textContent = data.message;
      $(".provider-badge").textContent = data.configured
        ? (data.provider === "deepseek" ? "DeepSeek · " : "OpenAI · ") +
          data.model
        : "AI 待配置";
      $("#capability").textContent =
        data.message + " 旧版模板按钮仍为零费用演示。";
      $("#chat-send").disabled = !data.configured || !!running;
    })
    .catch((e) => {
      $("#ai-config-message").textContent = "模型状态暂时无法读取。";
      notice(e.message);
    });
  return { attach, detach, targetChanged };
}
