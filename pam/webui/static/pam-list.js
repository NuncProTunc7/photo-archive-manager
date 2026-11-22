/* The review list (M14 Task 2; U2, U25): the progress line, the two tabs, the groups with
 * their first rows and `Show N more`, and the wide layout's left panel (All names / Needs a
 * look / Skipped, the date-and-event tree, the trust filter). One row's render is
 * `pam-row.js`'s; this file holds the page state and every request the list makes.
 *
 * THREE RULES, each one executed by `tests/js/list_test.js`:
 *   - EVERY NUMBER IS THE SERVER'S. The progress line is drawn from the `counts` a decision's
 *     own response carries (design 8.2's `fresh` counts), never from arithmetic over the rows
 *     this page happens to hold - a page holds a few rows of each group, the server holds the
 *     plan. A group's header numbers are `GET /plan/{id}/list`'s; the `Needs a look` count is
 *     the size of the server's own filtered set.
 *   - PAGING CANNOT SKIP OR REPEAT. `Show N more` asks for `offset` = the rows this group
 *     already holds, over `items?list_group=`'s total order.
 *   - THE CIRCLE CALLS THE EXISTING `decide`, with the row's id and `pam-row.js`'s action. No
 *     new write path: a decision widens to the companion unit on the server (design 7.3), and
 *     the partners it moved come back in `companions`.
 *
 * NOTHING READS `document` AT LOAD (the element map is built on first use), so node can
 * require this file. `start` in `app.js` calls `startList` once the plan is known.
 */

"use strict";

/* A group's first rows, and the page `Show N more` adds. Both within design 8.2's
 * `MAX_LIMIT` (200). */
var LIST_FIRST_ROWS = 20;
var LIST_PAGE = 50;

/* UX spec 5: the estimate appears once >= 10 decisions have been made THIS SESSION, and
 * is hidden before that. Client-side only - the server records no timing and should not.
 * Moved from the card page (Step 6b part 2). */
var PACE_SAMPLES_NEEDED = 10;

/* The copy the list assembles. W2's progress line, W5's tab, U2's group header and left
 * panel, W11's all-low line. */
var LIST_COPY = {
  approvedOf: " of ",
  approvedTail: " names approved",
  skippedTail: " skipped · ",
  stillToDecide: " still to decide",
  tabAll: "All ",
  tabAttention: "Needs a look ",
  showMore: "Show ",
  showMoreTail: " more",
  file: " file",
  files: " files",
  needALook: " need a look",
  allLow: "pam isn't sure of these",
  allDates: "All dates",
  /* UX 5 rev 9 / B1 (the A4 / M8 ruling): the end of a pass, moved unchanged from the card
   * page's `advanceToNextUndecided`. */
  passComplete: "Nothing left undecided in this view — pass complete.",
  /* W3 (UX 5a step 1) with OD-3's second sentence, and W4 (5a step 3): U8's spelled copy. */
  lookCount: " names need a look.",
  /* W3's singular (Task 2 ruling on 2f's concern 2): an inflection of the spelled copy, so
   * one name never reads "1 names need a look." (HT-M7 P1's defect class). */
  lookCountOne: " name needs a look.",
  lookWhy: " pam isn't sure about these names. Everything else looks right.",
  lookFirst: "Look at the ",
  lookFirstTail: " first",
  nothingNeedsALook: "Nothing needs a look. Rename when you're ready, or keep checking names.",
  /* M14 Task 3 (U3, W15): the group box's accessible name - "the square beside a group to
   * pick all of it". */
  pickGroup: "Pick all of ",
  /* M14 Task 3 (U3, OD-1, W3, W12): the one-tap shortcuts. N is always the server's
   * `shortcut_count` - the page never computes it. */
  approveN: "Approve ",
  approveOther: "Approve the other ",
  /* After a shortcut. The spec gives no sentence for it; this is the bar's own "Approved M."
   * (slice 3c's proposed copy, for HT-M14's wording read). */
  shortcutDone: "Approved ",
};

/* The list's views: the two tabs and the left panel's three entries are one choice. */
var VIEW_ALL = "all";
var VIEW_ATTENTION = "attention";
var VIEW_SKIPPED = "skipped";

var LIST = {
  planId: null,
  /* M14 Task 3 review R2 F4: true while a shortcut's write and its reload are in flight, so a
   * double click posts once and a later "Approved 0." cannot overwrite the first report. */
  shortcutBusy: false,
  counts: null,
  attentionTodo: null,
  /* M14 Task 3: `/list`'s top-level summary for the CURRENT view - `{undecided,
   * attention_undecided, shortcut_count}` - the callout's two numbers. `null` until it has
   * arrived (3b: the callout reads these, the same call the top shortcut writes with). */
  summary: null,
  /* `q` and `sort` are design 8.2's search and sort (Step 6b part 2, moved from the card
   * page's filters); `status` is set only by `Jump to first undecided`, to `proposed`. */
  view: { mode: VIEW_ALL, tier: "", group: "", q: "", sort: SORT_TRUST, status: "" },
  /* One entry per group - {group, section, count, rows, more, items, total} - in the
   * server's order, and the same entries by key. */
  groups: [],
  byKey: {},
  /* item id -> {item, node, key}. */
  rows: {},
  /* Bumped on every view change, so a page that arrives for an old view is dropped. */
  generation: 0,
  refreshTimer: null,
  /* The keyboard's row, by item id (Step 5): an id survives paging, an index would not. */
  cursorId: null,
  /* The page's other surfaces, which the keys hand a row to. `app.js` sets them; each is
   * optional, so a key whose surface is not wired does nothing rather than throw. */
  hooks: {},
  /* One entry per SINGLE-item decision: the seconds it took. A bulk decide is not a
   * pace sample - 400 items in one click would report a pace no human ever worked at,
   * and UX spec 5's estimate is "at your pace". Moved from the card page's `state`. */
  pace: [],
  lastDecisionAt: null,
};

/* --- the pure parts ------------------------------------------------------------------- */

/* W2: `812 of 923 names approved` · bar · `15 skipped · 96 still to decide`, from one
 * `counts` object exactly as the server sent it. */
function progressText(counts) {
  return {
    approved: counts.approved + LIST_COPY.approvedOf + counts.total + LIST_COPY.approvedTail,
    rest: counts.skipped + LIST_COPY.skippedTail + counts.remaining + LIST_COPY.stillToDecide,
    percent: counts.total > 0 ? Math.round((counts.approved / counts.total) * 100) : 0,
  };
}

