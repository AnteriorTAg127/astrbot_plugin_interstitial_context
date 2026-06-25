const bridge = window.AstrBotPluginPage;
const $ = (id) => document.getElementById(id);

let allUsers = [];
let allSessions = [];
let currentTab = "user";
let selectedId = null;
let currentRecords = [];
let editingRecord = null;
let currentMutes = [];
let currentRels = [];

// ===== 选项卡切换 =====
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    currentTab = btn.dataset.tab;
    showPanel(currentTab);
  });
});

function showPanel(tab) {
  ["user", "session", "mute", "relationship"].forEach((t) => {
    const panel = document.getElementById("panel-" + t);
    if (panel) panel.style.display = t === tab ? "" : "none";
  });
  // 原有用户/会话标签使用 detail-panel（无 id），单独控制
  const mainPanel = document.querySelector(".detail-panel:not(.tab-panel)");
  if (mainPanel) {
    mainPanel.style.display = tab === "user" || tab === "session" ? "" : "none";
  }
  const sidebar = document.querySelector(".sidebar");
  if (sidebar) sidebar.style.display = tab === "user" || tab === "session" ? "" : "none";

  if (tab === "user" || tab === "session") {
    selectedId = null;
    $("search-input").value = "";
    renderDetailEmpty();
    renderList();
  }
  if (tab === "mute") loadMutes();
  if (tab === "relationship") {
    loadRelationships();
    loadRelationshipTypes();
  }
}

// ===== 加载数据 =====
async function loadUsers() {
  try {
    const data = await bridge.apiGet("users");
    allUsers = Array.isArray(data) ? data : [];
  } catch (e) {
    console.error("加载用户列表失败:", e);
    allUsers = [];
  }
}

async function loadSessions() {
  try {
    const data = await bridge.apiGet("sessions");
    allSessions = Array.isArray(data) ? data : [];
  } catch (e) {
    console.error("加载会话列表失败:", e);
    allSessions = [];
  }
}

// ===== 渲染左侧列表 =====
function renderList() {
  const listEl = $("id-list");
  const search = $("search-input").value.trim().toLowerCase();

  let items = [];
  if (currentTab === "user") {
    items = allUsers
      .filter((u) => {
        const text = (u.user_id + " " + (u.nickname || "")).toLowerCase();
        return text.includes(search);
      })
      .map((u) => {
        const label = u.nickname ? `${u.nickname}(${u.user_id})` : u.user_id;
        return { id: u.user_id, label };
      });
  } else if (currentTab === "session") {
    items = allSessions
      .filter((s) => {
        const text = (s.session_id + " " + (s.session_name || "")).toLowerCase();
        return text.includes(search);
      })
      .map((s) => {
        const label = s.session_name ? `${s.session_name}(${s.session_id})` : s.session_id;
        return { id: s.session_id, label };
      });
  }

  listEl.innerHTML = "";
  if (!items.length) {
    $("list-empty").classList.remove("hidden");
  } else {
    $("list-empty").classList.add("hidden");
    for (const item of items) {
      const li = document.createElement("li");
      li.textContent = item.label;
      li.dataset.id = item.id;
      if (selectedId === item.id) li.classList.add("active");
      li.addEventListener("click", () => selectItem(item.id));
      listEl.appendChild(li);
    }
  }
}

// ===== 搜索 =====
$("search-input").addEventListener("input", renderList);

// ===== 选择项 =====
async function selectItem(id) {
  selectedId = id;
  renderList();

  try {
    const data =
      currentTab === "user"
        ? await bridge.apiGet("affections", { user_id: id })
        : await bridge.apiGet("affections", { session_id: id });

    currentRecords = Array.isArray(data) ? data : data ? [data] : [];
    for (const r of currentRecords) {
      if (!r.level) r.level = "-";
    }
    renderDetail();
  } catch (e) {
    console.error("加载详情失败:", e);
    currentRecords = [];
    renderDetailEmpty("加载失败");
  }
}

