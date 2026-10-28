(() => {
  "use strict";

  const people = {
    a: { name: "Alex Morgan", initials: "AM", email: "alex.morgan@example.test" },
    b: { name: "Blair Chen", initials: "BC", email: "blair.chen@example.test" }
  };
  const rolePermissions = {
    viewer: ["view", "download"],
    commenter: ["view", "download", "comment"],
    editor: ["view", "download", "edit"],
    manager: ["view", "download", "edit", "delete", "share", "manage"],
    owner: ["view", "download", "edit", "delete", "share", "manage"]
  };
  const makeVersion = (version, author, note, date, type = "minor") => ({
    id: crypto.randomUUID(), version, author, note, date, type
  });
  const state = {
    user: "a",
    view: "workspace",
    folderId: null,
    selectedId: null,
    search: { query: "", type: "all", tag: "all", author: "all", date: "all", size: "all", sort: "name" },
    items: [
      { id: "folder-project", name: "Project Atlas", type: "folder", owner: "a", folderId: null, modified: "2026-10-24", tags: [], permissions: {} },
      { id: "folder-legal", name: "Legal", type: "folder", owner: "a", folderId: null, modified: "2026-10-20", tags: [], permissions: {} },
      { id: "doc-brief", name: "Project Atlas — Brief.docx", type: "docx", owner: "a", folderId: "folder-project", modified: "2026-10-25", size: 182000, tags: ["Project Atlas", "Planning"], author: "Alex Morgan", metadata: { classification: "Internal", department: "Product" }, comments: "Scope, milestones, and initial delivery plan.", versions: [makeVersion("1.0", "Alex Morgan", "Initial draft", "2026-10-20", "major"), makeVersion("1.1", "Alex Morgan", "Added delivery milestones", "2026-10-25")] , permissions: { b: { role: "viewer" } } },
      { id: "doc-roadmap", name: "Product roadmap.pdf", type: "pdf", owner: "a", folderId: "folder-project", modified: "2026-10-23", size: 2940000, tags: ["Planning", "Roadmap"], author: "Alex Morgan", metadata: { classification: "Internal", department: "Product" }, comments: "Q4 roadmap review.", versions: [makeVersion("1.0", "Alex Morgan", "Published for review", "2026-10-23", "major")], permissions: {} },
      { id: "doc-budget", name: "Budget forecast.xlsx", type: "xlsx", owner: "a", folderId: "folder-project", modified: "2026-10-19", size: 536000, tags: ["Finance", "Planning"], author: "Alex Morgan", metadata: { classification: "Confidential", department: "Finance" }, comments: "Working forecast, illustrative demo data.", versions: [makeVersion("1.0", "Alex Morgan", "Initial forecast", "2026-10-19", "major")], permissions: {} },
      { id: "doc-policy", name: "Records policy.pdf", type: "pdf", owner: "a", folderId: "folder-legal", modified: "2026-10-11", size: 782000, tags: ["Policy", "Records"], author: "Alex Morgan", metadata: { classification: "Internal", department: "Legal" }, comments: "Retention overview.", versions: [makeVersion("1.0", "Alex Morgan", "Approved policy draft", "2026-10-11", "major")], permissions: {} },
      { id: "folder-research", name: "Research", type: "folder", owner: "b", folderId: null, modified: "2026-10-21", tags: [], permissions: {} },
      { id: "doc-review", name: "Quarterly review.pptx", type: "pptx", owner: "b", folderId: "folder-research", modified: "2026-10-22", size: 4260000, tags: ["Review", "Q4"], author: "Blair Chen", metadata: { classification: "Internal", department: "Operations" }, comments: "Quarterly operating review.", versions: [makeVersion("1.0", "Blair Chen", "Initial review deck", "2026-10-18", "major"), makeVersion("1.1", "Blair Chen", "Added operating metrics", "2026-10-22")], permissions: { a: { role: "editor" } } },
      { id: "doc-notes", name: "Interview notes.txt", type: "txt", owner: "b", folderId: "folder-research", modified: "2026-10-17", size: 12000, tags: ["Research"], author: "Blair Chen", metadata: { classification: "Confidential", department: "Research" }, comments: "Synthetic notes for the demo.", versions: [makeVersion("1.0", "Blair Chen", "Imported notes", "2026-10-17", "major")], permissions: {} }
    ],
    recycle: [],
    favourites: ["doc-brief", "doc-review"],
    audit: [],
    adminUsers: [
      { name: "Alex Morgan", email: "alex.morgan@example.test", role: "Records Manager", status: "Active" },
      { name: "Blair Chen", email: "blair.chen@example.test", role: "Contributor", status: "Active" }
    ],
    adminGroups: ["Records Team", "Project Atlas"],
    adminRoles: ["Admin", "Records Manager", "Contributor", "Reader", "Auditor"],
    customRolePermissions: {},
    repositories: [
      { name: "Product Workspace", owner: "Alex Morgan", usage: "8.2 GB", quota: 20 },
      { name: "Research Archive", owner: "Blair Chen", usage: "6.6 GB", quota: 15 }
    ],
    securitySettings: { provider: "", timeout: "30", mfa: false, ip: false, saved: false },
    versionRetention: { mode: "count", value: 20 },
    uploadPolicy: {
      maxMb: 100,
      allowed: ["pdf", "doc", "docx", "odt", "rtf", "xls", "xlsx", "csv", "ppt", "pptx", "txt", "md", "png", "jpg", "jpeg", "gif", "webp", "svg", "dwg", "dxf", "step", "stp", "mp3", "wav", "mp4", "mov", "zip", "7z", "tar", "gz"],
      blocked: []
    },
    emails: [
      { id: "mail-1", sender: "Taylor Reed", subject: "Q4 planning materials", date: "2026-10-25", snippet: "Sharing the latest planning notes for your review.", attachments: ["planning-notes.pdf"] },
      { id: "mail-2", sender: "Jordan Lee", subject: "Records retention update", date: "2026-10-23", snippet: "A reminder about the upcoming retention review.", attachments: [] },
      { id: "mail-3", sender: "Casey Park", subject: "Project Atlas kickoff", date: "2026-10-20", snippet: "Agenda and background reading for the kickoff.", attachments: ["agenda.docx", "overview.pdf"] }
    ]
  };

  const content = document.getElementById("content");
  const modal = document.getElementById("modal");
  const modalContent = document.getElementById("modal-content");
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[char]);
  const currentPerson = () => people[state.user];
  const getItem = (id) => state.items.find((item) => item.id === id);
  const formatSize = (size) => size <= 0 ? "0 B" : size < 1000000 ? `${Math.max(1, Math.round(size / 1000))} KB` : `${(size / 1000000).toFixed(1)} MB`;
  const iconFor = (item) => item.shortcut ? "↗" : item.type === "folder" ? "▰" : ({ pdf: "PDF", docx: "W", xlsx: "X", pptx: "P", txt: "T", png: "IMG", jpg: "IMG", csv: "CSV" }[item.type] || item.type.toUpperCase());
  const highlightName = (name) => {
    const query = state.search.query.trim();
    if (!query) return esc(name);
    const index = name.toLowerCase().indexOf(query.toLowerCase());
    return index < 0 ? esc(name) : `${esc(name.slice(0, index))}<mark class="highlight">${esc(name.slice(index, index + query.length))}</mark>${esc(name.slice(index + query.length))}`;
  };
  const effectiveAccess = (item, user = state.user) => {
    if (!item || item.owner === user) return item ? { role: "owner", inheritance: "owner" } : null;
    if (item.shortcut) return effectiveAccess(getItem(item.targetId), user);
    if (item.permissions?.[user]) return { ...item.permissions[user], inheritance: "direct" };
    let parent = getItem(item.folderId);
    while (parent) {
      if (parent.owner === user) return { role: "manager", inheritance: "inherited" };
      if (parent.permissions?.[user]) return { ...parent.permissions[user], inheritance: "inherited" };
      parent = getItem(parent.folderId);
    }
    return null;
  };
  const itemRole = (item) => effectiveAccess(item)?.role || null;
  const displayRole = (role) => {
    if (!role) return "No access";
    if (role.startsWith("custom:")) return state.adminRoles.find((name) => name.toLowerCase() === role.slice(7)) || role.slice(7);
    return role[0].toUpperCase() + role.slice(1);
  };
  const can = (item, permission) => {
    if (!item) return false;
    if (item.shortcut) return can(getItem(item.targetId), permission);
    if (item.owner === state.user) return true;
    const access = effectiveAccess(item);
    if (!access) return false;
    return access.permissions ? access.permissions.includes(permission) : Boolean(rolePermissions[access.role]?.includes(permission));
  };
  const canModify = (item) => can(item, "edit") && (!item.checkedOutBy || item.checkedOutBy === state.user);
  const logAction = (message) => state.audit.unshift({ date: new Date().toLocaleString(), user: currentPerson().name, message });
  const toast = (message, kind = "") => {
    const node = document.createElement("div");
    node.className = `toast ${kind}`;
    node.textContent = message;
    document.getElementById("toast-region").append(node);
    window.setTimeout(() => node.remove(), 3500);
  };
  const button = (label, action, classes = "button", extra = "") =>
    `<button type="button" class="${classes}" data-action="${esc(action)}" ${extra}>${label}</button>`;
  const head = (title, description, actions = "") => `
    <div class="page-head"><div><p class="eyebrow">DOCUMENT MANAGEMENT · LOCAL DEMO</p><h1>${title}</h1><p>${description}</p></div><div class="page-actions">${actions}</div></div>`;
  const folderChildren = (folderId) => {
    const parent = getItem(folderId);
    const owner = parent?.owner || state.user;
    return state.items.filter((item) => item.folderId === folderId && !item.deleted && (item.owner === state.user || effectiveAccess(item)) && (owner === state.user || effectiveAccess(item)));
  };
  const visibleShared = () => state.items.filter((item) => item.owner !== state.user && effectiveAccess(item) && !item.deleted);
  const ownedShared = () => state.items.filter((item) => item.owner === state.user && Object.keys(item.permissions || {}).length && !item.deleted);
  const inCurrentView = () => {
    let rows = [];
    if (state.view === "workspace") rows = folderChildren(state.folderId);
    if (state.view === "shared-me") rows = visibleShared();
    if (state.view === "shared-by") rows = ownedShared();
    if (state.view === "recent") rows = state.items.filter((item) => item.owner === state.user && !item.deleted && item.type !== "folder");
    if (state.view === "favourites") rows = state.items.filter((item) => state.favourites.includes(item.id) && effectiveAccess(item) && !item.deleted);
    if (state.view === "search") rows = filteredSearchResults();
    return sortItems(rows);
  };
  const sortItems = (rows) => [...rows].sort((a, b) => {
    const direction = state.search.sort === "modified" ? b.modified.localeCompare(a.modified) : a.name.localeCompare(b.name);
    return direction;
  });
  const filteredSearchResults = () => {
    const q = state.search.query.trim().toLowerCase();
    return state.items.filter((item) => {
      if (item.deleted || !(item.owner === state.user || item.permissions?.[state.user])) return false;
      if (q && ![item.name, item.author, ...(item.tags || []), item.comments].join(" ").toLowerCase().includes(q)) return false;
      if (state.search.type !== "all" && item.type !== state.search.type) return false;
      if (state.search.tag !== "all" && !(item.tags || []).includes(state.search.tag)) return false;
      if (state.search.author !== "all" && item.author !== state.search.author) return false;
      if (state.search.date !== "all") {
        const days = Number(state.search.date);
        const cutoff = new Date(Date.now() - days * 86400000).toISOString().slice(0, 10);
        if (item.modified < cutoff) return false;
      }
      if (state.search.size !== "all") {
        const size = item.size || 0;
        if (state.search.size === "small" && size >= 1000000) return false;
        if (state.search.size === "medium" && (size < 1000000 || size >= 10000000)) return false;
        if (state.search.size === "large" && size < 10000000) return false;
      }
      function editMetadata(item) {
        if (!can(item, "edit")) return toast("You do not have permission to edit metadata.", "error");
        showModal("Edit metadata", "Required classification and controlled tags are validated locally.", `<div class="field"><label>Classification <span class="muted">(required)</span></label><select name="classification" required>${["Internal","Confidential","Public"].map((value) => `<option ${item.metadata?.classification === value ? "selected" : ""}>${value}</option>`).join("")}</select></div><div class="field"><label>Department</label><input name="department" value="${esc(item.metadata?.department || "")}" required></div><div class="field"><label>Tags</label><input name="tags" value="${esc((item.tags || []).join(", "))}" placeholder="Comma-separated controlled tags"></div>`, (form) => {
          const tags = String(form.get("tags")).split(",").map((tag) => tag.trim()).filter(Boolean);
          if (!tags.length) { toast("Add at least one classification tag.", "error"); return false; }
          item.metadata = { ...item.metadata, classification: form.get("classification"), department: form.get("department").trim() };
          item.tags = tags; item.modified = today(); logAction(`updated metadata for “${item.name}”`); toast("Metadata updated in local demo state.");
        }, "Save metadata");
      }
      function addComment(item) {
        showModal("Add a comment", `Comment on “${esc(item.name)}”.`, `<div class="field"><label>Comment</label><textarea name="comment" required maxlength="500"></textarea></div>`, (form) => {
          item.discussion = item.discussion || [];
          item.discussion.unshift({ author: currentPerson().name, date: today(), text: form.get("comment").trim() });
          logAction(`commented on “${item.name}”`); toast("Comment added to local demo state.");
        }, "Add comment");
      }
      return true;
    });
  };
  const rowsTable = (rows, options = {}) => {
    if (!rows.length) return `<div class="empty-state"><div>▧</div><strong>${options.emptyTitle || "Nothing here yet"}</strong>${options.emptyText || "Items that you can access will appear here."}</div>`;
    return `<table class="item-table"><thead><tr><th>Name</th><th>Access</th><th>Modified</th><th>Size</th><th></th></tr></thead><tbody>${rows.map((item) => `
      <tr class="${state.selectedId === item.id ? "selected" : ""}" data-row="${esc(item.id)}">
        <td><div class="item-name"><span class="item-icon ${item.type === "folder" ? "folder" : ""}">${esc(iconFor(item))}</span><span><button data-action="open-item" data-id="${esc(item.id)}">${highlightName(item.name)}</button>${item.owner !== state.user ? `<small class="item-sub">Owned by ${esc(people[item.owner].name)}</small>` : item.type === "folder" ? `<small class="item-sub">Private by default · inherited access</small>` : `<small class="item-sub">${esc((item.tags || []).join(" · "))}</small>`}</span></div></td>
        <td><span class="badge ${item.owner === state.user ? "green" : "blue"}">${item.owner === state.user ? "Owner" : esc(displayRole(itemRole(item)))}</span></td>
        <td class="muted">${esc(item.modified || "—")}</td><td class="muted">${item.type === "folder" ? "—" : formatSize(item.size || 0)}</td>
        <td><button class="row-menu" title="Select item" data-action="select-item" data-id="${esc(item.id)}">···</button></td>
      </tr>`).join("")}</tbody></table>`;
  };
  const breadcrumb = () => {
    const parents = [];
    let node = getItem(state.folderId);
    while (node) { parents.unshift(node); node = getItem(node.folderId); }
    return `<div class="breadcrumbs"><button data-action="go-root">My Workspace</button>${parents.map((folder) => `<span>›</span><button data-action="open-folder" data-id="${esc(folder.id)}">${esc(folder.name)}</button>`).join("")}</div>`;
  };
  const permissionDescription = (item) => item.owner === state.user ? "You own this item" : `${people[item.owner].name} shared this with you`;

  function renderWorkspace() {
    const folder = getItem(state.folderId);
    const viewTitles = { "shared-me": "Shared with me", "shared-by": "Shared by me", recent: "Recent", favourites: "Favourites", search: "Search results" };
    const title = folder?.name || viewTitles[state.view] || "My Workspace";
    const selected = getItem(state.selectedId);
    let rows = inCurrentView();
    const isShared = ["shared-me", "shared-by"].includes(state.view);
    const canCreateHere = !folder || can(folder, "edit");
    const actions = canCreateHere ? `${button("＋ New folder", "create-folder", "button primary")}${button("↑ Upload", "upload", "button")}` : "";
    const itemsTitle = state.view === "shared-me" ? "Shared with me" : state.view === "shared-by" ? "Shared by me" : state.view === "recent" ? "Recent documents" : state.view === "favourites" ? "Favourites" : "Items";
    const searchFilters = state.view === "search" ? `<div class="filter-row">
      <input class="search-field" id="filter-query" value="${esc(state.search.query)}" placeholder="Keywords or exact phrase…">
      <select id="filter-type"><option value="all">All file types</option>${["pdf","docx","xlsx","pptx","txt","png","jpg","csv"].map((type) => `<option ${state.search.type === type ? "selected" : ""} value="${type}">${type.toUpperCase()}</option>`).join("")}</select>
      <select id="filter-tag"><option value="all">All tags</option>${[...new Set(state.items.flatMap((item) => item.tags || []))].map((tag) => `<option ${state.search.tag === tag ? "selected" : ""}>${esc(tag)}</option>`).join("")}</select>
      <select id="filter-author"><option value="all">All authors</option>${Object.values(people).map((person) => `<option ${state.search.author === person.name ? "selected" : ""}>${esc(person.name)}</option>`).join("")}</select>
      <select id="filter-date"><option value="all">Any date</option><option value="7" ${state.search.date === "7" ? "selected" : ""}>Last 7 days</option><option value="30" ${state.search.date === "30" ? "selected" : ""}>Last 30 days</option></select>
      <select id="filter-size"><option value="all">Any size</option><option value="small" ${state.search.size === "small" ? "selected" : ""}>Under 1 MB</option><option value="medium" ${state.search.size === "medium" ? "selected" : ""}>1–10 MB</option><option value="large" ${state.search.size === "large" ? "selected" : ""}>Over 10 MB</option></select>
      <select id="filter-sort"><option value="name" ${state.search.sort === "name" ? "selected" : ""}>Name</option><option value="modified" ${state.search.sort === "modified" ? "selected" : ""}>Recently modified</option></select>
    </div>` : "";
    const descriptions = {
      workspace: "Private to you until you choose to share. Folder access is inherited unless overridden.",
      search: "Simulated results from local demo items only. Search is not server-backed.",
      "shared-me": "Items shared with the selected demo user.",
      "shared-by": "Items you have shared with the other demo user.",
      recent: "Your recently modified documents.",
      favourites: "Your starred documents and folders."
    };
    let titleBlock = head(esc(title), descriptions[state.view] || descriptions.workspace, state.view === "search" ? "" : actions);
    if (state.view === "workspace") titleBlock += breadcrumb();
    const listing = `<section class="panel"><div class="toolbar"><div class="toolbar-left"><strong>${itemsTitle}</strong>${isShared ? `<span class="muted">${rows.length} items</span>` : ""}</div><div class="toolbar-right"><select aria-label="Sort items" id="sort-items"><option value="name" ${state.search.sort === "name" ? "selected" : ""}>Name</option><option value="modified" ${state.search.sort === "modified" ? "selected" : ""}>Last modified</option></select><button class="button small" data-action="refresh">↻</button></div></div>${searchFilters}${rowsTable(rows, { emptyTitle: state.view === "search" ? "No search results" : "No items found", emptyText: "Create a folder or upload a demo file to get started." })}</section>`;
    content.innerHTML = titleBlock + `<div class="workspace-layout"><div>${state.view === "workspace" && !folder ? `<div class="stats-grid"><div class="stat-card"><span>My documents</span><strong>${state.items.filter((item) => item.owner === state.user && item.type !== "folder" && !item.deleted).length}</strong></div><div class="stat-card"><span>Folders</span><strong>${state.items.filter((item) => item.owner === state.user && item.type === "folder" && !item.deleted).length}</strong></div><div class="stat-card"><span>Shared by me</span><strong>${ownedShared().length}</strong></div><div class="stat-card"><span>Shared with me</span><strong>${visibleShared().length}</strong></div></div>` : ""}${listing}</div>${detailsPanel(selected)}</div>`;
    updateNav();
  }

  function detailsPanel(item) {
    if (!item) return `<aside class="panel details-panel"><div class="panel-head"><h2>Details</h2></div><div class="empty-state"><strong>Select an item</strong>Choose a file or folder to view its details and available actions.</div></aside>`;
    if (item.shortcut) {
      const target = getItem(item.targetId);
      return `<aside class="panel details-panel"><div class="panel-head"><div class="details-title"><span class="item-icon">↗</span><div><h2>${esc(item.name)}</h2><p>Shortcut · link only, no duplicate file contents</p></div></div><button class="icon-button" data-action="clear-selection" aria-label="Close details">×</button></div><div class="panel-body"><div class="detail-row"><span>Target</span><strong>${esc(target?.name || "Unavailable")}</strong></div><div class="detail-row"><span>Location</span><span>${esc(getItem(item.folderId)?.name || "My Workspace")}</span></div><div class="detail-actions">${target && can(target, "view") ? button("Open shortcut", "open-shortcut", "button small", `data-id="${esc(item.id)}"`) : `<span class="badge amber">Target unavailable</span>`}${button("Remove shortcut", "remove-shortcut", "button small danger", `data-id="${esc(item.id)}"`)}</div></div></aside>`;
    }
    const owner = item.owner === state.user;
    const role = itemRole(item);
    const access = effectiveAccess(item);
    const actions = [
      item.type !== "folder" ? button("Preview", "preview", "button small") : "",
      item.type !== "folder" && canModify(item) ? button("Edit", "edit", "button small") : "",
      item.type !== "folder" && can(item, "download") ? button("Download", "download", "button small") : "",
      can(item, "share") ? button("Share", "share", "button small") : "",
      can(item, "manage") ? button("Manage access", "manage-access", "button small") : "",
      item.type !== "folder" && item.owner === state.user ? button("Create shortcut", "create-shortcut", "button small") : "",
      can(item, "edit") ? button("Rename", "rename", "button small") : "",
      can(item, "edit") ? button("Move", "move", "button small") : "",
      can(item, "edit") ? button("Copy", "copy", "button small") : "",
      button(state.favourites.includes(item.id) ? "★ Favourited" : "☆ Favourite", "toggle-favourite", "button small")
    ].filter(Boolean).join("");
    return `<aside class="panel details-panel"><div class="panel-head"><div class="details-title"><span class="item-icon ${item.type === "folder" ? "folder" : ""}">${esc(iconFor(item))}</span><div><h2>${esc(item.name)}</h2><p>${item.type === "folder" ? "Folder" : esc(item.type.toUpperCase())} · ${esc(permissionDescription(item))}</p></div></div><button class="icon-button" data-action="clear-selection" aria-label="Close details">×</button></div><div class="panel-body">
      <div class="callout ${owner ? "" : "warning"}">${owner ? "Private by default. Only you can access this item until you grant access." : `Your current access is ${esc(displayRole(role))} (${access.inheritance}). ${role === "viewer" ? "Edit, upload-version, delete, sharing, and permission controls are unavailable." : "Editing access does not automatically include permission management."}`}</div>
      <div class="detail-row"><span>Owner</span><strong>${esc(people[item.owner].name)}</strong></div>
      <div class="detail-row"><span>Effective access</span><span>${item.owner === state.user ? "Owner" : `${esc(access.inheritance)} · ${esc(displayRole(role))}`}${item.type === "folder" ? " · inheritance enabled" : ""}</span></div>
      <div class="detail-row"><span>Modified</span><span>${esc(item.modified || "—")}</span></div>
      ${item.type !== "folder" ? `<div class="detail-row"><span>Size</span><span>${formatSize(item.size || 0)}</span></div><div class="detail-row"><span>Version</span><span>${esc(item.versions?.at(-1)?.version || "1.0")}</span></div><div class="detail-row"><span>Classification</span><span>${esc(item.metadata?.classification || "Internal")}</span></div><div class="detail-row"><span>Department</span><span>${esc(item.metadata?.department || "General")}</span></div><div class="detail-row"><span>Tags</span><span>${esc((item.tags || []).join(", ") || "None")}</span></div>${item.checkedOutBy ? `<div class="detail-row"><span>Checked out by</span><span>${esc(people[item.checkedOutBy].name)}</span></div>` : ""}` : `<div class="detail-row"><span>Access</span><span>Inherited · override available</span></div>`}
      ${item.discussion?.length ? `<div class="section-title"><h2>Comments</h2></div>${item.discussion.map((comment) => `<div class="detail-row"><span>${esc(comment.author)} · ${esc(comment.date)}</span><span>${esc(comment.text)}</span></div>`).join("")}` : ""}
      <div class="detail-actions">${actions}</div>
      ${item.type !== "folder" ? button("Version history", "versions", "button small") : ""}
      ${item.type !== "folder" && canModify(item) ? button("New version", "new-version", "button small") : ""}
      ${item.type !== "folder" && can(item, "edit") ? button(item.checkedOutBy === state.user ? "Check in" : "Check out", item.checkedOutBy === state.user ? "check-in" : "check-out", "button small", item.checkedOutBy && item.checkedOutBy !== state.user ? "disabled" : "") : ""}
      ${item.type !== "folder" && can(item, "edit") ? button("Edit metadata", "metadata", "button small") : ""}
      ${item.type !== "folder" && can(item, "comment") ? button("Add comment", "add-comment", "button small") : ""}
      ${can(item, "delete") ? button("Move to recycle bin", "delete", "button small danger") : ""}
    </div></aside>`;
  }

  function renderSearch() {
    state.view = "search";
    renderWorkspace();
    const field = document.getElementById("filter-query");
    if (field) field.focus();
  }

  function renderRecycle() {
    content.innerHTML = `${head("Recycle Bin", "Deleted items are retained for 30 days in this local demo.", "")}
      <section class="panel"><div class="panel-head"><div><h2>Deleted items</h2><p>Original location, deletion date, and simulated expiry.</p></div></div>
      ${state.recycle.length ? `<table class="item-table"><thead><tr><th>Name</th><th>Original location</th><th>Deleted</th><th>Expires</th><th>Actions</th></tr></thead><tbody>${state.recycle.map((entry) => `<tr><td><div class="item-name"><span class="item-icon">${esc(iconFor(entry.item))}</span><strong>${esc(entry.item.name)}</strong></div></td><td>${esc(entry.location)}</td><td>${esc(entry.deleted)}</td><td>${esc(entry.expires)}</td><td>${button("Restore", "restore", "button small", `data-id="${esc(entry.item.id)}"`)} ${button("Delete forever", "permanent-delete", "button small danger", `data-id="${esc(entry.item.id)}"`)}</td></tr>`).join("")}</tbody></table>` : `<div class="empty-state"><div>♲</div><strong>Your recycle bin is empty</strong>Deleted items will appear here for 30 days.</div>`}</section>`;
    updateNav();
  }

  function renderVersions(id) {
    const item = getItem(id || state.selectedId);
    if (!item) { toast("Select a document first.", "error"); return renderWorkspace(); }
    state.selectedId = item.id;
    const versions = [...(item.versions || [])].reverse();
    content.innerHTML = `${head("Version history", `${esc(item.name)} · prior versions are retained in this demo.`, `${canModify(item) ? button("↑ Upload new version", "new-version", "button primary") : ""}${canModify(item) ? button("✎ Edit document", "edit", "button") : ""}`)}
      <section class="panel"><div class="panel-head"><div><h2>Version history</h2><p>Committed versions are separate from any unsaved editor changes.</p></div><span class="badge blue">${versions.length} versions</span></div>
      <div class="version-list">${versions.map((version, index) => `<article class="version">            <div class="version-pin">${index === 0 ? "●" : "○"}</div><div><h3>v${esc(version.version)} ${index === 0 ? '<span class="badge green">Current version</span>' : ""} <span class="badge">${esc(version.type)}</span></h3><p>${esc(version.author)} · ${esc(version.date)} · ${esc(version.note)}</p><div class="version-actions">${button("Preview", "preview-version", "button small", `data-version="${esc(version.id)}"`)}${button("Download", "download", "button small")}${versions.length > 1 && index !== 0 ? button("Compare", "compare", "button small", `data-version="${esc(version.id)}"`) : ""}${index !== 0 && canModify(item) ? button("Restore", "restore-version", "button small", `data-version="${esc(version.id)}"`) : ""}</div></div></article>`).join("")}</div></section>
      <p class="muted">Comparison is a conceptual side-by-side/redline preview. Binary, Office, and unsupported format comparisons require a backend integration.</p>`;
    updateNav();
  }

  function renderAdmin(view) {
    const configs = {
      admin: ["Admin Dashboard", "Illustrative configuration overview. No operational settings are connected.", [["Users", state.adminUsers.length], ["Repositories", 2], ["Storage used", "14.8 GB"], ["Pending reviews", 3]]],
      users: ["Users & Groups", "Manage demo identities and groups; changes remain in this page session.", null],
      roles: ["Roles & Permissions", "Predefined roles are examples only and are not enforced by a server.", null],
      security: ["Authentication & Security", "Settings below are placeholders and do not configure real security controls.", null],
      repositories: ["Repositories & Quotas", "Illustrative repositories and quota configuration.", null],
      audit: ["Audit Log", "Activity generated during this browser session only.", null],
      health: ["System Health", "Simulated health indicators; no service checks are performed.", null]
    };
    const [title, description, stats] = configs[view] || configs.admin;
    let body = "";
    if (stats) body = `<div class="stats-grid">${stats.map(([label, value]) => `<div class="stat-card"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join("")}</div><div class="panel"><div class="panel-head"><h2>Workspace overview</h2><span class="badge amber">Simulated</span></div><div class="panel-body"><div class="callout warning">Not connected to an identity provider, document store, audit service, malware scanner, or system monitor. Do not rely on these values for operations.</div></div></div>`;
    if (view === "users") body = `<section class="panel"><div class="panel-head"><div><h2>Users</h2><p>Demo identities are isolated from real application accounts.</p></div>${button("＋ Add demo user", "add-user", "button primary")}</div><table class="item-table"><thead><tr><th>User</th><th>Role</th><th>Status</th><th>Actions</th></tr></thead><tbody>${state.adminUsers.map((user, index) => `<tr><td><strong>${esc(user.name)}</strong><small class="item-sub">${esc(user.email)}</small></td><td>${esc(user.role)}</td><td><span class="badge green">${esc(user.status)}</span></td><td>${button("Change role", "change-user-role", "button small", `data-user-index="${index}"`)}</td></tr>`).join("")}</tbody></table></section><section class="panel"><div class="panel-head"><h2>Groups</h2>${button("＋ Create group", "create-group", "button small")}</div><div class="panel-body">${state.adminGroups.map((group) => `<span class="tag">${esc(group)} · demo group</span>`).join(" ")}</div></section>`;
    if (view === "roles") body = `<section class="panel"><div class="panel-head"><div><h2>Role catalog</h2><p>Role names and permission sets are examples only.</p></div>${button("＋ Create custom role", "custom-role", "button primary")}</div><table class="item-table"><thead><tr><th>Role</th><th>Example permissions</th><th>Scope</th></tr></thead><tbody>${state.adminRoles.map((name) => `<tr><td><strong>${esc(name)}</strong></td><td>${state.customRolePermissions[name] ? esc(state.customRolePermissions[name].join(", ")) : name === "Reader" ? "View, download" : name === "Auditor" ? "View, audit history" : "View, edit, manage as assigned"}</td><td>Repository / folder / document</td></tr>`).join("")}</tbody></table></section>`;
    if (view === "security") body = `<form id="security-form" class="panel"><div class="panel-head"><div><h2>Authentication and security</h2><p>All controls are illustrative and have no operational effect.</p></div><span class="badge amber">${state.securitySettings.saved ? "Demo settings saved" : "Not active"}</span></div><div class="panel-body"><div class="form-grid"><div class="field"><label>Single sign-on provider</label><select name="provider"><option value="" ${state.securitySettings.provider === "" ? "selected" : ""}>Not configured</option><option value="SAML 2.0" ${state.securitySettings.provider === "SAML 2.0" ? "selected" : ""}>SAML 2.0 (demo)</option><option value="OIDC / Entra ID" ${state.securitySettings.provider === "OIDC / Entra ID" ? "selected" : ""}>OIDC / Entra ID (demo)</option><option value="Google" ${state.securitySettings.provider === "Google" ? "selected" : ""}>Google (demo)</option></select></div><div class="field"><label>Session timeout</label><select name="timeout"><option value="30" ${state.securitySettings.timeout === "30" ? "selected" : ""}>30 minutes</option><option value="15" ${state.securitySettings.timeout === "15" ? "selected" : ""}>15 minutes</option><option value="60" ${state.securitySettings.timeout === "60" ? "selected" : ""}>60 minutes</option></select></div><label class="check-line"><input type="checkbox" name="mfa" ${state.securitySettings.mfa ? "checked" : ""}> Require MFA (demo only)</label><label class="check-line"><input type="checkbox" name="ip" ${state.securitySettings.ip ? "checked" : ""}> Restrict by IP (demo only)</label></div><div class="modal-actions"><button class="button primary" type="submit">Save demo settings</button></div></div></form><div class="callout warning">TLS, AES-256 encryption, customer-managed keys, LDAP, and device policies are not implemented here.</div>`;
    if (view === "repositories") body = `<section class="panel"><div class="panel-head"><h2>Repositories &amp; quotas</h2>${button("＋ Add repository", "add-repository", "button primary")}</div><table class="item-table"><thead><tr><th>Repository</th><th>Owner</th><th>Usage</th><th>Quota</th><th>Status</th></tr></thead><tbody>${state.repositories.map((repo) => `<tr><td>${esc(repo.name)}</td><td>${esc(repo.owner)}</td><td>${esc(repo.usage)}</td><td>${esc(repo.quota)} GB</td><td><span class="badge green">Demo</span></td></tr>`).join("")}</tbody></table></section><form id="upload-policy-form" class="panel"><div class="panel-head"><div><h2>Upload policy</h2><p>Browser-only allow/block lists and maximum file size.</p></div><span class="badge amber">Demo only</span></div><div class="panel-body"><div class="form-grid"><div class="field"><label>Allowed extensions (comma separated)</label><textarea name="allowed" required>${esc(state.uploadPolicy.allowed.join(", "))}</textarea></div><div class="field"><label>Blocked extensions (comma separated)</label><textarea name="blocked">${esc(state.uploadPolicy.blocked.join(", "))}</textarea></div><div class="field"><label>Maximum size per file (MB)</label><input type="number" name="maxMb" min="1" max="1024" value="${state.uploadPolicy.maxMb}" required></div></div><div class="modal-actions"><button class="button primary" type="submit">Save demo upload policy</button></div></div></form><form id="version-retention-form" class="panel"><div class="panel-head"><div><h2>Version retention</h2><p>Illustrative retention rule; existing demo history is not purged.</p></div><span class="badge amber">Demo only</span></div><div class="panel-body"><div class="form-grid"><div class="field"><label>Retention rule</label><select name="mode"><option value="count" ${state.versionRetention.mode === "count" ? "selected" : ""}>Keep the last N versions</option><option value="years" ${state.versionRetention.mode === "years" ? "selected" : ""}>Keep versions for X years</option></select></div><div class="field"><label>${state.versionRetention.mode === "count" ? "Number of versions" : "Number of years"}</label><input type="number" name="value" min="1" max="10000" value="${state.versionRetention.value}" required></div></div><div class="modal-actions"><button class="button primary" type="submit">Save demo retention rule</button></div></div></form>`;
    if (view === "audit") body = `<section class="panel"><div class="panel-head"><div><h2>Audit events</h2><p>Current session only · not a compliance audit trail.</p></div></div>${state.audit.length ? `<ul class="timeline">${state.audit.map((event) => `<li><span class="muted">${esc(event.date)}</span><span>${esc(event.user)} ${esc(event.message)}</span><span class="badge amber">Demo</span></li>`).join("")}</ul>` : `<div class="empty-state"><strong>No session activity yet</strong>Try creating a folder, sharing an item, or updating a version.</div>`}</section>`;
    if (view === "health") body = `<div class="cards-grid">${[["Document service", "Not connected"], ["Search index", "Simulated"], ["Malware scanning", "Not implemented"], ["Email integration", "Future integration"]].map(([name, value]) => `<article class="mini-card"><h3>${esc(name)}</h3><p>${esc(value)}</p><span class="badge amber">Demo only</span></article>`).join("")}</div>`;
    content.innerHTML = head(title, description) + body;
    updateNav();
  }

  function renderEmail() {
    content.innerHTML = `${head("Email Archive", "A future integration surface for the existing Gmail Archive. No Gmail API calls are made.", "")}
      <section class="panel"><div class="filter-row"><input class="search-field" id="email-query" placeholder="Search sender, subject, or email text…"><select id="email-filter"><option value="all">All email</option><option value="attachments">With attachments</option></select><select id="email-sort"><option value="date">Newest first</option><option value="sender">Sender</option></select></div><div id="email-list">${renderEmailRows(state.emails)}</div></section>
      <div class="callout" style="margin-top:14px">Illustrative local email records only. Connecting to archived messages and downloading real attachments require a future integration with the existing application.</div>`;
    updateNav();
  }
  function renderEmailRows(emails) {
    return emails.length ? emails.map((mail) => `<div class="email-row" data-action="email-detail" data-id="${esc(mail.id)}"><span class="avatar avatar-small">${esc(mail.sender.slice(0, 1))}</span><span><strong>${esc(mail.subject)}</strong><small>${esc(mail.sender)} · ${esc(mail.snippet)}</small></span><span class="muted">${esc(mail.date)} ${mail.attachments.length ? " · 📎" + mail.attachments.length : ""}</span></div>`).join("") : `<div class="empty-state"><strong>No matching emails</strong>Try another search or filter.</div>`;
  }

  function updateNav() {
    document.querySelectorAll(".nav-item").forEach((node) => node.classList.toggle("active", node.dataset.view === state.view));
    document.getElementById("shared-count").textContent = visibleShared().length || "";
    document.getElementById("sidebar-user").textContent = currentPerson().name;
    document.querySelector(".profile").textContent = currentPerson().initials;
  }

  function render() {
    if (state.view === "recycle") return renderRecycle();
    if (["admin", "users", "roles", "security", "repositories", "audit", "health"].includes(state.view)) return renderAdmin(state.view);
    if (state.view === "email") return renderEmail();
    renderWorkspace();
  }

  function showModal(title, description, fields, onSave, saveLabel = "Save") {
    modalContent.innerHTML = `<div class="modal-content"><div class="modal-head"><div><h2>${title}</h2><p>${description}</p></div><button class="icon-button" data-action="close-modal" aria-label="Close">×</button></div><form id="demo-form">${fields}<div class="modal-actions"><button type="button" class="button" data-action="close-modal">Cancel</button><button type="submit" class="button primary">${saveLabel}</button></div></form></div>`;
    modal.showModal();
    modalContent.querySelector("input,select,textarea")?.focus();
    modalContent.querySelector("#demo-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const result = onSave(new FormData(form), form);
      const finish = (saveResult) => {
        if (saveResult !== false) modal.close();
        render();
      };
      if (result && typeof result.then === "function") {
        const submit = form.querySelector('[type="submit"]');
        submit.disabled = true;
        result.then(finish).catch((error) => {
          submit.disabled = false;
          toast(`Could not complete this demo action: ${error.message}`, "error");
        });
      } else finish(result);
    });
  }
  const nameField = (value = "", label = "Name") => `<div class="field"><label>${label}</label><input name="name" required maxlength="120" value="${esc(value)}" title="Avoid / \\\\ : * ? &quot; &lt; &gt; |"></div>`;
  const folderSelect = (selected = "") => `<div class="field"><label>Destination folder</label><select name="folderId"><option value="">My Workspace</option>${state.items.filter((item) => item.type === "folder" && !item.deleted && (item.owner === state.user || can(item, "edit"))).map((folder) => `<option value="${esc(folder.id)}" ${selected === folder.id ? "selected" : ""}>${esc(folder.name)}</option>`).join("")}</select></div>`;

  function createFolder() {
    showModal("Create a folder", "Folders are private by default; permissions can be shared separately.", nameField() + folderSelect(state.folderId || ""), (form) => {
      const name = String(form.get("name")).trim();
      const folderId = form.get("folderId") || null;
      if (!validName(name, folderId)) return false;
      const item = { id: crypto.randomUUID(), name, type: "folder", owner: state.user, folderId, modified: today(), tags: [], permissions: {} };
      state.items.push(item); logAction(`created private folder “${name}”`); state.selectedId = item.id; toast("Private folder created.");
    }, "Create folder");
  }
  function createShortcut(item) {
    if (item.owner !== state.user || item.type === "folder") return toast("Only your own documents can be shortcut in this demo.", "error");
    const name = `Shortcut to ${item.name}`;
    showModal("Create shortcut", "Create a link in another folder. This does not copy document contents.", folderSelect(state.folderId || ""), (data) => {
      const folderId = data.get("folderId") || null;
      if (!validName(name, folderId)) return false;
      const shortcut = { id: crypto.randomUUID(), name, type: "shortcut", shortcut: true, targetId: item.id, owner: state.user, folderId, modified: today(), permissions: {}, tags: [] };
      state.items.push(shortcut); state.selectedId = shortcut.id; logAction(`created shortcut to “${item.name}”`); toast("Shortcut created; it links to the original document.");
    }, "Create shortcut");
  }
  function validName(name, folderId, exceptId = "") {
    if (!name.trim() || /[\\/:*?"<>|]/.test(name)) { toast("Name is required and cannot contain / \\ : * ? \" < > |.", "error"); return false; }
    if (state.items.some((item) => item.id !== exceptId && !item.deleted && item.folderId === folderId && item.name.toLowerCase() === name.toLowerCase())) { toast("A file or folder with that name already exists here.", "error"); return false; }
    return true;
  }
  const today = () => new Date().toISOString().slice(0, 10);
  function newDocument() {
    showModal("Create a document", "Create a simulated document record. No document contents are stored.", `${nameField("Untitled document")}<div class="field"><label>Document type</label><select name="type"><option value="docx">Word document (.docx)</option><option value="xlsx">Spreadsheet (.xlsx)</option><option value="pptx">Presentation (.pptx)</option><option value="txt">Text file (.txt)</option></select></div>${folderSelect(state.folderId || "")}<div class="field"><label>Classification</label><select name="classification"><option>Internal</option><option>Confidential</option><option>Public</option></select></div>`, (form) => {
      const name = addExtension(String(form.get("name")).trim(), form.get("type"));
      const folderId = form.get("folderId") || null;
      if (!validName(name, folderId)) return false;
      const doc = { id: crypto.randomUUID(), name, type: form.get("type"), owner: state.user, folderId, modified: today(), size: 0, tags: [], author: currentPerson().name, metadata: { classification: form.get("classification"), department: "General" }, comments: "New demo document.", permissions: {}, versions: [makeVersion("1.0", currentPerson().name, "Initial version", today(), "major")] };
      state.items.push(doc); state.selectedId = doc.id; logAction(`created private document “${name}”`); toast("Private document created with version 1.0.");
    }, "Create document");
  }
  function addExtension(name, type) { return name.toLowerCase().endsWith(`.${type}`) ? name : `${name}.${type}`; }
  function uploadFiles(files, existing = null) {
    showModal(existing ? "Upload a new version" : "Upload demo files", "Files are inspected only in your browser. File bytes are not uploaded or saved.", `<div class="field"><label>Select files</label><input type="file" name="files" accept="${state.uploadPolicy.allowed.map((extension) => `.${esc(extension)}`).join(",")}" ${existing ? "" : "multiple"} required><small class="muted">Office, PDF, images, CAD, audio/video, text, and archive formats · demo limit ${state.uploadPolicy.maxMb} MB per file.</small></div>${existing ? `<div class="field"><label>Version increment</label><select name="versionType"><option value="minor">Minor version (e.g. 1.1)</option><option value="major">Major version (e.g. 2.0)</option></select></div><div class="field"><label>Change comment</label><input name="comment" required maxlength="300" placeholder="Describe what changed"></div>` : `${folderSelect(state.folderId || "")}<div class="field"><label>Tags (comma separated)</label><input name="tags" placeholder="Planning, Q4"><small class="muted">Demo required field: add at least one classification below.</small></div><div class="field"><label>Classification</label><select name="classification" required><option value="">Choose classification</option><option>Internal</option><option>Confidential</option><option>Public</option></select></div>`}<div class="callout warning">Frontend simulation only: no malware scan, server upload, or persistent file storage.</div>`, (data, form) => {
      const input = form.querySelector('[name="files"]');
      if (!input.files.length) { toast("Choose files to continue.", "error"); return false; }
      const fileList = Array.from(input.files);
      for (const file of fileList) {
        const extension = file.name.split(".").pop().toLowerCase();
        if (!state.uploadPolicy.allowed.includes(extension) || state.uploadPolicy.blocked.includes(extension)) {
          toast(`The .${extension} extension is not allowed by the demo upload policy.`, "error");
          return false;
        }
        if (file.size > state.uploadPolicy.maxMb * 1024 * 1024) {
          toast(`${file.name} exceeds the demo limit of ${state.uploadPolicy.maxMb} MB.`, "error");
          return false;
        }
      }
      if (existing) {
        const newName = fileList[0].name;
        if (newName.split(".").pop().toLowerCase() !== existing.type) { toast(`Choose a .${existing.type} file for this document.`, "error"); return false; }
      } else {
        const folderId = data.get("folderId") || null;
        if (!data.get("classification")) { toast("Classification is required.", "error"); return false; }
        const batchNames = new Set();
        for (const file of fileList) {
          const uniqueName = file.name.toLowerCase();
          if (batchNames.has(uniqueName) || !validName(file.name, folderId)) return false;
          batchNames.add(uniqueName);
        }
      }
      const progress = document.createElement("div");
      progress.className = "upload-progress";
      progress.innerHTML = `<div class="progress-label">Simulating a local-only file operation… <span>0%</span></div><div class="progress-track"><div class="progress-fill"></div></div>`;
      form.append(progress);
      return new Promise((resolve) => {
        let amount = 0;
        const timer = window.setInterval(() => {
          amount = Math.min(100, amount + 20);
          progress.querySelector(".progress-label span").textContent = `${amount}%`;
          progress.querySelector(".progress-fill").style.width = `${amount}%`;
          if (amount < 100) return;
          window.clearInterval(timer);
          if (existing) {
            addNewVersion(existing, data.get("comment"), data.get("versionType"), fileList[0].size);
          } else {
            const folderId = data.get("folderId") || null;
            const tags = String(data.get("tags")).split(",").map((tag) => tag.trim()).filter(Boolean);
            for (const file of fileList) {
              const type = file.name.split(".").pop().toLowerCase();
              const doc = { id: crypto.randomUUID(), name: file.name, type, owner: state.user, folderId, modified: today(), size: file.size, tags, author: currentPerson().name, metadata: { classification: data.get("classification"), department: "General" }, comments: "Uploaded in the local demo.", permissions: {}, versions: [makeVersion("1.0", currentPerson().name, "Initial upload", today(), "major")] };
              state.items.push(doc); state.selectedId = doc.id; logAction(`uploaded private demo document “${file.name}”`);
            }
            toast(`${fileList.length} file${fileList.length === 1 ? "" : "s"} added to local demo state.`);
          }
          resolve();
        }, 90);
      });
    }, existing ? "Create version" : "Simulate upload");
  }

  function addNewVersion(item, note, type = "minor", size = item.size) {
    const last = item.versions.at(-1);
    const parts = last.version.split(".").map(Number);
    const next = type === "major" ? `${parts[0] + 1}.0` : `${parts[0]}.${(parts[1] || 0) + 1}`;
    item.versions.push(makeVersion(next, currentPerson().name, note, today(), type));
    item.modified = today(); item.size = size; item.comments = note;
    logAction(`created version ${next} of “${item.name}”`); toast(`Version ${next} created; prior history was retained.`);
    state.selectedId = item.id;
  }
  function editDocument(item) {
    if (!canModify(item)) return toast("You do not have available edit access (the document may be checked out by another user).", "error");
    showModal("Edit document", "Edit the demo summary and commit a new version. Autosave is not a version checkpoint.", `<div class="callout">Unsaved changes exist only in this dialog. Save creates a permanent demo version.</div><div class="field"><label>Document summary</label><textarea name="summary">${esc(item.comments || "")}</textarea></div><div class="field"><label>Change comment <span class="muted">(mandatory)</span></label><input name="comment" required maxlength="300"></div>`, (data) => {
      if (!String(data.get("comment") || "").trim()) { toast("A change comment is required.", "error"); return false; }
      const summary = data.get("summary");
      addNewVersion(item, data.get("comment"), "minor", item.size);
      item.comments = summary;
    }, "Save committed edit");
    const form = modalContent.querySelector("#demo-form");
    form.dataset.unsaved = "true";
    form.dataset.initialSummary = item.comments || "";
    form.addEventListener("input", () => {
      form.dataset.dirty = String(form.elements.summary.value !== form.dataset.initialSummary || Boolean(form.elements.comment.value));
    });
  }
  function renameItem(item) {
    if (!can(item, "edit")) return toast("You do not have permission to rename this item.", "error");
    showModal(`Rename ${item.type === "folder" ? "folder" : "document"}`, "Names must be unique in their destination and use valid characters.", nameField(item.name), (form) => {
      const name = String(form.get("name")).trim();
      if (!validName(name, item.folderId, item.id)) return false;
      item.name = name; item.modified = today(); state.selectedId = item.id; logAction(`renamed item to “${name}”`); toast("Item renamed.");
    }, "Rename");
  }
  function moveItem(item, copy = false) {
    if (!can(item, "edit")) return toast("You do not have permission to move this item.", "error");
    const destinations = state.items.filter((folder) => folder.type === "folder" && folder.owner === state.user && folder.id !== item.id && !folder.deleted);
    const options = `<div class="field"><label>Destination folder</label><select name="folderId"><option value="">My Workspace</option>${destinations.map((folder) => `<option value="${esc(folder.id)}">${esc(folder.name)}</option>`).join("")}</select></div>`;
    showModal(copy ? "Copy item" : "Move item", copy ? "A separate private copy will be created." : "Choose the new parent folder.", options, (form) => {
      const folderId = form.get("folderId") || null;
      if (folderId === item.id || isDescendant(folderId, item.id)) { toast("A folder cannot be moved into itself or one of its descendants.", "error"); return false; }
      if (copy) {
        const idMap = new Map([[item.id, crypto.randomUUID()]]);
        const descendants = [];
        const collect = (parent) => {
          state.items.filter((child) => child.folderId === parent && !child.deleted).forEach((child) => {
            descendants.push(child);
            if (child.type === "folder") collect(child.id);
          });
        };
        if (item.type === "folder") collect(item.id);
        const duplicate = cloneItem(item, idMap.get(item.id), nextCopyName(item.name, folderId), folderId);
        state.items.push(duplicate);
        descendants.forEach((child) => {
          idMap.set(child.id, crypto.randomUUID());
          state.items.push(cloneItem(child, idMap.get(child.id), child.name, idMap.get(child.folderId)));
        });
        state.selectedId = duplicate.id; logAction(`copied “${item.name}”`);
      } else { item.folderId = folderId; item.modified = today(); logAction(`moved “${item.name}”`); }
      toast(copy ? "Private copy created." : "Item moved.");
    }, copy ? "Copy" : "Move");
  }
  function cloneItem(item, id, name, folderId) {
    return { ...item, id, name, folderId, owner: state.user, modified: today(), permissions: {}, versions: item.versions?.map((version) => ({ ...version, id: crypto.randomUUID() })) };
  }
  function isDescendant(candidate, folderId) {
    let node = getItem(candidate);
    while (node) { if (node.id === folderId) return true; node = getItem(node.folderId); }
    return false;
  }
  function nextCopyName(name, folderId) {
    let copyName = `${name} (copy)`, index = 2;
    while (state.items.some((item) => !item.deleted && item.folderId === folderId && item.name.toLowerCase() === copyName.toLowerCase())) copyName = `${name} (copy ${index++})`;
    return copyName;
  }
  function deleteItem(item) {
    if (!can(item, "delete")) return toast("You do not have permission to delete this item.", "error");
    showModal("Move to recycle bin?", `“${item.name}” will be available to restore for 30 days.`, `<div class="callout warning">This is a local demo action. Real recycle-bin retention is not configured.</div>`, () => {
      const folder = getItem(item.folderId);
      const affectedIds = [item.id];
      const collect = (parent) => state.items.filter((child) => child.folderId === parent && !child.deleted).forEach((child) => {
        affectedIds.push(child.id);
        if (child.type === "folder") collect(child.id);
      });
      if (item.type === "folder") collect(item.id);
      state.recycle.unshift({ item, affectedIds, location: folder?.name || "My Workspace", originalFolderId: item.folderId, deleted: today(), expires: new Date(Date.now() + 30 * 86400000).toISOString().slice(0, 10) });
      affectedIds.forEach((id) => { const affected = getItem(id); if (affected) affected.deleted = true; });
      state.selectedId = null; logAction(`moved “${item.name}” to the recycle bin`); toast("Item moved to the recycle bin.");
    }, "Move to bin");
  }
  function restoreEntry(id) {
    const entry = state.recycle.find((deleted) => deleted.item.id === id);
    if (!entry) return;
    const restore = (folderId) => {
      const conflict = state.items.find((item) => !item.deleted && item.owner === state.user && item.folderId === folderId && item.name.toLowerCase() === entry.item.name.toLowerCase());
      if (conflict) { toast("Name conflict: rename or remove the existing item before restoring.", "error"); return false; }
      entry.item.folderId = folderId;
      (entry.affectedIds || [entry.item.id]).forEach((id) => { const affected = getItem(id); if (affected) affected.deleted = false; });
      entry.item.modified = today();
      state.recycle = state.recycle.filter((deleted) => deleted !== entry); logAction(`restored “${entry.item.name}”`); toast("Item restored."); return true;
    };
    const original = state.items.find((item) => item.id === entry.originalFolderId && item.type === "folder" && item.owner === state.user && !item.deleted);
    const locations = state.items.filter((item) => item.type === "folder" && item.owner === state.user && !item.deleted);
    showModal("Restore item", "Restore to its original location or choose another folder. Conflicts are checked before restoring.", `<div class="field"><label>Restore destination</label><select name="folderId"><option value="">My Workspace</option>${locations.map((folder) => `<option value="${esc(folder.id)}" ${folder === original ? "selected" : ""}>${esc(folder.name)}</option>`).join("")}</select></div>`, (form) => restore(form.get("folderId") || null), "Restore");
  }
  function permanentDelete(id) {
    showModal("Delete permanently?", "This removes the item from this local demo session and cannot be undone.", `<div class="callout warning">Permanent deletion is simulated. No real files are deleted.</div>`, () => {
      const entry = state.recycle.find((deleted) => deleted.item.id === id);
      const removeIds = new Set(entry?.affectedIds || [id]);
      state.recycle = state.recycle.filter((deleted) => deleted.item.id !== id);
      state.items = state.items.filter((item) => !removeIds.has(item.id));
      logAction("permanently deleted an item from the demo recycle bin"); toast("Demo item permanently deleted.");
    }, "Delete forever");
  }

  function openShare(item, manage = false) {
    if (!can(item, "share")) return toast("You do not have permission to share this item.", "error");
    const shared = Object.entries(item.permissions || {}).map(([user, config]) => `<div class="detail-row"><span>${esc(people[user].name)} · ${esc(displayRole(config.role))}</span>${can(item, "manage") ? button("Revoke", "revoke-share", "button small danger", `data-id="${esc(item.id)}" data-user="${user}"`) : ""}</div>`).join("");
    const permissions = ["view", "download", "comment", "edit", "delete", "share", "manage"].map((permission) => `<label class="check-line"><input type="checkbox" name="permission" value="${permission}" ${rolePermissions.viewer.includes(permission) ? "checked" : ""} ${permission === "view" ? "disabled" : ""}> ${permission[0].toUpperCase() + permission.slice(1)}</label>`).join("");
    const otherUser = state.user === "a" ? "b" : "a";
    const fields = `<div class="field"><label>User or group</label><select name="target"><option value="${otherUser}">${esc(people[otherUser].name)} (${esc(people[otherUser].email)})</option>${state.adminGroups.map((group) => `<option value="${esc(`group:${group}`)}">${esc(group)} · demo group</option>`).join("")}</select></div>
      <div class="field"><label>Access level</label><select name="role"><option value="viewer">Viewer / Read</option><option value="commenter">Commenter</option><option value="editor">Editor / Write</option><option value="manager">Manager</option>${Object.keys(state.customRolePermissions).map((role) => `<option value="custom:${role.toLowerCase()}">${esc(role)} · custom</option>`).join("")}<option disabled>Owner · current owner only</option></select></div>
      <div class="field"><label>Advanced individual permissions</label><div class="permission-grid">${permissions}</div></div>
      <div class="callout">Direct access overrides inherited access. Owner access is controlled by ownership and cannot be granted here. This is a local UI simulation only.</div>
      ${shared ? `<div class="section-title"><h2>Current direct access</h2></div>${shared}` : `<p class="muted">No other demo user currently has direct access.</p>`}`;
    showModal(manage ? "Manage access" : "Share item", `${esc(item.name)} · effective access shown below is simulated.`, fields, (data, form) => {
      const target = data.get("target");
      const role = data.get("role");
      item.permissions = item.permissions || {};
      const grants = String(target).startsWith("group:")
        ? Object.keys(people).filter((user) => user !== state.user)
        : [target];
      grants.forEach((user) => {
        item.permissions[user] = { role, permissions: [...form.querySelectorAll('[name="permission"]:checked')].map((input) => input.value) };
      });
      const recipient = String(target).startsWith("group:") ? target.slice(6) : people[target].name;
      logAction(`shared “${item.name}” with ${recipient} as ${role}`); toast(`Shared with ${recipient} as ${role}.`);
    }, "Save access");
  }

  function previewItem(item) {
    const safeName = esc(item.name);
    showModal(`Preview · ${safeName}`, "Illustrative browser preview. Actual file rendering requires a document preview service.", `<div class="panel-body" style="min-height:160px;background:#f8fafc;border:1px solid #edf0f4;border-radius:7px"><p class="eyebrow">${esc(item.type.toUpperCase())} · DEMO PREVIEW</p><h2>${safeName}</h2><p>${esc(item.comments || "Preview content is not available in this demo.")}</p><p class="muted">Metadata: ${esc(item.metadata?.classification || "Internal")} · ${esc((item.tags || []).join(", ") || "No tags")}</p></div><p class="muted">Rendering for Office, PDF, image, audio, and video files is integration-pending.</p>`, () => {}, "Close preview");
    modalContent.querySelector("#demo-form .modal-actions")?.remove();
  }

  function compareVersions(item, versionId) {
    const old = item.versions.find((version) => version.id === versionId);
    const current = item.versions.at(-1);
    if (!old || !current) return;
    const supported = ["txt", "docx"].includes(item.type);
    showModal("Compare versions", `${item.name} · v${old.version} → v${current.version}`, `<div class="callout ${supported ? "" : "warning"}">${supported ? "Illustrative text comparison. This is not a binary or Office redline." : `Comparison for .${esc(item.type)} is not supported in this demo. Side-by-side metadata is shown instead.`}</div><div class="split" style="margin-top:12px"><div class="panel-body"><strong>v${esc(old.version)}</strong><p>${esc(old.author)} · ${esc(old.date)}</p><div class="compare-box"><span class="diff-del">− ${esc(old.note)}</span></div></div><div class="panel-body"><strong>v${esc(current.version)}</strong><p>${esc(current.author)} · ${esc(current.date)}</p><div class="compare-box"><span class="diff-add">+ ${esc(current.note)}</span></div></div></div>`, () => {}, "Done");
    modalContent.querySelector("#demo-form .modal-actions")?.remove();
  }
  function previewVersion(item, versionId) {
    const version = item?.versions?.find((entry) => entry.id === versionId);
    if (!version) return toast("Version is no longer available.", "error");
    showModal(`Preview v${esc(version.version)}`, `${esc(item.name)} · ${esc(version.author)} · ${esc(version.date)}`, `<div class="panel-body" style="min-height:150px;background:#f8fafc;border:1px solid #edf0f4;border-radius:7px"><p class="eyebrow">ILLUSTRATIVE VERSION PREVIEW</p><h2>${esc(item.name)}</h2><p>${esc(version.note)}</p><p class="muted">Version ${esc(version.version)} · ${esc(version.type)} checkpoint</p></div><p class="muted">Actual historical file contents are not stored in this demo.</p>`, () => {}, "Close preview");
    modalContent.querySelector("#demo-form .modal-actions")?.remove();
  }
  function restoreVersion(item, versionId) {
    const old = item.versions.find((version) => version.id === versionId);
    if (!old || !canModify(item)) return toast("You cannot restore this version.", "error");
    showModal("Restore this version?", `Restoring v${old.version} creates a new version; no history will be overwritten.`, `<div class="callout">${esc(old.note)} · ${esc(old.date)}</div>`, () => {
      addNewVersion(item, `Restored from v${old.version}`, "major", item.size);
    }, "Create restored version");
  }

  document.addEventListener("click", (event) => {
    const target = event.target.closest("[data-action], [data-view]");
    if (!target) return;
    const action = target.dataset.action;
    if (target.dataset.view) {
      state.view = target.dataset.view; state.folderId = null; state.selectedId = null; render(); document.getElementById("sidebar").classList.remove("open"); return;
    }
    const item = getItem(target.dataset.id || state.selectedId);
    switch (action) {
      case "mobile-menu": document.getElementById("sidebar").classList.toggle("open"); break;
      case "dismiss-notice": target.closest(".notice").remove(); break;
      case "profile": toast(`Viewing as ${currentPerson().name}. Switch identity in the sidebar.`); break;
      case "new-menu": showModal("Create new", "Choose a simulated item to add to your private workspace.", `${button("Create folder", "modal-create-folder", "button")}${button("Create document", "modal-create-document", "button")}${button("Upload files", "modal-upload", "button")}`, () => {}); modalContent.querySelector("#demo-form .modal-actions")?.remove(); break;
      case "modal-create-folder": modal.close(); createFolder(); break;
      case "modal-create-document": modal.close(); newDocument(); break;
      case "modal-upload": modal.close(); uploadFiles([]); break;
      case "create-folder": createFolder(); break;
      case "create-document": newDocument(); break;
      case "upload": uploadFiles([]); break;
      case "open-folder": state.folderId = target.dataset.id; state.view = "workspace"; state.selectedId = null; render(); break;
      case "go-root": state.folderId = null; state.view = "workspace"; state.selectedId = null; render(); break;
      case "open-item":
        if (item?.shortcut) {
          const linkedItem = getItem(item.targetId);
          if (linkedItem && can(linkedItem, "view")) { state.folderId = linkedItem.folderId; state.view = "workspace"; state.selectedId = linkedItem.id; }
          else toast("The shortcut target is no longer available to this demo user.", "error");
        } else if (item?.type === "folder") { state.folderId = item.id; state.view = "workspace"; state.selectedId = null; }
        else state.selectedId = item?.id || null;
        render(); break;
      case "select-item": state.selectedId = item?.id || null; render(); break;
      case "clear-selection": state.selectedId = null; render(); break;
      case "toggle-favourite":
        if (state.favourites.includes(item.id)) state.favourites = state.favourites.filter((id) => id !== item.id);
        else state.favourites.push(item.id);
        toast(state.favourites.includes(item.id) ? "Added to favourites." : "Removed from favourites."); render(); break;
      case "refresh": render(); toast("List refreshed from local demo state."); break;
      case "preview": if (item) previewItem(item); break;
      case "download": toast("Download is integration-pending; no file is sent or generated.", "info"); break;
      case "edit": if (item) editDocument(item); break;
      case "new-version": if (item) uploadFiles([], item); break;
      case "check-out":
        if (!can(item, "edit")) return toast("You do not have edit access.", "error");
        if (item.checkedOutBy && item.checkedOutBy !== state.user) return toast("This document is checked out by another demo user.", "error");
        item.checkedOutBy = state.user; logAction(`checked out “${item.name}”`); toast("Document checked out for this demo user."); render(); break;
      case "check-in":
        if (item?.checkedOutBy !== state.user) return toast("Only the user who checked it out can check it in.", "error");
        delete item.checkedOutBy; logAction(`checked in “${item.name}”`); toast("Document checked in."); render(); break;
      case "metadata":
        if (item) editMetadata(item);
        break;
      case "add-comment":
        if (item) addComment(item);
        break;
      case "versions": if (item) renderVersions(item.id); break;
      case "preview-version": if (item) previewVersion(item, target.dataset.version); break;
      case "compare": if (item) compareVersions(item, target.dataset.version); break;
      case "restore-version": if (item) restoreVersion(item, target.dataset.version); break;
      case "share": if (item) openShare(item); break;
      case "manage-access": if (item) openShare(item, true); break;
      case "rename": if (item) renameItem(item); break;
      case "move": if (item) moveItem(item); break;
      case "copy": if (item) moveItem(item, true); break;
      case "create-shortcut": if (item) createShortcut(item); break;
      case "open-shortcut": {
        const shortcut = getItem(target.dataset.id);
        const targetItem = getItem(shortcut?.targetId);
        if (targetItem && can(targetItem, "view")) { state.folderId = targetItem.folderId; state.selectedId = targetItem.id; state.view = "workspace"; render(); }
        else toast("The shortcut target is no longer available to this demo user.", "error");
        break;
      }
      case "remove-shortcut":
        state.items = state.items.filter((entry) => entry.id !== target.dataset.id);
        state.favourites = state.favourites.filter((id) => id !== target.dataset.id);
        state.selectedId = null; logAction("removed a shortcut"); toast("Shortcut removed."); render(); break;
      case "delete": if (item) deleteItem(item); break;
      case "restore": restoreEntry(target.dataset.id); break;
      case "permanent-delete": permanentDelete(target.dataset.id); break;
      case "close-modal": {
        const form = modalContent.querySelector("#demo-form");
        if (form?.dataset.dirty === "true" && !window.confirm("Discard unsaved document changes?")) break;
        modal.close();
        break;
      }
      case "revoke-share": {
        const resource = getItem(target.dataset.id);
        modal.close();
        showModal("Revoke access?", `Remove ${people[target.dataset.user].name}'s direct access to “${resource?.name || "item"}”?`, `<div class="callout warning">This only changes the demo permission state.</div>`, () => {
          delete resource.permissions[target.dataset.user]; logAction(`revoked ${people[target.dataset.user].name}'s access to “${resource.name}”`); toast("Demo access revoked.");
        }, "Revoke access");
        break;
      }
      case "email-detail": {
        const mail = state.emails.find((entry) => entry.id === target.dataset.id);
        if (mail) showModal(esc(mail.subject), `From ${esc(mail.sender)} · ${esc(mail.date)}`, `<p>${esc(mail.snippet)}</p><div class="section-title"><h2>Attachments</h2></div>${mail.attachments.length ? mail.attachments.map((attachment) => `<div class="detail-row"><span>📎 ${esc(attachment)}</span>${button("Download", "email-download", "button small", `data-name="${esc(attachment)}"`)}</div>`).join("") : `<p class="muted">No attachments.</p>`}<div class="callout">Email content and attachments are illustrative. Gmail integration is not connected.</div>`, () => {}, "Close");
        modalContent.querySelector("#demo-form .modal-actions")?.remove();
        break;
      }
      case "email-download": toast(`Downloading “${target.dataset.name}” requires future email integration.`, "info"); break;
      case "add-user": addDemoUser(); break;
      case "change-user-role": changeUserRole(Number(target.dataset.userIndex)); break;
      case "create-group": showModal("Create demo group", "Groups are illustrative and session-only.", nameField("", "Group name"), (form) => {
        const name = String(form.get("name")).trim();
        if (state.adminGroups.some((group) => group.toLowerCase() === name.toLowerCase())) { toast("A demo group with that name already exists.", "error"); return false; }
        state.adminGroups.push(name); logAction(`created demo group “${name}”`); toast(`Demo group “${name}” created.`);
      }, "Create group"); break;
      case "custom-role": showModal("Create custom role", "Choose a role name and example permissions. No server-side role is created.", nameField("", "Role name") + `<div class="field"><label>Permissions</label><div class="permission-grid">${["view","download","comment","edit","delete","share","manage"].map((permission) => `<label class="check-line"><input type="checkbox" name="customPermission" value="${permission}" ${permission === "view" ? "checked disabled" : ""}> ${permission[0].toUpperCase() + permission.slice(1)}</label>`).join("")}</div></div>`, (form) => {
        const name = String(form.get("name")).trim();
        if (state.adminRoles.some((role) => role.toLowerCase() === name.toLowerCase())) { toast("A role with that name already exists.", "error"); return false; }
        const permissions = [...new Set(["view", ...form.getAll("customPermission")])];
        state.adminRoles.push(name); state.customRolePermissions[name] = permissions;
        rolePermissions[`custom:${name.toLowerCase()}`] = permissions;
        logAction(`created demo role “${name}”`); toast(`Demo role “${name}” created.`);
      }, "Create role"); break;
      case "add-repository": showModal("Add demo repository", "Repository configuration is not connected to storage.", nameField("", "Repository name") + `<div class="field"><label>Quota (GB)</label><input type="number" min="1" name="quota" required></div>`, (form) => {
        const name = String(form.get("name")).trim();
        if (state.repositories.some((repo) => repo.name.toLowerCase() === name.toLowerCase())) { toast("A repository with that name already exists.", "error"); return false; }
        state.repositories.push({ name, owner: currentPerson().name, usage: "0 GB", quota: Number(form.get("quota")) });
        logAction(`added demo repository “${name}”`); toast(`Demo repository “${name}” added.`);
      }, "Add repository"); break;
      case "download-email": toast("Email downloads require the future Gmail Archive integration.", "info"); break;
    }
  });

  function addDemoUser() {
    showModal("Add demo user", "Adds a local demonstration identity only.", `<div class="form-grid"><div class="field"><label>Name</label><input name="name" required></div><div class="field"><label>Email</label><input type="email" name="email" required></div><div class="field full"><label>Role</label><select name="role"><option>Reader</option><option>Contributor</option><option>Records Manager</option></select></div></div>`, (form) => {
      if (state.adminUsers.some((user) => user.email.toLowerCase() === String(form.get("email")).toLowerCase())) { toast("A demo user with that email already exists.", "error"); return false; }
      state.adminUsers.push({ name: form.get("name").trim(), email: form.get("email").trim(), role: form.get("role"), status: "Active" }); logAction("added a demo user"); toast("Demo user added.");
    }, "Add demo user");
  }
  function changeUserRole(index) {
    const user = state.adminUsers[index];
    if (!user) return;
    showModal("Change demo role", `Update the display role for ${esc(user.name)}.`, `<div class="field"><label>Role</label><select name="role">${["Admin","Records Manager","Contributor","Reader","Auditor"].map((role) => `<option ${user.role === role ? "selected" : ""}>${role}</option>`).join("")}</select></div>`, (form) => {
      user.role = form.get("role"); logAction(`changed ${user.name}'s demo role`); toast("Demo role updated.");
    }, "Save role");
  }

  document.addEventListener("change", (event) => {
    if (event.target.id === "user-switch") {
      state.user = event.target.value; state.folderId = null; state.selectedId = null; state.view = "workspace"; render();
    } else if (event.target.id === "sort-items") { state.search.sort = event.target.value; render(); }
    else if (event.target.id === "filter-type") { state.search.type = event.target.value; render(); }
    else if (event.target.id === "filter-tag") { state.search.tag = event.target.value; render(); }
    else if (event.target.id === "filter-author") { state.search.author = event.target.value; render(); }
    else if (event.target.id === "filter-sort") { state.search.sort = event.target.value; render(); }
    else if (event.target.id === "filter-date") { state.search.date = event.target.value; render(); }
    else if (event.target.id === "filter-size") { state.search.size = event.target.value; render(); }
    else if (event.target.id === "email-filter" || event.target.id === "email-sort") filterEmails();
    else if (event.target.name === "role" && event.target.closest("#demo-form")) {
      const permissions = rolePermissions[event.target.value] || [];
      event.target.closest("#demo-form").querySelectorAll('[name="permission"]').forEach((checkbox) => {
        checkbox.checked = permissions.includes(checkbox.value);
      });
    }
  });
  document.addEventListener("input", (event) => {
    if (event.target.id === "filter-query") { state.search.query = event.target.value; renderWorkspace(); const field = document.getElementById("filter-query"); field.focus(); field.setSelectionRange(field.value.length, field.value.length); }
    if (event.target.id === "email-query") filterEmails();
  });
  function filterEmails() {
    const q = (document.getElementById("email-query")?.value || "").toLowerCase();
    const filter = document.getElementById("email-filter")?.value || "all";
    const sort = document.getElementById("email-sort")?.value || "date";
    let emails = state.emails.filter((mail) => (!q || `${mail.sender} ${mail.subject} ${mail.snippet}`.toLowerCase().includes(q)) && (filter !== "attachments" || mail.attachments.length));
    emails.sort((a, b) => sort === "sender" ? a.sender.localeCompare(b.sender) : b.date.localeCompare(a.date));
    document.getElementById("email-list").innerHTML = renderEmailRows(emails);
  }
  document.getElementById("global-search").addEventListener("submit", (event) => {
    event.preventDefault(); state.search.query = document.getElementById("global-query").value.trim(); state.search.type = "all"; state.search.tag = "all"; state.search.author = "all"; state.view = "search"; state.folderId = null; state.selectedId = null; render();
  });
  document.getElementById("global-query").addEventListener("focus", () => {});
  document.addEventListener("submit", (event) => {
    if (event.target.id === "security-form") {
      event.preventDefault();
      const data = new FormData(event.target);
      state.securitySettings = { provider: data.get("provider"), timeout: data.get("timeout"), mfa: data.has("mfa"), ip: data.has("ip"), saved: true };
      logAction("saved illustrative security settings"); toast("Demo settings saved; no security settings were changed."); renderAdmin("security");
    } else if (event.target.id === "upload-policy-form") {
      event.preventDefault();
      const data = new FormData(event.target);
      const parseExtensions = (value) => String(value).split(",").map((extension) => extension.trim().replace(/^\./, "").toLowerCase()).filter(Boolean);
      const allowed = parseExtensions(data.get("allowed"));
      const blocked = parseExtensions(data.get("blocked"));
      const maxMb = Number(data.get("maxMb"));
      if (!allowed.length || [...allowed, ...blocked].some((extension) => !/^[a-z0-9]+$/.test(extension)) || !Number.isInteger(maxMb) || maxMb < 1 || maxMb > 1024) {
        toast("Enter valid extensions and a file limit from 1 to 1024 MB.", "error"); return;
      }
      state.uploadPolicy = { allowed: [...new Set(allowed)], blocked: [...new Set(blocked)], maxMb };
      logAction("updated the illustrative upload policy"); toast("Demo upload policy saved."); renderAdmin("repositories");
    } else if (event.target.id === "version-retention-form") {
      event.preventDefault();
      const data = new FormData(event.target);
      const mode = data.get("mode");
      const value = Number(data.get("value"));
      if (!Number.isInteger(value) || value < 1 || value > (mode === "years" ? 100 : 10000)) {
        toast(mode === "years" ? "Enter a retention period from 1 to 100 years." : "Enter a version count from 1 to 10000.", "error"); return;
      }
      state.versionRetention = { mode, value };
      logAction("updated the illustrative version-retention rule"); toast("Demo version-retention rule saved."); renderAdmin("repositories");
    }
  });
  document.addEventListener("change", (event) => {
    if (event.target.name === "mode" && event.target.form?.id === "version-retention-form") {
      const label = event.target.form.querySelector('input[name="value"]').closest(".field").querySelector("label");
      label.textContent = event.target.value === "count" ? "Number of versions" : "Number of years";
    }
  });
  document.getElementById("modal").addEventListener("click", (event) => {
    if (event.target === modal) modal.close();
  });
  document.getElementById("modal").addEventListener("cancel", (event) => {
    const form = modalContent.querySelector("#demo-form");
    if (form?.dataset.dirty === "true" && !window.confirm("Discard unsaved document changes?")) event.preventDefault();
  });
  document.getElementById("user-switch").value = state.user;
  render();
})();
