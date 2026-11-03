"use strict";

// ---------------------------------------------------------------- helpers
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "text") el.textContent = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

async function api(path, { method = "GET", body, form, raw } = {}) {
  const opts = { method, credentials: "same-origin", headers: {} };
  if (method !== "GET") opts.headers["X-Recall"] = "1";
  if (form) opts.body = form;
  else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (res.status === 401 && path !== "/api/login") {
    showLogin();
    throw new ApiError(401, "Please sign in again");
  }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : (j.detail?.[0]?.msg || msg);
    } catch (_) { /* ignore */ }
    throw new ApiError(res.status, msg);
  }
  return raw ? res : res.json();
}

let toastTimer;
function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("error", isError);
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3500);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------- state
const state = { user: null, meta: { industries: [], tags: [], count: 0 } };

const FIELD_LABELS = {
  name: "Name", name_native: "Name (native)", title: "Title", title_native: "Title (native)",
  department: "Department", company: "Company", company_native: "Company (native)",
  phones: "Phones", emails: "Emails", website: "Website", address: "Address",
  address_native: "Address (native)", city: "City", province_state: "Province / State",
  country: "Country", postal_code: "Postal code", wechat: "WeChat", whatsapp: "WhatsApp",
  linkedin: "LinkedIn", other_contacts: "Other contacts", industry: "Industry",
  description: "Description", notes: "Notes", tags: "Tags", created_at: "Added", raw_text: "Card text",
};

// Form layout. type: text | textarea | phones | lines | tags
const USER_FIELDS = [
  ["industry", "text", "e.g. Furniture"],
  ["description", "textarea", "What they supply / do, where you met…"],
  ["tags", "tags", "comma separated, e.g. canton-fair, sofas"],
  ["notes", "textarea", ""],
];
const CARD_FIELDS = [
  ["name", "text"], ["name_native", "text"],
  ["title", "text"], ["title_native", "text"],
  ["company", "text"], ["company_native", "text"],
  ["department", "text"],
  ["phones", "phones", "one per line, e.g. mobile: +86 138 0000 0000"],
  ["emails", "lines", "one per line"],
  ["website", "text"], ["wechat", "text"], ["whatsapp", "text"], ["linkedin", "text"],
  ["other_contacts", "lines", "one per line, e.g. QQ: 12345"],
  ["address", "textarea"], ["address_native", "textarea"],
  ["city", "text"], ["province_state", "text"], ["country", "text"], ["postal_code", "text"],
];
const PHONE_TYPES = ["mobile", "office", "fax", "other"];

function phonesToText(phones) { return (phones || []).map((p) => `${p.type}: ${p.number}`).join("\n"); }
function textToPhones(text) {
  return text.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
    const m = l.match(/^(mobile|office|fax|other)\s*[:：]\s*(.+)$/i);
    return m ? { type: m[1].toLowerCase(), number: m[2].trim() } : { type: "other", number: l };
  });
}
const linesToList = (t) => t.split("\n").map((s) => s.trim()).filter(Boolean);
const tagsToList = (t) => t.split(/[,，]/).map((s) => s.trim()).filter(Boolean);

function fieldToText(type, value) {
  if (type === "phones") return phonesToText(value);
  if (type === "lines") return (value || []).join("\n");
  if (type === "tags") return (value || []).join(", ");
  return value || "";
}
function textToField(type, text) {
  if (type === "phones") return textToPhones(text);
  if (type === "lines") return linesToList(text);
  if (type === "tags") return tagsToList(text);
  return text.trim();
}

function displayValue(field, value) {
  if (field === "phones") return (value || []).map((p) => `${p.number} (${p.type})`).join("\n");
  if (Array.isArray(value)) return value.join(field === "tags" ? ", " : "\n");
  if (field === "created_at" && value) return new Date(value).toLocaleDateString();
  return value || "";
}

// ---------------------------------------------------------------- contact form
/**
 * Builds an editable contact form. `fill(extraction)` merges AI results into
 * fields the user has not typed in, so manual input is never overwritten.
 */