/* U2's group header count: `N files`, then ` · K need a look` when any do. */
function groupCountText(group) {
  var text = group.total + (group.total === 1 ? LIST_COPY.file : LIST_COPY.files);
  if (group.attention_undecided > 0) {
    text += " · " + group.attention_undecided + LIST_COPY.needALook;
  }
  return text;
}

/* `Show N more`: N is what the click will add - the rest, up to a page. `null` when the
 * group holds every row. */
function showMoreText(loaded, total) {
  var rest = total - loaded;
  if (rest <= 0) {
    return null;
  }
  return LIST_COPY.showMore + Math.min(rest, LIST_PAGE) + LIST_COPY.showMoreTail;
}

/* W3 / W4, UX 5a's callout, as plain data: `null` (no callout), or the sentence and the
 * button's label (`null` for W4, which has none). `todo` is the UNDECIDED attention count,
 * the server's (5a's two-number rule) - `null` until it has arrived, and then no callout,
 * because the list shows only the server's numbers. */
function calloutText(counts, todo, other) {
  if (!counts || counts.total === 0 || counts.remaining === 0 || todo === null) {
    return null;
  }
  if (todo > 0) {
    /* W3's second button, `Approve the other N` (OD-1): N is `/list`'s top-level
     * `shortcut_count`, shown only when it is a number above zero. */
    return {
      text: todo + (todo === 1 ? LIST_COPY.lookCountOne : LIST_COPY.lookCount) + LIST_COPY.lookWhy,
      button: LIST_COPY.lookFirst + todo + LIST_COPY.lookFirstTail,
      other: typeof other === "number" && other > 0 ? LIST_COPY.approveOther + other : null,
    };
  }
  return { text: LIST_COPY.nothingNeedsALook, button: null, other: null };
}

/* UX spec 5 rev 8's session resume, WITHOUT A GREETING (HT-M7's P6 / amendment B6), moved
 * unchanged from the card page's `renderResumeBanner`: shown once any decision exists and
 * something is still undecided. `null` when there is no banner. */
function resumeText(counts) {
  if (!counts || counts.approved + counts.skipped === 0 || counts.remaining === 0) {
    return null;
  }
  return counts.approved + " approved, " + counts.remaining + " remaining";
}

/* UX spec 5: `<N> min at your pace`, from the MEDIAN observed seconds-per-decision times
 * the number still undecided, and HIDDEN until >= 10 decisions have been made this
 * session. Median rather than mean deliberately: one interruption - a phone call in the
 * middle of a review - moves a mean by minutes and a median not at all. Moved from the card
 * page's `renderEstimate`. */
function estimateText(pace, remaining) {
  if (pace.length < PACE_SAMPLES_NEEDED || !remaining) {
    return null;
  }
  var minutes = Math.max(1, Math.round((median(pace) * remaining) / 60));
  return "≈" + minutes + " min at your pace";
}

function median(samples) {
  var sorted = samples.slice().sort(function (a, b) {
    return a - b;
  });
  var middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 1
    ? sorted[middle]
    : (sorted[middle - 1] + sorted[middle]) / 2;
}

/* The filters a view adds to every items request, in design 8.2's own parameters. */
function viewParams(view) {
  var params = {};
  if (view.mode === VIEW_ATTENTION) {
    params.attention = "1";
  }
  if (view.mode === VIEW_SKIPPED) {
    params.status = "skipped";
  } else if (view.status) {
    params.status = view.status;
  }
  if (view.tier) {
    params.tier = view.tier;
  }
  if (view.group) {
    params.group = view.group;
  }
  /* Step 6b part 2: the card page's search and sort, over the same `items` parameters. */
  if (view.q) {
    params.q = view.q;
  }
  if (view.sort) {
    params.sort = view.sort;
  }
  return params;
}

/* One group's page: `list_group`, the view, and the offset and limit. */
function groupQuery(key, view, offset, limit) {
  var params = new URLSearchParams();
  params.set("list_group", key);
  var extra = viewParams(view);
  for (var name in extra) {
    if (Object.prototype.hasOwnProperty.call(extra, name)) {
      params.set(name, extra[name]);
    }
  }
  params.set("offset", String(offset));
  params.set("limit", String(limit));
  return params.toString();
}

/* --- the page's elements, on first use ------------------------------------------------- */

var listEl = null;

function listElements() {
  if (listEl === null) {
    listEl = {
      progressApproved: document.getElementById("progress-approved"),
      progressFill: document.getElementById("progress-fill"),
      progressRest: document.getElementById("progress-rest"),
      tabAll: document.getElementById("tab-all"),
      tabAttention: document.getElementById("tab-attention"),
      viewAll: document.getElementById("view-all"),
      viewAttention: document.getElementById("view-attention"),
      viewSkipped: document.getElementById("view-skipped"),
      trustFilter: document.getElementById("trust-filter"),
      listTree: document.getElementById("list-tree"),
      listNote: document.getElementById("list-note"),
      list: document.getElementById("list"),
      /* Step 6b part 2: the controls and states moved from the card page. */
      search: document.getElementById("search"),
      sort: document.getElementById("sort"),
      clearFilters: document.getElementById("clear-filters"),
      clearFiltersEmpty: document.getElementById("clear-filters-empty"),
      emptyState: document.getElementById("empty-state"),
      donePanel: document.getElementById("done-panel"),
      resumeBanner: document.getElementById("resume-banner"),
      resumeText: document.getElementById("resume-text"),
      jumpUndecided: document.getElementById("jump-undecided"),
      estimate: document.getElementById("estimate"),
      lookCallout: document.getElementById("look-callout"),
      lookCalloutText: document.getElementById("look-callout-text"),
      lookCalloutButton: document.getElementById("look-callout-button"),
      lookCalloutOther: document.getElementById("look-callout-other"),
      /* Step 6b final: `Accept all` itself, wired here now the card page is gone. */
      acceptAll: document.getElementById("accept-all"),
      acceptAllCount: document.getElementById("accept-all-count"),
      /* UX 5's fourth state: `serverGone` (`pam-core.js`) shows it. */
      errorBanner: document.getElementById("error-banner"),
    };
  }
  return listEl;
}

/* --- drawing ------------------------------------------------------------------------- */

