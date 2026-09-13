import { setupComponents } from "/components.js";
import { createAutowrite } from "/autowrite.js";
import { createChat } from "/chat.js";
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const uuid = () => crypto.randomUUID();
const el = (tag, text = "", cls = "") => {
  const n = document.createElement(tag);
  n.textContent = text;
  if (cls) n.className = cls;
  return n;
};
let worlds = [],
  current = null,
  jobs = [],
  doc = null,
  view = "manuscript",
  dirty = false,
  settingsDirty = false;
let batchRuns = [];
let activeJob = null,
  timer = null,
  epoch = 0,
  busy = false,
  noticeTimer = null;
const requestKeys = new Map();
const stageNames = [
  "固定输入与章纲",
  "人物信息裁剪",
  "模板成文",
  "结构与引用检查",
];
const povNames = {
  third_limited: "第三人称 · 限知",
  first: "第一人称",
  omniscient: "第三人称 · 全知",
};
function notice(text) {
  clearTimeout(noticeTimer);
  $("#notice").textContent = text;
  noticeTimer = setTimeout(() => ($("#notice").textContent = ""), 8000);
}
async function api(path, method = "GET", body) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const fingerprint = method + path + JSON.stringify(body);
  if (["POST", "PUT", "PATCH"].includes(method)) {
    if (!requestKeys.has(fingerprint)) requestKeys.set(fingerprint, uuid());
    headers["Idempotency-Key"] = requestKeys.get(fingerprint);
  }
  const response = await fetch("/api/v1" + path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data;
  try {
    data = await response.json();
  } catch {
    throw Error(
      "服务暂不可用，请检查本地数据库与服务进程后重试。作品并未因此被删除。",
    );
  }
  if (!response.ok) {
    if (response.status < 500) requestKeys.delete(fingerprint);
    throw Error(
      data.error?.message ||
        data.detail?.map?.((x) => x.msg).join("；") ||
        "操作未完成，请重试。",
    );
  }
  requestKeys.delete(fingerprint);
  return data;
}
function act(fn) {
  return async (event) => {
    if (busy) return;
    busy = true;
    try {
      await fn(event);
    } catch (error) {
      notice(error.message);
    } finally {
      busy = false;
    }
  };
}
function btn(text, fn, cls = "") {
  const b = el("button", text, cls);
  b.type = "button";
  b.addEventListener("click", act(fn));
  return b;
}
function guard() {
  if (!dirty && !settingsDirty) return true;
  const leave = window.confirm(
    "还有未保存的修改。离开会丢失这些修改，是否继续？",
  );
  if (leave && settingsDirty) fillSettings();
  return leave;
}
function endSession() {
  chat.detach();
  autowrite.detach();
  clearTimeout(timer);
  activeJob = null;
  epoch++;
  $("#sidebar").classList.remove("open");
}
function wordCount() {
  return [...$("#editor-body").value.replace(/\s/g, "")].length;
}
function count() {
  $("#word-count").textContent = wordCount().toLocaleString() + " 字";
}
function showView(next) {
  view = next;
  $("#breadcrumb-title").textContent =
    next === "manuscript"
      ? $("#editor-title").value || "新章节"
      : { settings: "人物与设定", extras: "角色旁页", autowrite: "全 AI 写作" }[
          next
        ] || "写作空间";
  document.body.classList.toggle("auto-view", next === "autowrite");
  for (const name of ["manuscript", "settings", "extras", "autowrite"])
    $("#" + name + "-view").hidden = name !== next;
  $$("[data-view]").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === next),
  );
  $("#sidebar").classList.remove("open");
}
async function library() {
  if (!guard()) return;
  endSession();
  dirty = settingsDirty = false;
  current = null;
  doc = null;
  document.body.classList.add("library-view");
  document.body.classList.remove("auto-view", "focus-mode");
  history.replaceState(null, "", "#");
  $("#worlds").replaceChildren(el("p", "正在加载作品…", "empty-state"));
  $("#world-count").textContent = "…";
  try {
    worlds = await api("/worlds");
  } catch (error) {
    $("#library").hidden = false;
    $("#workspace").hidden = true;
    $("#book-nav").hidden = true;
    $("#world-count").textContent = "—";
    $("#library-meta").textContent = "作品加载失败";
    $("#worlds").replaceChildren(
      el(
        "p",
        "暂时无法连接作品库。请检查本地服务后重试；这不表示作品已被删除。",
        "empty-state",
      ),
      btn("重新加载", library, "secondary"),
    );
    notice(error.message);
    return;
  }
  $("#workspace").hidden = true;
  $("#library").hidden = false;
  $("#book-nav").hidden = true;
  $("#breadcrumb-title").textContent = "作品集";
  $("#world-count").textContent = worlds.length;
  $("#worlds").replaceChildren();
  $("#library-meta").textContent = worlds.length + " 部作品 · 仅自己可见";
  worlds.forEach((w, i) => {
    const b = btn("", () => openBranch(w.branches[0].id), "book");
    const cover = el("div", "", "book-cover");
    cover.append(
      el("span", "PRIVATE MANUSCRIPT", "book-tag"),
      el("h3", w.title),
      el("span", String(i + 1).padStart(2, "0"), "volume"),
    );
    const meta = el("div", "", "book-meta");
    meta.append(
      el("span", w.branches.length + " 条故事路线"),
      el("span", "继续写作 ↗"),
    );
    b.append(cover, meta);
    $("#worlds").append(b);
  });
  if (!worlds.length)
    $("#worlds").append(el("p", "这里将收藏你的第一部作品。", "empty-state"));
}
async function openBranch(id, preferred = {}) {
  if (!guard()) return;
  endSession();
  const ticket = epoch;
  const [data, list] = await Promise.all([
    api("/branches/" + id + "/state"),
    api("/worlds"),
  ]);
  if (ticket !== epoch) return;
  current = data;
  document.body.classList.remove("library-view");
  worlds = list;
  jobs = await api("/branches/" + id + "/jobs");
  if (ticket !== epoch) return;
  dirty = settingsDirty = false;
  history.replaceState(null, "", "#branch/" + id);
  $("#library").hidden = true;
  $("#workspace").hidden = false;
  $("#book-nav").hidden = false;
  $("#extra-content").hidden = true;
  renderChrome();
  fillSettings();
  renderExtras();
  batchRuns = [];
  const pending = jobs.find((j) => j.draft_status === "pending");
  const scene = current.scenes.find((s) => s.id === preferred.sceneId);
  if (scene) selectScene(scene);
  else if (preferred.acceptedScene) selectScene(current.scenes.at(-1));
  else if (pending && !preferred.blank)
    await selectDraft(pending.draft_id, false);
  else if (current.scenes.length && !preferred.blank)
    selectScene(current.scenes.at(-1));
  else
    selectNew(
      current.chapter_plans.find(
        (p) => !current.scenes.some((s) => s.plan_id === p.id),
      ),
    );
  if (preferred.settings) showView("settings");
  await chat.attach();
  await autowrite.attach();
  const running = jobs.find(
    (j) =>
      !["conversation", "autowrite"].includes(j.kind) &&
      ["queued", "running"].includes(j.status),
  );
  if (running) {
    activeJob = running.job_id;
    watch();
  } else $("#task-banner").hidden = true;
}
function renderChrome() {
  const world = worlds.find((w) => w.id === current.world_id);
  $("#work-title").textContent = world?.title || "作品";
  $("#side-title").textContent = world?.title || "作品";
  $("#mini-cover").textContent = (world?.title || "未尽").slice(0, 2);
  $("#project-type").textContent =
    current.brief.work_type === "fanfic" ? "同人创作" : "原创小说";
  $("#breadcrumb-title").textContent = world?.title || "作品";
  $("#work-type").textContent =
    current.brief.work_type === "fanfic"
      ? "FANFICTION / 同人创作"
      : "ORIGINAL / 原创小说";
  $("#work-relation").textContent =
    current.brief.relationship || "人物关系尚待展开";
  $("#revision-label").textContent = "已保存版本 " + current.version;
  $("#world-count").textContent = worlds.length;
  $("#routes").replaceChildren(
    ...world.branches.map(
      (b) => new Option(b.title, b.id, false, b.id === current.branch_id),
    ),
  );
  $("#export").href = "/api/v1/branches/" + current.branch_id + "/export";
  $("#pov-label").textContent = povNames[current.brief.pov];
  $("#extra-count").textContent = current.artifacts.length;
  renderNav();
  renderContext();
}
function renderNav() {
  const list = $("#chapter-list");
  list.replaceChildren();
  const pending = jobs.filter(
    (j) => j.draft_status === "pending" && j.draft_kind === "scene",
  );
  const used = new Set();
  const entries = current.scenes.map((scene) => ({
    scene,
    title: scene.title,
    plan: current.chapter_plans.find((p) => p.id === scene.plan_id),
  }));
  for (const plan of current.chapter_plans)
    if (!current.scenes.some((s) => s.plan_id === plan.id))
      entries.push({ plan, title: plan.title });
  for (const entry of entries) {
    entry.drafts = pending.filter((j) =>
      entry.scene
        ? j.replace_scene_id === entry.scene.id
        : j.draft_plan_id === entry.plan.id,
    );
    entry.drafts.forEach((j) => used.add(j.draft_id));
  }
  for (const j of pending.filter((j) => !used.has(j.draft_id)))
    entries.push({ title: j.draft_title, drafts: [j] });
  entries.forEach((entry, i) => {
    const selected =
      entry.drafts.some((j) => j.draft_id === doc?.draft?.id) ||
      (entry.scene && doc?.scene?.id === entry.scene.id) ||
      (entry.plan && doc?.plan?.id === entry.plan.id);
    const b = btn(
      "",
      async () => {
        if (entry.drafts.length) return selectDraft(entry.drafts[0].draft_id);
        if (!guard()) return;
        dirty = settingsDirty = false;
        if (entry.scene) selectScene(entry.scene);
        else selectNew(entry.plan);
      },
      "chapter-entry" + (selected ? " selected" : ""),
    );
    b.append(
      el("span", String(i + 1).padStart(2, "0"), "chapter-num"),
      el("span", entry.title || "未命名章节", "chapter-name"),
      el(
        "span",
        entry.drafts.length
          ? entry.scene
            ? "有修订稿"
            : "草稿 · 待审阅"
          : entry.scene
            ? "已定稿"
            : "构思中",
        "chapter-state",
      ),
    );
    list.append(b);
    if (entry.scene && entry.drafts.length)
      list.append(
        btn(
          "查看已定稿正文",
          () => {
            if (guard()) selectScene(entry.scene);
          },
          "chapter-subaction",
        ),
      );
    for (const j of entry.drafts.slice(1))
      list.append(
        btn(
          "备选稿 · " + j.draft_title,
          () => selectDraft(j.draft_id),
          "chapter-subaction",
        ),
      );
  });
  if (!entries.length)
    list.append(
      btn("＋ 开始第一章", beginChapter, "chapter-entry chapter-empty-action"),
    );
  $("#chapter-count").textContent = entries.length;
  const extras = jobs.filter(
    (j) => j.draft_status === "pending" && j.draft_kind !== "scene",
  );
  $("#draft-count").textContent = extras.length;
  $("#draft-count").closest(".side-heading").hidden = !extras.length;
  $("#draft-list").replaceChildren(
    ...extras.map((j) =>
      btn(
        j.draft_title || "旁页草稿",
        () => selectDraft(j.draft_id),
        "chapter-subaction",
      ),
    ),
  );
  for (const run of batchRuns) {
    const remaining = run.outline.filter((_, i) => !run.imports[i]);
    if (!remaining.length && !["orchestrating", "paused"].includes(run.status))
      continue;
    const b = btn("", () => showView("autowrite"), "chapter-entry batch-nav");
    b.append(
      el("span", "✧", "chapter-num"),
      el("span", "AI 连续写作", "chapter-name"),
      el(
        "span",
        `${run.chapters.length} / ${run.chapter_count} 章 · 点击审阅进度`,
        "chapter-state",
      ),
    );
    list.append(b);
    run.outline.forEach((chapter, i) => {
      if (run.imports[i]) return;
      list.append(
        btn(
          chapter.title + (run.chapters[i] ? " · 待审阅" : " · 章纲"),
          () => autowrite.openChapter(run.id, i),
          "chapter-subaction",
        ),
      );
    });
  }
}
function beginChapter() {
  if (!guard()) return;
  dirty = settingsDirty = false;
  const plan = current.chapter_plans.find(
    (p) =>
      !current.scenes.some((s) => s.plan_id === p.id) &&
      !jobs.some(
        (j) => j.draft_status === "pending" && j.draft_plan_id === p.id,
      ),
  );
  selectNew(plan);
  $("#editor-title").focus();
}
function renderContext() {
  $("#scene-intent").hidden = !doc?.plan?.summary;
  $("#scene-intent-text").textContent = doc?.plan?.summary || "";
  const panel = $("#context-brief");
  panel.replaceChildren();
  panel.append(
    el(
      "p",
      "AI 本次参考已保存的人物与设定、本章目标、当前稿件及最近三章；不会自动记住整本书。",
      "context-scope",
    ),
  );
  const fields = [
    ["本章目标", doc?.plan?.summary],
    ["原作锚点", current.brief.canon_anchor],
    ["设定边界", current.brief.boundaries],
    ["叙事风格", current.brief.style],
  ];
  for (const [name, value] of fields) {
    if (!value) continue;
    const b = el("div", "", "context-block");
    b.append(el("small", name), el("p", value));
    panel.append(b);
  }
  if (!panel.childElementCount)
    panel.append(
      el("p", "在「设定与章纲」中记录本章方向与人物边界。", "muted"),
    );
  $("#context-cast").replaceChildren();
  for (const p of current.characters) {
    const b = btn(
      "",
      async () => {
        const data = await api(
          "/branches/" +
            current.branch_id +
            "/characters/" +
            p.id +
            "/context?scene=" +
            (doc?.scene?.number ?? current.scenes.length),
        );
        $("#character-knowledge").hidden = false;
        $("#character-knowledge").textContent =
          p.name +
          " · 当前已知\n" +
          (data.knowledge.map((k) => k.text).join("\n") ||
            "没有已记录的私密认知。") +
          "\n\n仅该人物可见的知识投影，不是模型思考过程。";
      },
      "context-person",
    );
    const desc = el("div");
    desc.append(el("strong", p.name), el("small", p.trait));
    b.append(
      el("span", p.name.slice(0, 1), "person-initial"),
      desc,
      el("span", "↗"),
    );
    $("#context-cast").append(b);
  }
  $("#character-knowledge").hidden = true;
}
function setEditor(title, body, readOnly) {
  $("#discard-local").hidden = true;
  $("#draft-comparison").hidden = true;
  $("#editor-title").value = title;
  $("#breadcrumb-title").textContent = title || "新章节";
  $("#editor-body").value = body;
  $("#editor-title").readOnly = readOnly;
  $("#editor-body").readOnly = readOnly;
  dirty = false;
  count();
  $("#save-draft").hidden = readOnly;
  $("#accept-draft").hidden = doc?.type !== "draft";
  $("#reject-draft").hidden = doc?.type !== "draft";
  $("#edit-scene").hidden =
    !readOnly || doc?.scene?.id !== current.scenes.at(-1)?.id;
  $("#next-chapter").hidden = !readOnly;
  $("#fork-open").textContent =
    readOnly && doc?.scene?.id !== current.scenes.at(-1)?.id
      ? "从本章另写"
      : "另开路线";
  $("#fork-open").hidden = !doc?.scene;
  $("#template-generate").hidden = true;
  $("#editor-status").textContent = readOnly
    ? "已定稿"
    : doc?.type === "draft"
      ? "草稿"
      : "待创作";
  $("#editor-status").classList.toggle("draft", !readOnly);
  $("#save-status").textContent = readOnly
    ? "独立版本已保存"
    : doc?.type === "draft"
      ? "已保存至数据库"
      : "尚未保存";
  $("#chapter-kicker").textContent =
    doc?.draft?.content.kind && doc.draft.content.kind !== "scene"
      ? "SIDE STORY / 角色旁页"
      : "CHAPTER " +
        String(
          doc?.scene?.number ||
            doc?.replaceScene?.number ||
            current.scenes.length + 1,
        ).padStart(2, "0");
  $("#target-count").textContent = doc?.plan
    ? "目标 " + doc.plan.target_words + " 字"
    : "";
  $("#source-label").textContent =
    doc?.draft?.content.mock || doc?.scene?.mock
      ? "模板来源"
      : ["openai", "deepseek"].includes(
            doc?.draft?.content.source || doc?.scene?.source,
          )
        ? "AI 稿件"
        : "作者手写";
  renderNav();
  renderContext();
  showView("manuscript");
  chat.targetChanged();
}
function selectNew(plan) {
  doc = { type: "new", plan };
  setEditor(plan?.title || "", "", false);
  $("#editor-note").textContent =
    "先保存草稿，再确认定稿。你可以随时修改自己的文字。";
  $("#review-panel").replaceChildren(
    el("p", "保存草稿后，检查标题、长度与关联引用。", "muted"),
  );
}
function selectScene(scene) {
  doc = {
    type: "scene",
    scene,
    plan: current.chapter_plans.find((p) => p.id === scene.plan_id),
  };
  setEditor(scene.title, scene.body, true);
  $("#editor-note").textContent = scene.mock
    ? "该稿源于固定模板，不代表真实 AI 写作质量。"
    : "作者定稿。修订将建立新的版本，不修改历史记录。";
  $("#review-panel").replaceChildren(
    el("p", "已定稿。旧版本不可覆盖；正文事实未自动提取。", "muted"),
  );
}
async function selectDraft(id, protect = true) {
  if (protect && !guard()) return;
  const ticket = epoch;
  const d = await api("/drafts/" + id);
  if (ticket !== epoch) return;
  if (d.status !== "pending") {
    const accepted = current.scenes.find(
      (scene) => scene.id === d.accepted_scene_id,
    );
    if (accepted) {
      dirty = settingsDirty = false;
      selectScene(accepted);
      return;
    }
    notice(
      d.status === "accepted"
        ? "该章已经定稿，请从章节目录打开正文。"
        : "这份草稿已不再待审阅。",
    );
    return;
  }
  dirty = settingsDirty = false;
  doc = {
    type: "draft",
    draft: d,
    plan: current.chapter_plans.find((p) => p.id === d.content.plan_id),
  };
  setEditor(d.content.title, d.content.body, false);
  if (d.comparison) {
    $("#draft-comparison").hidden = false;
    $("#comparison-before").textContent = d.comparison.before;
  }
  $("#editor-note").textContent =
    d.base_revision_id !== current.revision_id
      ? "作品设定或正文已更新，这份草稿基于旧版本，不能直接定稿。请复制需要保留的文字，核对新设定后重新起稿。"
      : d.content.note || "作者草稿，尚未写入正文。";
  renderReview(d.structure_review);
  await loadTrace(d.job_id);
}
function renderReview(report) {
  $("#review-panel").replaceChildren();
  for (const c of report.checks) {
    const row = el("div", "", "check-row");
    row.append(el("span", c.passed ? "✓" : "!"), el("div", c.name));
    $("#review-panel").append(row);
  }
  $("#review-panel").append(el("p", report.notice, "review-note"));
}
async function saveDraft() {
  const fields = [$("#editor-title"), $("#editor-body")];
  fields.forEach((f) => (f.readOnly = true));
  try {
    await persistDraft();
  } finally {
    fields.forEach((f) => (f.readOnly = doc?.type === "scene"));
  }
}
async function persistDraft() {
  if (doc?.type === "scene") return;
  const title = $("#editor-title").value.trim(),
    body = $("#editor-body").value.trim();
  if (!title || !body) throw Error("请填写章节标题和正文。");
  $("#save-status").textContent = "保存中…";
  let id;
  if (doc.type === "draft") {
    id = doc.draft.id;
    await api("/drafts/" + id, "PATCH", {
      expected_version: doc.draft.version,
      title,
      body,
    });
  } else {
    const data = { base_revision_id: current.revision_id, title, body };
    if (doc.plan) data.plan_id = doc.plan.id;
    if (doc.replaceScene) data.replace_scene_id = doc.replaceScene.id;
    const response = await api(
      "/branches/" + current.branch_id + "/manual-drafts",
      "POST",
      data,
    );
    id = response.draft_id;
  }
  const latestTitle = $("#editor-title").value,
    latestBody = $("#editor-body").value;
  const changedDuringSave =
    latestTitle.trim() !== title || latestBody.trim() !== body;
  dirty = false;
  jobs = await api("/branches/" + current.branch_id + "/jobs");
  await selectDraft(id, false);
  if (changedDuringSave) {
    $("#editor-title").value = latestTitle;
    $("#editor-body").value = latestBody;
    dirty = true;
    $("#save-status").textContent = "较早修改已保存，新输入待保存";
    count();
    throw Error("保存期间又有输入，已保留新文字，请再保存后继续。");
  }
  notice("草稿已保存。刷新或离开后，可以从左侧找回。");
}
async function acceptDraft() {
  if (!$("#semantic-ack").checked) throw Error("请先确认已核对正文设定。");
  const d = doc.draft;
  await api("/drafts/" + d.id + "/accept", "POST", {
    expected_revision_id: d.base_revision_id,
    expected_content_hash: d.content_hash,
    acknowledge_semantic_review: true,
  });
  $("#accept-dialog").close();
  dirty = false;
  const bid = current.branch_id;
  const wasExtra = d.content.kind !== "scene";
  await openBranch(bid, { acceptedScene: !wasExtra });
  if (wasExtra) showView("extras");
  notice("已保存为新版本。历史稿件保持不变。");
}
function field(label, value, name, type = "text", max = 400) {
  const l = el("label", label);
  const input = el(type === "textarea" ? "textarea" : "input");
  input.name = name;
  input.value = value || "";
  if (type !== "textarea") input.type = type;
  input.maxLength = max;
  l.append(input);
  return l;
}
function fillSettings() {
  const form = $("#settings-form");
  for (const [name, value] of Object.entries(current.brief)) {
    if (form.elements.namedItem(name))
      form.elements.namedItem(name).value = value;
  }
  form.elements.allow_locked_changes.checked = false;
  $("#character-fields").replaceChildren();
  for (const p of current.characters) {
    const box = el("fieldset");
    box.dataset.id = p.id;
    box.append(
      el("legend", p.name),
      field("名字", p.name, "name"),
      field("核心特点", p.trait, "trait"),
      field("愿望", p.desire, "desire"),
    );
    $("#character-fields").append(box);
  }
  $("#plan-fields").replaceChildren();
  current.chapter_plans.forEach(addPlanField);
  settingsDirty = false;
}
function addPlanField(
  plan = { id: uuid(), title: "", summary: "", target_words: 1500 },
) {
  const box = el("div", "", "plan-field");
  box.dataset.id = plan.id;
  const top = el("div", "", "plan-top");
  const name = field("章名", plan.title, "plan-title", "text", 120);
  name.querySelector("input").required = true;
  const target = field("目标字数", plan.target_words, "plan-target", "number");
  target.querySelector("input").min = 100;
  target.querySelector("input").max = 20000;
  target.querySelector("input").required = true;
  const remove = btn(
    "×",
    () => {
      if (current.scenes.some((s) => s.plan_id === plan.id)) {
        notice("已定稿章纲不能删除。");
        return;
      }
      box.remove();
      settingsDirty = true;
    },
    "remove-plan",
  );
  remove.setAttribute("aria-label", "删除这条章纲");
  top.append(name, target, remove);
  box.append(
    top,
    field("这一章要发生什么？", plan.summary, "plan-summary", "textarea", 3000),
  );
  $("#plan-fields").append(box);
}
async function saveSettings() {
  if (dirty) throw Error("请先保存正文草稿，再更新作品设定。");
  const form = $("#settings-form");
  const brief = {};
  for (const name of [
    "work_type",
    "fandom",
    "canon_anchor",
    "relationship",
    "divergence",
    "boundaries",
    "style",
    "pov",
  ])
    brief[name] = form.elements.namedItem(name).value;
  const characters = $$("#character-fields fieldset").map((box) => ({
    id: box.dataset.id,
    name: box.querySelector("[name=name]").value,
    trait: box.querySelector("[name=trait]").value,
    desire: box.querySelector("[name=desire]").value,
  }));
  const chapter_plans = $$("#plan-fields .plan-field").map((box) => ({
    id: box.dataset.id,
    title: box.querySelector("[name=plan-title]").value,
    summary: box.querySelector("[name=plan-summary]").value,
    target_words: Number(box.querySelector("[name=plan-target]").value),
  }));
  await api("/branches/" + current.branch_id + "/workspace", "PUT", {
    expected_revision_id: current.revision_id,
    brief,
    characters,
    chapter_plans,
    allow_locked_changes: form.elements.allow_locked_changes.checked,
  });
  settingsDirty = false;
  await openBranch(current.branch_id, { settings: true });
  notice("设定与章纲已保存为新版本。已定稿正文保持原样。");
}
async function startJob(kind, extra = {}) {
  if (doc?.replaceScene) throw Error("章节修订请手动编辑并保存草稿。");
  if (dirty || settingsDirty) throw Error("请先保存当前修改。");
  if (jobs.some((j) => ["running", "queued"].includes(j.status)))
    throw Error("已有任务运行中，请先等待或停止。");
  if (doc?.type === "draft" && view === "manuscript")
    throw Error("请先处理当前草稿。");
  const response = await api(
    "/branches/" + current.branch_id + "/jobs",
    "POST",
    { kind, base_revision_id: current.revision_id, ...extra },
  );
  activeJob = response.job_id;
  jobs = await api("/branches/" + current.branch_id + "/jobs");
  $("#task-banner").hidden = false;
  showView("manuscript");
  await inspectorTab("activity");
  watch();
}
async function watch() {
  if (!activeJob) return;
  const jid = activeJob,
    ticket = epoch;
  try {
    const j = await api("/jobs/" + jid);
    if (ticket !== epoch || jid !== activeJob) return;
    $("#task-banner").hidden = false;
    $("#task-status").textContent =
      j.status === "queued" ? "等待后台任务" : stageNames[Math.min(j.step, 3)];
    await loadTrace(jid);
    if (ticket !== epoch) return;
    if (j.status === "succeeded") {
      activeJob = null;
      $("#task-banner").hidden = true;
      jobs = await api("/branches/" + current.branch_id + "/jobs");
      renderNav();
      if (!dirty && !settingsDirty) await selectDraft(j.draft_id, false);
      else notice("新草稿已在左侧保存，不会覆盖你正在编辑的文字。");
      return;
    }
    if (["failed", "cancelled"].includes(j.status)) {
      activeJob = null;
      $("#task-banner").hidden = true;
      notice(j.status === "failed" ? "任务失败，原稿保留。" : "任务已停止。");
      return;
    }
    timer = setTimeout(watch, 800);
  } catch (e) {
    notice("连接中断，可刷新后找回任务。" + e.message);
  }
}
async function loadTrace(jid) {
  const ticket = epoch;
  const data = await api("/jobs/" + jid + "/trace");
  if (ticket !== epoch) return;
  const panel = $("#activity-content");
  panel.replaceChildren();
  panel.append(
    el(
      "p",
      data.source === "author"
        ? "作者手写 · 无模型调用"
        : ["openai", "deepseek"].includes(data.source)
          ? (data.source === "deepseek" ? "DeepSeek" : "OpenAI") +
            " · 实际任务记录"
          : "模板工作流 · 非真实 Agent 推理",
      "muted",
    ),
  );
  if (data.source === "author") {
    panel.append(el("p", "正文已保存为草稿，等待作者定稿。", "muted"));
  } else
    for (let i = 0; i < 4; i++) {
      const step = data.steps.find((s) => s.index === i);
      const row = el("div", "", "trace-step" + (!step ? " wait" : ""));
      row.append(
        el(
          "strong",
          ["openai", "deepseek"].includes(data.source) && i === 2
            ? "模型回复与成文"
            : stageNames[i],
        ),
        el(
          "p",
          !step
            ? "尚未完成"
            : i === 0
              ? "已冻结故事版本 " + data.base_revision_id.slice(0, 8)
              : i === 1
                ? ["openai", "deepseek"].includes(data.source)
                  ? "已提供人物公开设定；私密认知未发出"
                  : "已检查 " +
                    step.result.validated_characters +
                    " 位人物的知识投影"
                : i === 2
                  ? "已写入 " + step.result.characters_written + " 个字符"
                  : step.result.scope === "conversation_only"
                    ? "讨论未写入正文"
                    : step.result.passed
                      ? "结构检查通过；语义未审查"
                      : "结构检查未通过",
        ),
      );
      panel.append(row);
    }
  panel.append(
    el(
      "p",
      "模型调用 " +
        data.model_calls +
        " 次 · " +
        (data.cost === null ? "费用以供应商账单为准" : "费用 " + data.cost) +
        (data.usage
          ? " · 输入 " +
            data.usage.input_tokens +
            " / 输出 " +
            data.usage.output_tokens +
            " tokens"
          : ""),
      "review-note",
    ),
  );
}
async function inspectorTab(name) {
  $$("[data-inspect]").forEach((b) =>
    b.classList.toggle("active", b.dataset.inspect === name),
  );
  for (const n of ["chat", "context", "activity", "history"])
    $("#" + n + "-panel").hidden = name !== n;
  if (name === "history" && current) {
    const ticket = epoch;
    const rows = await api("/branches/" + current.branch_id + "/history");
    if (ticket !== epoch) return;
    $("#history-content").replaceChildren();
    const labels = {
      initial: "建立作品",
      workspace_edit: "更新设定与章纲",
      scene: "正文定稿",
      private_artifact: "旁页定稿",
      perspective: "视角定稿",
      fork: "建立新路线",
    };
    for (const r of rows) {
      const block = el("div", "", "history-entry");
      block.append(
        el("strong", labels[r.kind] || r.kind),
        el(
          "small",
          new Date(r.created_at).toLocaleString() +
            " · " +
            r.scene_count +
            " 章",
        ),
        el("code", r.hash),
      );
      $("#history-content").append(block);
    }
  }
  if (name === "activity" && !activeJob && !doc?.draft) {
    const latest = jobs[0];
    if (latest) await loadTrace(latest.job_id);
    else
      $("#activity-content").replaceChildren(
        el("p", "尚无执行记录。保存手写稿或运行模板后可查看。", "muted"),
      );
  }
}
function renderExtras() {
  $("#extra-scene").replaceChildren(
    ...current.scenes.map((s) => new Option(s.title, s.id)),
  );
  $("#extra-person").replaceChildren(
    ...current.characters.map((p) => new Option(p.name, p.id)),
  );
  $$("[data-extra]").forEach((b) => (b.disabled = !current.scenes.length));
  $("#extra-list").replaceChildren();
  for (const a of current.artifacts) {
    const row = el("div", "", "extra-entry");
    const title = el("div");
    title.append(
      el("strong", a.title),
      el(
        "small",
        (current.characters.find((p) => p.id === a.character_id)?.name || "") +
          " · " +
          (a.stale
            ? "原章节已修订，待重作"
            : "关联第 " + a.scene_number + " 幕"),
      ),
    );
    row.append(title);
    const b = btn(
      a.stale ? "需复核" : a.revealed ? "阅读" : "打开旁页",
      () => reveal(a),
      "text-button",
    );
    b.disabled = a.stale;
    row.append(b);
    $("#extra-list").append(row);
  }
}
async function reveal(a) {
  await api("/branches/" + current.branch_id + "/reading-progress", "PUT", {
    scene_count: a.scene_number,
  });
  const data = await api(
    "/branches/" + current.branch_id + "/artifacts/" + a.id + "/reveal",
    "POST",
  );
  $("#extra-content").replaceChildren(
    el("div", "PRIVATE PAGE / 角色旁页", "eyebrow"),
    el("h2", data.title),
    el("p", data.body),
    btn("收起", () => ($("#extra-content").hidden = true), "text-button"),
  );
  $("#extra-content").hidden = false;
}
function openCreate() {
  if (!guard()) return;
  $("#create-dialog").showModal();
}
$("#home").addEventListener("click", act(library));
$("#library-nav").addEventListener("click", act(library));
$("#create-open").addEventListener("click", openCreate);
$("#create-library").addEventListener("click", openCreate);
$("#create-close").addEventListener("click", () => $("#create-dialog").close());
$("#help-toggle").addEventListener(
  "click",
  () => ($("#capability").hidden = !$("#capability").hidden),
);
$("#mobile-menu").addEventListener("click", () =>
  $("#sidebar").classList.toggle("open"),
);
$("#create-form").addEventListener("submit", (event) => {
  event.preventDefault();
  act(async () => {
    const f = new FormData(event.target);
    const characters = [0, 1].map((i) => ({
      name: f.get("name" + i),
      trait: f.get("trait" + i),
      desire: f.get("desire" + i),
      secret: f.get("secret" + i),
    }));
    const response = await api("/worlds", "POST", {
      title: f.get("title"),
      opening: f.get("opening"),
      characters,
      brief: { work_type: f.get("work_type"), fandom: f.get("fandom") },
    });
    $("#create-dialog").close();
    dirty = settingsDirty = false;
    await openBranch(response.branch_id, { blank: true });
    notice("作品已建立。可以直接落笔，也可以让右侧搭档帮你构思。");
  })();
});
$("#demo").addEventListener(
  "click",
  act(async () => {
    const response = await api("/worlds", "POST", {
      title: "最后一班列车",
      opening: "分别五年后，两人在车站重逢。",
      characters: [
        {
          name: "林舟",
          trait: "把想说的话留到最后",
          desire: "重新理解对方",
          secret: "他曾写过一封未寄出的信。",
        },
        {
          name: "许岚",
          trait: "敏感但不愿主动追问",
          desire: "确认自己是否仍然重要",
        },
      ],
      brief: {
        relationship: "林舟 & 许岚 · 久别重逢",
        style: "克制、留白，关系通过动作推进",
      },
    });
    await openBranch(response.branch_id, { blank: true });
    notice("原创示例已建立。你可以直接写作，也可以运行模板起稿。");
  }),
);
$$("[data-view]").forEach((b) =>
  b.addEventListener("click", () => showView(b.dataset.view)),
);
$$("[data-inspect]").forEach((b) =>
  b.addEventListener(
    "click",
    act(() => inspectorTab(b.dataset.inspect)),
  ),
);
$("#outline-nav").addEventListener("click", () => showView("settings"));
$("#routes").addEventListener(
  "change",
  act(async () => {
    const target = $("#routes").value;
    await openBranch(target);
    $("#routes").value = current.branch_id;
  }),
);
$("#inspector-toggle").addEventListener("click", () => {
  const hidden = !$("#inspector").hidden;
  $("#inspector").hidden = hidden;
  $("#workspace-grid").classList.toggle("no-inspector", hidden);
  $(".application").classList.toggle("without-assistant", hidden);
  $("#inspector-toggle").setAttribute("aria-expanded", String(!hidden));
});
for (const selector of ["#editor-title", "#editor-body"])
  $(selector).addEventListener("input", () => {
    dirty = true;
    $("#save-status").textContent = "有未保存修改";
    $("#discard-local").hidden = false;
    count();
  });
