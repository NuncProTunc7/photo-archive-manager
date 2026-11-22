/*
 * THE SELECTION AND ITS BAR, EXECUTED (M14 Task 3 slice 3c; U3, U8 W13/W14, U17).
 *
 * `pam-selection.js` (the selection as data) and `pam-bar.js` (the bar) are run here under a
 * real `node`, in the page's shared scope - `pam-core.js`, `pam-row.js`, `pam-selection.js`,
 * `pam-list.js` and `pam-bar.js` loaded in the page's order as classic scripts - over a small
 * hand-written DOM and a stub `api` answering the routes the bar calls.
 *
 * EVERY EXPECTATION IS SPELLED FROM U17's AND U3's TEXT, never read back out of the scripts:
 * the wire format's field names, which rows a group's key stands for, the two sentences with
 * their singulars, the 409's sentence, and the approve body's two low fields.
 *
 * WHAT IT PINS (the T3 mutation it is for): "a group's box sends the group key, so its
 * unloaded rows are included" is T3 mutation 6 - `toggleGroup` enumerating the loaded ids
 * instead of sending the key.
 *
 * `tests/test_webui_selection_page.py` runs this file and reads the names it prints: every
 * check prints `ok <name>` or `FAIL <name>` and the run ends with `ran <n>`. A planted copy of
 * either script goes in through `PAM_SELECTION_JS` / `PAM_BAR_JS`.
 */

"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

/* --- a small DOM -------------------------------------------------------------------------- */

function makeNode(tag) {
  const node = {
    tagName: String(tag || "div").toUpperCase(),
    children: [],
    parentNode: null,
    attributes: {},
    listeners: {},
    style: {},
    hidden: false,
    disabled: false,
    className: "",
    textContent: "",
    value: "",
    type: "",
    get firstChild() {
      return node.children.length > 0 ? node.children[0] : null;
    },
    appendChild(child) {
      if (child.parentNode) {
        child.parentNode.removeChild(child);
      }
      node.children.push(child);
      child.parentNode = node;
      return child;
    },
    removeChild(child) {
      node.children = node.children.filter((each) => each !== child);
      child.parentNode = null;
      return child;
    },
    replaceChild(fresh, old) {
      const at = node.children.indexOf(old);
      node.children[at] = fresh;
      fresh.parentNode = node;
      old.parentNode = null;
      return old;
    },
    setAttribute(name, value) {
      node.attributes[name] = String(value);
    },
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(node.attributes, name)
        ? node.attributes[name]
        : null;
    },
    addEventListener(name, handler) {
      (node.listeners[name] = node.listeners[name] || []).push(handler);
    },
    click(event) {
      return (node.listeners.click || []).map((handler) => handler(event));
    },
    querySelectorAll(tagName) {
      return descendants(node).filter((each) => each.tagName === tagName.toUpperCase());
    },
  };
  return node;
}

function descendants(node) {
  const found = [];
  for (const child of node.children) {
    found.push(child, ...descendants(child));
  }
  return found;
}

function byClass(node, className) {
  return descendants(node).filter((each) => each.className === className);
}

const byId = {};
global.document = {
  getElementById(id) {
    if (!byId[id]) {
      byId[id] = makeNode("div");
    }
    return byId[id];
  },
  createElement: makeNode,
  querySelectorAll() {
    return [];
  },
};

/* THE PAGE'S SHARED SCOPE, REPRODUCED: each script run as a classic script in this context,
 * in `index.html`'s order, so every top-level name is a global exactly as in the browser. */
const STATIC_DIR = path.join(__dirname, "..", "..", "pam", "webui", "static");
const SCRIPTS = [
  path.join(STATIC_DIR, "pam-core.js"),
  path.join(STATIC_DIR, "pam-row.js"),
  process.env.PAM_SELECTION_JS || path.join(STATIC_DIR, "pam-selection.js"),
  path.join(STATIC_DIR, "pam-list.js"),
  process.env.PAM_BAR_JS || path.join(STATIC_DIR, "pam-bar.js"),
];
for (const file of SCRIPTS) {
  vm.runInThisContext(fs.readFileSync(file, "utf-8"), { filename: file });
}