function renderProgress() {
  var el = listElements();
  if (!LIST.counts) {
    return;
  }
  var text = progressText(LIST.counts);
  el.progressApproved.textContent = text.approved;
  el.progressRest.textContent = text.rest;
  el.progressFill.style.width = text.percent + "%";
  el.tabAll.textContent = LIST_COPY.tabAll + LIST.counts.total;
  if (LIST.attentionTodo !== null) {
    el.tabAttention.textContent = LIST_COPY.tabAttention + LIST.attentionTodo;
  }
  renderListStates();
}

/* The page's states that follow the counts (UX 5 "States"), redrawn with the progress line,
 * which every write's response reaches. Step 6b part 2. */
function renderListStates() {
  renderDonePanel();
  renderResumeBanner();
  renderEstimate();
  renderCallout();
}

function renderResumeBanner() {
  var el = listElements();
  var text = resumeText(LIST.counts);
  el.resumeBanner.hidden = text === null;
  el.resumeText.textContent = text || "";
}

function renderEstimate() {
  var el = listElements();
  var text = estimateText(LIST.pace, LIST.counts ? LIST.counts.remaining : 0);
  el.estimate.hidden = text === null;
  el.estimate.textContent = text || "";
}

function renderCallout() {
  var el = listElements();
  /* M14 Task 3 (3b's ruling): the callout's numbers are `/list`'s top-level fields for this
   * view, so its two numbers add up and `Approve the other N` writes the N it shows. */
  var summary = LIST.summary;
  var callout = calloutText(
    LIST.counts,
    summary ? summary.attention_undecided : null,
    summary ? summary.shortcut_count : null
  );
  el.lookCallout.hidden = callout === null;
  el.lookCalloutText.textContent = callout ? callout.text : "";
  el.lookCalloutButton.hidden = !callout || callout.button === null;
  el.lookCalloutButton.textContent = callout && callout.button ? callout.button : "";
  el.lookCalloutOther.hidden = !callout || !callout.other;
  el.lookCalloutOther.textContent = callout && callout.other ? callout.other : "";
}

/* One pace sample, taken only for a decision a human made one at a time. The clock runs
 * from the previous decision, so the first one after landing measures the time spent
 * reading the page - which is why it takes ten of them before the estimate appears. */
function recordPace() {
  var now = Date.now();
  if (LIST.lastDecisionAt !== null) {
    LIST.pace.push((now - LIST.lastDecisionAt) / 1000);
  }
  LIST.lastDecisionAt = now;
}

/* UX 5's "plan fully decided" state, moved unchanged from the card page: every number is the
 * server's `counts`. M14 Task 6c (ruling B5): it ends on W16's sentence, since the page renames
 * after Confirm - no longer on `pam apply N`. */
function renderDonePanel() {
  var el = listElements();
  var counts = LIST.counts;
  clear(el.donePanel);
  if (!counts || counts.total === 0 || counts.remaining > 0) {
    el.donePanel.hidden = true;
    return;
  }
  el.donePanel.hidden = false;
  el.donePanel.appendChild(
    make(
      "span",
      "done-text",
      "Every name in this plan is decided — " +
        counts.approved +
        " approved, " +
        counts.skipped +
        " skipped. Nothing is renamed until you confirm."
    )
  );
}

/* UX 5's empty-filter state (`No items match — [clear filters]`): shown when the view leaves
 * every group empty, judged from the totals the server sent for this view. */
function renderEmptyState() {
  var el = listElements();
  var any = false;
  for (var i = 0; i < LIST.groups.length; i += 1) {
    if (LIST.groups[i].total > 0) {
      any = true;
    }
  }
  el.emptyState.hidden = any;
}

function renderTabs() {
  var el = listElements();
  var pairs = [
    [el.tabAll, VIEW_ALL],
    [el.tabAttention, VIEW_ATTENTION],
    [el.viewAll, VIEW_ALL],
    [el.viewAttention, VIEW_ATTENTION],
    [el.viewSkipped, VIEW_SKIPPED],
  ];
  for (var i = 0; i < pairs.length; i += 1) {
    pairs[i][0].setAttribute("aria-pressed", LIST.view.mode === pairs[i][1] ? "true" : "false");
  }
}

function showListNote(message) {
  var el = listElements();
  el.listNote.hidden = !message;
  el.listNote.textContent = message || "";
}

/* ADDING to the note rather than replacing it, moved from the card page's `addNote`: one
 * keystroke can write rule 7's companion sentence and then the end-of-pass sentence, and
 * rule 7 is normative where the completion sentence is a courtesy. */
function addListNote(message) {
  var note = listElements().listNote;
  var standing = note.hidden ? "" : note.textContent;
  showListNote(standing ? standing + " " + message : message);
}

function groupSection(group) {
  var section = make("section", "list-group");
  section.setAttribute("data-key", group.key);
  var header = make("div", "group-header");
  header.appendChild(make("h2", "group-title", group.title));
  if (group.date_line && group.date_line !== group.title) {
    header.appendChild(make("p", "group-date", group.date_line));
  }
  var count = make("p", "group-count", groupCountText(group));
  header.appendChild(count);
  /* M14 Task 3 (U3, OD-1, W12): `pam isn't sure of these` for a group made only of low-trust
   * names, else its `Approve N` when N - the server's `shortcut_count` - is above zero.
   * Both are drawn once and shown or hidden by `paintShortcut`, so a refresh can move them. */
  var low = make("p", "group-low", LIST_COPY.allLow);
  header.appendChild(low);
  var shortcut = make("button", "group-shortcut");
  shortcut.type = "button";
  shortcut.addEventListener("click", function () {
    runShortcut(group.key);
  });
  header.appendChild(shortcut);
  paintShortcut({ low: low, shortcut: shortcut }, group);
  /* M14 Task 3 (U3, U25): the group's box, drawn only once the bar has wired the selection.
   * It sends the group's KEY (`pam-selection.js`), so rows not loaded yet are in it too. */
  var box = null;
  if (typeof LIST.hooks.toggleGroup === "function") {
    box = make("button", "group-box");
    box.type = "button";
    box.setAttribute("data-state", "none");
    box.setAttribute("aria-pressed", "false");
    box.setAttribute("aria-label", LIST_COPY.pickGroup + group.title);
    box.addEventListener("click", function () {
      callHook("toggleGroup", group.key);
    });
    header.appendChild(box);
  }
  section.appendChild(header);
  var rows = make("div", "group-rows");
  section.appendChild(rows);
  var more = make("button", "show-more");
  more.type = "button";
  more.hidden = true;
  more.addEventListener("click", function () {
    loadGroupPage(group.key, LIST_PAGE);
  });
  section.appendChild(more);
  return {
    group: group,
    section: section,
    count: count,
    rows: rows,
    more: more,
    box: box,
    low: low,
    shortcut: shortcut,
    items: [],
    total: 0,
  };
}

