"use strict";

const $ = (sel) => document.querySelector(sel);
const el = (tag, props = {}, ...kids) => {
  const n = Object.assign(document.createElement(tag), props);
  for (const k of kids) n.append(k);
  return n;
};

let current = null; // path of open file
let dirty = false;

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), 1800);
}

const IMG = /\.(png|jpe?g|gif|svg|webp)$/i;
const PDF = /\.pdf$/i;

// --- tree ------------------------------------------------------------------

async function loadTree() {
  const nodes = await api("/api/tree");
  const root = $("#tree");
  root.textContent = "";
  root.append(renderNodes(nodes));
}

function renderNodes(nodes) {
  const frag = document.createDocumentFragment();
  for (const node of nodes) frag.append(renderNode(node));
  return frag;
}

function renderNode(node) {
  const wrap = el("div", { className: "node" });
  const isFolder = node.type === "folder";
  const twist = el("span", { className: "tw", textContent: isFolder ? "▸" : "" });
  const label = el("span", { textContent: node.name });
  const row = el("div", { className: "row" }, twist, label);
  row.dataset.path = node.path;
  if (node.tags && node.tags.length)
    row.append(el("span", { className: "tag", textContent: " #" + node.tags.join(" #") }));

  if (isFolder) {
    const children = el("div", { className: "children hidden" });
    let built = false;
    row.onclick = () => {
      const open = children.classList.toggle("hidden");
      twist.textContent = open ? "▸" : "▾";
      if (!built) { children.append(renderNodes(node.children || [])); built = true; }
    };
    wrap.append(row, children);
  } else {
    row.onclick = () => openFile(node.path, row);
    wrap.append(row);
  }
  return wrap;
}

function markActive(row) {
  document.querySelectorAll(".node .row.active").forEach((r) => r.classList.remove("active"));
  if (row) row.classList.add("active");
}

// --- open / view / edit ----------------------------------------------------

async function openFile(path, row) {
  if (dirty && !confirm("Discard unsaved changes?")) return;
  markActive(row);
  const detail = await api("/api/file?path=" + encodeURIComponent(path));
  current = path;
  dirty = false;
  renderPane(detail);
}

function docHead(detail) {
  const head = el("div", { className: "doc-head" });
  head.append(el("h2", { textContent: detail.name }));
  const dirtyFlag = el("span", { className: "dirty hidden", textContent: "● unsaved" });
  head.append(dirtyFlag, el("span", { className: "grow" }));
  head._dirty = dirtyFlag;

  const rename = el("button", { textContent: "Rename", onclick: () => doRename(detail.path) });
  const del = el("button", { className: "danger", textContent: "Delete", onclick: () => doDelete(detail.path) });
  head.append(rename, del);
  return head;
}

function renderPane(detail) {
  const pane = $("#pane");
  pane.textContent = "";
  const head = docHead(detail);
  pane.append(head);

  if (detail.type === "uni" || detail.type === "text") {
    if (detail.type === "uni") pane.append(tagsEditor(detail));
    const isUni = detail.type === "uni";
    const editor = isUni
      ? el("div", { className: "editor", contentEditable: "true", innerHTML: detail.content || "<p></p>" })
      : el("textarea", { className: "editor", rows: 24, value: detail.text || "" });
    const setDirty = () => { dirty = true; head._dirty.classList.remove("hidden"); };
    editor.addEventListener("input", setDirty);
    const save = el("button", { className: "primary", textContent: "Save",
      onclick: async () => {
        const content = isUni ? editor.innerHTML : editor.value;
        await api("/api/content", { method: "POST", body: { path: detail.path, content } });
        dirty = false; head._dirty.classList.add("hidden"); toast("Saved");
      } });
    pane.append(el("div", { style: "margin:10px 0" }, save), editor);
  } else if (IMG.test(detail.path)) {
    pane.append(el("img", { className: "viewer", src: "/raw?path=" + encodeURIComponent(detail.path) }));
  } else if (PDF.test(detail.path)) {
    pane.append(el("embed", { className: "viewer", src: "/raw?path=" + encodeURIComponent(detail.path) }));
  } else {
    pane.append(el("p", { className: "empty",
      textContent: `Binary file (${detail.size ?? "?"} bytes). No preview.` }));
  }
}

function tagsEditor(detail) {
  const box = el("div", { className: "tags-edit doc-head" });
  const input = el("input", { value: (detail.tags || []).join(", "), placeholder: "tags, comma separated" });
  const save = el("button", { textContent: "Save tags",
    onclick: async () => {
      const tags = input.value.split(",").map((t) => t.trim()).filter(Boolean);
      await api("/api/tags", { method: "POST", body: { path: detail.path, tags } });
      toast("Tags saved"); loadTree();
    } });
  box.append(el("span", { className: "tag", textContent: "Tags:" }), input, save);
  return box;
}

// --- mutations -------------------------------------------------------------

async function doRename(path) {
  const name = prompt("New name:", path.split("/").pop());
  if (!name) return;
  await api("/api/rename", { method: "POST", body: { path, new_name: name } });
  toast("Renamed"); await loadTree();
  const parent = path.includes("/") ? path.slice(0, path.lastIndexOf("/") + 1) : "";
  openFile(parent + name);
}

async function doDelete(path) {
  if (!confirm(`Delete ${path}?`)) return;
  await api("/api/delete", { method: "POST", body: { path } });
  toast("Deleted"); current = null; dirty = false;
  $("#pane").innerHTML = '<div class="empty">Select a file from the tree.</div>';
  loadTree();
}

async function doNew(kind) {
  const name = prompt(kind === "file" ? "New file name (e.g. notes.uni):" : "New folder name:");
  if (!name) return;
  const dir = current && current.includes("/") ? current.slice(0, current.lastIndexOf("/")) : "";
  const route = kind === "file" ? "/api/file" : "/api/folder";
  await api(route, { method: "POST", body: { path: dir, name } });
  await loadTree();
  if (kind === "file") openFile(dir ? dir + "/" + name : name);
}

// --- search ----------------------------------------------------------------

let searchTimer;
function onSearch(e) {
  clearTimeout(searchTimer);
  const q = e.target.value.trim();
  const box = $("#results");
  if (!q) return box.classList.add("hidden");
  searchTimer = setTimeout(async () => {
    const hits = await api("/api/retrieve?q=" + encodeURIComponent(q) + "&k=8");
    box.textContent = "";
    if (!hits.length) { box.classList.add("hidden"); return; }
    for (const h of hits) {
      const r = el("div", { className: "r" },
        el("span", { textContent: h.path }),
        el("small", { textContent: h.snippet || "" }));
      r.onclick = () => { box.classList.add("hidden"); $("#search").value = ""; openFile(h.path); };
      box.append(r);
    }
    box.classList.remove("hidden");
  }, 250);
}

// --- wire up ---------------------------------------------------------------

$("#search").addEventListener("input", onSearch);
document.addEventListener("click", (e) => {
  if (!e.target.closest(".search")) $("#results").classList.add("hidden");
});
$("#new-file").onclick = () => doNew("file");
$("#new-folder").onclick = () => doNew("folder");
$("#reindex").onclick = async () => {
  const r = await api("/api/reindex", { method: "POST" });
  toast(r.indexed ? `Indexed ${r.total} files` : "Lexical mode (no embedder)");
};
window.addEventListener("beforeunload", (e) => { if (dirty) e.preventDefault(); });

loadTree().catch((e) => toast("Failed to load: " + e.message));