/* --- the copy, spelled from U3 and the controller's 409 ruling ---------------------------- */

const LOW_PLURAL = "Includes 2 names that need review. Approve will approve them too.";
const LOW_SINGULAR = "Includes 1 name that needs review. Approve will approve it too.";
const DONE_PLURAL =
  "Approved 5, including 2 names that need review. Every rename can be undone later.";
const DONE_SINGULAR =
  "Approved 4, including 1 name that needs review. Every rename can be undone later.";
const CHANGED = "The selection changed. Check the count and try again.";

/* --- the stub server -------------------------------------------------------------------- */

let calls = [];
let routes = null;

global.api = function (pathname, options) {
  const body = options && options.body ? JSON.parse(options.body) : null;
  calls.push({ path: pathname, body: body });
  try {
    return Promise.resolve(routes(pathname, body));
  } catch (refusal) {
    return Promise.reject(refusal);
  }
};

function card(id, fields) {
  return Object.assign(
    {
      id: id,
      file_id: id,
      old_filename: "IMG_000" + id + ".HEIC",
      new_filename: "2019-07-04_00" + id + ".heic",
      media_type: "image",
      ext: "heic",
      tier: "high",
      trust_label: "High trust",
      edited: false,
      noop: false,
      status: "proposed",
      unit_key: "u" + id,
      unit_size: 1,
      has_thumb: false,
      attention: false,
      note_code: null,
      look_alikes: [],
      look_alikes_more: 0,
    },
    fields,
  );
}

/* One list group, `key`, of `total` items of which the page loads the first `loaded`. */
function serve(key, total, loaded, answer) {
  const items = [];
  for (let id = 1; id <= total; id += 1) {
    items.push(card(id));
  }
  routes = function (pathname, body) {
    if (pathname.endsWith("/selection") || pathname.endsWith("/decide-selection")) {
      return answer(pathname, body);
    }
    if (pathname.split("?")[0].endsWith("/list")) {
      return {
        groups: [
          {
            key: key,
            title: "July 4, 2019",
            date_line: "July 4, 2019",
            total: total,
            undecided: total,
            attention_undecided: 0,
            all_low: false,
          },
        ],
      };
    }
    const params = new URLSearchParams(pathname.split("?")[1] || "");
    if (params.get("limit") === "0") {
      return { total: 0, items: [] };
    }
    const offset = Number(params.get("offset"));
    const limit = Math.min(Number(params.get("limit")), loaded - offset);
    return { total: total, items: items.slice(offset, offset + Math.max(limit, 0)) };
  };
}

let wired = false;

/* A fresh page: the model empty, the bar wired once, the list loaded with no delay. */
async function page(key, total, loaded, answer) {
  clearTimeout(LIST.refreshTimer);
  LIST.refreshTimer = null;
  selClear();
  BAR.delay = 0;
  BAR.preview = null;
  BAR.wire = null;
  BAR.pending = false;
  BAR.anchor = null;
  PAGE.stopped = false;
  if (!wired) {
    wireBar();
    wired = true;
  }
  LIST.planId = 7;
  LIST.counts = null;
  LIST.view = { mode: "all", tier: "", group: "", q: "", sort: "trust_asc", status: "" };
  LIST.groups = [];
  LIST.byKey = {};
  LIST.rows = {};
  clear(document.getElementById("list"));
  serve(key, total, loaded, answer);
  await setView({});
  await BAR.previewDone;
  calls = [];
}

function el(id) {
  return document.getElementById(id);
}

function posted(suffix) {
  return calls.filter((each) => each.path === "api/plan/7/" + suffix);
}

const tests = [];
function test(name, body) {
  tests.push([name, body]);
}

/* --- the model (U17), pure ---------------------------------------------------------------- */

test("a row's square sends its id, and ticking it again takes it out", () => {
  selClear();
  selToggleRow({ id: 3, key: "2019-07-04" });
  assert.deepEqual(selToWire({}), { view: {}, ids: [3] });
  assert.equal(selRowState({ id: 3, key: "2019-07-04" }), "all");
  selToggleRow({ id: 3, key: "2019-07-04" });
  assert.deepEqual(selToWire({}), { view: {} });
  assert.equal(selIsEmpty(), true);
});