function renderMore(entry) {
  var text = showMoreText(entry.items.length, entry.total);
  entry.more.hidden = text === null;
  entry.more.textContent = text || "";
}

/* U16: the row's thumbnail opens the preview on that row, as the card's did. */
function previewRow(item) {
  callHook("preview", item);
}

/* Step 6b: the row's `↺` hands the row to `pam-detail.js`'s restore, through a hook, as `C`
 * hands it to the editor. */
function restoreRowName(item) {
  callHook("restore", item);
}

/* M14 Task 3 (U3): the row's square, from the selection the bar wired - or none before it is. */
function rowSquare(item, key) {
  var state = callHook("rowState", { id: item.id, key: key });
  if (state === undefined) {
    return null;
  }
  return {
    state: state,
    onToggle: function (event) {
      callHook("toggleRow", { item: item, key: key, shift: Boolean(event && event.shiftKey) });
    },
  };
}

function placeRow(entry, item) {
  var node = buildRow(item, decideRow, previewRow, restoreRowName, rowSquare(item, entry.group.key));
  markRow(node, item.id);
  LIST.rows[item.id] = { item: item, node: node, key: entry.group.key };
  entry.rows.appendChild(node);
}

function repaintRow(itemId) {
  var held = LIST.rows[itemId];
  if (!held) {
    return;
  }
  var fresh = buildRow(
    held.item,
    decideRow,
    previewRow,
    restoreRowName,
    rowSquare(held.item, held.key)
  );
  markRow(fresh, itemId);
  if (held.node.parentNode) {
    held.node.parentNode.replaceChild(fresh, held.node);
  }
  held.node = fresh;
}

/* The left panel's date-and-event tree, from `GET /plan/{id}/groups` - the nav tree the
 * card page drew, now filtering the list. */
function renderTree(tree) {
  var el = listElements();
  clear(el.listTree);
  var list = make("ul");
  list.appendChild(treeEntry("", LIST_COPY.allDates, LIST.counts ? LIST.counts.total : 0));
  for (var i = 0; i < tree.length; i += 1) {
    var year = tree[i];
    list.appendChild(treeEntry(year.key, year.label, year.count));
    var months = year.months || [];
    for (var m = 0; m < months.length; m += 1) {
      list.appendChild(treeEntry(months[m].key, months[m].label, months[m].count));
      var events = months[m].events || [];
      for (var e = 0; e < events.length; e += 1) {
        list.appendChild(treeEntry(events[e].key, events[e].label, events[e].count));
      }
    }
  }
  el.listTree.appendChild(list);
}

function treeEntry(key, label, count) {
  var item = make("li");
  var button = make("button", "tree-entry");
  button.type = "button";
  button.setAttribute("data-group", key);
  button.setAttribute("aria-current", LIST.view.group === key ? "true" : "false");
  button.appendChild(make("span", "nav-label", label));
  button.appendChild(make("span", "nav-count", " (" + count + ")"));
  button.addEventListener("click", function () {
    setView({ group: key });
  });
  item.appendChild(button);
  return item;
}

/* --- requests ------------------------------------------------------------------------ */

