/* BidBrief frontend. Plain JavaScript, no build step. All URLs are relative. */
(() => {
  "use strict";

  // ------------------------------------------------------------ helpers
  const $ = (sel, root = document) => root.querySelector(sel);

  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v === null || v === undefined || v === false) continue;
        if (k === "class") el.className = v;
        else if (k === "text") el.textContent = v;
        else if (k === "html") el.innerHTML = v; // only used with our own static SVG strings
        else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
        else if (v === true) el.setAttribute(k, "");
        else el.setAttribute(k, v);
      }
    }
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }

  const ICON = {
    check: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12.5l5 5L20 6.5"/></svg>',
    upload: '<svg class="big" viewBox="0 0 64 64" aria-hidden="true"><path d="M16 6h22l12 12v40H16z"/><path d="M38 6v12h12"/><path d="M33 48V28M25 36l8-8 8 8"/></svg>',
    search: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg>',
    back: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg>',
    download: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11M7 10l5 5 5-5M5 20h14"/></svg>',
    print: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 8V3h10v5M7 17H4v-7h16v7h-3M7 14h10v7H7z"/></svg>',
    trash: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M10 7V4h4v3M6 7l1 13h10l1-13"/></svg>',
    ask: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v11H9l-5 4z"/></svg>',
  };
  const icon = (name) => h("span", { html: ICON[name], style: "display:contents" });

  const local = {
    get(k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { window.localStorage.setItem(k, v); } catch (e) { /* storage blocked */ } },
    del(k) { try { window.localStorage.removeItem(k); } catch (e) { /* storage blocked */ } },
  };

  const state = {
    config: null,
    code: local.get("bb_code") || "",
    remaining: null,
    files: [],
    name: "",
    pkg: null,
    tab: "summary",
    answers: {},
    itemQuery: "",
    onlyCheck: false,
  };

  async function api(path, opts = {}) {
    const headers = Object.assign({}, opts.headers || {});
    if (state.code) headers["X-Trial-Code"] = state.code;
    if (opts.json !== undefined) {
      headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.json);
    }
    const res = await fetch(path, { method: opts.method || "GET", headers, body: opts.body });
    let data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (!res.ok) {
      const msg = (data && (typeof data.detail === "string" ? data.detail : null)) || `Something went wrong (${res.status}).`;
      const err = new Error(msg);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  const qs = (obj) => Object.entries(obj).flatMap(([k, v]) =>
    Array.isArray(v) ? v.map((x) => `${k}=${encodeURIComponent(x)}`) : (v === "" || v == null ? [] : [`${k}=${encodeURIComponent(v)}`])).join("&");

  const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + "s")}`;

  function pageRanges(pages) {
    if (!pages.length) return "";
    const out = [];
    let a = pages[0], b = pages[0];
    for (const p of pages.slice(1)) {
      if (p === b + 1) { b = p; continue; }
      out.push(a === b ? `${a}` : `${a}–${b}`);
      a = b = p;
    }
    out.push(a === b ? `${a}` : `${a}–${b}`);
    return out.join(", ");
  }

  function setMain(...nodes) {
    const app = $("#app");
    app.replaceChildren(...nodes);
  }

  // ------------------------------------------------------------ access code
  function renderAccess() {
    const box = $("#access");
    box.replaceChildren();
    if (!state.config || !state.config.trial_required) return;
    if (state.code) {
      const left = state.remaining == null ? "" : ` · ${plural(state.remaining, "package")} left`;
      box.append(h("span", { class: "code-pill" }, "Code ", h("strong", { text: state.code }), left),
        h("button", { class: "link-btn small", onclick: () => { state.code = ""; state.remaining = null; local.del("bb_code"); renderAccess(); route(); } }, "Sign out"));
    } else {
      box.append(h("button", { class: "btn ghost", onclick: () => askCode() }, "I have an access code"));
    }
  }

  let codeCallback = null;
  function askCode(onOk) {
    codeCallback = onOk || null;
    const modal = $("#modal");
    modal.hidden = false;
    $("#code-error").textContent = "";
    $("#modal-text").textContent = `Your code lets you add your own bid packages. Packages you add are only visible with your code. Ask ${state.config ? state.config.contact : "us"} for one.`;
    const input = $("#code-input");
    input.value = state.code || "";
    setTimeout(() => input.focus(), 30);
  }
  function closeModal() { $("#modal").hidden = true; codeCallback = null; }
  $("#code-cancel").addEventListener("click", closeModal);
  $("#code-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const code = $("#code-input").value.trim();
    if (!code) { $("#code-error").textContent = "Type your access code."; return; }
    try {
      const r = await api("api/trial", { method: "POST", json: { code } });
      if (!r.ok) { $("#code-error").textContent = r.message || "That code isn't recognised."; return; }
      state.code = r.code || code.toUpperCase();
      state.remaining = r.remaining;
      local.set("bb_code", state.code);
      const cb = codeCallback;
      closeModal();
      renderAccess();
      if (cb) cb(); else route();
    } catch (err) {
      $("#code-error").textContent = err.message;
    }
  });

  async function refreshRemaining() {
    if (!state.config || !state.config.trial_required || !state.code) return;
    try {
      const r = await api("api/trial", { method: "POST", json: { code: state.code } });
      if (r.ok) state.remaining = r.remaining;
      else { state.code = ""; state.remaining = null; local.del("bb_code"); }
    } catch (e) { /* offline: keep what we have */ }
    renderAccess();
  }

  // ------------------------------------------------------------ home
  async function showHome() {
    document.title = "BidBrief";
    const hero = h("section", { class: "hero" },
      h("h1", { text: "Know what's in the bid package before you price it." }),
      h("p", { class: "lede", text: "Upload a bid package. BidBrief finds the deadlines, quantities, costly clauses, risks and addenda changes, with the exact page for every result." }));

    const how = h("section", { class: "how", "aria-label": "What happens next" },
      h("h2", { text: "What happens next" }),
      h("ol", null,
        h("li", null, h("b", { text: "Drop the PDFs" }), h("span", { text: "The bid package and any addenda." })),
        h("li", null, h("b", { text: "BidBrief reads every page" }), h("span", { text: "Usually 1 to 3 minutes." })),
        h("li", null, h("b", { text: "Get the brief" }), h("span", { text: "Key dates, bid items, risks, changes, and answers to your questions." }))));

    const benefits = h("section", { class: "benefits", "aria-label": "What you get" },
      benefit("Faster bid review", "The facts an estimator needs, on one page."),
      benefit("Catch expensive clauses", "Late penalties, rock, night work, testing, insurance."),
      benefit("See what the addenda change", "Old wording next to new, side by side."),
      benefit("Export bid items", "Quantities to Excel, copied exactly as printed."),
      benefit("Check every result", "Tap any yellow page number to see the passage on the page."));

    const trust = h("p", { class: "trust" },
      "BidBrief uses AI to read the documents. Every result is linked to its page, and anything it cannot confirm is flagged for you to check. It does not price the job or decide whether to bid.");

    const sheet = h("section", { class: "sheet", "aria-label": "Add a bid package" });
    renderSheet(sheet);
    const list = h("section", { "aria-labelledby": "pk-title" },
      h("div", { class: "section-title" }, h("h2", { id: "pk-title", text: "Bid packages" })),
      h("p", { class: "empty", text: "Loading…" }));
    setMain(h("div", { class: "home" }, hero, sheet, how, benefits, trust), list);

    try {
      const pkgs = await api("api/packages");
      const ul = h("ul", { class: "pkg-list" });
      if (!pkgs.length) {
        list.replaceChildren(list.firstChild, h("p", { class: "empty", text: "No packages yet. Add one above." }));
        return;
      }
      for (const p of pkgs) {
        ul.append(h("li", null, h("a", { class: "pkg-row", href: `#/p/${p.id}/summary` },
          h("div", null,
            h("h3", null, p.title, p.mine ? h("span", { class: "tag mine", text: "Yours" }) : null,
              p.synthetic ? h("span", { class: "tag sample", text: "Test sample" }) : null),
            h("div", { class: "meta" },
              p.owner ? h("span", { text: p.owner }) : null,
              h("span", { text: plural(p.pages, "page") }),
              h("span", { text: plural(p.items, "bid item") }),
              p.high ? h("span", { class: "hot", text: `${p.high} costly risk${p.high === 1 ? "" : "s"}` }) : h("span", { text: plural(p.risks, "risk") }),
              p.addenda ? h("span", { text: `${plural(p.addenda, "addendum", "addenda")}, ${plural(p.changes, "change")}` }) : null)),
          h("span", { class: "open", text: "Open" }))));
      }
      list.replaceChildren(list.firstChild, ul);
    } catch (err) {
      list.replaceChildren(list.firstChild, h("p", { class: "error-box", text: err.message }));
    }
  }

  function benefit(title, text) {
    return h("div", { class: "benefit" }, h("span", { html: ICON.check }), h("div", null, h("b", { text: title }), h("span", { text })));
  }

  function titleBlock(rows) {
    return h("div", { class: "titleblock", "aria-hidden": "true" },
      ...rows.map(([label, value, wide]) => h("div", { class: wide ? "wide" : "" }, label ? h("b", { text: label }) : null, value)));
  }

  function defaultTitleBlock() {
    const max = state.config ? state.config.max_pages : 600;
    return titleBlock([
      ["", "Your next bid", true],
      ["Files", "PDF, plus any addenda"],
      ["Size", `Up to ${max} pages`],
      ["Scanned pages", "Read automatically"],
      ["Your documents", state.config && state.config.trial_required ? "Private to your code" : "Stay on this server"],
    ]);
  }

  function renderSheet(sheet, error) {
    sheet.replaceChildren();
    if (!state.files.length) {
      const input = h("input", { type: "file", accept: "application/pdf,.pdf", multiple: true, "aria-label": "Choose PDF files" });
      input.addEventListener("change", () => onFiles(input.files, sheet));
      const drop = h("label", { class: "drop" },
        h("div", null,
          h("span", { html: ICON.upload }),
          h("h2", { text: "Drop the bid package here" }),
          h("p", { text: "or click to choose the PDF files" }),
          error ? h("p", { class: "form-error", role: "alert", text: error }) : null),
        input);
      ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
      ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, () => drop.classList.remove("over")));
      drop.addEventListener("drop", (e) => { e.preventDefault(); onFiles(e.dataTransfer.files, sheet); });
      sheet.append(drop, defaultTitleBlock());
      return;
    }
    const total = state.files.reduce((s, f) => s + f.size, 0);
    const nameInput = h("input", { type: "text", id: "pkg-name", placeholder: "e.g. Dracut Pleasant St water main", maxlength: "120", value: state.name });
    nameInput.addEventListener("input", () => { state.name = nameInput.value; });
    const go = h("button", { class: "btn primary", onclick: () => startUpload(sheet) }, "Read the package");
    sheet.append(h("div", { class: "selected" },
      h("h2", { text: "Ready to read", style: "font-size:var(--step-2)" }),
      h("ul", null, ...state.files.map((f) => h("li", null, h("span", { text: f.name }), h("span", { class: "muted", text: `${(f.size / 1048576).toFixed(1)} MB` })))),
      h("div", null, h("label", { for: "pkg-name", class: "small muted", text: "Short name (optional)" }), nameInput),
      error ? h("p", { class: "form-error", role: "alert", text: error }) : null,
      h("div", { class: "row" }, go,
        h("button", { class: "btn ghost", onclick: () => { state.files = []; renderSheet(sheet); } }, "Choose other files"))),
    titleBlock([
      ["", state.name || "New bid package", true],
      ["Files", plural(state.files.length, "PDF")],
      ["Size", `${(total / 1048576).toFixed(1)} MB`],
      ["Time", "About 1 to 3 minutes"],
      ["Then", "Brief, items, risks"],
    ]));
    setTimeout(() => go.focus(), 30);
  }

  function onFiles(fileList, sheet) {
    const files = Array.from(fileList || []).filter((f) => /\.pdf$/i.test(f.name) || f.type === "application/pdf");
    if (!files.length) { renderSheet(sheet, "Those aren't PDF files. Bid packages need to be PDFs."); return; }
    state.files = files;
    if (!state.name) state.name = files[0].name.replace(/\.pdf$/i, "").replace(/[_-]+/g, " ").slice(0, 80);
    renderSheet(sheet);
  }

  function startUpload(sheet) {
    const cfg = state.config || {};
    if (cfg.trial_required && !state.code) { askCode(() => startUpload(sheet)); return; }
    if (cfg.trial_required && state.remaining === 0) {
      renderSheet(sheet, `This access code has no packages left. Contact ${cfg.contact} to continue.`);
      return;
    }
    const form = new FormData();
    state.files.forEach((f) => form.append("files", f, f.name));
    form.append("name", state.name || "");
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "api/upload");
    if (state.code) xhr.setRequestHeader("X-Trial-Code", state.code);
    sheet.replaceChildren(h("div", { class: "processing", "aria-live": "polite" },
      h("h2", { text: "Sending the files" }),
      h("p", { class: "msg", id: "up-msg", text: "0%" }),
      scaleBar(0)), defaultTitleBlock());
    xhr.upload.addEventListener("progress", (e) => {
      if (!e.lengthComputable) return;
      const f = e.loaded / e.total;
      const msg = $("#up-msg");
      if (msg) msg.textContent = `${Math.round(f * 100)}%`;
      updateScale(sheet, f);
    });
    xhr.addEventListener("load", () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch (e) { data = null; }
      if (xhr.status >= 200 && xhr.status < 300 && data) {
        state.files = []; state.name = "";
        if (data.remaining != null) { state.remaining = data.remaining; renderAccess(); }
        location.hash = `#/job/${data.job_id}`;
      } else {
        const msg = (data && typeof data.detail === "string") ? data.detail : `Upload failed (${xhr.status}).`;
        if (xhr.status === 403 && /access code/i.test(msg) && !/no packages left/i.test(msg)) { askCode(() => startUpload(sheet)); }
        renderSheet(sheet, msg);
      }
    });
    xhr.addEventListener("error", () => renderSheet(sheet, "The upload was interrupted. Check your connection and try again."));
    xhr.send(form);
  }

  function scaleBar(frac) {
    const bar = h("div", { class: "scalebar", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(Math.round(frac * 100)) });
    for (let i = 0; i < 10; i++) bar.append(h("span", { class: i < Math.round(frac * 10) ? "on" : "" }));
    return h("div", null, bar, h("div", { class: "scale-labels" }, h("span", { text: "0" }), h("span", { text: "50%" }), h("span", { text: "Done" })));
  }
  function updateScale(root, frac) {
    const bar = $(".scalebar", root);
    if (!bar) return;
    bar.setAttribute("aria-valuenow", String(Math.round(frac * 100)));
    [...bar.children].forEach((s, i) => s.classList.toggle("on", i < Math.round(frac * 10)));
  }

  // ------------------------------------------------------------ processing
  const STEPS = [
    ["Reading every page", 0.0],
    ["Finding the bid items", 0.42],
    ["Indexing the documents", 0.55],
    ["Finding dates, bonds and risks", 0.68],
    ["Checking every quote against its page", 0.88],
    ["Answering the example questions", 0.93],
  ];
  let pollTimer = null;

  function showJob(jobId) {
    document.title = "Reading the package · BidBrief";
    const steps = h("ol", { class: "steps" }, ...STEPS.map(([t]) => h("li", null, h("span", { class: "dot" }), h("span", { text: t }))));
    const msg = h("p", { class: "msg", text: "Starting" });
    const clock = h("span", { class: "muted small" });
    const sheet = h("section", { class: "sheet" },
      h("div", { class: "processing", "aria-live": "polite" },
        h("h2", { text: "Reading the package" }), msg, steps, scaleBar(0), h("p", { style: "margin-top:10px" }, clock)),
      defaultTitleBlock());
    setMain(h("a", { class: "back", href: "#/" }, icon("back"), "All packages"),
      h("div", { style: "max-width:760px;margin:20px auto 0" }, sheet,
        h("p", { class: "muted small", style: "margin-top:14px", text: "You can leave this page open. Large packages take a few minutes." })));
    clearInterval(pollTimer);
    const tick = async () => {
      if (!location.hash.startsWith(`#/job/${jobId}`)) { clearInterval(pollTimer); return; }
      let job;
      try { job = await api(`api/jobs/${jobId}`); } catch (err) {
        clearInterval(pollTimer);
        msg.textContent = err.status === 404 ? "This job is no longer running. Open the package from the list." : err.message;
        return;
      }
      const f = job.fraction || 0;
      msg.textContent = job.state === "queued" ? job.message : (job.pages ? `${plural(job.pages, "page")}. ${job.message}` : job.message);
      [...steps.children].forEach((li, i) => {
        const start = STEPS[i][1], end = i + 1 < STEPS.length ? STEPS[i + 1][1] : 1.01;
        li.className = job.state === "done" || f >= end ? "done" : (f >= start && job.state === "running" ? "active" : "");
      });
      updateScale(sheet, f);
      if (job.elapsed != null) clock.textContent = `${Math.floor(job.elapsed / 60)}:${String(Math.floor(job.elapsed % 60)).padStart(2, "0")} elapsed`;
      if (job.state === "done") {
        clearInterval(pollTimer);
        refreshRemaining();
        setTimeout(() => { location.hash = `#/p/${job.package_id}/summary`; }, 500);
      } else if (job.state === "error") {
        clearInterval(pollTimer);
        refreshRemaining();
        sheet.replaceChildren(h("div", { class: "processing" },
          h("h2", { text: "This package could not be read" }),
          h("p", { class: "msg", text: "Your trial was not used. Check the files are real PDFs (not password protected) and try again." }),
          job.error ? h("p", { class: "muted small", style: "margin-top:10px", text: `Details: ${job.error}` }) : null,
          h("p", { style: "margin-top:18px" }, h("a", { class: "btn primary", href: "#/" }, "Back"))));
      }
    };
    tick();
    pollTimer = setInterval(tick, 1200);
  }

  // ------------------------------------------------------------ chips & drawer
  function chipsFor(citations, title) {
    const groups = new Map();
    for (const c of citations || []) {
      const key = `${c.file}|${c.page}|${c.verified}`;
      if (!groups.has(key)) groups.set(key, { ...c, quotes: [] });
      groups.get(key).quotes.push(c.quote);
    }
    const wrap = h("span", { class: "chips" });
    for (const g of groups.values()) {
      const label = g.short ? [h("span", { class: "file", text: g.short }), ` p. ${g.page}`] : [`p. ${g.page}`];
      wrap.append(h("button", {
        class: `chip${g.verified ? "" : " unverified"}`, type: "button",
        title: g.verified ? `Open page ${g.page}${g.short ? " of " + g.short : ""}` : "This quote could not be confirmed on the page",
        onclick: () => openPage({ file: g.file, short: g.short, page: g.page, quotes: g.quotes, title }),
      }, ...label));
    }
    return wrap;
  }

  let lastFocus = null;
  async function openPage({ file, short, page, quotes = [], title = "" }) {
    const pkg = state.pkg;
    if (!pkg) return;
    lastFocus = document.activeElement;
    const drawer = $("#drawer"), body = $("#drawer-body");
    $("#drawer-title").textContent = `Page ${page}`;
    $("#drawer-file").textContent = `${short || "Main document"} · ${file}`;
    body.replaceChildren();
    if (title) body.append(h("p", { class: "muted", text: title }));
    const q = quotes.filter(Boolean).slice(0, 3);
    for (const quote of q) body.append(h("blockquote", { class: "quote" }, h("mark", { text: quote })));
    const closeBox = h("div", { class: "page-img loading", hidden: true });
    const fullBox = h("div", { class: "page-img loading" });
    const closeCap = h("p", { class: "caption", text: "Close-up of the passage", hidden: true });
    body.append(closeCap, closeBox, h("p", { class: "caption", text: q.length ? "The whole page, passage highlighted" : "The whole page" }), fullBox);
    $("#drawer-backdrop").hidden = false;
    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    $("#drawer-close").focus();

    const base = `api/packages/${encodeURIComponent(pkg.id)}/page?`;
    const params = { file, page, q, code: state.code };
    try {
      const res = await fetch(base + qs(params), { headers: state.code ? { "X-Trial-Code": state.code } : {} });
      if (!res.ok) throw new Error("This page could not be shown.");
      const n = parseInt(res.headers.get("X-Highlights") || "0", 10);
      const url = URL.createObjectURL(await res.blob());
      fullBox.classList.remove("loading");
      fullBox.replaceChildren(h("img", { src: url, alt: `Page ${page} of ${file}` }));
      if (q.length && n > 0) {
        closeBox.hidden = false; closeCap.hidden = false;
        const img = h("img", { src: base + qs({ ...params, close: 1 }), alt: `Close-up of the quoted passage on page ${page}` });
        img.addEventListener("load", () => closeBox.classList.remove("loading"));
        closeBox.replaceChildren(img);
      } else if (q.length) {
        body.insertBefore(h("p", { class: "warn", text: "The quote could not be located on this page. Read the page carefully before relying on it." }), closeCap);
      }
    } catch (err) {
      fullBox.classList.remove("loading");
      fullBox.replaceChildren(h("p", { class: "error-box", text: err.message }));
    }
  }

  function closeDrawer() {
    const drawer = $("#drawer");
    if (!drawer.classList.contains("open")) return;
    drawer.classList.remove("open");
    drawer.setAttribute("aria-hidden", "true");
    $("#drawer-backdrop").hidden = true;
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }
  $("#drawer-close").addEventListener("click", closeDrawer);
  $("#drawer-backdrop").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { closeDrawer(); if (!$("#modal").hidden) closeModal(); }
  });

  // ------------------------------------------------------------ package
  const TABS = [
    ["summary", "Summary", "Summary", "summary"],
    ["items", "Bid items", "Items", "items"],
    ["risks", "Risks", "Risks", "risks"],
    ["changes", "Changes", "Changes", "changes"],
    ["ask", "Ask", "Ask", "ask"],
  ];
  const NAV_ICON = {
    summary: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h9l4 4v14H6z"/><path d="M9 11h7M9 15h7M9 7h3"/></svg>',
    items: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v14H4z"/><path d="M4 10h16M4 15h16M9 5v14"/></svg>',
    risks: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18v.5"/></svg>',
    changes: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 8h13l-3-3M20 16H7l3 3"/></svg>',
    ask: ICON.ask.replace('aria-hidden="true"', 'aria-hidden="true"'),
  };
  const SEV = {
    High: ["Could cost real money", "These may change your price, your schedule or who carries the risk."],
    Medium: ["Plan and price for it", "Unusual costs or requirements to include in the bid."],
    Low: ["Good to know", "Normal for this kind of job, but worth knowing."],
  };

  function checkedSet(id) {
    try { return new Set(JSON.parse(local.get(`bb_checked_${id}`) || "[]")); } catch (e) { return new Set(); }
  }
  function saveChecked(id, set) { local.set(`bb_checked_${id}`, JSON.stringify([...set])); }

  function goTab(pkg, tab, opts = {}) {
    if (opts.filter === "check") state.onlyCheck = true;
    if (state.tab === tab && opts.filter === "check") { rerenderPanel(pkg, "items"); }
    location.hash = `#/p/${pkg.id}/${tab}`;
  }

  async function showPackage(id, tab) {
    state.tab = TABS.some(([k]) => k === tab) ? tab : "summary";
    if (state.pkg && state.pkg.id === id && $("#pkg-root")) { selectTab(state.tab); return; }
    setMain(h("p", { class: "muted", text: "Opening the package…" }));
    let pkg;
    try { pkg = await api(`api/packages/${encodeURIComponent(id)}`); } catch (err) {
      setMain(h("a", { class: "back", href: "#/" }, icon("back"), "All packages"),
        h("div", { class: "error-box", style: "margin-top:20px" },
          err.status === 404 ? "This package isn't available. It may be private to another access code, or it was deleted." : err.message));
      return;
    }
    state.pkg = pkg;
    state.itemQuery = "";
    state.onlyCheck = false;
    document.title = `${pkg.title} · BidBrief`;
    const f = Object.fromEntries(pkg.brief.map((x) => [x.name, x]));
    pkg._fields = f;
    const subParts = [f.owner, f.location].filter((x) => x && x.status === "found" && x.value).map((x) => x.value);

    const head = h("div", { class: "pkg-head" },
      h("div", null,
        h("h1", { text: pkg.title }),
        subParts.length ? h("p", { class: "sub", text: subParts.join(". ") }) : null),
      h("div", { class: "actions" },
        h("button", { class: "btn small-btn", onclick: () => window.print() }, icon("print"), "Print brief"),
        pkg.items.rows.length ? h("a", { class: "btn small-btn", href: `api/packages/${encodeURIComponent(pkg.id)}/items.xlsx?${qs({ code: state.code })}` }, icon("download"), "Bid items (Excel)") : null,
        pkg.mine ? h("button", { class: "btn small-btn danger", onclick: () => deletePackage(pkg) }, icon("trash"), "Delete") : null));

    const banners = [];
    if (pkg.synthetic) banners.push(h("p", { class: "banner", text: "This is a made-up test package for checking the software. It is not a real bid." }));
    if (pkg.note) banners.push(h("p", { class: "banner info", text: pkg.note }));

    const facts = h("section", { class: "facts", "aria-label": "Key facts" },
      ...["bid_due", "pre_bid_meeting", "completion_time", "bid_security", "bonds"].map((n) => factCell(pkg, f[n])),
      h("div", { class: "fact" }, h("span", { class: "label", text: "Package" }),
        h("span", { class: "value", text: `${plural(pkg.stats.pages, "page")} in ${plural(pkg.stats.files, "file")}` }),
        h("span", { class: "fact-note", text: readingLine(pkg) })));

    const tabs = h("div", { class: "tabs", role: "tablist", "aria-label": "Package views" },
      ...TABS.map(([k, label]) => h("button", {
        class: "tab", role: "tab", id: `tab-${k}`, "aria-controls": `panel-${k}`, "aria-selected": "false",
        onclick: () => goTab(pkg, k) }, label, tabCount(pkg, k))));
    const bottom = h("nav", { class: "bottomnav", "aria-label": "Package views" },
      ...TABS.map(([k, , short, ic]) => h("button", { class: "bn", id: `bn-${k}`, onclick: () => goTab(pkg, k), "aria-label": short },
        h("span", { html: NAV_ICON[ic], style: "display:contents" }), h("span", { class: "bn-label", text: short }), tabCount(pkg, k, true))));

    const panels = TABS.map(([k]) => h("section", { class: `panel${k === "ask" ? " ask-panel" : ""}`, id: `panel-${k}`, role: "tabpanel", "aria-labelledby": `tab-${k}` }));
    setMain(h("div", { id: "pkg-root", class: "has-bottomnav" },
      h("a", { class: "back", href: "#/" }, icon("back"), "All packages"),
      head,
      h("p", { class: "exec", text: execLine(pkg) }),
      h("p", { class: "verify-line" }, "Every result links to its page. Tap a ", h("span", { class: "chip demo", text: "p. 12" }), " to see the passage."),
      ...banners, facts, tabs, ...panels,
      h("p", { class: "print-only small", style: "margin-top:20px", text: "Made with BidBrief. Every item cites its page; check the page before relying on it." })),
      bottom);
    for (const [k] of TABS) rerenderPanel(pkg, k);
    selectTab(state.tab);
  }

  function rerenderPanel(pkg, k) {
    const panel = $(`#panel-${k}`);
    if (!panel) return;
    const build = { summary: summaryPanel, items: itemsPanel, risks: risksPanel, changes: changesPanel, ask: askPanel }[k];
    panel.replaceChildren(build(pkg));
  }

  function tabCount(pkg, k, small) {
    let n = null, hot = false;
    if (k === "items") n = pkg.items.rows.length;
    if (k === "risks") { n = pkg.risks.count; hot = pkg.risks.groups.some((g) => g.severity === "High"); }
    if (k === "changes") { n = pkg.changes.items.length; hot = n > 0; }
    if (k === "summary") { const open = openReview(pkg).length; if (open) { n = open; hot = true; } }
    if (n === null || (k !== "items" && !n)) return null;
    return h("span", { class: `n${hot ? " hot" : ""}${small ? " sm" : ""}`, id: small ? `bn-n-${k}` : `tab-n-${k}`, text: String(n) });
  }

  function openReview(pkg) {
    const done = checkedSet(pkg.id);
    return pkg.review.filter((r) => r.level !== "low" && !done.has(r.key));
  }

  function readingLine(pkg) {
    const s = pkg.stats;
    const read = s.pages - s.unreadable;
    let t = `${read} of ${s.pages} pages read`;
    if (s.ocr) t += `, ${s.ocr} from scans`;
    if (s.unreadable) t += `. ${plural(s.unreadable, "page")} need${s.unreadable === 1 ? "s" : ""} a person to read.`;
    return t;
  }

  function execLine(pkg) {
    const parts = [];
    parts.push(plural(pkg.items.rows.length, "bid item"));
    const by = Object.fromEntries(pkg.risks.groups.map((g) => [g.severity, g.flags.length]));
    if (pkg.model_on) {
      if (by.High) parts.push(`${by.High} risk${by.High === 1 ? "" : "s"} that could cost real money`);
      if (by.Medium) parts.push(`${by.Medium} to plan and price for`);
      if (!by.High && !by.Medium) parts.push("no major risks flagged");
    }
    if (pkg.changes.addenda.length) {
      parts.push(`${plural(pkg.changes.addenda.length, "addendum", "addenda")} with ${plural(pkg.changes.items.length, "change")}`);
    }
    const open = pkg.review.filter((r) => r.level !== "low").length;
    if (open) parts.push(`${open} thing${open === 1 ? "" : "s"} to check before you rely on it`);
    return `This package has ${listJoin(parts)}.`;
  }

  function listJoin(parts) {
    if (parts.length <= 1) return parts.join("");
    return parts.slice(0, -1).join(", ") + " and " + parts[parts.length - 1];
  }

  function selectTab(k) {
    for (const [key] of TABS) {
      const t = $(`#tab-${key}`), p = $(`#panel-${key}`), b = $(`#bn-${key}`);
      if (!p) continue;
      const on = key === k;
      if (t) t.setAttribute("aria-selected", String(on));
      if (b) b.classList.toggle("on", on);
      p.hidden = !on;
    }
    if (k === "items" && state.onlyCheck && state.pkg) rerenderPanel(state.pkg, "items");
    if (window.matchMedia("(max-width: 720px)").matches) {
      const tabsEl = $(".tabs");
      if (tabsEl && document.activeElement && document.activeElement.classList.contains("bn")) {
        const y = (k === "summary" ? $(".exec") : $(`#panel-${k}`)).getBoundingClientRect().top + window.scrollY - 70;
        window.scrollTo({ top: Math.max(0, y) });
      }
    }
  }

  function valueNode(field, cls = "value") {
    if (!field || field.status === "missing") return h("span", { class: `${cls} missing`, text: "Not found in the documents" });
    if (field.status === "unverified") {
      return h("span", null, h("span", { class: `${cls} unverified`, text: field.value }),
        h("span", { class: "note-unverified", style: "display:block", text: "Not confirmed on the page. Check it." }));
    }
    return h("span", { class: cls, text: field.value });
  }

  function factCell(pkg, field) {
    return h("div", { class: "fact" },
      h("span", { class: "label", text: field ? field.label : "" }),
      valueNode(field),
      field && field.changed_by && field.changed_by.length
        ? h("button", { class: "redline", type: "button", onclick: () => goTab(pkg, "changes") }, `Changed in ${field.changed_by.join(", ")}`)
        : null,
      field && field.citations.length ? chipsFor(field.citations, field.label) : null);
  }

  function summaryPanel(pkg) {
    const f = pkg._fields;
    const left = h("div", null,
      h("div", { class: "block" },
        h("h3", { text: "The work" }),
        f.scope && f.scope.status !== "missing"
          ? h("div", null, h("p", { class: `scope${f.scope.status === "unverified" ? " unverified" : ""}`, text: f.scope.value }),
            f.scope.status === "unverified" ? h("p", { class: "note-unverified", style: "margin-top:6px", text: "Some details could not be confirmed on the cited page. Check it." }) : null,
            h("div", { class: "scope-chips" }, chipsFor(f.scope.citations, "The work")))
          : h("p", { class: "value missing", text: "Not found in the documents" })),
      h("div", { class: "block" },
        h("h3", { text: "Start here" }),
        pkg.read_first.length
          ? h("ol", { class: "callouts" }, ...pkg.read_first.map((r) =>
            h("li", null, h("span", { class: "text", text: r.reason }), chipsFor(r.citations, r.reason))))
          : h("p", { class: "muted", text: "No pages picked yet." })));

    const high = pkg.risks.groups.filter((g) => g.severity === "High").flatMap((g) => g.flags);
    const right = h("div", null,
      reviewBlock(pkg),
      h("div", { class: "block" },
        h("h3", { text: high.length ? "Could cost real money" : "Risks" }),
        high.length
          ? h("ul", { class: "risk-mini" }, ...high.slice(0, 4).map((r) =>
            h("li", null, h("span", { class: "cat", text: r.category }), h("span", { text: r.reason }), chipsFor(r.citations, `${r.category}: ${r.reason}`))))
          : h("p", { class: "muted", text: pkg.model_on ? "No high risks were flagged. Read the contract anyway." : "Risks need the model key." }),
        pkg.risks.count ? h("p", { style: "margin-top:12px" }, h("a", { href: `#/p/${pkg.id}/risks`, text: `See all ${plural(pkg.risks.count, "risk")}` })) : null),
      h("div", { class: "block" },
        h("h3", { text: "Bid items" }),
        pkg.items.rows.length
          ? h("p", null, `${plural(pkg.items.rows.length, "item")} from bid form page${pkg.items.pages.length === 1 ? "" : "s"} ${pageRanges(pkg.items.pages)}. `,
            h("a", { href: `#/p/${pkg.id}/items`, text: "Open the table" }))
          : h("p", { class: "muted", text: "No bid item table was found." })));
    return h("div", { class: "summary" }, left, right);
  }

  function reviewBlock(pkg) {
    const done = checkedSet(pkg.id);
    const items = pkg.review.slice().sort((a, b) => ({ high: 0, medium: 1, low: 2 }[a.level] - { high: 0, medium: 1, low: 2 }[b.level]));
    const box = h("div", { class: "block review" });
    const draw = () => {
      const open = items.filter((r) => r.level !== "low" && !done.has(r.key)).length;
      box.replaceChildren(
        h("h3", { text: "Check before you rely on it" }),
        h("p", { class: "review-status" + (open ? "" : " ok"), text: open ? `${open} open. ${readingLine(pkg)}` : `Nothing open. ${readingLine(pkg)}` }),
        items.length ? h("ul", { class: "review-list" }, ...items.map((r) => {
          const isDone = done.has(r.key);
          const cb = h("input", { type: "checkbox", "aria-label": `Mark "${r.title}" as checked` });
          cb.checked = isDone;
          cb.addEventListener("change", () => {
            if (cb.checked) done.add(r.key); else done.delete(r.key);
            saveChecked(pkg.id, done);
            draw();
            const n = openReview(pkg).length;
            for (const id of ["tab-n-summary", "bn-n-summary"]) { const el = document.getElementById(id); if (el) { el.textContent = String(n); el.hidden = !n; } }
          });
          let action = null;
          if (r.action && r.action.type === "page") {
            action = h("button", { class: "link-btn", type: "button",
              onclick: () => openPage({ file: r.action.file, short: r.action.short, page: r.action.page, quotes: r.action.quote ? [r.action.quote] : [], title: r.title }) }, "Open page");
          } else if (r.action && r.action.type === "tab") {
            action = h("button", { class: "link-btn", type: "button", onclick: () => goTab(pkg, r.action.tab, r.action) }, "Show me");
          }
          return h("li", { class: `lvl-${r.level}${isDone ? " done" : ""}` },
            r.level === "low" ? h("span", { class: "info-dot", "aria-hidden": "true" }) : cb,
            h("div", null, h("b", { text: r.title }), h("span", { class: "muted small", text: r.detail })),
            action);
        })) : h("p", { class: "muted", text: "Every page was read and every result was confirmed on its page." }));
    };
    draw();
    return box;
  }

  function itemsPanel(pkg) {
    const rows = pkg.items.rows;
    if (!rows.length) {
      return h("p", { class: "banner info", text: "No bid item table was found. If the bid form is a scanned image, see “Check before you rely on it” on the Summary." });
    }
    const search = h("input", { type: "search", placeholder: "Search items, e.g. hydrant, 12-inch, rock", "aria-label": "Search bid items", value: state.itemQuery });
    const only = h("input", { type: "checkbox" });
    only.checked = state.onlyCheck;
    const tbody = h("tbody");
    const count = h("span", { class: "muted small" });
    const draw = () => {
      const q = search.value.trim().toLowerCase();
      state.itemQuery = search.value; state.onlyCheck = only.checked;
      const shown = rows.filter((r) => (!only.checked || r.check) &&
        (!q || `${r.item} ${r.description} ${r.quantity} ${r.unit}`.toLowerCase().includes(q)));
      tbody.replaceChildren(...shown.map((r) => h("tr", { class: r.check ? "check" : "" },
        h("td", { class: "item", "data-label": "Item", text: r.item }),
        h("td", { class: "desc", "data-label": "Description" }, r.description,
          r.why ? h("span", { class: r.check ? "why" : "why soft", text: r.check ? `Check: ${r.why}` : r.why }) : null),
        h("td", { class: "num", "data-label": "Quantity" }, h("span", { class: "qty", text: r.quantity }), h("span", { class: "unit-sm", text: ` ${r.unit}` })),
        h("td", { class: "unit", "data-label": "Unit", text: r.unit }),
        h("td", { class: "pg", "data-label": "Page" }, chipsFor([{ file: r.file, short: r.short, page: r.page, quote: r.description.slice(0, 80), verified: true }], `Item ${r.item}`)))));
      count.textContent = shown.length === rows.length ? "" : `Showing ${shown.length} of ${rows.length}`;
      if (!shown.length) tbody.append(h("tr", null, h("td", { colspan: "5", class: "muted", text: "No items match." })));
    };
    search.addEventListener("input", draw);
    only.addEventListener("change", draw);
    const gaps = pkg.items.gaps.length ? ` Item numbers that look missing: ${pkg.items.gaps.join(", ")}.` : " Item numbering has no gaps.";
    const note = h("p", { class: "items-note" },
      `${plural(rows.length, "item")} from bid form page${pkg.items.pages.length === 1 ? "" : "s"} ${pageRanges(pkg.items.pages)}.${gaps} `,
      pkg.items.check ? h("strong", { text: `${plural(pkg.items.check, "row")} tinted yellow need a person to check them. ` }) : null,
      "Quantities are copied exactly as printed, never calculated.");
    const enc = encodeURIComponent(pkg.id);
    const panel = h("div", null,
      h("h2", { class: "panel-title", text: "Bid items" }),
      h("div", { class: "toolbar" },
        h("div", { class: "left" },
          h("div", { class: "search" }, h("span", { html: ICON.search, style: "display:contents" }), search),
          pkg.items.check ? h("label", { class: "toggle" }, only, "Only rows to check") : null, count),
        h("div", { class: "actions" },
          h("a", { class: "btn primary", href: `api/packages/${enc}/items.xlsx?${qs({ code: state.code })}` }, icon("download"), "Download Excel"),
          h("a", { class: "btn", href: `api/packages/${enc}/items.csv?${qs({ code: state.code })}` }, "CSV"))),
      note,
      h("div", { class: "table-wrap" }, h("table", { class: "items" },
        h("thead", null, h("tr", null, h("th", { text: "Item" }), h("th", { text: "Description" }), h("th", { text: "Quantity", style: "text-align:right" }),
          h("th", { text: "Unit" }), h("th", { text: "Page" }))),
        tbody)));
    draw();
    return panel;
  }

  function risksPanel(pkg) {
    const title = h("h2", { class: "panel-title", text: "Cost and contract risks" });
    if (!pkg.risks.groups.length) {
      return h("div", null, title, h("p", { class: "banner info", text: pkg.model_on ? "No risks were flagged. That does not mean there are none. Read the contract." : (pkg.note || "Risks need the model key.") }));
    }
    return h("div", null, title,
      h("p", { class: "muted", style: "margin-bottom:22px", text: "Each risk shows what the documents say and why it matters. Risks without a confirmed passage are left out." }),
      ...pkg.risks.groups.map((g) => h("section", { class: `risk-group sev-${g.severity}` },
        h("h3", null, SEV[g.severity][0], h("span", { class: "sev", text: `${g.flags.length} ${g.severity.toLowerCase()} priority` })),
        h("p", { class: "muted small sev-help", text: SEV[g.severity][1] }),
        h("ul", { class: "risk-cards" }, ...g.flags.map((r) => {
          const c = (r.citations || []).find((x) => x.verified) || (r.citations || [])[0];
          return h("li", null,
            h("div", { class: "risk-top" }, h("span", { class: "cat", text: r.category }), chipsFor(r.citations, `${r.category}: ${r.reason}`)),
            h("p", { class: "why-matters" }, h("span", { class: "k", text: "Why it matters" }), r.reason),
            c ? h("blockquote", { class: "says" }, h("span", { class: "k", text: "What it says" }), h("span", { class: "q", text: `“${c.quote}”` })) : null);
        })))));
  }

  function changesPanel(pkg) {
    const ch = pkg.changes;
    const title = h("h2", { class: "panel-title", text: "What the addenda change" });
    if (!ch.addenda.length) {
      return h("div", null, title, h("p", { class: "banner info", text: "No addenda were found in this package. If you have addenda, add them as PDFs together with the bid package." }));
    }
    const addList = h("p", { class: "muted", style: "margin-bottom:16px" },
      `Found ${listJoin(ch.addenda.map((a) => `${a.label} (${plural(a.pages.length, "page")})`))}. `,
      "Each change shows the new wording from the addendum and, when it was found, the original wording it replaces.");
    const cards = ch.items.map((c) => h("li", { class: "change" },
      h("div", { class: "change-top" }, h("span", { class: "kind", text: c.kind }), h("span", { class: "muted small", text: c.addendum })),
      h("p", { class: "change-sum", text: c.summary }),
      h("div", { class: "was-now" },
        h("div", { class: "was" }, h("span", { class: "k", text: "Before" }),
          c.old.length ? [h("span", { class: "q", text: `“${c.old[0].quote}”` }), chipsFor(c.old, `Before: ${c.summary}`)]
            : h("span", { class: "muted small", text: "The original wording was not found. Search the documents if it matters." })),
        h("div", { class: "now" }, h("span", { class: "k", text: "Now" }),
          c.new.length ? [h("span", { class: "q", text: `“${c.new[0].quote}”` }), chipsFor(c.new, `Now: ${c.summary}`)] : null))));
    return h("div", null, title, addList,
      ch.note ? h("p", { class: "banner", style: "margin-bottom:16px", text: ch.note }) : null,
      ch.items.length ? h("ul", { class: "changes" }, ...cards)
        : h("p", { class: "banner info", text: pkg.model_on ? "No changes were found in the addenda." : "Listing changes needs the model key." }),
      ch.dropped ? h("p", { class: "muted small", style: "margin-top:12px", text: `${plural(ch.dropped, "possible change")} left out because the addendum passage could not be confirmed.` }) : null);
  }

  function askPanel(pkg) {
    const list = h("ol", { class: "answers", reversed: true });
    const input = h("input", { id: "ask-input", type: "text", placeholder: "e.g. Are there night work restrictions?", maxlength: "500", "aria-label": "Your question", autocomplete: "off", enterkeyhint: "send" });
    const btn = h("button", { class: "btn primary", type: "submit" }, "Ask");
    const form = h("form", { class: "ask-box", onsubmit: (e) => { e.preventDefault(); submit(input.value); } }, input, btn);
    const answers = state.answers[pkg.id] || (state.answers[pkg.id] = []);

    const renderAnswer = (a) => {
      if (a.loading) return h("li", { class: "answer loading" }, h("p", { class: "q", text: a.question }), h("p", { class: "a thinking", text: "Reading the relevant pages" }));
      if (a.error) return h("li", { class: "answer" }, h("p", { class: "q", text: a.question }), h("p", { class: "a error-box", text: a.error }));
      const cls = a.status === "verified" ? "a" : a.status === "unverified" ? "a unverified" : "a notfound";
      const foot = h("div", { class: "foot" });
      if (a.status === "verified") {
        foot.append(h("span", { text: "Source:" }), chipsFor(a.citations.filter((c) => c.verified), a.question));
      } else if (a.status === "unverified") {
        foot.append(h("span", { text: "Not confirmed on the cited page. Check it:" }), chipsFor(a.citations, a.question));
      } else if (a.checked && a.checked.length) {
        foot.append(h("span", { text: "Closest pages checked:" }),
          chipsFor(a.checked.map((c) => ({ ...c, quote: "", verified: true })), a.question));
      }
      return h("li", { class: "answer" }, h("p", { class: "q", text: a.question }), h("p", { class: cls, text: a.text }), foot);
    };
    const draw = () => list.replaceChildren(...answers.slice().reverse().map(renderAnswer));

    async function submit(text) {
      const q = (text || "").trim();
      if (!q) { input.focus(); return; }
      input.value = "";
      const entry = { question: q, loading: true };
      answers.push(entry);
      draw();
      btn.disabled = true;
      try {
        const res = await api(`api/packages/${encodeURIComponent(pkg.id)}/ask`, { method: "POST", json: { question: q } });
        Object.assign(entry, res, { loading: false });
      } catch (err) {
        Object.assign(entry, { loading: false, error: err.message });
      }
      btn.disabled = false;
      draw();
      if (!window.matchMedia("(max-width: 720px)").matches) input.focus();
    }
    draw();
    return h("div", null,
      h("h2", { class: "panel-title", text: "Ask the bid package" }),
      h("p", { class: "ask-help" }, "Ask in plain English. Answers are written by AI using only this package, and each one shows the page it came from. If the documents don't say, it tells you."),
      !pkg.model_on ? h("p", { class: "banner info", style: "margin-bottom:16px", text: "Answers need the model key. You will still see the pages most likely to have the answer." }) : null,
      form,
      h("div", { class: "suggest" }, h("span", { class: "muted", style: "align-self:center", text: "Try:" }),
        ...pkg.examples.map((q) => h("button", { type: "button", onclick: () => submit(q) }, q))),
      list);
  }

  async function deletePackage(pkg) {
    if (!window.confirm(`Delete "${pkg.title}" and its files from BidBrief? This cannot be undone.`)) return;
    try {
      await api(`api/packages/${encodeURIComponent(pkg.id)}`, { method: "DELETE" });
      state.pkg = null;
      location.hash = "#/";
    } catch (err) { window.alert(err.message); }
  }

  // ------------------------------------------------------------ router
  async function route() {
    closeDrawer();
    clearInterval(pollTimer);
    const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
    if (parts[0] === "p" && parts[1]) return showPackage(decodeURIComponent(parts[1]), parts[2]);
    state.pkg = null;
    if (parts[0] === "job" && parts[1]) return showJob(parts[1]);
    return showHome();
  }
  window.addEventListener("hashchange", () => {
    const parts = location.hash.replace(/^#\/?/, "").split("/");
    const samePkg = parts[0] === "p" && state.pkg && state.pkg.id === decodeURIComponent(parts[1] || "");
    if (!samePkg) window.scrollTo(0, 0);
    route();
  });

  (async function init() {
    try { state.config = await api("api/config"); } catch (e) { state.config = { trial_required: false, contact: "the owner", max_pages: 600 }; }
    renderAccess();
    await refreshRemaining();
    route();
  })();
})();