/* T3 mutation 6. Two of the group's five rows are loaded; the box must send the KEY. */
test("a group's box sends the group key, so its unloaded rows are included", () => {
  selClear();
  const group = { key: "e12", ids: [1, 2] };
  selToggleGroup(group, ["e12"]);
  assert.deepEqual(selToWire({}), { view: {}, groups: ["e12"] });
  assert.equal(selGroupState(group), "all");
  /* An unloaded row of the group (id 5) is in it, by the key. */
  assert.equal(selRowState({ id: 5, key: "e12" }), "all");
  selToggleGroup(group, ["e12"]);
  assert.deepEqual(selToWire({}), { view: {} });
});

test("a row unticked inside a ticked group is an exception, and ticking it again takes it back", () => {
  selClear();
  const group = { key: "2019-07-04", ids: [1, 2, 3] };
  selToggleGroup(group, [group.key]);
  selToggleRow({ id: 2, key: group.key });
  assert.deepEqual(selToWire({}), { view: {}, groups: ["2019-07-04"], except_ids: [2] });
  assert.equal(selRowState({ id: 2, key: group.key }), "none");
  assert.equal(selGroupState(group), "some");
  selToggleRow({ id: 2, key: group.key });
  assert.deepEqual(selToWire({}), { view: {}, groups: ["2019-07-04"] });
  assert.equal(selGroupState(group), "all");
  /* Ticking a `some` group's box makes it `all` again and drops the exception. */
  selToggleRow({ id: 3, key: group.key });
  selToggleGroup(group, [group.key]);
  assert.deepEqual(selToWire({}), { view: {}, groups: ["2019-07-04"] });
});

test("Select all sends all, an unticked row is an exception, and Select all again empties it", () => {
  selClear();
  selToggleAll();
  assert.deepEqual(selToWire({}), { view: {}, all: true });
  selToggleRow({ id: 4, key: "undated" });
  assert.deepEqual(selToWire({}), { view: {}, all: true, except_ids: [4] });
  selToggleAll();
  assert.equal(selIsEmpty(), true);
  assert.deepEqual(selToWire({}), { view: {} });
});

test("unticking one group under Select all keeps every other group by its key", () => {
  selClear();
  selToggleAll();
  selToggleGroup({ key: "e12", ids: [1] }, ["2019-07-04", "e12", "undated"]);
  assert.deepEqual(selToWire({}), { view: {}, groups: ["2019-07-04", "undated"] });
  assert.equal(selRowState({ id: 9, key: "e12" }), "none");
});

test("rowState and groupState read none, some and all", () => {
  selClear();
  const group = { key: "2019-07-04", ids: [1, 2] };
  const cases = [
    [() => {}, "none", "none"],
    [() => selToggleRow({ id: 1, key: group.key }), "some", "all"],
    [() => selToggleGroup(group, [group.key]), "all", "all"],
    [() => selToggleRow({ id: 1, key: group.key }), "some", "none"],
  ];
  assert.equal(cases.length, 4);
  for (const [act, groupState, rowOne] of cases) {
    act();
    assert.equal(selGroupState(group), groupState);
    assert.equal(selRowState({ id: 1, key: group.key }), rowOne);
  }
});

test("Shift-click adds the loaded rows between two rows as ids, either way round", () => {
  const rows = [1, 2, 3, 4, 5].map((id) => ({ id: id, key: "2019-07-04" }));
  selClear();
  selSelectRange(rows, 2, 4);
  assert.deepEqual(selToWire({}), { view: {}, ids: [2, 3, 4] });
  selClear();
  selSelectRange(rows, 5, 3);
  assert.deepEqual(selToWire({}).ids.slice().sort(), [3, 4, 5]);
  /* Inside a ticked group, a range takes exceptions back rather than adding ids. */
  selClear();
  selToggleGroup({ key: "2019-07-04", ids: [1, 2, 3, 4, 5] }, ["2019-07-04"]);
  selToggleRow(rows[1]);
  selToggleRow(rows[2]);
  selSelectRange(rows, 1, 3);
  assert.deepEqual(selToWire({}), { view: {}, groups: ["2019-07-04"] });
});