let formCounter = 0;
function contactForm(container, initial, { submitLabel, onSubmit, onCancel }) {
  container.replaceChildren();
  const inputs = {};
  const dirty = new Set();
  const images = { front_image: initial.front_image || null, back_image: initial.back_image || null };
  let rawText = initial.raw_text || "";

  const listId = `industry-options-${++formCounter}`;
  const industryList = h("datalist", { id: listId, class: "industry-options" },
    state.meta.industries.map((i) => h("option", { value: i })));

  function makeField([name, type, placeholder]) {
    const id = `f-${name}`;
    const value = fieldToText(type, initial[name]);
    const el = (type === "text")
      ? h("input", { id, name, value, placeholder: placeholder || "", autocomplete: "off",
                     list: name === "industry" ? listId : null })
      : h("textarea", { id, name, rows: type === "textarea" ? 3 : 2, placeholder: placeholder || "" });
    if (type !== "text") el.value = value;
    el.addEventListener("input", () => dirty.add(name));
    inputs[name] = { el, type };
    return h("label", { for: id, class: type === "text" ? "" : "wide" }, FIELD_LABELS[name], el);
  }

  const form = h("form", { class: "card-form" },
    industryList,
    h("fieldset", {}, h("legend", { text: "Your details" }), USER_FIELDS.map(makeField)),
    h("fieldset", { class: "card-fields" }, h("legend", { text: "From the card" }), CARD_FIELDS.map(makeField)),
  );
  const rawBox = h("details", { class: "raw" }, h("summary", { text: "Card text" }), h("pre", { text: rawText }));
  const error = h("p", { class: "error", hidden: true });
  const actions = h("div", { class: "row" },
    h("button", { type: "submit", class: "primary", text: submitLabel || "Save" }),
    onCancel ? h("button", { type: "button", class: "ghost", text: "Cancel", onclick: onCancel }) : null,
  );
  form.append(rawBox, error, actions);
  container.append(form);

  function values() {
    const out = { ...images, raw_text: rawText };
    for (const [name, { el, type }] of Object.entries(inputs)) out[name] = textToField(type, el.value);
    return out;
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    error.hidden = true;
    const btn = $("button[type=submit]", form);
    btn.disabled = true;
    try {
      await onSubmit(values());
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
    } finally {
      btn.disabled = false;
    }
  });

  return {
    values,
    setImages(front, back) { images.front_image = front; images.back_image = back; },
    fill(extraction) {
      for (const [name, { el, type }] of Object.entries(inputs)) {
        if (!(name in extraction) || dirty.has(name) || el.value.trim()) continue;
        el.value = fieldToText(type, extraction[name]);
        if (el.value) { el.classList.add("filled"); setTimeout(() => el.classList.remove("filled"), 1500); }
      }
      if (extraction.raw_text && !rawText) { rawText = extraction.raw_text; $("pre", rawBox).textContent = rawText; }
    },
  };
}

// ---------------------------------------------------------------- scan view
let scanCtl = null;
let scanJob = 0;

function resetScan() {
  for (const side of ["front", "back"]) {
    const pick = $(`#${side}-pick`);
    $("input", pick).value = "";
    const img = $("img", pick);
    if (img.src) URL.revokeObjectURL(img.src);
    img.removeAttribute("src");
    img.hidden = true;
    pick.classList.remove("has-image");
  }
  $("#read-btn").disabled = true;
  $("#scan-status").textContent = "";
  scanJob++;
  scanCtl = contactForm($("#scan-form"), {}, { submitLabel: "Save contact", onSubmit: saveScanned });
}

async function confirmDuplicates(values, excludeId) {
  const dupes = await api("/api/contacts/duplicates", {
    method: "POST",
    body: { emails: values.emails, phones: values.phones.map((p) => p.number), exclude_id: excludeId || null },
  });
  if (!dupes.length) return true;
  const names = dupes.map((d) => [d.name, d.company].filter(Boolean).join(" – ") || "(unnamed)").join("\n");
  return confirm(`You already have a contact with the same email or phone:\n\n${names}\n\nSave anyway?`);
}

