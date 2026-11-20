/* Loment Studio — 源码参数台。
 * 浏览器不解析 Loment：参数表、矩阵、补丁预览、校验全部问 Rust 后端。 */

const $ = (id) => document.getElementById(id);

const state = {
  mode: "params",
  path: "",
  doc: null,
  edits: new Map(),
  active: null,
  search: "",
  changes: [],
  diags: null,
  axes: ["mode", "gc", "runtime"],
  axisMeta: [],
  cells: [],
  current: [],
};

async function api(path, opts) {
  const res = await fetch(path, opts);
  const text = await res.text();
  if (!res.ok) throw new Error(text || res.statusText);
  return text ? JSON.parse(text) : {};
}
const post = (path, body) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

const optValue = (s) => String(s).split(" — ")[0];
const isNumeric = (ty) => /^[ui]\d+$/.test(ty);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function setStatus(text) {
  $("status").textContent = text;
}

/* ---------------------------------------------------------------- open */

async function open(path) {
  const rel = (path || "").trim();
  if (!rel) return;
  setStatus("读取 " + rel + " …");
  try {
    const doc = await api(`/api/params?path=${encodeURIComponent(rel)}`);
    state.path = rel;
    state.doc = doc;
    state.edits.clear();
    state.active = null;
    state.changes = [];
    state.diags = null;
    $("path").value = rel;
    $("diags").innerHTML = '<span class="muted">还没有校验。</span>';
    render();
    renderDiff();
    setStatus(`${doc.lines} 行 · ${doc.items.length} 个参数`);
    if (state.mode === "env") loadMatrix();
  } catch (e) {
    setStatus(String(e));
    $("params").innerHTML = `<div class="empty"><h2>打不开这个文件</h2><p>${esc(e)}</p></div>`;
  }
}

/* ---------------------------------------------------------------- groups */

function groups() {
  const order = ["语言环境", "自定义开关", "能力域", "项目配置", "常量"];
  const map = new Map();
  for (const it of state.doc.items) {
    if (!map.has(it.group)) map.set(it.group, []);
    map.get(it.group).push(it);
  }
  const rank = (g) => {
    const i = order.indexOf(g);
    return i < 0 ? 50 : i;
  };
  return [...map.entries()].sort((a, b) => rank(a[0]) - rank(b[0]) || a[0].localeCompare(b[0]));
}

function matches(item) {
  const q = state.search.trim().toLowerCase();
  if (!q) return true;
  return (
    item.id.toLowerCase().includes(q) ||
    (item.label || item.name).toLowerCase().includes(q) ||
    item.help.toLowerCase().includes(q) ||
    item.value.toLowerCase().includes(q)
  );
}

/* ---------------------------------------------------------------- render */

function render() {
  if (!state.doc) return;
  renderRail();
  renderParams();
  $("apply").disabled = state.edits.size === 0;
  $("pending").textContent = state.edits.size;
}

function renderRail() {
  const info = $("fileinfo");
  const file = state.doc.path.split(/[\\/]/).pop();
  const rows = [
    ["文件", file, state.doc.path],
    ["行数", state.doc.lines],
    ...groups().map(([g, items]) => [g, items.length]),
  ];
  info.innerHTML = "";
  for (const [k, v, title] of rows) {
    const d = document.createElement("div");
    d.innerHTML = `<span>${esc(k)}</span><b title="${esc(title || v)}">${esc(v)}</b>`;
    info.append(d);
  }
  const nav = $("railnav");
  nav.innerHTML = "<h3>跳转</h3><div class='railnav'></div>";
  const box = nav.querySelector(".railnav");
  for (const [group, items] of groups()) {
    const a = document.createElement("a");
    a.href = "#";
    a.innerHTML = `<span>${esc(group)}</span><b>${items.length}</b>`;
    a.onclick = (e) => {
      e.preventDefault();
      if (state.mode !== "params") setMode("params");
      document.getElementById("g-" + slug(group))?.scrollIntoView({ block: "start" });
    };
    box.append(a);
  }
}

const slug = (s) => s.replace(/\s+/g, "-");