test("toWire sends U17's four view fields and the date tree's group, and leaves out the empty ones", () => {
  selClear();
  selToggleRow({ id: 1, key: "k" });
  const wire = selToWire({
    tier: "medium",
    status: "proposed",
    attention: true,
    q: "beach",
    group: "2019-07",
    sort: "date",
    mode: "attention",
  });
  /* `group` is the controller's M14 Task 3 ruling (slice 3d); `sort` and `mode` never go. */
  assert.deepEqual(wire.view, {
    tier: "medium",
    status: "proposed",
    attention: true,
    q: "beach",
    group: "2019-07",
  });
  assert.deepEqual(selToWire({ tier: "", status: "", attention: false, q: "" }).view, {});
  /* The list's own view, in U17's names: the Needs a look tab and the Skipped view. */
  assert.deepEqual(selViewOf({ mode: "attention", tier: "low", q: "", status: "", group: "x" }), {
    attention: true,
    tier: "low",
    group: "x",
  });
  assert.deepEqual(selViewOf({ mode: "all", group: "" }), {});
  assert.deepEqual(selViewOf({ mode: "skipped", status: "proposed" }), { status: "skipped" });
  assert.deepEqual(selViewOf({ mode: "all", status: "proposed", q: "x", sort: "date" }), {
    status: "proposed",
    q: "x",
  });
});

test("a view change keeps the ticked ids and drops the groups, Select all and exceptions", () => {
  selClear();
  selToggleRow({ id: 7, key: "a" });
  selToggleGroup({ key: "b", ids: [8] }, ["a", "b"]);
  selToggleRow({ id: 8, key: "b" });
  selViewChanged();
  assert.deepEqual(selToWire({}), { view: {}, ids: [7] });
  selToggleAll();
  selViewChanged();
  assert.deepEqual(selToWire({}), { view: {}, ids: [7] });
});

/* --- the bar (W13, W14) -------------------------------------------------------------------- */

function previewOf(items, low) {
  return { plan_id: 7, items: items, units: items, low: low, overridden: [], attention: 0 };
}

test("the bar shows only while something is selected, and N is the preview's widened items", async () => {
  await page("2019-07-04", 3, 3, () => previewOf(2, 0));
  assert.equal(el("selection-bar").hidden, true, "an empty selection shows the bar");
  await barToggleRow(LIST.rows[1].item, "2019-07-04", false);
  assert.equal(el("selection-bar").hidden, false);
  /* One row ticked; the server widened it to its unit of two. */
  assert.equal(el("bar-count").textContent, "2 selected");
  assert.deepEqual(posted("selection")[0].body, {
    selection: { view: {}, ids: [1] },
  });
  assert.equal(el("bar-low").hidden, true, "no low name, and a low line");
  await barClear();
  assert.equal(el("selection-bar").hidden, true);
  assert.equal(posted("decide-selection").length, 0, "Clear wrote something");
});

test("the low line reads U3's plural and singular from the preview's low", async () => {
  let low = 2;
  await page("2019-07-04", 3, 3, () => previewOf(3, low));
  await barToggleRow(LIST.rows[1].item, "2019-07-04", false);
  assert.equal(el("bar-low").hidden, false);
  assert.equal(el("bar-low").textContent, LOW_PLURAL);
  low = 1;
  await barToggleRow(LIST.rows[2].item, "2019-07-04", false);
  assert.equal(el("bar-low").textContent, LOW_SINGULAR);
  low = 0;
  await barToggleRow(LIST.rows[3].item, "2019-07-04", false);
  assert.equal(el("bar-low").hidden, true);
});