async function saveScanned(values) {
  if (!Object.entries(values).some(([k, v]) => !["front_image", "back_image", "raw_text"].includes(k) && (Array.isArray(v) ? v.length : v))) {
    throw new Error("Nothing to save yet – add at least a name, company or phone.");
  }
  if (!(await confirmDuplicates(values))) return;
  await api("/api/contacts", { method: "POST", body: values });
  toast("Contact saved");
  await loadMeta();
  resetScan();
}

function setupScan() {
  for (const side of ["front", "back"]) {
    const pick = $(`#${side}-pick`);
    $("input", pick).addEventListener("change", (e) => {
      const file = e.target.files[0];
      const img = $("img", pick);
      if (img.src) URL.revokeObjectURL(img.src);
      if (file) {
        img.src = URL.createObjectURL(file);
        img.hidden = false;
        pick.classList.add("has-image");
      }
      $("#read-btn").disabled = !$("#front-input").files[0];
    });
  }
  $("#read-btn").addEventListener("click", readCard);
  resetScan();
}

async function readCard() {
  const front = $("#front-input").files[0];
  if (!front) return;
  const back = $("#back-input").files[0];
  const status = $("#scan-status");
  const myJob = ++scanJob;
  const ctl = scanCtl;
  $("#read-btn").disabled = true;
  status.className = "status busy";
  status.textContent = "Uploading…";
  try {
    const form = new FormData();
    form.append("front", front);
    if (back) form.append("back", back);
    const started = await api("/api/cards/extract", { method: "POST", form });
    ctl.setImages(started.front_image, started.back_image);
    status.textContent = "Reading card…";
    const deadline = Date.now() + 180000;
    while (Date.now() < deadline) {
      await sleep(1500);
      if (myJob !== scanJob) return; // form was reset
      const job = await api(`/api/cards/extract/${started.job_id}`);
      if (job.status === "done") {
        ctl.fill(job.result);
        status.className = "status ok";
        status.textContent = "Card read – please check the details.";
        return;
      }
      if (job.status === "error") throw new Error(job.error);
    }
    throw new Error("Reading the card took too long.");
  } catch (err) {
    if (myJob !== scanJob) return;
    status.className = "status err";
    status.textContent = `${err.message} You can still type the details in.`;
  } finally {
    if (myJob === scanJob) $("#read-btn").disabled = !$("#front-input").files[0];
  }
}