function getSelectedLabel() {
  if (currentTab === "user") {
    const u = allUsers.find((x) => x.user_id === selectedId);
    return u && u.nickname ? `${u.nickname}(${selectedId})` : selectedId;
  }
  const s = allSessions.find((x) => x.session_id === selectedId);
  return s && s.session_name ? `${s.session_name}(${selectedId})` : selectedId;
}

function renderDetailEmpty(msg) {
  $("detail-title").textContent = msg || "请从左侧选择";
  $("btn-add").style.display = "none";
  $("detail-tbody").innerHTML = "";
  $("detail-empty").classList.remove("hidden");
  if (msg) $("detail-empty").textContent = msg;
  else $("detail-empty").textContent = "从左侧选择用户或会话以查看记录";
}

function renderDetail() {
  const tbody = $("detail-tbody");
  tbody.innerHTML = "";
  const label = getSelectedLabel();

  if (!currentRecords.length) {
    $("detail-title").textContent =
      currentTab === "user" ? `用户 ${label}` : `会话 ${label}`;
    $("btn-add").style.display = "inline-block";
    $("detail-empty").classList.remove("hidden");
    $("detail-empty").textContent = "暂无记录";
    return;
  }

  $("detail-empty").classList.add("hidden");
  $("btn-add").style.display = "inline-block";
  $("detail-title").textContent =
    currentTab === "user" ? `用户 ${label}` : `会话 ${label}`;
  $("th-id").textContent = currentTab === "user" ? "会话 ID" : "用户 ID";

  for (const r of currentRecords) {
    const tr = document.createElement("tr");
    const affClass = r.affection >= 0 ? "positive" : "negative";
    const idCell = currentTab === "user" ? esc(r.session_id) : esc(r.user_id);
    tr.innerHTML = `
      <td>${idCell}</td>
      <td>${esc(r.nickname || "-")}</td>
      <td><span class="affection-val ${affClass}">${r.affection}</span></td>
      <td><span class="level-badge">${esc(r.level || "-")}</span></td>
      <td class="actions">
        <button class="sm edit-btn" data-action="aff-edit" data-uid="${esc(r.user_id)}" data-sid="${esc(r.session_id)}">编辑</button>
        <button class="sm del-btn" data-action="aff-del" data-uid="${esc(r.user_id)}" data-sid="${esc(r.session_id)}">删除</button>
      </td>`;
    tbody.appendChild(tr);
  }
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = String(s);
  return d.innerHTML;
}

function formatTime(iso) {
  if (!iso) return "-";
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString();
  } catch {
    return iso;
  }
}

// ===== 添加按钮（好感度） =====
$("btn-add").addEventListener("click", () => {
  const prefillUser = currentTab === "user" ? selectedId : "";
  const prefillSession = currentTab === "session" ? selectedId : "";
  openModal("add", null, prefillUser, prefillSession);
});

// ===== 统一表格按钮代理 =====
document.addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-action]");
  if (!btn) return;
  const action = btn.dataset.action;

  // —— 好感度编辑/删除 ——
  if (action === "aff-edit") {
    const uid = btn.dataset.uid;
    const sid = btn.dataset.sid;
    const rec = currentRecords.find((r) => r.user_id === uid && r.session_id === sid);
    if (rec) openModal("edit", rec);
    return;
  }
  if (action === "aff-del") {
    const uid = btn.dataset.uid;
    const sid = btn.dataset.sid;
    showConfirm(`确定删除用户 ${uid} 在会话 ${sid} 的记录？`, async () => {
      try {
        await bridge.apiGet("affections/delete", { user_id: uid, session_id: sid });
        if (selectedId) await selectItem(selectedId);
        await Promise.all([loadUsers(), loadSessions()]);
        renderList();
      } catch (err) {
        alert("删除失败: " + err.message);
      }
    });
    return;
  }

  // —— 屏蔽解除 ——
  if (action === "mute-del") {
    const mid = btn.dataset.mid;
    showConfirm(`确定解除屏蔽记录 #${mid}？`, async () => {
      try {
        await bridge.apiPost("freeze_list/remove", { id: mid });
        loadMutes();
      } catch (err) {
        alert("解除失败: " + err.message);
      }
    });
    return;
  }

  // —— 关系编辑/解绑 ——
  if (action === "rel-edit") {
    const uid = btn.dataset.uid;
    const rec = currentRels.find((r) => r.user_id === uid);
    if (rec) openRelModal("edit", rec);
    return;
  }
  if (action === "rel-del") {
    const uid = btn.dataset.uid;
    showConfirm(`确定解绑用户 ${uid} 的关系？`, async () => {
      try {
        await bridge.apiPost("relationships/unbind", { user_id: uid });
        loadRelationships();
      } catch (err) {
        alert("解绑失败: " + err.message);
      }
    });
    return;
  }
});