function renderParams() {
  const box = $("params");
  box.innerHTML = "";
  if (!state.doc) return;
  let shown = 0;
  for (const [group, items] of groups()) {
    const visible = items.filter(matches);
    if (!visible.length) continue;
    const head = document.createElement("div");
    head.className = "grouphead";
    head.id = "g-" + slug(group);
    head.innerHTML = `<h2>${esc(group)}</h2><span class="n">${visible.length}${visible.length !== items.length ? " / " + items.length : ""}</span>`;
    box.append(head);
    for (const item of visible) {
      box.append(renderRow(item));
      shown++;
    }
  }
  $("count").textContent = state.search ? `匹配 ${shown}` : `${state.doc.items.length} 项`;
  if (!shown) box.innerHTML = '<div class="empty"><p>没有匹配的参数。</p></div>';
}

function currentValue(item) {
  return state.edits.has(item.id) ? state.edits.get(item.id) : item.value;
}

function renderRow(item) {
  const row = document.createElement("div");
  row.className = "prow" + (state.edits.has(item.id) ? " changed" : "") + (state.active === item.id ? " active" : "");
  row.dataset.id = item.id;

  const meta = document.createElement("div");
  meta.className = "pmeta";
  const line = item.line ? `第 ${item.line} 行` : "默认值 · 源码未写";
  const refs = item.refs ? ` · 引用 ${item.refs}` : "";
  meta.innerHTML = `<span class="name">${esc(item.label || item.name)}</span><span class="sub">${esc(line)}${item.ty ? " · " + esc(item.ty) : ""}${refs}${state.edits.has(item.id) ? ' · <span class="flag">待应用</span>' : ""}</span>`;

  const ctl = document.createElement("div");
  ctl.className = "pctl";
  ctl.append(controlFor(item));
  if (item.unit) {
    const u = document.createElement("span");
    u.className = "unit";
    u.textContent = item.unit;
    ctl.append(u);
  }

  const help = document.createElement("div");
  help.className = "phelp";
  help.innerHTML = esc(item.help);
  if (item.options.length) {
    const o = document.createElement("span");
    o.className = "opts";
    o.textContent = "可选 " + item.options.map(optValue).join(" / ");
    help.append(o);
  }
  if (item.src_help && item.src_help !== item.help) {
    const o = document.createElement("span");
    o.className = "opts";
    o.textContent = "源码注释：" + item.src_help;
    help.append(o);
  }
  if (item.note) {
    const n = document.createElement("span");
    n.className = "note";
    n.textContent = item.note;
    help.append(n);
  }

  row.append(meta, ctl, help);
  row.onclick = (e) => {
    if (e.target.closest("select,input,button")) return;
    state.active = item.id;
    renderParams();
    showItem(item);
  };
  return row;
}

function controlFor(item) {
  if (item.kind === "choice") {
    const sel = document.createElement("select");
    for (const o of item.options) {
      const v = optValue(o);
      const opt = document.createElement("option");
      opt.value = v;
      opt.textContent = v;
      if (v === currentValue(item)) opt.selected = true;
      sel.append(opt);
    }
    sel.onchange = () => stage(item.id, sel.value);
    return sel;
  }
  if (item.kind === "switch" || item.ty === "bool") {
    if (item.kind === "switch") {
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = currentValue(item) === "on";
      cb.onchange = () => stage(item.id, cb.checked ? "on" : "off");
      return cb;
    }
    const sel = document.createElement("select");
    for (const v of ["true", "false"]) {
      const opt = document.createElement("option");
      opt.value = v;
      opt.textContent = v;
      if (v === currentValue(item)) opt.selected = true;
      sel.append(opt);
    }
    sel.onchange = () => stage(item.id, sel.value);
    return sel;
  }
  const inp = document.createElement("input");
  inp.value = currentValue(item);
  inp.spellcheck = false;
  if (item.kind === "const" || isNumeric(item.ty)) inp.inputMode = "numeric";
  if (item.min != null) inp.min = item.min;
  if (item.max != null) inp.max = item.max;
  inp.onchange = () => stage(item.id, inp.value);
  return inp;
}

/* ---------------------------------------------------------------- help */