test("Approve is disabled while a preview is in flight, and sends the low count it showed", async () => {
  let release = null;
  let low = 2;
  await page("2019-07-04", 3, 3, (pathname, body) => {
    if (pathname.endsWith("/decide-selection")) {
      return { decided: 3, low_included: body.expected_low, note: null, counts: null };
    }
    return previewOf(3, low);
  });
  await barToggleRow(LIST.rows[1].item, "2019-07-04", false);
  assert.equal(el("bar-approve").disabled, false);
  /* A second change whose preview has not come back: Approve must send nothing. */
  const shown = routes;
  routes = (pathname, body) =>
    pathname.endsWith("/selection")
      ? new Promise((resolve) => {
          release = () => resolve(previewOf(3, 1));
        })
      : shown(pathname, body);
  const waiting = barToggleRow(LIST.rows[2].item, "2019-07-04", false);
  /* Past the (zero) debounce, so the preview request is out and unanswered. */
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(typeof release, "function", "the preview was never asked for");
  assert.equal(el("bar-approve").disabled, true, "Approve enabled while in flight");
  await barApprove();
  assert.equal(posted("decide-selection").length, 0, "Approve sent while a preview was in flight");
  release();
  await waiting;
  assert.equal(el("bar-approve").disabled, false);
  routes = shown;
  calls = [];
  await barApprove();
  const sent = posted("decide-selection");
  assert.equal(sent.length, 1);
  /* The count shown last is 1 (the preview that came back), so 1 is what is acknowledged. */
  assert.deepEqual(sent[0].body, {
    selection: { view: {}, ids: [1, 2] },
    action: "approve",
    include_low: true,
    expected_low: 1,
  });
  /* With no low name: `expected_low` 0 and no `include_low`. */
  low = 0;
  await barToggleRow(LIST.rows[3].item, "2019-07-04", false);
  calls = [];
  await barApprove();
  assert.deepEqual(posted("decide-selection")[0].body, {
    selection: { view: {}, ids: [3] },
    action: "approve",
    expected_low: 0,
  });
});

test("after Approve the page says U3's sentence from the response's low_included", async () => {
  let included = 2;
  let decided = 5;
  await page("2019-07-04", 3, 3, (pathname) => {
    if (pathname.endsWith("/decide-selection")) {
      return {
        decided: decided,
        low_included: included,
        note: null,
        counts: { total: 3, approved: 3, skipped: 0, remaining: 0, attention: 0 },
      };
    }
    /* The preview said 3 low: the sentence must not use it. */
    return previewOf(3, 3);
  });
  await barToggleRow(LIST.rows[1].item, "2019-07-04", false);
  await barApprove();
  assert.equal(el("list-note").textContent, DONE_PLURAL);
  assert.equal(selIsEmpty(), true, "the selection outlived its approval");
  assert.equal(el("selection-bar").hidden, true);
  assert.deepEqual(LIST.counts, { total: 3, approved: 3, skipped: 0, remaining: 0, attention: 0 });
  /* The list was asked for again. */
  assert.ok(calls.some((each) => each.path === "api/plan/7/list"), "the list was not reloaded");
  included = 1;
  decided = 4;
  await barToggleRow(LIST.rows[1].item, "2019-07-04", false);
  await barApprove();
  assert.equal(el("list-note").textContent, DONE_SINGULAR);
});

test("a 409 asks for the preview again and says the selection changed", async () => {
  let low = 1;
  await page("2019-07-04", 3, 3, (pathname) => {
    if (pathname.endsWith("/decide-selection")) {
      low = 2;
      throw new ApiRefusal("stale", 409, { error: "stale", selection: previewOf(3, 2) });
    }
    return previewOf(3, low);
  });
  await barToggleRow(LIST.rows[1].item, "2019-07-04", false);
  calls = [];
  await barApprove();
  await BAR.previewDone;
  assert.equal(el("bar-note").textContent, CHANGED);
  assert.equal(el("bar-note").hidden, false);
  assert.equal(posted("selection").length, 1, "the preview was not asked for again");
  assert.equal(el("bar-low").textContent, LOW_PLURAL);
  assert.equal(selIsEmpty(), false, "a refused approval cleared the selection");
});

test("the request wrapper keeps a refusal's body, so a 409 can carry the fresh preview", async () => {
  const saved = global.fetch;
  global.fetch = () =>
    Promise.resolve({
      ok: false,
      status: 409,
      text: () => Promise.resolve(JSON.stringify({ error: "stale", selection: { low: 2 } })),
    });
  try {
    /* The page's `api` (core), not the stub. */
    const core = require(path.join(STATIC_DIR, "pam-core.js"));
    let refusal = null;
    try {
      await core.api("api/plan/7/decide-selection", {});
    } catch (failure) {
      refusal = failure;
    }
    assert.ok(refusal instanceof core.ApiRefusal);
    assert.equal(refusal.status, 409);
    assert.equal(refusal.message, "stale");
    assert.deepEqual(refusal.payload, { error: "stale", selection: { low: 2 } });
  } finally {
    global.fetch = saved;
  }
});

