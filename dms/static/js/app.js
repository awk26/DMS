(function () {
  "use strict";

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const csrf = $('meta[name="csrf-token"]').content;

  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    const b = crypto.getRandomValues(new Uint8Array(16));
    return Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  }

  // ---------------------------------------------------------------- toasts
  function toast(message, kind) {
    let box = $(".toasts");
    if (!box) {
      box = document.createElement("div");
      box.className = "toasts";
      box.setAttribute("aria-live", "polite");
      document.body.appendChild(box);
    }
    const el = document.createElement("div");
    el.className = "toast toast-" + (kind || "info");
    el.textContent = message;
    const close = document.createElement("button");
    close.className = "toast-close";
    close.setAttribute("aria-label", "Dismiss");
    close.textContent = "×";
    el.appendChild(close);
    box.appendChild(el);
    autoDismiss(el);
  }
  function autoDismiss(el) {
    const ms = el.classList.contains("toast-error") ? 10000 : 5000;
    setTimeout(() => el.remove(), ms);
  }
  $$(".toast").forEach(autoDismiss);
  document.addEventListener("click", (e) => {
    if (e.target.classList.contains("toast-close")) e.target.closest(".toast").remove();
  });

  // ---------------------------------------------------------------- idempotency keys
  $$('input[name="idempotency_key"]').forEach((i) => { i.value = uuid(); });

  // ---------------------------------------------------------------- folder selects
  let folders = [];
  try { folders = JSON.parse($("#folder-data").textContent || "[]"); } catch (e) { folders = []; }

  function descendantsOf(id) {
    const out = new Set([String(id)]);
    let grew = true;
    while (grew) {
      grew = false;
      folders.forEach((f) => {
        if (f.parent !== null && out.has(String(f.parent)) && !out.has(String(f.id))) { out.add(String(f.id)); grew = true; }
      });
    }
    return out;
  }

  let rootPerms = {};
  try { rootPerms = JSON.parse(($("#root-perms") || {}).textContent || "{}"); } catch (e) { rootPerms = {}; }

  function fillFolderSelect(select, selected, exclude) {
    const needEdit = select.hasAttribute("data-need-edit");
    select.innerHTML = "";
    if (!needEdit || rootPerms.edit) select.appendChild(new Option("Root (top level)", ""));
    folders.forEach((f) => {
      if (exclude && exclude.has(String(f.id))) return;
      const opt = new Option("\u00a0\u00a0\u00a0".repeat(f.depth + 1) + f.name, f.id);
      opt.title = f.path;
      if (needEdit && !f.can) opt.disabled = true;
      select.appendChild(opt);
    });
    select.value = selected == null ? "" : String(selected);
    if (select.selectedIndex < 0 || (select.options[select.selectedIndex] || {}).disabled) {
      const firstOk = Array.from(select.options).find((o) => !o.disabled);
      select.value = firstOk ? firstOk.value : "";
    }
  }
  $$("select[data-folder-select]").forEach((s) => fillFolderSelect(s, s.dataset.selected || ""));

  // Selects that save their form as soon as they change (e.g. a person's access level).
  document.addEventListener("change", (e) => {
    const sel = e.target.closest("select[data-autosubmit]");
    if (sel && sel.form) sel.form.submit();
  });

  // ---------------------------------------------------------------- modals
  let lastFocus = null;
  function openModal(id) {
    const m = document.getElementById(id);
    if (!m) return;
    lastFocus = document.activeElement;
    m.hidden = false;
    const first = m.querySelector("input[type=text], select, input[type=file], button[data-confirm-ok]");
    if (first) setTimeout(() => first.focus(), 30);
  }
  function closeModal(m) {
    if (!m) return;
    m.hidden = true;
    if (lastFocus) lastFocus.focus();
  }
  document.addEventListener("click", (e) => {
    const opener = e.target.closest("[data-modal-open]");
    if (opener) { e.preventDefault(); openModal(opener.dataset.modalOpen); return; }
    if (e.target.closest("[data-modal-close]")) { closeModal(e.target.closest(".modal")); return; }
    if (e.target.classList.contains("modal")) closeModal(e.target);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") $$(".modal:not([hidden])").forEach(closeModal);
  });

  // ---------------------------------------------------------------- rename / move
  document.addEventListener("click", (e) => {
    const r = e.target.closest("[data-rename]");
    if (r) {
      const m = $("#rename-modal");
      const form = $("form", m);
      form.action = r.dataset.action;
      $("#rename-title").textContent = r.dataset.kind === "folder" ? "Rename folder" : "Rename document";
      const input = $("#rename-input");
      input.value = r.dataset.name || "";
      openModal("rename-modal");
      setTimeout(() => {
        const dot = input.value.lastIndexOf(".");
        input.setSelectionRange(0, r.dataset.kind !== "folder" && dot > 0 ? dot : input.value.length);
      }, 40);
      return;
    }
    const mv = e.target.closest("[data-move]");
    if (mv) {
      const m = $("#move-modal");
      const form = $("form", m);
      form.action = mv.dataset.action;
      const select = $("#move-select");
      const isFolder = mv.dataset.kind === "folder";
      const mode = mv.dataset.mode || "move";
      select.name = isFolder ? "parent_id" : "folder_id";
      fillFolderSelect(select, mv.dataset.current, isFolder ? descendantsOf(mv.dataset.self) : null);
      const what = isFolder ? "folder" : "document";
      const name = mv.dataset.name || "";
      const titles = { move: "Move " + what, copy: "Copy " + what, link: "Add shortcut" };
      const buttons = { move: "Move here", copy: "Copy here", link: "Add shortcut here" };
      const labels = {
        move: "Choose a new location for \u201c" + name + "\u201d.",
        copy: "Choose where to put a copy of \u201c" + name + "\u201d" + (isFolder ? " and everything in it." : ". The copy starts at version 1.0."),
        link: "\u201c" + name + "\u201d will also appear in the chosen folder. The document itself stays where it is.",
      };
      $("#move-title").textContent = titles[mode];
      $("[data-move-submit]", m).textContent = buttons[mode];
      $("[data-move-label]", m).textContent = labels[mode];
      openModal("move-modal");
    }
  });

  // ---------------------------------------------------------------- new version / save attachment
  function resetUploadForm(form) {
    form.reset();
    const key = $('input[name="idempotency_key"]', form);
    if (key) key.value = uuid();
    const err = $(".form-error", form);
    if (err) err.hidden = true;
    const files = $(".dropzone-files", form);
    if (files) files.textContent = "";
    const btn = $("button[type=submit]", form);
    if (btn) btn.disabled = false;
  }
  document.addEventListener("click", (e) => {
    const nv = e.target.closest("[data-new-version]");
    if (nv) {
      const m = $("#new-version-modal");
      const form = $("form", m);
      resetUploadForm(form);
      form.action = nv.dataset.action;
      $("input[type=file]", form).accept = nv.dataset.ext ? "." + nv.dataset.ext : "";
      $("[data-nv-label]", m).textContent = "Updating “" + nv.dataset.name + "” (currently version " + nv.dataset.version +
        "). The current version stays in the history. The new file must be a ." + nv.dataset.ext + " file.";
      openModal("new-version-modal");
      return;
    }
    const imp = e.target.closest("[data-import-attachment]");
    if (imp) {
      const m = $("#import-modal");
      $("[data-import-part]", m).value = imp.dataset.part;
      $('input[name="idempotency_key"]', m).value = uuid();
      $("[data-import-label]", m).textContent = "Save a copy of “" + imp.dataset.name + "” as a new document.";
      openModal("import-modal");
    }
  });

  // ---------------------------------------------------------------- dropdown menus
  document.addEventListener("click", (e) => {
    $$("details.menu[open]").forEach((d) => {
      if (!d.contains(e.target) || e.target.closest(".menu-list button, .menu-list a")) d.open = false;
    });
  });

  // ---------------------------------------------------------------- highlight a just-uploaded document
  const hl = $("tr.is-highlight");
  if (hl) hl.scrollIntoView({ block: "center" });

  // ---------------------------------------------------------------- confirmations
  let pendingForm = null;
  document.addEventListener("submit", (e) => {
    const form = e.target;
    if (form.dataset.confirm && !form.dataset.confirmed) {
      e.preventDefault();
      pendingForm = form;
      const m = $("#confirm-modal");
      $("[data-confirm-text]", m).textContent = form.dataset.confirm;
      const danger = /delete|trash|disconnect|permanent/i.test(form.dataset.confirm);
      const ok = $("[data-confirm-ok]", m);
      ok.className = "btn " + (danger ? "btn-danger" : "btn-primary");
      ok.textContent = danger ? "Yes, continue" : "Confirm";
      openModal("confirm-modal");
    }
  }, true);
  $("[data-confirm-ok]").addEventListener("click", () => {
    if (!pendingForm) return;
    pendingForm.dataset.confirmed = "1";
    closeModal($("#confirm-modal"));
    if (pendingForm.requestSubmit) pendingForm.requestSubmit(); else pendingForm.submit();
    pendingForm = null;
  });

  // ---------------------------------------------------------------- uploads (with progress)
  $$("[data-dropzone]").forEach((zone) => {
    const input = $("input[type=file]", zone);
    const label = $(".dropzone-files", zone);
    const show = () => {
      const names = Array.from(input.files || []).map((f) => f.name);
      label.textContent = names.length > 3 ? names.length + " files selected" : names.join(", ");
    };
    input.addEventListener("change", show);
    ["dragenter", "dragover"].forEach((ev) => zone.addEventListener(ev, () => zone.classList.add("is-over")));
    ["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, () => zone.classList.remove("is-over")));
  });

  $$("form[data-upload-form]").forEach((form) => {
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const input = $("input[type=file]", form);
      const err = $(".form-error", form);
      const progress = $(".progress", form);
      const bar = $(".progress-bar", form);
      const btn = $("button[type=submit]", form);
      err.hidden = true;
      if (!input.files.length) { err.textContent = "Please choose a file."; err.hidden = false; return; }
      const xhr = new XMLHttpRequest();
      xhr.open("POST", form.action);
      xhr.setRequestHeader("X-CSRFToken", csrf);
      xhr.setRequestHeader("X-Requested-With", "fetch");
      xhr.setRequestHeader("Accept", "application/json");
      xhr.upload.addEventListener("progress", (ev) => {
        if (ev.lengthComputable) bar.style.width = Math.round((ev.loaded / ev.total) * 100) + "%";
      });
      xhr.onload = () => {
        let data = {};
        try { data = JSON.parse(xhr.responseText); } catch (x) { /* non-JSON error page */ }
        if (xhr.status >= 200 && xhr.status < 300 && data.ok) {
          window.location.href = data.redirect || window.location.href;
        } else {
          err.textContent = data.error || (xhr.status === 413 ? "The file is too large." : "Upload failed (" + xhr.status + ").");
          err.hidden = false;
          btn.disabled = false;
          progress.hidden = true;
        }
      };
      xhr.onerror = () => {
        err.textContent = "Network error. Check your connection and try again.";
        err.hidden = false;
        btn.disabled = false;
        progress.hidden = true;
      };
      btn.disabled = true;
      progress.hidden = false;
      bar.style.width = "0";
      xhr.send(new FormData(form)); // same idempotency key on retry
    });
  });

  // ---------------------------------------------------------------- Gmail sync
  function pollStatus(startedAt, attempts) {
    fetch("/gmail/status", { headers: { Accept: "application/json" } })
      .then((r) => r.json())
      .then((s) => {
        const done = !s.sync_in_progress && s.last_sync_at && new Date(s.last_sync_at) >= startedAt;
        if (done || attempts > 40) {
          toast(done ? "Synchronization finished." : "Synchronization is still running in the background.", done ? "success" : "info");
          setTimeout(() => window.location.reload(), 900);
        } else {
          setTimeout(() => pollStatus(startedAt, attempts + 1), 2000);
        }
      })
      .catch(() => setTimeout(() => pollStatus(startedAt, attempts + 1), 3000));
  }
  $$("form[data-sync-form]").forEach((form) => {
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const btn = $("button", form);
      btn.disabled = true;
      const startedAt = new Date(Date.now() - 2000);
      fetch(form.action, {
        method: "POST",
        headers: { "X-CSRFToken": csrf, "X-Requested-With": "fetch", Accept: "application/json" },
        body: new FormData(form),
      })
        .then((r) => r.json().then((d) => ({ status: r.status, data: d })))
        .then(({ status, data }) => {
          if (status === 202) { toast("Checking Gmail for newly archived emails…", "info"); pollStatus(startedAt, 0); }
          else if (data.ok) { toast(data.message, "success"); setTimeout(() => window.location.reload(), 900); }
          else { toast(data.error || "Synchronization failed.", "error"); btn.disabled = false; }
        })
        .catch(() => { toast("Could not start synchronization.", "error"); btn.disabled = false; });
    });
  });

  // ---------------------------------------------------------------- custom metadata fields (upload / properties)
  function fieldInput(f, value) {
    const wrap = document.createElement("div");
    wrap.className = "field";
    const id = "meta-" + f.id + "-" + Math.random().toString(36).slice(2, 7);
    const label = document.createElement("label");
    label.htmlFor = id;
    label.textContent = f.label + (f.required ? " *" : "");
    wrap.appendChild(label);
    let input;
    if (f.type === "choice" || f.type === "boolean") {
      input = document.createElement("select");
      input.appendChild(new Option("", ""));
      const choices = f.type === "boolean" ? [["yes", "Yes"], ["no", "No"]] : f.choices.map((c) => [c, c]);
      choices.forEach(([v, t]) => input.appendChild(new Option(t, v)));
    } else {
      input = document.createElement("input");
      input.type = f.type === "date" ? "date" : "text";
      if (f.type === "number") input.inputMode = "decimal";
    }
    input.id = id;
    input.name = "meta_" + f.id;
    if (f.required && f.type !== "boolean") input.required = true;
    if (value != null) input.value = value;
    wrap.appendChild(input);
    if (f.help) {
      const help = document.createElement("small");
      help.className = "muted";
      help.textContent = f.help;
      wrap.appendChild(help);
    }
    return wrap;
  }

  let schemaSeq = 0;
  function loadSchema(form, first) {
    const box = $("[data-metadata-fields]", form);
    if (!box) return;
    const folderSel = $("select[name=folder_id]", form);
    const folderId = folderSel ? folderSel.value : (form.dataset.folderId || "");
    const typeSel = $("[data-doctype-select]", form);
    const typeId = typeSel ? typeSel.value : "";
    const current = {};
    $$("[name^=meta_]", box).forEach((el) => { current[el.name.slice(5)] = el.value; });
    if (first && box.dataset.values) {
      try { Object.assign(current, JSON.parse(box.dataset.values)); } catch (e) { /* ignore */ }
    }
    const my = ++schemaSeq;
    fetch("/documents/schema.json?folder_id=" + encodeURIComponent(folderId) + "&type_id=" + encodeURIComponent(typeId),
      { headers: { Accept: "application/json" } })
      .then((r) => r.json())
      .then((data) => {
        if (my !== schemaSeq) return;
        if (typeSel && typeSel.options.length <= 1 && data.types) {
          data.types.forEach((t) => typeSel.appendChild(new Option(t.name, t.id)));
          if (typeSel.dataset.selected) typeSel.value = typeSel.dataset.selected;
        }
        const list = $("#tag-options");
        if (list && !list.children.length && data.tags) {
          data.tags.forEach((t) => { const o = document.createElement("option"); o.value = t; list.appendChild(o); });
        }
        box.innerHTML = "";
        data.fields.forEach((f) => box.appendChild(fieldInput(f, current[String(f.id)])));
      })
      .catch(() => { /* metadata is validated on the server as well */ });
  }
  $$("form[data-metadata-form]").forEach((form) => {
    const inModal = form.closest(".modal");
    form.addEventListener("change", (e) => {
      if (e.target.matches("select[name=folder_id], [data-doctype-select]")) loadSchema(form, false);
    });
    if (inModal) {
      document.addEventListener("click", (e) => {
        const opener = e.target.closest("[data-modal-open]");
        if (opener && opener.dataset.modalOpen === inModal.id) setTimeout(() => loadSchema(form, true), 0);
      });
    }
  });

  // ---------------------------------------------------------------- version compare picks
  const picks = $$("[data-compare-pick]");
  if (picks.length) {
    const btn = $("[data-compare-submit]");
    const update = () => {
      const chosen = picks.filter((p) => p.checked);
      picks.forEach((p) => { p.disabled = !p.checked && chosen.length >= 2; });
      if (btn) btn.disabled = chosen.length !== 2;
    };
    picks.forEach((p) => p.addEventListener("change", update));
    update();
  }

  // ---------------------------------------------------------------- sidebar (mobile)
  const toggle = $("[data-toggle-sidebar]");
  if (toggle) toggle.addEventListener("click", () => $("#sidebar").classList.toggle("open"));

  // ---------------------------------------------------------------- back links
  $$("[data-back]").forEach((a) => a.addEventListener("click", (e) => {
    if (window.history.length > 1) { e.preventDefault(); window.history.back(); }
  }));

  // ---------------------------------------------------------------- global search suggestions
  const searchInput = $("[data-global-search]");
  if (searchInput) {
    const results = $(".search-results");
    let timer = null;
    let seq = 0;
    const hide = () => { results.hidden = true; };
    searchInput.addEventListener("input", () => {
      clearTimeout(timer);
      const q = searchInput.value.trim();
      if (q.length < 2) { hide(); return; }
      timer = setTimeout(() => {
        const my = ++seq;
        fetch("/documents/search.json?q=" + encodeURIComponent(q), { headers: { Accept: "application/json" } })
          .then((r) => r.json())
          .then((data) => {
            if (my !== seq) return;
            results.innerHTML = "";
            const add = (title, items, sub) => {
              if (!items.length) return;
              const g = document.createElement("div");
              g.className = "group";
              g.textContent = title;
              results.appendChild(g);
              items.forEach((it) => {
                const a = document.createElement("a");
                a.href = it.url;
                const s = document.createElement("span"); s.textContent = it.name;
                const sm = document.createElement("small"); sm.textContent = it[sub];
                a.append(s, sm);
                results.appendChild(a);
              });
            };
            add("Folders", data.folders, "path");
            add("Documents", data.documents, "folder");
            if (!results.children.length) {
              const empty = document.createElement("div");
              empty.className = "group";
              empty.textContent = "No name matches \u2014 press Enter to search content";
              results.appendChild(empty);
            }
            results.hidden = false;
          })
          .catch(hide);
      }, 180);
    });
    document.addEventListener("click", (e) => { if (!e.target.closest(".search")) hide(); });
    searchInput.addEventListener("keydown", (e) => { if (e.key === "Escape") hide(); });
  }
})();