function showItem(item) {
  const opts = item.options.length
    ? `<ul>${item.options.map((o) => `<li>${esc(o)}</li>`).join("")}</ul>`
    : "";
  const where = item.line ? `第 ${item.line} 行` : "源码未写，当前用默认值";
  const range = item.min != null || item.max != null ? ` · 范围 ${item.min ?? "−∞"}…${item.max ?? "∞"}${item.unit ? " " + item.unit : ""}` : "";
  $("help").innerHTML = `
    <div class="title">${esc(item.id)}</div>
    <div>${esc(item.help)}</div>
    ${opts}
    ${item.note ? `<div class="why">${esc(item.note)}</div>` : ""}
    <div class="line">${esc(where)}${item.default ? " · 默认 " + esc(item.default) : ""}${range}</div>`;
}

/* ---------------------------------------------------------------- edits */

function stage(id, value) {
  const item = state.doc.items.find((i) => i.id === id);
  if (!item) return;
  if (String(value) === String(item.value)) state.edits.delete(id);
  else state.edits.set(id, String(value));
  render();
  previewSoon();
}

let previewTimer = null;
function previewSoon() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(preview, 120);
}

async function preview() {
  if (!state.doc) return;
  if (!state.edits.size) {
    state.changes = [];
    renderDiff();
    return;
  }
  try {
    const r = await post("/api/preview", { path: state.path, edits: editsArray() });
    state.changes = r.changes || [];
    renderDiff();
  } catch (e) {
    $("diff").innerHTML = `<span class="why">${esc(e)}</span>`;
  }
}

const editsArray = () => [...state.edits.entries()].map(([id, value]) => ({ id, value }));

function renderDiff() {
  const box = $("diff");
  if (!state.edits.size) {
    box.innerHTML = '<span class="muted">没有改动</span>';
    $("apply").disabled = true;
    $("pending").textContent = 0;
    return;
  }
  if (!state.changes.length) {
    box.innerHTML = '<span class="muted">计算中…</span>';
    return;
  }
  box.innerHTML = "";
  for (const c of state.changes) {
    const h = document.createElement("div");
    h.className = "hunk";
    h.innerHTML = `<div class="ln">${c.before ? "第 " + c.line + " 行" : "第 " + c.line + " 行（新增）"}</div>`;
    if (c.before) {
      const d = document.createElement("div");
      d.className = "del";
      d.textContent = "- " + c.before;
      h.append(d);
    }
    const a = document.createElement("div");
    a.className = "add";
    a.textContent = "+ " + c.after;
    h.append(a);
    box.append(h);
  }
}

/* ---------------------------------------------------------------- actions */

async function apply() {
  if (!state.edits.size) return;
  try {
    const r = await post("/api/apply", { path: state.path, edits: editsArray() });
    setStatus(`已写回 ${r.written} · ${r.changes.length} 处`);
    await open(state.path);
  } catch (e) {
    setStatus(String(e));
  }
}

async function runCheck() {
  if (!state.doc) return;
  setStatus("校验中…");
  try {
    const r = await post("/api/check", { path: state.path });
    state.diags = r.diags || [];
    const box = $("diags");
    if (!state.diags.length) {
      box.innerHTML = '<span class="ok">lomentc --check: 通过</span>';
      setStatus("lomentc --check: 通过");
    } else {
      box.innerHTML = "";
      for (const d of state.diags) {
        const row = document.createElement("div");
        row.className = "row";
        row.textContent = `${d.line}:${d.col} ${d.code || ""} ${d.message || ""}`;
        box.append(row);
      }
      setStatus(`lomentc --check: ${state.diags.length} 条诊断`);
    }
  } catch (e) {
    $("diags").innerHTML = `<div class="row">${esc(e)}</div>`;
    setStatus(String(e));
  }
}

/* ---------------------------------------------------------------- matrix */

async function loadMatrix() {
  if (!state.doc) return;
  try {
    const r = await api(`/api/matrix?path=${encodeURIComponent(state.path)}&axes=${encodeURIComponent(state.axes.join(","))}`);
    state.axisMeta = r.axes;
    state.cells = r.cells;
    state.current = r.current;
    renderMatrix();
  } catch (e) {
    $("matrix").innerHTML = `<div class="empty"><p>${esc(e)}</p></div>`;
  }
}