test("Skip sends the selection with no low fields, and every bulk write is decide-selection", async () => {
  await page("2019-07-04", 5, 2, (pathname) =>
    pathname.endsWith("/decide-selection")
      ? { decided: 5, low_included: 0, note: null, counts: null }
      : previewOf(5, 2),
  );
  await barToggleGroup("2019-07-04");
  calls = [];
  await barSkip();
  assert.deepEqual(posted("decide-selection")[0].body, {
    selection: { view: {}, groups: ["2019-07-04"] },
    action: "skip",
  });
  await barToggleGroup("2019-07-04");
  await barApprove();
  const writes = calls.filter((each) => /\/(decide|decide-filter)$/.test(each.path));
  assert.deepEqual(writes, [], "a bulk action enumerated ids to decide or decide-filter");
});

test("a row's square, a group's box, Select all and Space drive the selection", async () => {
  await page("2019-07-04", 5, 2, () => previewOf(1, 0));
  const square = byClass(LIST.rows[1].node, "row-square");
  assert.equal(square.length, 1, "a row has no square");
  await Promise.all(square[0].click({ shiftKey: false }));
  await BAR.previewDone;
  assert.deepEqual(posted("selection").pop().body.selection, { view: {}, ids: [1] });
  assert.equal(byClass(LIST.rows[1].node, "row-square")[0].getAttribute("data-state"), "all");
  const box = LIST.byKey["2019-07-04"].box;
  assert.ok(box, "a group has no box");
  await Promise.all(box.click());
  await BAR.previewDone;
  assert.deepEqual(posted("selection").pop().body.selection, {
    view: {},
    groups: ["2019-07-04"],
  });
  assert.equal(box.getAttribute("data-state"), "all");
  el("select-all").click();
  await BAR.previewDone;
  assert.deepEqual(posted("selection").pop().body.selection, { view: {}, all: true });
  assert.equal(el("select-all").getAttribute("aria-pressed"), "true");
  el("select-all").click();
  await BAR.previewDone;
  assert.equal(selIsEmpty(), true);
  /* Space on the row under the keyboard. */
  LIST.cursorId = 2;
  const space = { key: " ", target: makeNode("div"), preventDefault() {} };
  onListKey(space);
  await BAR.previewDone;
  assert.deepEqual(posted("selection").pop().body.selection, { view: {}, ids: [2] });
  /* The Needs a look tab scopes the next preview's view. */
  await setView({ mode: "attention" });
  await BAR.previewDone;
  assert.deepEqual(posted("selection").pop().body.selection, {
    view: { attention: true },
    ids: [2],
  });
});

test("on a stopped page the bar selects nothing and writes nothing", async () => {
  await page("2019-07-04", 3, 3, () => previewOf(1, 0));
  await barToggleRow(LIST.rows[1].item, "2019-07-04", false);
  try {
    PAGE.stopped = true;
    calls = [];
    await barApprove();
    await barSkip();
    await barToggleRow(LIST.rows[2].item, "2019-07-04", false);
    await barToggleAll();
    assert.deepEqual(calls, []);
    assert.deepEqual(selToWire({}), { view: {}, ids: [1] });
  } finally {
    PAGE.stopped = false;
  }
  await barApprove();
  assert.equal(posted("decide-selection").length, 1, "the running page's Approve sent nothing");
});

/* Task 3 review R2 F4: while one `decide-selection` and its reload are in flight, a second
 * Approve or Skip sends nothing; once it settles the bar writes again. */