function listPost(body) {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

/* One group's next page. The offset is the rows the group already holds - the one
 * spelling under which paging over a total order neither skips nor repeats. */
function loadGroupPage(key, limit) {
  var entry = LIST.byKey[key];
  if (!entry) {
    return Promise.resolve();
  }
  var generation = LIST.generation;
  var path =
    "api/plan/" + LIST.planId + "/items?" + groupQuery(key, LIST.view, entry.items.length, limit);
  return api(path)
    .then(function (payload) {
      if (generation !== LIST.generation) {
        return;
      }
      entry.total = payload.total;
      for (var i = 0; i < payload.items.length; i += 1) {
        entry.items.push(payload.items[i]);
        placeRow(entry, payload.items[i]);
      }
      /* A group the view leaves empty is not drawn at all. */
      entry.section.hidden = entry.total === 0;
      renderMore(entry);
    })
    .catch(function (failure) {
      if (failure instanceof ApiRefusal) {
        showListNote(failure.message);
      }
    });
}

/* Every group's first rows, one group at a time, in the server's order. */
function loadFirstRows() {
  var chain = Promise.resolve();
  LIST.groups.forEach(function (entry) {
    chain = chain.then(function () {
      return loadGroupPage(entry.group.key, LIST_FIRST_ROWS);
    });
  });
  return chain;
}

/* A group's shortcut line from `/list`'s fields: `all_low` -> W11's sentence and no button;
 * `shortcut_count` > 0 -> `Approve N`; otherwise neither (a group with nothing the shortcut
 * would take - every name decided, or every undecided one needing a look - offers no button;
 * W12's `All approved` is not drawn, because `/list` carries no approved count to say so). */
function paintShortcut(entry, group) {
  entry.low.hidden = !group.all_low;
  var count = group.shortcut_count;
  var shown = !group.all_low && typeof count === "number" && count > 0;
  entry.shortcut.hidden = !shown;
  entry.shortcut.textContent = shown ? LIST_COPY.approveN + count : "";
}

/* The view as `/list` takes it (design 8.2, M14 Task 3): `items`' view parameters - the tab,
 * the trust filter, the search and the date tree's `group` - without the sort, which is not
 * part of a view and which `/list` refuses. */
function listViewParams(view) {
  var params = viewParams(view);
  delete params.sort;
  return params;
}

function listPath(view) {
  var query = new URLSearchParams(listViewParams(view)).toString();
  return "api/plan/" + LIST.planId + "/list" + (query ? "?" + query : "");
}

/* A shortcut's body, 3b's exactly: `{"action": "approve", "shortcut": true, "list_group"?,
 * ...the view}` - the same view `/list` counted its N over, so the write is that N. */
function shortcutBody(view, key) {
  var body = { action: "approve", shortcut: true };
  if (key) {
    body.list_group = key;
  }
  var params = listViewParams(view);
  for (var name in params) {
    if (Object.prototype.hasOwnProperty.call(params, name)) {
      body[name] = name === "attention" ? true : params[name];
    }
  }
  return body;
}

/* `/list`'s top-level summary, kept for the callout. */
function takeSummary(payload) {
  if (typeof payload.attention_undecided === "number") {
    LIST.summary = {
      undecided: payload.undecided,
      attention_undecided: payload.attention_undecided,
      shortcut_count: payload.shortcut_count,
    };
  } else {
    LIST.summary = null;
  }
  renderCallout();
  /* M14 Task 6c (W16, ruling B2): the footer's N - the files Confirm would rename - is this
   * payload's `renames`, drawn by `pam-confirm.js` through the hook. */
  callHook("renames", payload.renames);
}

/* The groups, freshly drawn for the current view. */
function loadGroups() {
  var el = listElements();
  LIST.generation += 1;
  var generation = LIST.generation;
  return api(listPath(LIST.view)).then(function (payload) {
    takeSummary(payload);
    clear(el.list);
    LIST.groups = [];
    LIST.byKey = {};
    LIST.rows = {};
    var groups = payload.groups || [];
    for (var i = 0; i < groups.length; i += 1) {
      var entry = groupSection(groups[i]);
      LIST.groups.push(entry);
      LIST.byKey[groups[i].key] = entry;
      el.list.appendChild(entry.section);
    }
    return loadFirstRows().then(function () {
      if (generation === LIST.generation) {
        renderEmptyState();
      }
    });
  });
}

/* The `Needs a look` count: the size of the server's own set of undecided attention items -
 * the number UX 5a's callout and the tab must agree on. `limit=0` asks for the size only. */
function loadAttentionTodo() {
  return api("api/plan/" + LIST.planId + "/items?attention=1&status=proposed&limit=0").then(
    function (payload) {
      LIST.attentionTodo = payload.total;
      renderProgress();
    }
  );
}

/* `Accept all`'s count (F1 R2), moved from the card page's `refreshPresetCounts` with the
 * same scope and the same source: NOT scoped to the view - the plan's undecided high and
 * medium names, each the size of the server's own filtered set. */
function refreshAcceptAllCount() {
  return Promise.all([
    listCount({ status: "proposed", tier: "high" }),
    listCount({ status: "proposed", tier: "medium" }),
  ])
    .then(function (both) {
      listElements().acceptAllCount.textContent = "(" + (both[0] + both[1]) + ")";
    })
    .catch(function (failure) {
      /* A count on a button is decoration; a refusal fetching one must not take the
       * list down with it. A dead server is already handled by `api`. */
      if (!(failure instanceof ApiRefusal)) {
        throw failure;
      }
    });
}

/* The size of one server-filtered set: `limit=0` asks for the size and none of it. */
function listCount(scope) {
  var params = new URLSearchParams();
  for (var name in scope) {
    if (Object.prototype.hasOwnProperty.call(scope, name)) {
      params.set(name, scope[name]);
    }
  }
  params.set("limit", "0");
  return api("api/plan/" + LIST.planId + "/items?" + params.toString()).then(function (payload) {
    return payload.total;
  });
}

/* Group headers and the tab count, re-asked after decisions - the server counts them. */
function refreshHeaders() {
  return Promise.all([
    loadAttentionTodo(),
    refreshAcceptAllCount(),
    api(listPath(LIST.view)).then(function (payload) {
      takeSummary(payload);
      var groups = payload.groups || [];
      for (var i = 0; i < groups.length; i += 1) {
        var entry = LIST.byKey[groups[i].key];
        if (entry) {
          entry.count.textContent = groupCountText(groups[i]);
          paintShortcut(entry, groups[i]);
        }
      }
    }),
  ]);
}

function scheduleRefresh() {
  if (LIST.refreshTimer !== null) {
    clearTimeout(LIST.refreshTimer);
  }
  LIST.refreshTimer = setTimeout(function () {
    LIST.refreshTimer = null;
    refreshHeaders();
  }, 400);
}

/* --- deciding ------------------------------------------------------------------------ */

/* The circle. One id, the existing `decide`, and the action `pam-row.js` chose. */
function decideRow(item) {
  return decideAction(item, circleAction(item));
}

/* One row, one of design 8.2's three actions, through the existing `decide`. Shared by the
 * circle and the keys, so a decision made either way repaints and counts the same way.
 *
 * M14 Task 3 (Task 2 review R2 F3): A STOPPED PAGE DECIDES NOTHING HERE TOO, not only in
 * `pam-keys.js::onKeyDown` - the circle and the preview's buttons reach this path without a
 * key, and `serverGone` disabling the controls is the page's first line, not its only one. */
function decideAction(item, action) {
  if (PAGE.stopped) {
    return Promise.resolve();
  }
  showListNote("");
  return api(
    "api/plan/" + LIST.planId + "/decide",
    listPost({ action: action, item_ids: [item.id] })
  )
    .then(function (result) {
      var moved = [item.id].concat(result.companions || []);
      for (var i = 0; i < moved.length; i += 1) {
        var held = LIST.rows[moved[i]];
        if (held) {
          held.item.status = ROW_AFTER[action];
          repaintRow(moved[i]);
        }
      }
      /* THE RESPONSE'S COUNTS, NEVER A LOCAL TALLY. */
      if (result.counts) {
        LIST.counts = result.counts;
      }
      /* A one-row decision is a pace sample (UX 5's estimate). */
      recordPace();
      renderProgress();
      /* The preview is the second surface showing this row: it draws the decision as
       * `Decision:` and as whether `Not decided` is there at all (Step 6a). */
      if (callHook("previewOpen")) {
        callHook("previewMoved", listCurrent());
      }
      if (result.note) {
        showListNote(result.note);
      }
      scheduleRefresh();
    })
    .catch(function (failure) {
      if (failure instanceof ApiRefusal) {
        showListNote(failure.message);
      }
    });
}

/* --- keys (M14 Task 2 Step 5; U2, U16) ------------------------------------------------ */

/* U2's keys in the list: `↑/↓` move, `X` approve, `Z` skip, `C` edit the name, `Space`
 * select, `Enter` the preview, `?` the shortcut list; inside the preview `←/→` still move
 * (U16). `listKeyAction` is the PURE half - an event and the page's three open/closed facts
 * in, one action name out - so node executes every rule; `onListKey` performs it.
 *
 * KEYS ARE INERT WHILE A FIELD HAS FOCUS (HT-M7's check): a user typing a name must not
 * approve the row - `Cox` alone would edit, skip and approve it. The name field's Escape
 * is the one exception, and it belongs to the field. A focused button owns Enter and
 * Space, so taking them here would fire the button and the shortcut at once.
 *
 * `Space` SELECTS the row under the keyboard (M14 Task 3, U3): it hands the row to the
 * selection through the bar's hook, as a row's square does. */
function listKeyAction(event, open) {
  if (event.ctrlKey || event.altKey || event.metaKey) {
    return null;
  }
  var target = event.target;
  var tag = target && target.tagName ? target.tagName.toLowerCase() : "";
  if (tag === "input" || tag === "select" || tag === "textarea") {
    return open.editorField && target === open.editorField && event.key === "Escape"
      ? "close-editor"
      : null;
  }
  if (tag === "button" && (event.key === "Enter" || event.key === " ")) {
    return null;
  }
  if (open.shortcuts) {
    return event.key === "?" || event.key === "Escape" || event.key === "Enter"
      ? "close-shortcuts"
      : null;
  }
  if (event.key === "?") {
    return "shortcuts";
  }
  if (event.key === "Escape") {
    return open.preview ? "close-preview" : null;
  }
  if (event.key === "ArrowUp") {
    return "previous";
  }
  if (event.key === "ArrowDown") {
    return "next";
  }
  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    if (!open.preview) {
      return null;
    }
    return event.key === "ArrowLeft" ? "previous" : "next";
  }
  if (event.key === "Enter") {
    return open.preview ? "close-preview" : "preview";
  }
  if (event.key === " ") {
    return "select";
  }
  var letter = event.key && event.key.length === 1 ? event.key.toLowerCase() : "";
  return { x: "approve", z: "skip", c: "edit" }[letter] || null;
}