function renderMatrix() {
  const box = $("matrix");
  box.innerHTML = "";
  const bar = document.createElement("div");
  bar.className = "matrixbar";
  bar.append(labelSpan("轴"));
  for (const axis of state.axisMeta) {
    const l = document.createElement("label");
    l.className = "axis";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = state.axes.includes(axis.dim);
    cb.onchange = () => {
      state.axes = cb.checked ? [...state.axes, axis.dim] : state.axes.filter((d) => d !== axis.dim);
      if (!state.axes.length) state.axes = [axis.dim];
      loadMatrix();
    };
    l.append(cb, document.createTextNode(axis.label));
    bar.append(l);
  }
  const count = document.createElement("span");
  count.className = "muted num";
  count.textContent = `${state.cells.filter((c) => c.valid).length} / ${state.cells.length} 合法`;
  bar.append(count);
  box.append(bar);

  const head = document.createElement("div");
  head.className = "mrow";
  head.style.color = "var(--faint)";
  head.innerHTML = `<span></span><div>按 ${state.axes.map((d) => esc(state.axisMeta.find((a) => a.dim === d)?.label || d)).join(" / ")} 排列</div><span></span>`;
  box.append(head);
  const currentKey = new Map(state.current.map((c) => [c.dim, c.value]));
  for (const cell of state.cells) {
    const row = document.createElement("div");
    const here = cell.combo.every(([d, v]) => currentKey.get(d) === v);
    row.className = `mrow ${cell.valid ? "valid" : "invalid"}${here ? " current" : ""}`;
    const dot = document.createElement("span");
    dot.className = "dot";
    const main = document.createElement("div");
    const combo = document.createElement("div");
    combo.className = "combo";
    combo.innerHTML = cell.combo
      .map(([d, v]) => `<b title="${esc(d)}">${esc(v)}</b>`)
      .join('<span>/</span>');
    main.append(combo);
    if (cell.reason) {
      const why = document.createElement("div");
      why.className = "why";
      why.textContent = cell.reason;
      main.append(why);
    }
    const act = document.createElement("div");
    act.className = "act";
    if (cell.valid && !here) {
      const b = document.createElement("button");
      b.textContent = "写入头部";
      b.onclick = () => {
        for (const [d, v] of cell.combo) state.edits.set(d, v);
        setMode("params");
        render();
        preview();
      };
      act.append(b);
    } else if (here) {
      const tag = document.createElement("span");
      tag.className = "muted";
      tag.textContent = "当前";
      act.append(tag);
    } else {
      const b = document.createElement("button");
      b.disabled = true;
      b.textContent = "不可用";
      act.append(b);
    }
    row.append(dot, main, act);
    row.onclick = (e) => {
      if (e.target.closest("button")) return;
      showCell(cell);
    };
    box.append(row);
  }
}

function labelSpan(text) {
  const s = document.createElement("span");
  s.className = "muted";
  s.textContent = text;
  return s;
}

function showCell(cell) {
  const combo = cell.combo.map(([d, v]) => `${d} = ${v}`).join("  ");
  $("help").innerHTML = `
    <div class="title">${esc(combo)}</div>
    <div>${cell.valid ? "这一组是编译器接受的组合。" : "这一组会被编译器拒绝。"}</div>
    ${cell.reason ? `<div class="why">${esc(cell.reason)}</div>` : ""}
    <div class="line">点“写入头部”会把这三个取值加入待应用，不会立刻写盘。</div>`;
}

/* ---------------------------------------------------------------- modes */

function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll(".modes [role=tab]").forEach((b) => b.classList.toggle("on", b.dataset.mode === mode));
  $("params").hidden = mode !== "params";
  $("matrix").hidden = mode !== "env";
  $("search").hidden = mode !== "params";
  $("count").hidden = mode !== "params";
  if (mode === "env") loadMatrix();
}

/* ---------------------------------------------------------------- add */

let addParam = null;

function openAdd() {
  $("modal").hidden = false;
  renderAddFields();
  $("addhint").textContent = "";
}