test("a second Approve or Skip while a write is in flight sends nothing, and the next one after it settles does", async () => {
  await page("2019-07-04", 3, 3, () => previewOf(1, 0));
  await barToggleRow(LIST.rows[1].item, "2019-07-04", false);
  await BAR.previewDone;
  calls = [];
  const first = barApprove();
  const second = barApprove();
  const third = barSkip();
  await Promise.all([first, second, third]);
  assert.equal(posted("decide-selection").length, 1, "a double click wrote twice");
  assert.equal(BAR.writing, false);
  await barToggleRow(LIST.rows[2].item, "2019-07-04", false);
  await BAR.previewDone;
  await barApprove();
  assert.equal(posted("decide-selection").length, 2, "the bar stayed busy after its write settled");
});

/* --- Task 3 review R2 closures (the model's rulings, and the bar's preview races) ----------- */

/* 3c's rulings, each spelled: Select all subsumes the groups AND their exceptions (M2929); Select
 * all off is Clear, ticked ids included (M2930); a group's box speaks for the group, so its rows'
 * own ticks go when it is ticked and nothing of it stays when it is unticked (M2932). */
test("Select all on drops earlier exceptions, Select all off drops ticked ids, and a box off leaves none of its rows", () => {
  selClear();
  const group = { key: "e12", ids: [1, 2] };
  selToggleGroup(group, ["e12"]);
  selToggleRow({ id: 2, key: "e12" });
  selToggleAll();
  assert.deepEqual(selToWire({}), { view: {}, all: true });
  assert.equal(selRowState({ id: 2, key: "e12" }), "all");
  selClear();
  selToggleRow({ id: 7, key: "a" });
  selToggleAll();
  assert.deepEqual(selToWire({}), { view: {}, ids: [7], all: true });
  selToggleAll();
  assert.equal(selIsEmpty(), true, "Select all off kept a ticked id");
  assert.deepEqual(selToWire({}), { view: {} });
  selClear();
  selToggleRow({ id: 1, key: "e12" });
  selToggleGroup(group, ["e12"]);
  assert.deepEqual(selToWire({}), { view: {}, groups: ["e12"] });
  selToggleGroup(group, ["e12"]);
  assert.equal(selRowState({ id: 1, key: "e12" }), "none");
  assert.deepEqual(selToWire({}), { view: {} });
});

/* A stub whose `/selection` answers only when the test says so, in any order. */
function heldPreviews(shown) {
  const held = [];
  routes = (pathname, body) =>
    pathname.endsWith("/selection")
      ? new Promise((resolve) => held.push({ body: body, resolve: resolve }))
      : shown(pathname, body);
  return held;
}

function tick() {
  return new Promise((resolve) => setTimeout(resolve, 10));
}

/* THE CONSTRUCTION (W13: the count shown is the count sent). An older preview that answers
 * while a newer one is out must not enable Approve, and one that answers AFTER the newer one
 * must not replace it - either would send a selection or a low count the bar no longer shows
 * (M2942). */
test("a preview that answers after a newer change is dropped, so the bar and Approve follow the newest", async () => {
  await page("2019-07-04", 3, 3, (pathname) =>
    pathname.endsWith("/decide-selection")
      ? { decided: 3, low_included: 0, note: null, counts: null }
      : previewOf(1, 0),
  );
  const shown = routes;
  const held = heldPreviews(shown);
  const changes = [];
  for (const id of [1, 2, 3]) {
    changes.push(barToggleRow(LIST.rows[id].item, "2019-07-04", false));
    await tick();
  }
  assert.equal(held.length, 3, "three previews were not asked for");
  /* The oldest answers while newer ones are out: Approve stays disabled and sends nothing. */
  held[0].resolve(previewOf(1, 1));
  await changes[0];
  assert.equal(el("bar-approve").disabled, true, "an old preview enabled Approve");
  await barApprove();
  assert.equal(posted("decide-selection").length, 0, "Approve sent on an old preview");
  /* The newest answers, and then the middle one arrives late: nothing may move. */
  held[2].resolve(previewOf(3, 0));
  await changes[2];
  held[1].resolve(previewOf(2, 1));
  await changes[1];
  assert.equal(el("bar-count").textContent, "3 selected");
  assert.equal(el("bar-low").hidden, true, "a late preview's low line replaced the newest");
  routes = shown;
  calls = [];
  await barApprove();
  assert.deepEqual(posted("decide-selection")[0].body, {
    selection: { view: {}, ids: [1, 2, 3] },
    action: "approve",
    expected_low: 0,
  });
});