/* The rows in the order the page shows them: every loaded row of every drawn group. */
function listOrder() {
  var order = [];
  for (var g = 0; g < LIST.groups.length; g += 1) {
    if (!LIST.groups[g].section.hidden) {
      order = order.concat(LIST.groups[g].items);
    }
  }
  return order;
}

function markRow(node, itemId) {
  node.setAttribute("data-cursor", itemId === LIST.cursorId ? "true" : "false");
}

function listCurrent() {
  var held = LIST.cursorId === null ? null : LIST.rows[LIST.cursorId];
  return held ? held.item : null;
}

function setListCursor(itemId) {
  LIST.cursorId = itemId;
  for (var id in LIST.rows) {
    if (Object.prototype.hasOwnProperty.call(LIST.rows, id)) {
      markRow(LIST.rows[id].node, LIST.rows[id].item.id);
    }
  }
  var held = LIST.rows[itemId];
  if (held && held.node.scrollIntoView) {
    held.node.scrollIntoView({ block: "nearest" });
  }
  /* An open preview shows the keyboard's row, so it follows every move - by key, by its own
   * Previous/Next buttons, and after a decision's advance (U16). */
  if (held && callHook("previewOpen")) {
    callHook("previewMoved", held.item);
  }
}

/* `↑/↓` (and `←/→` in the preview): one row, clamped at the ends. With no row under the
 * keyboard yet, either key lands on the first row. */
function moveListCursor(delta) {
  var order = listOrder();
  if (order.length === 0) {
    return;
  }
  var at = -1;
  for (var i = 0; i < order.length; i += 1) {
    if (order[i].id === LIST.cursorId) {
      at = i;
    }
  }
  var next = at < 0 ? 0 : Math.max(0, Math.min(order.length - 1, at + delta));
  setListCursor(order[next].id);
}

/* After a decision by key, the next UNDECIDED row - UX 5's one keystroke per photo: below
 * first, then from the top, as the card page's `nextUndecided` wrapped.
 *
 * UX SPEC 5 REV 9 / B1 (the A4 / M8 ruling), moved from the card page's
 * `advanceToNextUndecided`: THE END OF A PASS IS A STATE. When no loaded row is undecided the
 * SERVER is asked how many are left in this view - the list holds a few rows of each group,
 * so the loaded rows cannot answer it. None: focus moves off the row (the decision keys are
 * inert again, so a repeated `X` cannot reset what it just approved), an open preview
 * closes, and the page says the pass is complete. Some (in pages not loaded yet): the
 * keyboard stays where it is. */
function advanceListCursor() {
  var order = listOrder();
  var at = -1;
  var i;
  for (i = 0; i < order.length; i += 1) {
    if (order[i].id === LIST.cursorId) {
      at = i;
    }
  }
  for (i = at + 1; i < order.length; i += 1) {
    if (order[i].status === "proposed") {
      setListCursor(order[i].id);
      return Promise.resolve();
    }
  }
  for (i = 0; i <= at; i += 1) {
    if (order[i].status === "proposed") {
      setListCursor(order[i].id);
      return Promise.resolve();
    }
  }
  return undecidedInView().then(function (left) {
    if (left === 0) {
      passComplete();
    }
  });
}

/* How many of this view's items are undecided, by the server (`limit=0`). A view that
 * selects only a decision (`Skipped`) holds none. */
function undecidedInView() {
  var params = viewParams(LIST.view);
  if (params.status && params.status !== "proposed") {
    return Promise.resolve(0);
  }
  params.status = "proposed";
  var query = new URLSearchParams();
  for (var name in params) {
    if (Object.prototype.hasOwnProperty.call(params, name)) {
      query.set(name, params[name]);
    }
  }
  query.set("limit", "0");
  return api("api/plan/" + LIST.planId + "/items?" + query.toString())
    .then(function (payload) {
      return payload.total;
    })
    .catch(function (failure) {
      if (failure instanceof ApiRefusal) {
        return null;
      }
      throw failure;
    });
}

function passComplete() {
  LIST.cursorId = null;
  for (var id in LIST.rows) {
    if (Object.prototype.hasOwnProperty.call(LIST.rows, id)) {
      markRow(LIST.rows[id].node, LIST.rows[id].item.id);
    }
  }
  if (callHook("previewOpen")) {
    callHook("closePreview");
  }
  addListNote(LIST_COPY.passComplete);
}

/* UX 5 rev 8's toggle, by key: `X` on an approved row and `Z` on a skipped one send
 * `reset` and stay put; any other decision advances. */
function keyDecision(item, action) {
  var undo = item.status === ROW_AFTER[action];
  return decideAction(item, undo ? "reset" : action).then(function () {
    if (!undo) {
      return advanceListCursor();
    }
  });
}