function closeAdd() {
  $("modal").hidden = true;
  addParam = null;
}

function field(label, id, value, placeholder) {
  return `<label>${esc(label)}<input id="${id}" value="${esc(value || "")}" placeholder="${esc(placeholder || "")}" spellcheck="false"></label>`;
}

function renderAddFields() {
  const kind = $("addkind").value;
  const box = $("addfields");
  if (kind === "const") {
    box.innerHTML =
      field("名字", "f_name", "", "LIMIT") +
      `<label>类型<select id="f_ty"><option>u32</option><option>u64</option><option>i32</option><option>i64</option><option>bool</option><option>str</option></select></label>` +
      field("初始值", "f_value", "", "64") +
      field("说明", "f_help", "", "写进行尾注释");
  } else if (kind === "switch") {
    box.innerHTML = field("名字", "f_name", "", "trace");
  } else if (kind === "capability") {
    box.innerHTML =
      field("名字", "f_name", "", "net") +
      field("空间", "f_space", "disk", "disk") +
      field("下界", "f_lo", "0", "0") +
      field("上界", "f_hi", "4", "4") +
      `<label>可撤销<input id="f_rev" type="checkbox"></label>`;
  } else {
    box.innerHTML =
      field("键", "f_key", "", "source_ext") +
      `<label>类型<select id="f_ty2"><option>str</option><option>u32</option><option>bool</option></select></label>` +
      field("值", "f_value", "", ".foo");
  }
}

function collectAdd() {
  const kind = $("addkind").value;
  if (kind === "const") {
    return { kind, name: $("f_name").value.trim(), ty: $("f_ty").value, value: $("f_value").value, help: $("f_help").value };
  }
  if (kind === "switch") return { kind, name: $("f_name").value.trim() };
  if (kind === "capability") {
    return {
      kind,
      name: $("f_name").value.trim(),
      space: $("f_space").value.trim(),
      lo: Number($("f_lo").value || 0),
      hi: Number($("f_hi").value || 0),
      revocable: $("f_rev").checked,
    };
  }
  return { kind, key: $("f_key").value.trim(), ty: $("f_ty2").value, value: $("f_value").value };
}

async function addPreview() {
  try {
    const param = collectAdd();
    const r = await post("/api/add", { path: state.path, param, dry: true });
    state.changes = r.changes || [];
    $("diff").innerHTML = "";
    for (const c of state.changes) {
      const h = document.createElement("div");
      h.className = "hunk";
      h.innerHTML = `<div class="ln">第 ${c.line} 行（新增）</div>`;
      const a = document.createElement("div");
      a.className = "add";
      a.textContent = "+ " + c.after;
      h.append(a);
      $("diff").append(h);
    }
    $("addhint").textContent = "预览在右侧；确认后点「写入」。";
    addParam = param;
  } catch (e) {
    $("addhint").textContent = String(e);
  }
}

async function addCommit() {
  try {
    const param = addParam || collectAdd();
    const r = await post("/api/add", { path: state.path, param, dry: false });
    closeAdd();
    setStatus(`已新增 · ${r.changes.length} 处`);
    await open(state.path);
  } catch (e) {
    $("addhint").textContent = String(e);
  }
}

/* ---------------------------------------------------------------- boot */

$("open").onclick = () => open($("path").value);
$("path").addEventListener("keydown", (e) => {
  if (e.key === "Enter") open($("path").value);
});
$("apply").onclick = apply;
$("check").onclick = runCheck;
$("add").onclick = openAdd;
$("addcancel").onclick = closeAdd;
$("addpreview").onclick = addPreview;
$("addcommit").onclick = addCommit;
$("addkind").onchange = renderAddFields;
$("search").addEventListener("input", () => {
  state.search = $("search").value;
  renderParams();
});
document.querySelectorAll(".modes [role=tab]").forEach((b) => {
  b.onclick = () => setMode(b.dataset.mode);
});
document.querySelectorAll(".empty .suggest button").forEach((b) => {
  b.onclick = () => open(b.dataset.path);
});

const q = new URLSearchParams(location.search);
open(q.get("path") || $("path").value);
if (q.get("mode") === "env") setMode("env");
