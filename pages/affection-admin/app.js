const bridge = window.AstrBotPluginPage;
const $ = (id) => document.getElementById(id);

let allUsers = [];
let allSessions = [];
let currentTab = "user";
let selectedId = null;
let currentRecords = [];
let editingRecord = null;

// ===== 选项卡切换 =====
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    currentTab = btn.dataset.tab;
    selectedId = null;
    $("search-input").value = "";
    renderDetailEmpty();
    renderList();
  });
});

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
  } else {
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
        <button class="sm edit-btn" data-uid="${esc(r.user_id)}" data-sid="${esc(r.session_id)}">编辑</button>
        <button class="sm del-btn" data-uid="${esc(r.user_id)}" data-sid="${esc(r.session_id)}">删除</button>
      </td>`;
    tbody.appendChild(tr);
  }
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = String(s);
  return d.innerHTML;
}

// ===== 添加按钮 =====
$("btn-add").addEventListener("click", () => {
  const prefillUser = currentTab === "user" ? selectedId : "";
  const prefillSession = currentTab === "session" ? selectedId : "";
  openModal("add", null, prefillUser, prefillSession);
});

// ===== 表格操作代理 =====
document.addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  const uid = btn.dataset.uid;
  const sid = btn.dataset.sid;
  if (!uid || !sid) return;

  if (btn.classList.contains("edit-btn")) {
    const rec = currentRecords.find((r) => r.user_id === uid && r.session_id === sid);
    if (rec) openModal("edit", rec);
  }
  if (btn.classList.contains("del-btn")) {
    openConfirmDelete(uid, sid);
  }
});

// ===== 模态框 =====
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

// ===== 删除确认 =====
let deleteTarget = null;

function openConfirmDelete(uid, sid) {
  deleteTarget = { user_id: uid, session_id: sid };
  $("confirm-text").textContent = `确定删除用户 ${uid} 在会话 ${sid} 的记录？`;
  $("confirm-modal").classList.remove("hidden");
}

$("btn-confirm-cancel").addEventListener("click", () => $("confirm-modal").classList.add("hidden"));

$("btn-confirm-ok").addEventListener("click", async () => {
  if (!deleteTarget) return;
  try {
    await bridge.apiGet("affections/delete", deleteTarget);
    $("confirm-modal").classList.add("hidden");
    deleteTarget = null;
    if (selectedId) await selectItem(selectedId);
    await Promise.all([loadUsers(), loadSessions()]);
    renderList();
  } catch (e) {
    console.error("删除失败:", e);
    alert("删除失败: " + e.message);
  }
});

// ===== 初始化 =====
await bridge.ready();
await Promise.all([loadUsers(), loadSessions()]);
renderList();