$("#settings-form").addEventListener("input", () => (settingsDirty = true));
$("#settings-form").addEventListener("submit", (event) => {
  event.preventDefault();
  act(saveSettings)();
});
const addPlan = () => {
  showView("settings");
  addPlanField();
  settingsDirty = true;
};
$("#plan-add").addEventListener("click", addPlan);
$("#add-chapter").addEventListener("click", act(beginChapter));
$("#next-chapter").addEventListener("click", act(beginChapter));
$("#discard-local").onclick = () => {
  const saved = doc?.draft?.content ||
    doc?.replaceScene || { title: doc?.plan?.title || "", body: "" };
  setEditor(saved.title, saved.body, false);
  notice("已还原为最近保存的内容。");
};
$("#save-draft").addEventListener("click", act(saveDraft));
$("#accept-draft").addEventListener(
  "click",
  act(async () => {
    if (dirty) await saveDraft();
    $("#semantic-ack").checked = false;
    $("#accept-dialog").showModal();
  }),
);
$("#accept-close").addEventListener("click", () => $("#accept-dialog").close());
$("#accept-confirm").addEventListener("click", act(acceptDraft));
$("#reject-draft").addEventListener(
  "click",
  act(async () => {
    if (!guard()) return;
    await api("/drafts/" + doc.draft.id + "/reject", "POST");
    dirty = false;
    await openBranch(current.branch_id);
  }),
);
$("#edit-scene").addEventListener("click", () => {
  const s = doc.scene;
  if (s.id !== current.scenes.at(-1)?.id) {
    notice("这章之后已有正文，请用“另开路线”保留后文。");
    return;
  }
  doc = {
    type: "new",
    replaceScene: s,
    plan: current.chapter_plans.find((p) => p.id === s.plan_id),
  };
  setEditor(s.title, s.body, false);
  $("#editor-note").textContent =
    "修订本章将保存新版本，关联旁页会标记为待复核。";
});
$("#template-generate").addEventListener(
  "click",
  act(() => startJob("scene", doc?.plan ? { plan_id: doc.plan.id } : {})),
);
$("#cancel-job").addEventListener(
  "click",
  act(async () => {
    if (activeJob) {
      await api("/jobs/" + activeJob + "/cancel", "POST");
      await watch();
    }
  }),
);
$$("[data-extra]").forEach((b) =>
  b.addEventListener(
    "click",
    act(() => {
      const type = b.dataset.extra;
      return startJob(
        type === "perspective" ? "perspective" : "private_artifact",
        {
          scene_id: $("#extra-scene").value,
          character_id: $("#extra-person").value,
          artifact_type: type === "diary" ? "diary" : "unsent_letter",
        },
      );
    }),
  ),
);
$("#fork-open").addEventListener("click", () => {
  $("#fork-anchor").textContent =
    "从「" + doc.scene.title + "」开始前建立分支。原路线和后文会完整保留。";
  $("#fork-dialog").showModal();
});
$("#fork-close").addEventListener("click", () => $("#fork-dialog").close());
$("#fork-confirm").addEventListener(
  "click",
  act(async () => {
    const response = await api(
      "/branches/" + current.branch_id + "/forks",
      "POST",
      {
        expected_revision_id: current.revision_id,
        before_scene_id: doc.scene.id,
        title: $("#fork-title").value,
        instruction: $("#fork-instruction").value,
      },
    );
    $("#fork-dialog").close();
    await openBranch(response.branch_id, { blank: true });
    notice("新路线已建立。现在可从这里重新落笔。");
  }),
);
window.addEventListener("beforeunload", (event) => {
  if (dirty || settingsDirty) {
    event.preventDefault();
    event.returnValue = "";
  }
});
window.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
    event.preventDefault();
    if (current) act(view === "settings" ? saveSettings : saveDraft)();
  }
  if (event.key === "Escape") $("#sidebar").classList.remove("open");
});
api("/health")
  .then((h) => {
    $("#health-label").textContent =
      h.database === "ok" && h.worker === "ok"
        ? "数据库已连接 · 任务进程在线"
        : "数据库 " + h.database + " · 任务进程 " + h.worker;
    $("#connection-dot").classList.toggle(
      "online",
      h.database === "ok" && h.worker === "ok",
    );
  })
  .catch(() => ($("#health-label").textContent = "服务暂未连接"));