// ---------------------------------------------------------------- downloads
async function download(body) {
  const res = await api("/api/export", { method: "POST", body, raw: true });
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = h("a", { href: url, download: `recall-contacts.${body.fmt}` });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

// ---------------------------------------------------------------- ask view
const EXAMPLES = [
  "Give me details of all furniture suppliers",
  "Give me emails of CEOs based in Guangzhou",
  "Phone numbers of everyone I tagged canton-fair",
  "Contacts in Shenzhen with WeChat",
];

function setupAsk() {
  $("#ask-examples").replaceChildren(...EXAMPLES.map((q) =>
    h("button", { type: "button", class: "chip", text: q, onclick: () => { $("#ask-input").value = q; runQuery(); } })));
  $("#ask-form").addEventListener("submit", (e) => { e.preventDefault(); runQuery(); });
}

async function runQuery() {
  const q = $("#ask-input").value.trim();
  if (!q) return;
  const out = $("#ask-result");
  out.replaceChildren(h("p", { class: "status busy", text: "Searching…" }));
  try {
    const res = await api("/api/query", { method: "POST", body: { question: q } });
    renderResults(out, res);
  } catch (err) {
    out.replaceChildren(h("p", { class: "error", text: err.message }));
  }
}

function renderResults(out, res) {
  const { contacts, fields, explanation } = res;
  const header = h("div", { class: "result-head" },
    h("p", {}, h("strong", { text: `${contacts.length} contact${contacts.length === 1 ? "" : "s"}` }), " · ", explanation),
  );
  if (!contacts.length) {
    out.replaceChildren(header, h("p", { class: "muted", text: "No matching contacts. Try rephrasing, or check the industry/description on your cards." }));
    return;
  }
  const ids = contacts.map((c) => c.id);
  header.append(h("div", { class: "row export-row" },
    h("button", { type: "button", class: "ghost", text: "Export CSV", onclick: () => download({ fmt: "csv", ids, fields }) }),
    h("button", { type: "button", class: "ghost", text: "Export Excel", onclick: () => download({ fmt: "xlsx", ids, fields }) }),
  ));
  const table = h("table", { class: "results" },
    h("thead", {}, h("tr", {}, fields.map((f) => h("th", { text: FIELD_LABELS[f] || f })))),
    h("tbody", {}, contacts.map((c) => h("tr", { onclick: () => showContact(c.id), tabindex: "0" },
      fields.map((f) => h("td", { text: displayValue(f, c[f]) }))))),
  );
  out.replaceChildren(header, h("div", { class: "table-wrap" }, table));
}

// ---------------------------------------------------------------- contacts view
let searchTimer;
async function loadContacts() {
  const q = $("#contacts-search").value.trim();
  const list = $("#contacts-list");
  try {
    const contacts = await api(`/api/contacts?q=${encodeURIComponent(q)}`);
    $("#contact-count").textContent = `(${state.meta.count})`;
    if (!contacts.length) {
      list.replaceChildren(h("li", { class: "muted empty", text: q ? "No matches." : "No contacts yet – scan your first card." }));
      return;
    }
    list.replaceChildren(...contacts.map((c) => h("li", {},
      h("button", { type: "button", class: "contact-item", onclick: () => showContact(c.id) },
        h("span", { class: "c-name", text: [c.name, c.name_native].filter(Boolean).join(" · ") || "(no name)" }),
        h("span", { class: "c-sub", text: [c.title || c.title_native, c.company || c.company_native].filter(Boolean).join(" – ") }),
        h("span", { class: "c-meta", text: [c.industry, c.city, c.country].filter(Boolean).join(" · ") }),
      ))));
  } catch (err) {
    list.replaceChildren(h("li", { class: "error", text: err.message }));
  }
}

function setupContacts() {
  $("#contacts-search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadContacts, 250); });
  $$("[data-export]", $("#view-contacts")).forEach((b) => b.addEventListener("click", () => download({ fmt: b.dataset.export })));
}

// ---------------------------------------------------------------- modal / detail
const modal = () => $("#modal");
function openModal(...content) {
  $("#modal-body").replaceChildren(h("button", { type: "button", class: "close", "aria-label": "Close", text: "×", onclick: closeModal }), ...content);
  if (!modal().open) modal().showModal();
}
function closeModal() { if (modal().open) modal().close(); }

async function showContact(id) {
  let c;
  try { c = await api(`/api/contacts/${id}`); } catch (err) { toast(err.message, true); return; }
  const imgs = [c.front_image, c.back_image].filter(Boolean).map((img) =>
    h("a", { href: `/api/images/${img}`, target: "_blank", rel: "noopener" }, h("img", { src: `/api/images/${img}`, alt: "Card photo", class: "card-photo" })));
  const order = ["name", "name_native", "title", "title_native", "department", "company", "company_native", "industry", "description",
    "phones", "emails", "website", "wechat", "whatsapp", "linkedin", "other_contacts", "address", "address_native",
    "city", "province_state", "country", "postal_code", "tags", "notes", "created_at"];
  const rows = order.filter((f) => displayValue(f, c[f])).map((f) => {
    let val = displayValue(f, c[f]);
    let node = h("dd", { text: val });
    if (f === "emails") node = h("dd", {}, c.emails.map((e) => h("div", {}, h("a", { href: `mailto:${e}`, text: e }))));
    if (f === "phones") node = h("dd", {}, c.phones.map((p) => h("div", {}, h("a", { href: `tel:${p.number.replace(/[^\d+]/g, "")}`, text: p.number }), ` (${p.type})`)));
    return [h("dt", { text: FIELD_LABELS[f] }), node];
  });
  openModal(
    h("h3", { text: c.name || c.name_native || "(no name)" }),
    imgs.length ? h("div", { class: "photo-row" }, imgs) : null,
    h("dl", { class: "details" }, rows.flat()),
    c.raw_text ? h("details", { class: "raw" }, h("summary", { text: "Card text" }), h("pre", { text: c.raw_text })) : null,
    h("div", { class: "row" },
      h("button", { type: "button", class: "primary", text: "Edit", onclick: () => editContact(c) }),
      h("button", { type: "button", class: "danger", text: "Delete", onclick: () => deleteContact(c) }),
    ),
  );
}

function editContact(c) {
  const box = h("div");
  openModal(h("h3", { text: "Edit contact" }), box);
  contactForm(box, c, {
    submitLabel: "Save changes",
    onCancel: () => showContact(c.id),
    onSubmit: async (values) => {
      if (!(await confirmDuplicates(values, c.id))) return;
      await api(`/api/contacts/${c.id}`, { method: "PUT", body: values });
      toast("Saved");
      await loadMeta();
      refreshCurrent();
      showContact(c.id);
    },
  });
}

async function deleteContact(c) {
  if (!confirm(`Delete ${c.name || c.name_native || "this contact"}? This also deletes the card photos.`)) return;
  try {
    await api(`/api/contacts/${c.id}`, { method: "DELETE" });
    closeModal();
    toast("Deleted");
    await loadMeta();
    refreshCurrent();
  } catch (err) { toast(err.message, true); }
}

// ---------------------------------------------------------------- account
async function showAccount() {
  let usage = null;
  try { usage = await api("/api/me/usage"); } catch (_) { /* ignore */ }
  const form = h("form", { class: "card-form compact" },
    h("label", {}, "Current password", h("input", { name: "current_password", type: "password", required: true, autocomplete: "current-password" })),
    h("label", {}, "New password", h("input", { name: "new_password", type: "password", required: true, minlength: "8", autocomplete: "new-password" })),
    h("button", { type: "submit", class: "primary", text: "Change password" }),
  );
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    try {
      await api("/api/me/password", { method: "POST", body: Object.fromEntries(fd) });
      toast("Password changed. Other devices were signed out.");
      closeModal();
    } catch (err) { toast(err.message, true); }
  });
  const scans = usage?.by_kind?.extract?.calls || 0;
  const queries = usage?.by_kind?.query?.calls || 0;
  openModal(
    h("h3", { text: state.user.username }),
    usage ? h("p", { class: "muted", text: `This month: ${scans} card${scans === 1 ? "" : "s"} read, ${queries} question${queries === 1 ? "" : "s"} · AI cost ≈ $${usage.total_cost_usd.toFixed(2)}` }) : null,
    h("h4", { text: "Change password" }), form,
    h("div", { class: "row" }, h("button", { type: "button", class: "danger", text: "Sign out", onclick: logout })),
  );
}