/* A refused preview (a 400) leaves nothing to approve: no count, no low line, Approve disabled
 * and sending nothing, and the refusal's message in the bar (M2943). */
test("a refused preview shows its message and leaves Approve nothing to send", async () => {
  let refuse = false;
  await page("2019-07-04", 3, 3, (pathname) => {
    if (refuse && pathname.endsWith("/selection")) {
      throw new ApiRefusal("q: too long", 400, { error: "q: too long" });
    }
    return pathname.endsWith("/decide-selection")
      ? { decided: 1, low_included: 1, note: null, counts: null }
      : previewOf(1, 1);
  });
  await barToggleRow(LIST.rows[1].item, "2019-07-04", false);
  assert.equal(el("bar-low").textContent, LOW_SINGULAR);
  refuse = true;
  await barToggleRow(LIST.rows[2].item, "2019-07-04", false);
  assert.equal(el("bar-note").textContent, "q: too long");
  assert.equal(el("bar-count").textContent, "");
  assert.equal(el("bar-low").hidden, true);
  assert.equal(el("bar-approve").disabled, true);
  calls = [];
  await barApprove();
  assert.equal(posted("decide-selection").length, 0, "Approve sent the last good preview's count");
});

/* W13: the preview is debounced - two changes inside the delay ask the server once, for the
 * selection after the second (M2944). */
test("two quick changes ask for one preview, of the second", async () => {
  await page("2019-07-04", 3, 3, () => previewOf(2, 0));
  BAR.delay = 30;
  try {
    barToggleRow(LIST.rows[1].item, "2019-07-04", false);
    await barToggleRow(LIST.rows[2].item, "2019-07-04", false);
    await new Promise((resolve) => setTimeout(resolve, 60));
    assert.equal(posted("selection").length, 1, "the preview was not debounced");
    assert.deepEqual(posted("selection")[0].body, { selection: { view: {}, ids: [1, 2] } });
  } finally {
    BAR.delay = 0;
  }
});

/* A preview that arrives after the page stopped does not re-enable Approve (M2946). */
test("a preview that arrives on a stopped page leaves Approve disabled", async () => {
  await page("2019-07-04", 3, 3, () => previewOf(1, 0));
  const shown = routes;
  const held = heldPreviews(shown);
  const change = barToggleRow(LIST.rows[1].item, "2019-07-04", false);
  await tick();
  try {
    PAGE.stopped = true;
    held[0].resolve(previewOf(1, 0));
    await change;
    assert.equal(el("bar-approve").disabled, true, "a stopped page drew Approve enabled");
  } finally {
    PAGE.stopped = false;
    routes = shown;
  }
});

/* Only a 409 that CARRIES a fresh preview is "the selection changed"; any other refusal - a
 * 409 from `_refuse_undecidable` among them - says its own message and asks for nothing (M2949). */
test("a refusal with no fresh preview says its own message, not the selection changed", async () => {
  await page("2019-07-04", 3, 3, (pathname) => {
    if (pathname.endsWith("/decide-selection")) {
      throw new ApiRefusal("Some of these names cannot be decided.", 409, {
        error: "Some of these names cannot be decided.",
      });
    }
    return previewOf(1, 0);
  });
  await barToggleRow(LIST.rows[1].item, "2019-07-04", false);
  calls = [];
  await barApprove();
  await BAR.previewDone;
  assert.equal(el("bar-note").textContent, "Some of these names cannot be decided.");
  assert.equal(posted("selection").length, 0, "a plain refusal asked for the preview again");
});

(async () => {
  let failed = 0;
  for (const [name, body] of tests) {
    try {
      await body();
      console.log("ok " + name);
    } catch (error) {
      failed += 1;
      console.log("FAIL " + name);
      const message = String(error && error.message ? error.message : error);
      console.log("  " + message.replace(/\s+/g, " ").slice(0, 300));
    }
  }
  console.log("ran " + (tests.length - failed));
  process.exit(failed === 0 ? 0 : 1);
})();