// ===== 模态框（好感度） =====
function openModal(mode, record, prefillUser = "", prefillSession = "") {
  editingRecord = mode === "edit" ? record : null;
  $("modal-title").textContent = mode === "edit" ? "编辑记录" : "添加记录";
  $("m-user").value = mode === "edit" ? record.user_id : prefillUser;
  $("m-session").value = mode === "edit" ? record.session_id : prefillSession;
  $("m-nickname").value = mode === "edit" ? (record.nickname || "") : "";
  $("m-affection").value = mode === "edit" ? record.affection : "0";
  $("m-user").disabled = mode === "edit";
  $("m-session").disabled = mode === "edit";
  $("modal").classList.remove("hidden");
}

$("btn-modal-cancel").addEventListener("click", () => $("modal").classList.add("hidden"));

$("btn-modal-ok").addEventListener("click", async () => {
  const userId = $("m-user").value.trim();
  const sessionId = $("m-session").value.trim();
  const nickname = $("m-nickname").value.trim();
  const affection = parseInt($("m-affection").value);
  if (!userId || !sessionId || isNaN(affection)) return;

  try {
    if (editingRecord) {
      await bridge.apiPost("affections/update", { user_id: userId, session_id: sessionId, nickname, affection });
    } else {
      await bridge.apiPost("affections", { user_id: userId, session_id: sessionId, nickname, affection });
    }
    $("modal").classList.add("hidden");
    if (selectedId) await selectItem(selectedId);
    await Promise.all([loadUsers(), loadSessions()]);
    renderList();
  } catch (e) {
    console.error("保存失败:", e);
    alert("保存失败: " + e.message);
  }
});

// ===== 确认弹窗 =====
let confirmAction = null;

function showConfirm(text, action) {
  $("confirm-text").textContent = text;
  confirmAction = action;
  $("confirm-modal").classList.remove("hidden");
}

$("btn-confirm-cancel").addEventListener("click", () => {
  $("confirm-modal").classList.add("hidden");
  confirmAction = null;
});

$("btn-confirm-ok").addEventListener("click", async () => {
  if (confirmAction) {
    const action = confirmAction;
    confirmAction = null;
    $("confirm-modal").classList.add("hidden");
    await action();
  }
});

// ==================== 屏蔽管理 ====================