const chat = createChat({
  api,
  notice,
  prepare: async () => {
    if (settingsDirty)
      throw Error(
        "人物与设定仍有修改，请先保存设定，再发送。这样 AI 才能引用正确的故事版本。",
      );
    if (busy) throw Error("正在保存，请稍后再发送。");
    busy = true;
    try {
      if (dirty) await saveDraft();
    } finally {
      busy = false;
    }
  },
  getContext: () => ({ current, doc, dirty: dirty || settingsDirty }),
  openDraft: async (id) => {
    jobs = await api("/branches/" + current.branch_id + "/jobs");
    await selectDraft(id);
  },
  showPanel: () => {
    $("#inspector").hidden = false;
    $("#workspace-grid").classList.remove("no-inspector");
    $(".application").classList.remove("without-assistant");
    $("#inspector-toggle").setAttribute("aria-expanded", "true");
    inspectorTab("chat");
  },
});
const autowrite = createAutowrite({
  onRuns: (runs) => {
    batchRuns = runs;
    renderNav();
  },
  showView,
  api,
  notice,
  getContext: () => ({ current, dirty: dirty || settingsDirty }),
  openDraft: async (id) => {
    if (!guard()) return;
    jobs = await api("/branches/" + current.branch_id + "/jobs");
    await selectDraft(id);
  },
});
const theme =
  localStorage.getItem("weijin-theme") === "dark" ? "dark" : "light";
document.documentElement.dataset.theme = theme;
$("#theme-toggle").textContent = theme === "dark" ? "切换到浅色" : "切换到深色";
$("#theme-toggle").onclick = () => {
  const next =
    document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("weijin-theme", next);
  $("#theme-toggle").textContent =
    next === "dark" ? "切换到浅色" : "切换到深色";
};
$("#type-toggle").onclick = () => {
  document.body.classList.toggle("large-type");
  $("#type-toggle").textContent = document.body.classList.contains("large-type")
    ? "使用标准字号"
    : "使用大字号";
};
$("#focus-toggle").onclick = () => {
  document.body.classList.toggle("focus-mode");
  $("#focus-toggle").textContent = document.body.classList.contains(
    "focus-mode",
  )
    ? "退出专注"
    : "专注";
};
setupComponents();
const initial = location.hash.match(/^#branch\/([a-f0-9-]{36})$/);
(initial ? openBranch(initial[1]) : library()).catch((error) =>
  notice(error.message),
);