async function logout() {
  try { await api("/api/logout", { method: "POST" }); } catch (_) { /* ignore */ }
  // Reload so nothing from this user's session stays in memory or on screen.
  location.replace("/");
}

// ---------------------------------------------------------------- admin
async function loadUsers() {
  const table = $("#users-table");
  try {
    const [users, usage] = await Promise.all([api("/api/admin/users"), api("/api/admin/usage")]);
    $("#admin-total").textContent = `AI cost this month, all users: ≈ $${usage.total_cost_usd.toFixed(2)}`;
    table.replaceChildren(
      h("thead", {}, h("tr", {}, ["User", "Role", "Status", "AI cost (month)", ""].map((t) => h("th", { text: t })))),
      h("tbody", {}, users.map((u) => {
        const self = u.id === state.user.id;
        return h("tr", {},
          h("td", { text: u.username }),
          h("td", { text: u.is_admin ? "Admin" : "User" }),
          h("td", { text: u.disabled ? "Disabled" : "Active" }),
          h("td", { text: `$${u.ai_cost_this_month.toFixed(2)}` }),
          h("td", { class: "actions" },
            h("button", { type: "button", class: "ghost small", text: "Reset password", onclick: () => adminResetPassword(u) }),
            self ? null : h("button", { type: "button", class: "ghost small", text: u.disabled ? "Enable" : "Disable", onclick: () => adminUpdate(u, { disabled: !u.disabled }) }),
            self ? null : h("button", { type: "button", class: "ghost small", text: u.is_admin ? "Make user" : "Make admin", onclick: () => adminUpdate(u, { is_admin: !u.is_admin }) }),
            self ? null : h("button", { type: "button", class: "danger small", text: "Delete", onclick: () => adminDelete(u) }),
          ));
      })),
    );
  } catch (err) { table.replaceChildren(h("tr", {}, h("td", { class: "error", text: err.message }))); }
}