function callHook(name, argument) {
  var hook = LIST.hooks[name];
  return typeof hook === "function" ? hook(argument) : undefined;
}

/* The keyboard. Returns the action it performed (or null), and `LIST.keyDone` is the
 * promise a decision made, so a caller - and node - can wait for it. */
function onListKey(event) {
  var action = listKeyAction(event, {
    shortcuts: Boolean(callHook("shortcutsOpen")),
    preview: Boolean(callHook("previewOpen")),
    editorField: LIST.hooks.editorField || null,
  });
  LIST.keyDone = Promise.resolve();
  if (action === null) {
    return null;
  }
  event.preventDefault();
  var item = listCurrent();
  if (action === "previous" || action === "next") {
    moveListCursor(action === "previous" ? -1 : 1);
  } else if (action === "approve" || action === "skip") {
    if (item) {
      LIST.keyDone = keyDecision(item, action);
    }
  } else if (action === "edit" || action === "preview" || action === "select") {
    if (item) {
      callHook(action, item);
    }
  } else if (action === "close-preview") {
    callHook("closePreview");
  } else if (action === "close-editor") {
    callHook("closeEditor");
  } else if (action === "shortcuts" || action === "close-shortcuts") {
    callHook("shortcuts");
  }
  return action;
}

/* --- Accept all (U2, U3: it stays as the top shortcut) ---------------------------------- */

/* M14 Task 2 Step 6b: the body and the sentence, moved unchanged from `pam-review.js`. */

/* F1 R2: `Accept all` approves "every item in the plan" except Low, so its scope IGNORES
 * THE VIEW ON PURPOSE - it is handed the view and carries none of it. The server selects
 * with `naming.accept.accept_all` (the one definition `pam apply --all` uses): undecided
 * units only, whole, Low held back, a skip never re-approved (M13-D1). */
function acceptAllScope(filters) {
  void filters;
  return { action: "approve", accept_all: true };
}

/* F1 R2's sentence, built from the server's response: the Low half only when it left any,
 * and rule 7's `note` after it when the server sent one. */
function acceptAllReport(result) {
  var said = "Accepted " + result.decided + ".";
  if (result.excluded_low > 0) {
    said +=
      " " +
      result.excluded_low +
      " low-confidence name(s) were not accepted - approve them one at a time.";
  }
  if (result.note) {
    said += " " + result.note;
  }
  return said;
}

/* F1 R2's `Accept all`: the whole plan's undecided units except Low, whatever the view
 * shows - `acceptAllScope` (below, the pure function `app_logic_test.js` runs) is
 * handed the view and carries none of it. M14 Task 2 Step 6a moved it here from the
 * card page (`pam-review.js`, retired at Step 6b) so that, after its write, THE LIST
 * REFRESHES FROM THE SERVER: the progress line is the response's `counts`, and the groups, their header counts, their rows and the
 * `Needs a look` count are all asked for again. Nothing here adds up what was approved.
 * R2's sentence, over the server's numbers, goes on the list's note line. */
function runAcceptAll() {
  if (callHook("stopped") || LIST.planId === null) {
    return Promise.resolve();
  }
  showListNote("");
  return api(
    "api/plan/" + LIST.planId + "/decide-filter",
    listPost(acceptAllScope(LIST.view))
  )
    .then(function (result) {
      if (result.counts) {
        LIST.counts = result.counts;
      }
      renderProgress();
      /* Step 6b part 2: Accept all's own count, re-asked like every other number here. */
      refreshAcceptAllCount();
      return Promise.all([loadGroups(), loadAttentionTodo()]).then(function () {
        showListNote(acceptAllReport(result));
      });
    })
    .catch(function (failure) {
      if (failure instanceof ApiRefusal) {
        showListNote(failure.message);
      }
    });
}

/* A one-tap shortcut (OD-1): a group's `Approve N` (with its key) or the callout's
 * `Approve the other N` (without), through `decide-filter`'s shortcut branch. Afterwards the
 * list and every count are asked for again - the page never adjusts a number itself. */
function runShortcut(key) {
  if (PAGE.stopped || callHook("stopped") || LIST.planId === null || LIST.shortcutBusy) {
    return Promise.resolve();
  }
  LIST.shortcutBusy = true;
  showListNote("");
  return api("api/plan/" + LIST.planId + "/decide-filter", listPost(shortcutBody(LIST.view, key)))
    .then(function (result) {
      if (result.counts) {
        LIST.counts = result.counts;
      }
      renderProgress();
      refreshAcceptAllCount();
      var reload = Promise.all([loadGroups(), loadAttentionTodo()]);
      return reload.then(function () {
        showListNote(
          LIST_COPY.shortcutDone + result.decided + "." + (result.note ? " " + result.note : "")
        );
      });
    })
    .catch(function (failure) {
      if (failure instanceof ApiRefusal) {
        showListNote(failure.message);
      }
    })
    .then(function () {
      LIST.shortcutBusy = false;
    });
}

/* --- views --------------------------------------------------------------------------- */

function setView(change) {
  /* M14 Task 3: a selection's groups and `Select all` belong to the view they were made in. */
  callHook("viewChanged");
  for (var name in change) {
    if (Object.prototype.hasOwnProperty.call(change, name)) {
      LIST.view[name] = change[name];
    }
  }
  /* UX 5a, moved from the card page's `setAttentionFilter`: the attention view sorts lowest
   * trust first, "so attention lands where it pays". */
  if (change.mode === VIEW_ATTENTION) {
    LIST.view.sort = SORT_TRUST;
    listElements().sort.value = SORT_TRUST;
  }
  /* A refusal or a report is about the view it was made in (the card page's `applyFilters`). */
  showListNote("");
  renderTabs();
  var entries = listElements().listTree.querySelectorAll("button");
  for (var i = 0; i < entries.length; i += 1) {
    var current = entries[i].getAttribute("data-group") === LIST.view.group;
    entries[i].setAttribute("aria-current", current ? "true" : "false");
  }
  return loadGroups();
}