async function loadMutes() {
  const tbody = $("mute-tbody");
  tbody.innerHTML = "";
  try {
    const data = await bridge.apiGet("freeze_list", {});
    currentMutes = Array.isArray(data) ? data : [];
    const now = new Date();
    $("mute-empty").classList.toggle("hidden", currentMutes.length > 0);
    for (const m of currentMutes) {
      const start = new Date(m.mute_start);
      const elapsed = (now - start) / 60000;
      const remaining = Math.max(0, m.mute_duration_minutes - elapsed);
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${esc(m.user_id)}</td>
        <td>${esc(m.session_id)}</td>
        <td>${esc(m.muted_by)}</td>
        <td>${esc(m.mute_reason || "-")}</td>
        <td>${m.mute_duration_minutes}min (剩余${Math.ceil(remaining)}min)</td>
        <td class="actions">
          <button class="sm del-btn" data-action="mute-del" data-mid="${m.id}">解除</button>
        </td>`;
      tbody.appendChild(tr);
    }
  } catch (e) {
    console.error("加载屏蔽列表失败:", e);
  }
}

$("btn-add-mute").addEventListener("click", () => {
  $("mu-user").value = "";
  $("mu-session").value = "";
  $("mu-duration").value = "60";
  $("mu-reason").value = "";
  $("mute-modal-title").textContent = "添加屏蔽";
  $("mute-modal").classList.remove("hidden");
});

$("btn-mute-cancel").addEventListener("click", () => $("mute-modal").classList.add("hidden"));

$("btn-mute-ok").addEventListener("click", async () => {
  const userId = $("mu-user").value.trim();
  const sessionId = $("mu-session").value.trim();
  const duration = parseInt($("mu-duration").value);
  const reason = $("mu-reason").value.trim();
  if (!userId || !sessionId || isNaN(duration) || duration <= 0) {
    alert("请填写完整：用户ID、会话ID、时长(>0)");
    return;
  }
  try {
    await bridge.apiPost("freeze_list/add", { user_id: userId, session_id: sessionId, duration_minutes: duration, reason });
    $("mute-modal").classList.add("hidden");
    loadMutes();
  } catch (e) {
    alert("添加失败: " + e.message);
  }
});

// ==================== 关系管理 ====================

async function loadRelationships() {
  const tbody = $("rel-tbody");
  tbody.innerHTML = "";
  try {
    const data = await bridge.apiGet("relationships", {});
    currentRels = Array.isArray(data) ? data : [];
    $("rel-empty").classList.toggle("hidden", currentRels.length > 0);
    for (const r of currentRels) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${esc(r.user_id)}</td>
        <td>${esc(r.relation_type)}</td>
        <td>${esc(r.relation_desc || "-")}</td>
        <td>${esc(r.bound_by)}</td>
        <td>${esc(formatTime(r.bound_at))}</td>
        <td class="actions">
          <button class="sm edit-btn" data-action="rel-edit" data-uid="${esc(r.user_id)}">编辑</button>
          <button class="sm del-btn" data-action="rel-del" data-uid="${esc(r.user_id)}">解绑</button>
        </td>`;
      tbody.appendChild(tr);
    }
  } catch (e) {
    console.error("加载关系列表失败:", e);
  }
}

async function loadRelationshipTypes() {
  const tbody = $("rel-types-tbody");
  if (!tbody) return;
  tbody.innerHTML = "";
  try {
    const data = await bridge.apiGet("relationship-types", {});
    const types = Array.isArray(data) ? data : [];
    const emptyEl = $("rel-types-empty");
    if (emptyEl) emptyEl.classList.toggle("hidden", types.length > 0);
    for (const t of types) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${esc(t.type || "-")}</td>
        <td>${esc(t.description || "-")}</td>`;
      tbody.appendChild(tr);
    }
  } catch (e) {
    console.error("加载预设关系类型失败:", e);
  }
}

$("btn-add-rel").addEventListener("click", () => {
  $("r-user").value = "";
  $("r-type").value = "";
  $("r-desc").value = "";
  $("rel-modal-title").textContent = "添加关系";
  $("rel-modal").classList.remove("hidden");
});

$("btn-rel-cancel").addEventListener("click", () => $("rel-modal").classList.add("hidden"));

$("btn-rel-ok").addEventListener("click", async () => {
  const userId = $("r-user").value.trim();
  const type = $("r-type").value.trim();
  const desc = $("r-desc").value.trim();
  if (!userId || !type) {
    alert("请填写完整：用户ID、关系类型");
    return;
  }
  try {
    await bridge.apiPost("relationships/add", { user_id: userId, relation_type: type, relation_desc: desc });
    $("rel-modal").classList.add("hidden");
    loadRelationships();
  } catch (e) {
    alert("添加失败: " + e.message);
  }
});

function openRelModal(mode, record) {
  $("r-user").value = record.user_id || "";
  $("r-type").value = record.relation_type || "";
  $("r-desc").value = record.relation_desc || "";
  $("rel-modal-title").textContent = mode === "edit" ? "编辑关系" : "添加关系";
  $("rel-modal").classList.remove("hidden");
}

// ===== 初始化 =====
await bridge.ready();
await Promise.all([loadUsers(), loadSessions()]);
renderList();
