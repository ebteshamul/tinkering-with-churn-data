(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  const state = {
    schema: null,
    threshold: 0.5,
    last: null,   // last single-customer result from the API
    batch: null,  // last batch result from the API
  };

  // ---- helpers ---------------------------------------------------------
  const esc = (s) =>
    String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function fmtPct(p) {
    const v = p * 100;
    if (v > 0 && v < 1) return "<1%";
    if (v > 99 && v < 100) return ">99%";
    return Math.round(v) + "%";
  }

  async function api(url, options = {}) {
    const res = await fetch(url, options);
    let body = null;
    try { body = await res.json(); } catch (_) { /* not JSON */ }
    if (!res.ok) {
      const err = new Error((body && body.error) || `Request failed (${res.status}).`);
      err.status = res.status;
      err.details = (body && body.details) || [];
      throw err;
    }
    return body;
  }

  function fieldByKey(key) {
    return state.schema.fields.find((f) => f.key === key);
  }

  function formatValue(key, value) {
    const f = fieldByKey(key);
    if (!f || f.type === "category") return String(value);
    const n = Number(value);
    const text = f.integer ? String(Math.round(n)) : String(Math.round(n * 10) / 10);
    return f.unit ? `${text} ${f.unit}` : text;
  }

  // ---- tabs ------------------------------------------------------------
  function initTabs() {
    const tabs = $$('[role="tab"]');
    const select = (tab, focus) => {
      tabs.forEach((t) => {
        const on = t === tab;
        t.setAttribute("aria-selected", on);
        t.tabIndex = on ? 0 : -1;
        $("#" + t.getAttribute("aria-controls")).hidden = !on;
      });
      if (focus) tab.focus();
    };
    tabs.forEach((tab, i) => {
      tab.addEventListener("click", () => select(tab));
      tab.addEventListener("keydown", (e) => {
        if (e.key === "ArrowRight") select(tabs[(i + 1) % tabs.length], true);
        if (e.key === "ArrowLeft") select(tabs[(i - 1 + tabs.length) % tabs.length], true);
      });
    });
  }

  // ---- form ------------------------------------------------------------
  function buildForm() {
    const host = $("#form-groups");
    host.innerHTML = "";
    const groups = [...new Set(state.schema.fields.map((f) => f.group))];
    groups.forEach((group) => {
      const fs = document.createElement("fieldset");
      fs.innerHTML = `<legend>${esc(group)}</legend><div class="fields"></div>`;
      const grid = $(".fields", fs);
      state.schema.fields.filter((f) => f.group === group).forEach((f) => grid.appendChild(createField(f)));
      host.appendChild(fs);
    });
  }

  function createField(f) {
    const wrap = document.createElement("div");
    wrap.className = "field";
    wrap.dataset.key = f.key;
    const id = "f-" + f.key;

    if (f.type === "number") {
      wrap.innerHTML = `
        <label for="${id}">${esc(f.label)}</label>
        <div class="input-wrap ${f.unit ? "has-unit" : ""}">
          <input type="number" id="${id}" name="${f.key}" min="0" step="${f.integer ? 1 : "any"}"
                 inputmode="${f.integer ? "numeric" : "decimal"}" value="${f.default}" required>
          ${f.unit ? `<span class="unit">${esc(f.unit)}</span>` : ""}
        </div>
        <span class="hint">Typical range ${f.min} to ${f.max}</span>`;
    } else if (f.options.length <= 3) {
      const radios = f.options
        .map((o) => `<label><input type="radio" name="${f.key}" value="${esc(o)}"${o === f.default ? " checked" : ""}><span>${esc(o)}</span></label>`)
        .join("");
      wrap.innerHTML = `<span class="label" id="${id}-l">${esc(f.label)}</span><div class="seg" role="radiogroup" aria-labelledby="${id}-l">${radios}</div>`;
    } else {
      const opts = f.options
        .map((o) => `<option value="${esc(o)}"${o === f.default ? " selected" : ""}>${esc(o)}</option>`)
        .join("");
      wrap.innerHTML = `<label for="${id}">${esc(f.label)}</label><select id="${id}" name="${f.key}">${opts}</select>`;
    }
    return wrap;
  }

  function readForm() {
    const values = {};
    let problem = "";
    state.schema.fields.forEach((f) => {
      if (f.type === "number") {
        const input = $("#f-" + f.key);
        const raw = input.value.trim();
        const n = Number(raw);
        const bad = raw === "" || !Number.isFinite(n) || n < 0;
        input.classList.toggle("invalid", bad);
        if (bad && !problem) problem = `Enter ${f.label.toLowerCase()} as a number, zero or greater.`;
        values[f.key] = raw;
      } else {
        const radio = $(`input[type="radio"][name="${f.key}"]:checked`);
        const select = $(`select[name="${f.key}"]`);
        values[f.key] = radio ? radio.value : select ? select.value : "";
      }
    });
    return { values, problem };
  }

  function setForm(values) {
    Object.entries(values).forEach(([key, value]) => {
      const f = fieldByKey(key);
      if (!f) return;
      if (f.type === "number") {
        $("#f-" + key).value = value;
      } else if (f.options.includes(value)) {
        const radio = $(`input[type="radio"][name="${key}"][value="${CSS.escape(value)}"]`);
        if (radio) radio.checked = true;
        const select = $(`select[name="${key}"]`);
        if (select) select.value = value;
      }
    });
    schedulePredict(0);
  }

  const PRESETS = {
    typical: () => Object.fromEntries(state.schema.fields.map((f) => [f.key, f.default])),
    leaving: () => ({
      PlanType: "Basic", ContractType: "Monthly", AutoPay: "No", PaymentMethod: "Cash",
      TenureMonths: 2, SupportTickets: 5, SatisfactionScore: 1, LastLoginDays: 40,
      ReferralCount: 0, DeviceCount: 1, AvgMonthlyUsageHours: 15,
    }),
    loyal: () => ({
      PlanType: "Premium", ContractType: "Yearly", AutoPay: "Yes", PaymentMethod: "Card",
      TenureMonths: 48, SupportTickets: 0, SatisfactionScore: 5, LastLoginDays: 2,
      ReferralCount: 3, DeviceCount: 4, AvgMonthlyUsageHours: 50,
    }),
  };

  // ---- single prediction ----------------------------------------------
  let timer = null;
  let controller = null;

  function schedulePredict(delay = 250) {
    clearTimeout(timer);
    timer = setTimeout(runPredict, delay);
  }

  function showEmpty(text) {
    $("#result-body").hidden = true;
    $("#result-empty").hidden = false;
    $("#result-empty-text").textContent = text;
    const r = $(".result");
    r.classList.remove("is-leave", "is-stay", "is-loading");
  }

  async function runPredict() {
    const { values, problem } = readForm();
    if (problem) return showEmpty(problem);

    if (controller) controller.abort();
    const mine = (controller = new AbortController());
    $(".result").classList.add("is-loading");
    try {
      state.last = await api("/api/predict", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...values, threshold: state.threshold }),
        signal: mine.signal,
      });
      $("#result-empty").hidden = true;
      $("#result-body").hidden = false;
      renderResult();
    } catch (e) {
      if (e.name === "AbortError") return;
      const detail = e.details && e.details.length ? " " + e.details.join(" ") : "";
      showEmpty(e.message + detail);
    } finally {
      if (controller === mine) $(".result").classList.remove("is-loading");
    }
  }

  function renderResult() {
    const res = state.last;
    if (!res) return;
    const t = state.threshold;
    const p = res.probability;
    const flagged = p >= t;

    const panel = $(".result");
    panel.classList.toggle("is-leave", flagged);
    panel.classList.toggle("is-stay", !flagged);

    // gauge
    const arc = $("#g-value");
    arc.style.opacity = p < 0.005 ? 0 : 1;
    arc.setAttribute("stroke-dasharray", `${(p * 100).toFixed(2)} 100`);
    const angle = Math.PI * (1 - t);
    const pt = (r) => [110 + r * Math.cos(angle), 110 - r * Math.sin(angle)];
    const [x1, y1] = pt(70);
    const [x2, y2] = pt(108);
    const tick = $("#g-tick");
    tick.setAttribute("x1", x1.toFixed(1)); tick.setAttribute("y1", y1.toFixed(1));
    tick.setAttribute("x2", x2.toFixed(1)); tick.setAttribute("y2", y2.toFixed(1));
    $("#g-number").textContent = fmtPct(p);

    // verdict
    $("#verdict").textContent = flagged ? "Likely to leave" : "Likely to stay";
    $("#verdict-detail").textContent =
      `The model puts this customer's chance of leaving at ${fmtPct(p)}, ` +
      `${flagged ? "above" : "below"} your ${Math.round(t * 100)}% flag line.`;

    // notes
    const notes = [...(res.warnings || [])];
    if (res.filled_in && res.filled_in.length)
      notes.push(`Filled in with typical values: ${res.filled_in.join(", ")}.`);
    $("#warnings").innerHTML = notes.map((n) => `<li>${esc(n)}</li>`).join("");

    // drivers
    const top = res.drivers.slice(0, 6);
    const max = Math.max(...top.map((d) => Math.abs(d.contribution)), 0.0001);
    $("#drivers-list").innerHTML = top
      .map((d) => {
        const up = d.contribution > 0;
        const width = Math.max((Math.abs(d.contribution) / max) * 50, 1.5).toFixed(1);
        return `<li class="driver">
          <div class="d-top"><span class="d-name">${esc(d.label)}</span><span class="d-val">${esc(formatValue(d.field, d.value))}</span></div>
          <div class="d-track"><div class="d-bar ${up ? "up" : "down"}" style="width:${width}%"></div></div>
          <span class="sr-only">${up ? "Raises" : "Lowers"} the estimated risk</span>
        </li>`;
      })
      .join("");

    renderImpact();
  }

  // ---- threshold -------------------------------------------------------
  function renderImpact() {
    const row = state.schema.threshold_table.reduce((best, r) =>
      Math.abs(r.threshold - state.threshold) < Math.abs(best.threshold - state.threshold) ? r : best);
    const el = $("#threshold-impact");
    if (row.flagged_share === 0) {
      el.textContent = "On the test customers, nobody reached this line, so it would flag no one.";
      return;
    }
    el.textContent =
      `On the test customers, a ${Math.round(row.threshold * 100)}% line caught ` +
      `${Math.round(row.recall * 100)}% of those who left. ` +
      `${Math.round(row.precision * 100)}% of the customers it flagged really left.`;
  }

  function setThreshold(t) {
    state.threshold = t;
    try { localStorage.setItem("churn-threshold", String(t)); } catch (_) { /* storage unavailable */ }
    ["single", "batch"].forEach((k) => {
      $("#threshold-" + k).value = t;
      $("#threshold-out-" + k).textContent = Math.round(t * 100) + "%";
    });
    renderResult();
    renderBatch();
    renderImpact();
  }

  // ---- batch -----------------------------------------------------------
  function showBatchError(message, details = []) {
    const box = $("#batch-error");
    box.hidden = false;
    box.innerHTML = esc(message) + (details.length ? `<ul>${details.map((d) => `<li>${esc(d)}</li>`).join("")}</ul>` : "");
  }

  async function uploadCsv(file) {
    $("#batch-error").hidden = true;
    if (!file) return;
    $("#dz-sub").textContent = `Scoring ${file.name}…`;
    const body = new FormData();
    body.append("file", file);
    body.append("threshold", state.threshold);
    try {
      state.batch = await api("/api/predict/batch", { method: "POST", body });
      $("#dz-sub").textContent = `${file.name}. Choose another file to replace it.`;
      $("#batch-results").hidden = false;
      renderBatch();
    } catch (e) {
      $("#dz-sub").textContent = "or drop it here. Up to 10,000 rows and 5 MB.";
      showBatchError(e.message, e.details);
    }
  }

  function renderBatch() {
    const data = state.batch;
    if (!data) return;
    const t = state.threshold;
    const rows = data.rows.map((r, i) => ({ ...r, i, flagged: r.probability >= t }));
    const flagged = rows.filter((r) => r.flagged).length;

    $("#sum-rows").textContent = data.n_rows.toLocaleString();
    $("#sum-flagged").textContent = flagged.toLocaleString();
    $("#sum-flagged-label").textContent = `flagged at ${Math.round(t * 100)}%`;
    $("#sum-avg").textContent = fmtPct(data.average_probability);

    const note = $("#sum-note");
    note.hidden = !data.rows_with_filled_values;
    note.textContent = `${data.rows_with_filled_values.toLocaleString()} rows had missing or unreadable values. Typical values from the training data were used for those.`;

    let view = $("#only-flagged").checked ? rows.filter((r) => r.flagged) : rows;
    const sort = $("#sort-by").value;
    if (sort === "desc") view = [...view].sort((a, b) => b.probability - a.probability);
    if (sort === "asc") view = [...view].sort((a, b) => a.probability - b.probability);

    const LIMIT = 200;
    $("#batch-body").innerHTML = view.slice(0, LIMIT).map((r) => `
      <tr>
        <td>${esc(r.id)}</td>
        <td><div class="pbar"><i><b class="${r.flagged ? "hot" : ""}" style="width:${(r.probability * 100).toFixed(0)}%"></b></i><span>${fmtPct(r.probability)}</span></div></td>
        <td><span class="tag ${r.flagged ? "leave" : "stay"}">${r.flagged ? "Flagged" : "Not flagged"}</span></td>
        <td>${esc(r.main_risk_factor || "None stands out")}</td>
        <td>${esc(r.plan)}</td>
        <td>${esc(r.contract)}</td>
        <td class="num">${esc(r.tenure)} mo</td>
        <td class="num">${esc(r.monthly_charges)}</td>
      </tr>`).join("");

    $("#table-note").textContent = view.length > LIMIT
      ? `Showing ${LIMIT} of ${view.length.toLocaleString()} customers. Download the results to see all of them.`
      : view.length === 0 ? "No customers match this filter." : `Showing all ${view.length.toLocaleString()} customers.`;
  }

  function downloadResults() {
    if (!state.batch) return;
    const t = state.threshold;
    const q = (v) => `"${String(v).replace(/"/g, '""')}"`;
    const lines = ["CustomerID,ChurnProbability,Flagged,MainRiskFactor"];
    state.batch.rows.forEach((r) =>
      lines.push([q(r.id), r.probability, r.probability >= t ? "Yes" : "No", q(r.main_risk_factor)].join(",")));
    const url = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: "churn_predictions.csv" });
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  // ---- about the model -------------------------------------------------
  function renderAbout() {
    const s = state.schema;
    const m = s.metrics;
    const c = m.confusion;
    $("#about-lede").textContent =
      `Trained on ${s.n_train.toLocaleString()} customers and checked on ${m.n_test.toLocaleString()} it had not seen. ` +
      `About ${Math.round(s.churn_rate * 100)}% of customers in the data left.`;

    const stat = (big, name, desc) =>
      `<div class="stat"><span class="big">${big}</span><span class="name">${name}</span><span class="desc">${desc}</span></div>`;
    $("#stats").innerHTML =
      stat(Math.round(m.accuracy * 100) + "%", "Accuracy", "Share of test customers called correctly at a 50% line.") +
      stat(Math.round(m.recall * 100) + "%", "Leavers caught", "Of customers who left, how many were flagged at 50%.") +
      stat(Math.round(m.precision * 100) + "%", "Flags that were right", "Of customers flagged at 50%, how many really left.") +
      stat(m.roc_auc.toFixed(2), "Ranking quality", "How well it ranks leavers above stayers. 1.00 is perfect, 0.50 is a coin flip.");

    $("#confusion-lede").textContent =
      `Of ${(c.fn + c.tp).toLocaleString()} test customers who left, the model missed ${c.fn.toLocaleString()} at a 50% line. ` +
      `Lowering the flag line on the first tab catches more of them, with more false alarms.`;
    $("#confusion").innerHTML = `
      <div></div><div class="h">Predicted to stay</div><div class="h">Predicted to leave</div>
      <div class="h">Stayed</div>
      <div class="cell good"><b>${c.tn.toLocaleString()}</b><span>correctly kept</span></div>
      <div class="cell bad"><b>${c.fp.toLocaleString()}</b><span>false alarms</span></div>
      <div class="h">Left</div>
      <div class="cell bad"><b>${c.fn.toLocaleString()}</b><span>missed</span></div>
      <div class="cell good"><b>${c.tp.toLocaleString()}</b><span>caught</span></div>`;

    const inf = Object.entries(s.influence).sort((a, b) => b[1] - a[1]);
    const top = inf[0][1] || 1;
    $("#influence").innerHTML = inf
      .map(([key, v]) => `<li><span>${esc(s.labels[key] || key)}</span>
        <span class="track"><span class="fill" style="display:block;width:${((v / top) * 100).toFixed(0)}%"></span></span>
        <span class="pct">${Math.round(v * 100)}%</span></li>`)
      .join("");
  }

  // ---- start -----------------------------------------------------------
  async function init() {
    initTabs();
    try {
      state.schema = await api("/api/schema");
    } catch (e) {
      const banner = $("#banner");
      banner.hidden = false;
      banner.innerHTML = e.status === 503
        ? "The model file was not found. Run <code>python train.py --data your_file.csv</code>, then restart the app."
        : esc(e.message);
      $("main").hidden = true;
      return;
    }

    try {
      const saved = parseFloat(localStorage.getItem("churn-threshold"));
      state.threshold = saved >= 0.05 && saved <= 0.95 ? saved : state.schema.default_threshold;
    } catch (_) {
      state.threshold = state.schema.default_threshold;
    }

    buildForm();
    renderAbout();

    const form = $("#customer-form");
    form.addEventListener("input", () => schedulePredict());
    form.addEventListener("change", () => schedulePredict());
    form.addEventListener("submit", (e) => e.preventDefault());
    $$("[data-preset]").forEach((b) => b.addEventListener("click", () => setForm(PRESETS[b.dataset.preset]())));

    ["single", "batch"].forEach((k) =>
      $("#threshold-" + k).addEventListener("input", (e) => setThreshold(parseFloat(e.target.value))));

    const input = $("#csv-input");
    input.addEventListener("change", () => { uploadCsv(input.files[0]); input.value = ""; });
    const dz = $("#dropzone");
    ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
    ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
    dz.addEventListener("drop", (e) => uploadCsv(e.dataTransfer.files[0]));
    $("#only-flagged").addEventListener("change", renderBatch);
    $("#sort-by").addEventListener("change", renderBatch);
    $("#download-csv").addEventListener("click", downloadResults);

    setThreshold(state.threshold);
    runPredict();
  }

  init();
})();