function wireList() {
  var el = listElements();
  var views = [
    [el.tabAll, VIEW_ALL],
    [el.tabAttention, VIEW_ATTENTION],
    [el.viewAll, VIEW_ALL],
    [el.viewAttention, VIEW_ATTENTION],
    [el.viewSkipped, VIEW_SKIPPED],
  ];
  views.forEach(function (pair) {
    pair[0].addEventListener("click", function () {
      setView({ mode: pair[1] });
    });
  });
  el.trustFilter.addEventListener("change", function () {
    setView({ tier: el.trustFilter.value });
  });
  /* Step 6b part 2, moved from the card page's `wireControls`. */
  el.search.addEventListener("change", function () {
    setView({ q: el.search.value.trim() });
  });
  el.sort.addEventListener("change", function () {
    /* Spec 8.2 names two sorts and the server refuses anything else with a 400 naming the
     * parameter; the page sends one of the two rather than whatever a control holds. */
    setView({ sort: el.sort.value === SORT_DATE ? SORT_DATE : SORT_TRUST });
  });
  el.clearFilters.addEventListener("click", clearListFilters);
  el.clearFiltersEmpty.addEventListener("click", clearListFilters);
  el.jumpUndecided.addEventListener("click", jumpToFirstUndecided);
  /* F1 R2's `Accept all` (U2/U3: the top shortcut stays), moved from the card page's
   * `wireControls` when Step 6b retired it. */
  el.acceptAll.addEventListener("click", runAcceptAll);
  /* W3's `Look at the N first`: the `Needs a look` tab. */
  el.lookCalloutButton.addEventListener("click", function () {
    setView({ mode: VIEW_ATTENTION });
  });
  /* W3's second button (OD-1): the view's `Approve the other N`. */
  el.lookCalloutOther.addEventListener("click", function () {
    runShortcut(null);
  });
}

/* UX spec 5's `[Jump to first undecided]`, moved from the card page. The first undecided row
 * the list holds; if it holds none, the view is narrowed to the undecided (the card page set
 * its Status filter to `proposed`) and the keyboard lands on its first row. */
function jumpToFirstUndecided() {
  listElements().resumeBanner.hidden = true;
  var order = listOrder();
  for (var i = 0; i < order.length; i += 1) {
    if (order[i].status === "proposed") {
      setListCursor(order[i].id);
      return Promise.resolve();
    }
  }
  return setView({ status: "proposed" }).then(function () {
    var first = listOrder();
    if (first.length > 0) {
      setListCursor(first[0].id);
    }
  });
}

/* `Clear filters` and the empty state's `clear filters`, moved from the card page's
 * `resetFilters`: back to every name, every tier, every date and no search. The sort stays
 * where the user put it, as it did there. */
function clearListFilters() {
  var el = listElements();
  el.search.value = "";
  el.trustFilter.value = "";
  return setView({ mode: VIEW_ALL, tier: "", group: "", q: "", status: "" });
}

/* The list's start, once `app.js` knows the plan. `summary` is `GET /plan/{id}`'s. */
function startList(planId, summary) {
  LIST.planId = planId;
  LIST.counts = summary
    ? {
        total: summary.total,
        approved: summary.approved,
        skipped: summary.skipped,
        remaining: summary.remaining,
        attention: summary.attention,
      }
    : null;
  wireList();
  listElements().sort.value = LIST.view.sort;
  renderTabs();
  renderProgress();
  return Promise.all([
    loadAttentionTodo(),
    refreshAcceptAllCount(),
    api("api/plan/" + planId + "/groups").then(function (payload) {
      renderTree(payload.groups || []);
    }),
    loadGroups(),
  ]).catch(function (failure) {
    if (failure instanceof ApiRefusal) {
      showListNote(failure.message);
    }
  });
}

/* --- the seams: the page's namespace, and node's -------------------------------------- */

if (typeof window !== "undefined") {
  window.PAM.list = {
    LIST: LIST,
    LIST_COPY: LIST_COPY,
    LIST_FIRST_ROWS: LIST_FIRST_ROWS,
    LIST_PAGE: LIST_PAGE,
    progressText: progressText,
    groupCountText: groupCountText,
    showMoreText: showMoreText,
    groupQuery: groupQuery,
    loadGroupPage: loadGroupPage,
    decideRow: decideRow,
    setView: setView,
    startList: startList,
    listKeyAction: listKeyAction,
    onListKey: onListKey,
    moveListCursor: moveListCursor,
    /* Step 6a: what `pam-detail.js` calls, and Accept all. */
    listCurrent: listCurrent,
    setListCursor: setListCursor,
    advanceListCursor: advanceListCursor,
    decideAction: decideAction,
    repaintRow: repaintRow,
    renderProgress: renderProgress,
    scheduleRefresh: scheduleRefresh,
    runAcceptAll: runAcceptAll,
    runShortcut: runShortcut,
    shortcutBody: shortcutBody,
    listViewParams: listViewParams,
    showListNote: showListNote,
    acceptAllScope: acceptAllScope,
    acceptAllReport: acceptAllReport,
    /* Step 6b part 2. */
    viewParams: viewParams,
    clearListFilters: clearListFilters,
    calloutText: calloutText,
    resumeText: resumeText,
    estimateText: estimateText,
    jumpToFirstUndecided: jumpToFirstUndecided,
    refreshAcceptAllCount: refreshAcceptAllCount,
    listElements: listElements,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    LIST: LIST,
    LIST_COPY: LIST_COPY,
    LIST_FIRST_ROWS: LIST_FIRST_ROWS,
    LIST_PAGE: LIST_PAGE,
    progressText: progressText,
    groupCountText: groupCountText,
    showMoreText: showMoreText,
    groupQuery: groupQuery,
    loadGroupPage: loadGroupPage,
    decideRow: decideRow,
    setView: setView,
    startList: startList,
    listKeyAction: listKeyAction,
    onListKey: onListKey,
    moveListCursor: moveListCursor,
    /* Step 6a: what `pam-detail.js` calls, and Accept all. */
    listCurrent: listCurrent,
    setListCursor: setListCursor,
    advanceListCursor: advanceListCursor,
    decideAction: decideAction,
    repaintRow: repaintRow,
    renderProgress: renderProgress,
    scheduleRefresh: scheduleRefresh,
    runAcceptAll: runAcceptAll,
    runShortcut: runShortcut,
    shortcutBody: shortcutBody,
    listViewParams: listViewParams,
    showListNote: showListNote,
    acceptAllScope: acceptAllScope,
    acceptAllReport: acceptAllReport,
    /* Step 6b part 2. */
    viewParams: viewParams,
    clearListFilters: clearListFilters,
    calloutText: calloutText,
    resumeText: resumeText,
    estimateText: estimateText,
    jumpToFirstUndecided: jumpToFirstUndecided,
    refreshAcceptAllCount: refreshAcceptAllCount,
    listElements: listElements,
  };
}