async function adminUpdate(u, body) {
  try { await api(`/api/admin/users/${u.id}`, { method: "PATCH", body }); loadUsers(); } catch (err) { toast(err.message, true); }
}
async function adminResetPassword(u) {
  const pw = prompt(`New temporary password for ${u.username} (min 8 characters):`);
  if (!pw) return;
  try { await api(`/api/admin/users/${u.id}`, { method: "PATCH", body: { password: pw } }); toast("Password reset; the user was signed out."); }
  catch (err) { toast(err.message, true); }
}
async function adminDelete(u) {
  const typed = prompt(`This permanently deletes ${u.username}'s account AND all of their cards and photos.\nType the username to confirm:`);
  if (typed !== u.username) return;
  try { await api(`/api/admin/users/${u.id}`, { method: "DELETE" }); toast("User deleted"); loadUsers(); }
  catch (err) { toast(err.message, true); }
}

function setupAdmin() {
  const form = $("#new-user-form");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    try {
      await api("/api/admin/users", { method: "POST", body: {
        username: fd.get("username"), password: fd.get("password"), is_admin: fd.get("is_admin") === "on",
      } });
      form.reset();
      toast("User added");
      loadUsers();
    } catch (err) { toast(err.message, true); }
  });
}

// ---------------------------------------------------------------- navigation / boot
let currentView = "scan";
function showView(name) {
  currentView = name;
  for (const v of ["scan", "ask", "contacts", "admin"]) $(`#view-${v}`).hidden = v !== name;
  $$(".tabbar button").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  refreshCurrent();
}
function refreshCurrent() {
  if (currentView === "contacts") loadContacts();
  if (currentView === "admin") loadUsers();
}

async function loadMeta() {
  try { state.meta = await api("/api/meta"); } catch (_) { /* ignore */ }
  $$("datalist.industry-options").forEach((dl) =>
    dl.replaceChildren(...state.meta.industries.map((i) => h("option", { value: i }))));
}

function clearUserData() {
  // Wipe everything that may show the previous user's contacts.
  state.meta = { industries: [], tags: [], count: 0 };
  $("#modal-body").replaceChildren();
  $("#ask-result").replaceChildren();
  $("#ask-input").value = "";
  $("#contacts-list").replaceChildren();
  $("#contacts-search").value = "";
  $("#contact-count").textContent = "";
  $("#users-table").replaceChildren();
  $("#admin-total").textContent = "";
  $("#scan-form").replaceChildren();
}

function showLogin() {
  state.user = null;
  closeModal();
  clearUserData();
  $("#app-view").hidden = true;
  $("#login-view").hidden = false;
}

async function enterApp(user) {
  state.user = user;
  $("#login-view").hidden = true;
  $("#app-view").hidden = false;
  $("#account-btn").textContent = user.username;
  $("#admin-tab").hidden = !user.is_admin;
  await loadMeta();
  resetScan();
  showView("scan");
}

function setupLogin() {
  const form = $("#login-form");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = $("#login-error");
    err.hidden = true;
    const fd = new FormData(form);
    try {
      const user = await api("/api/login", { method: "POST", body: { username: fd.get("username"), password: fd.get("password") } });
      form.reset();
      enterApp(user);
    } catch (ex) {
      err.textContent = ex.message;
      err.hidden = false;
    }
  });
}

async function boot() {
  setupLogin();
  setupScan();
  setupAsk();
  setupContacts();
  setupAdmin();
  $$(".tabbar button").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));
  $("#account-btn").addEventListener("click", showAccount);
  modal().addEventListener("click", (e) => { if (e.target === modal()) closeModal(); });
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  try {
    const user = await api("/api/me");
    enterApp(user);
  } catch (_) {
    showLogin();
  }
}

document.addEventListener("DOMContentLoaded", boot);
