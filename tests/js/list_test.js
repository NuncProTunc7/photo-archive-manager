/*
 * THE REVIEW LIST, EXECUTED (M14 Task 2 Steps 3-4; U2, U15, U25, W2, W5, W7-W10).
 *
 * `pam-row.js` (one row) and `pam-list.js` (the list) are run here under a real `node`, over
 * a small hand-written DOM and a stub `api` that answers the routes the list calls. Nothing
 * is read back out of the scripts to build an expectation: every sentence below is spelled
 * from U2/W7-W10, UX 5 rev 11 and the controller's Task 2 rulings, and every number from
 * the stub's own answer.
 *
 * WHAT IT PINS (the T2 mutations each check is for):
 *   - T2.4: an APPROVED row's circle sends `reset`; an undecided or skipped one `approve`.
 *   - T2.5: the progress line shows the decision RESPONSE's counts, which this test makes
 *     differ from any arithmetic over the rows the page holds.
 *   - T2.6: the no-op row's sentence, verbatim.
 *   - T2.8: the row-copy case list is asserted non-empty, and covering all five U15 codes,
 *     AT LOAD - before any check runs - so an emptied list is a failed run, never a quiet one.
 *   - the needs-a-look note per `note_code`, and paging that shows every id exactly once.
 *
 * `tests/test_webui_list_page.py` runs this file and reads the names it prints: every check
 * prints `ok <name>` or `FAIL <name>` and the run ends with `ran <n>`. A planted copy of
 * either script goes in through `PAM_ROW_JS` / `PAM_LIST_JS`.
 */

"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");

/* --- a small DOM: enough for `make`, attributes, events, and a tag query ------------- */

function makeNode(tag) {
  const node = {
    tagName: String(tag || "div").toUpperCase(),
    children: [],
    parentNode: null,
    attributes: {},
    listeners: {},
    style: {},
    hidden: false,
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
    removeAttribute(name) {
      delete node.attributes[name];
    },
    addEventListener(name, handler) {
      (node.listeners[name] = node.listeners[name] || []).push(handler);
    },
    click() {
      return (node.listeners.click || []).map((handler) => handler());
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

/* The review page's classic scripts share one global scope; node gives each `require` its
 * own, so each earlier script's names go on `global` first - the shared scope reproduced.
 * `lookAlikeLine` (U2: the look-alike line is unchanged copy) is `pam-core.js`'s. */
const STATIC_DIR = path.join(__dirname, "..", "..", "pam", "webui", "static");
Object.assign(global, require(path.join(STATIC_DIR, "pam-core.js")));
const row = require(process.env.PAM_ROW_JS || path.join(STATIC_DIR, "pam-row.js"));
Object.assign(global, row);
const list = require(process.env.PAM_LIST_JS || path.join(STATIC_DIR, "pam-list.js"));

/* --- the copy, spelled from the specs ------------------------------------------------- */

const NOOP_SENTENCE =
  "This file already has the right name — it is here because pam renames a group together.";

/* U15's five codes and the sentence each one puts on an undecided row: W10's three, and the
 * two the controller's Task 2 ruling reuses from shipped wording. */
const NOTE_SENTENCES = {
  file_mtime: "The date came from when the file was last changed, not from the camera. Check it.",
  ocr: "The date was read from the date printed on the photo. Check it matches.",
  none: "No date found anywhere. You can type one in.",
  utc_video: "Video time is UTC, local date may differ.",
  truncated: "Part of this name was shortened to fit.",
};

function card(fields) {
  return Object.assign(
    {
      id: 1,
      file_id: 1,
      old_filename: "IMG_0001.HEIC",
      new_filename: "2019-07-04_001.heic",
      media_type: "image",
      ext: "heic",
      tier: "high",
      trust_label: "High trust",
      edited: false,
      noop: false,
      status: "proposed",
      unit_key: "u1",
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

/* THE ROW-COPY CASES (T2.8): each a card, the `rowModel` field, and the string it must be -
 * written here by hand, independently of `ROW_COPY`. */
const ROW_COPY_CASES = [
  ["an undecided row reads Not decided", card({}), "decision", "Not decided"],
  ["an approved row reads Approved", card({ status: "approved" }), "decision", "Approved"],
  [
    "a skipped row reads Skipped, keeps its name",
    card({ status: "skipped" }),
    "decision",
    "Skipped, keeps its name",
  ],
  ["the old name reads was <old>", card({}), "was", "was IMG_0001.HEIC"],
  ["the new name is the served new name", card({}), "name", "2019-07-04_001.heic"],
  ["the label is the server's trust label", card({}), "label", "High trust"],
  ["an edited name is labelled Edited by you", card({ edited: true }), "label", "Edited by you"],
];
for (const code of Object.keys(NOTE_SENTENCES)) {
  ROW_COPY_CASES.push([
    "an undecided " + code + " row carries its note",
    card({ note_code: code, attention: true }),
    "note",
    NOTE_SENTENCES[code],
  ]);
}

/* AT LOAD, before any check: an emptied or shrunk case list fails the whole run. */
assert.ok(ROW_COPY_CASES.length >= 12, "the row-copy case list is empty or cut");
for (const code of ["file_mtime", "ocr", "none", "utc_video", "truncated"]) {
  assert.ok(
    ROW_COPY_CASES.some((each) => each[1].note_code === code),
    "the row-copy cases do not cover " + code,
  );
}

/* --- a stub server --------------------------------------------------------------------- */

let calls = [];
let routes = null;

function query(pathname) {
  return new URLSearchParams(pathname.split("?")[1] || "");
}

global.api = function (pathname, options) {
  const body = options && options.body ? JSON.parse(options.body) : null;
  calls.push({ path: pathname, body: body });
  return Promise.resolve(routes(pathname, body));
};

function fresh(planId, counts) {
  /* A header refresh a previous check scheduled must not land in this one's calls. */
  clearTimeout(list.LIST.refreshTimer);
  list.LIST.refreshTimer = null;
  calls = [];
  list.LIST.planId = planId;
  list.LIST.counts = counts || null;
  list.LIST.attentionTodo = null;
  list.LIST.summary = null;
  list.LIST.view = { mode: "all", tier: "", group: "" };
  list.LIST.groups = [];
  list.LIST.byKey = {};
  list.LIST.rows = {};
  clear(document.getElementById("list"));
}

/* A plan of one list group holding `items`, served in order and sliced by the query. */
function serveGroup(key, items, extra) {
  routes = function (pathname, body) {
    if (pathname.indexOf("/decide") !== -1) {
      return extra.decide(body);
    }
    if (pathname.split("?")[0].endsWith("/list")) {
      return {
        groups: [
          {
            key: key,
            title: "July 4, 2019",
            date_line: "July 4, 2019",
            total: items.length,
            undecided: items.length,
            attention_undecided: 0,
            all_low: false,
          },
        ],
        /* M14 Task 3: `/list`'s top-level summary, the callout's two numbers. */
        undecided: items.length,
        attention_undecided: extra.attentionTodo || 0,
        shortcut_count: extra.other || 0,
      };
    }
    if (pathname.endsWith("/groups")) {
      return { groups: [] };
    }
    const params = query(pathname);
    if (params.get("limit") === "0") {
      return { total: extra.attentionTodo || 0, items: [] };
    }
    const offset = Number(params.get("offset"));
    const limit = Number(params.get("limit"));
    return { total: items.length, items: items.slice(offset, offset + limit) };
  };
}

function rowNode(itemId) {
  return list.LIST.rows[itemId].node;
}

function circleOf(itemId) {
  return byClass(rowNode(itemId), "row-circle")[0];
}

function settle() {
  return new Promise((resolve) => setImmediate(resolve));
}

const tests = [];
function test(name, body) {
  tests.push([name, body]);
}

/* --- the row ---------------------------------------------------------------------------- */

test("every row-copy case reads its spelled string", () => {
  for (const [name, item, field, expected] of ROW_COPY_CASES) {
    assert.equal(row.rowModel(item)[field], expected, name);
  }
});

test("each note code gives its sentence while undecided and none once decided", () => {
  for (const code of Object.keys(NOTE_SENTENCES)) {
    assert.equal(row.rowModel(card({ note_code: code })).note, NOTE_SENTENCES[code], code);
    for (const status of ["approved", "skipped"]) {
      assert.equal(row.rowModel(card({ note_code: code, status: status })).note, null, code);
    }
  }
  assert.equal(row.rowModel(card({ note_code: null })).note, null);
  assert.equal(row.rowModel(card({ note_code: "a code pam never sends" })).note, null);
});

test("a no-op member of a group carries UX 5 rev 11's sentence verbatim", () => {
  const item = card({ noop: true, unit_size: 2 });
  assert.equal(row.rowModel(item).noop, NOOP_SENTENCE);
  const lines = byClass(row.buildRow(item, () => {}), "row-noop");
  assert.equal(lines.length, 1);
  assert.equal(lines[0].textContent, NOOP_SENTENCE);
  /* The sentence is about a group: a lone file never says it (M10 Task 1, C1). */
  assert.equal(row.rowModel(card({ noop: true, unit_size: 1 })).noop, null);
  assert.equal(byClass(row.buildRow(card({}), () => {}), "row-noop").length, 0);
});

test("the look-alike line is UX 5's unchanged sentence", () => {
  const item = card({ look_alikes: ["2019/a.jpg"], look_alikes_more: 0 });
  assert.equal(row.rowModel(item).alike, "Looks like 1 other photo in this plan: 2019/a.jpg.");
  assert.equal(row.rowModel(card({})).alike, null);
});

/* W9, spelled: the kind is the SERVER's `unit_kind` (the scanner's group kind), never the
 * files' extensions - so a `.MOV` named with `raw_jpeg` reads RAW + JPEG, because that is
 * what the catalog said. */
test("the unit disclosure reads the server's kind and count, and a lone file says nothing", () => {
  const cases = [
    [card({ unit_size: 2, unit_kind: "live_photo" }), "Live Photo · 2 files"],
    [card({ unit_size: 2, unit_kind: "raw_jpeg" }), "RAW + JPEG · 2 files"],
    [card({ unit_size: 2, unit_kind: "sidecar" }), "2 files"],
    [card({ unit_size: 3, unit_kind: null }), "3 files"],
    [
      card({ unit_size: 2, unit_kind: "raw_jpeg", old_filename: "IMG_9.MOV", ext: "mov" }),
      "RAW + JPEG · 2 files",
    ],
  ];
  assert.equal(cases.length, 5);
  for (const [item, expected] of cases) {
    assert.equal(row.rowModel(item).unit, expected, expected);
    const drawn = byClass(row.buildRow(item, () => {}), "row-unit");
    assert.equal(drawn.length, 1, expected);
    assert.equal(drawn[0].textContent, expected);
  }
  /* A lone file: nothing, whatever kind a stray field claims. */
  const lone = card({ unit_size: 1, unit_kind: "live_photo" });
  assert.equal(row.rowModel(lone).unit, null);
  assert.equal(byClass(row.buildRow(lone, () => {}), "row-unit").length, 0);
});

test("a video row draws the video glyph and never asks for a thumbnail", () => {
  const node = row.buildRow(card({ media_type: "video", ext: "mov", has_thumb: true }), () => {});
  const glyphs = byClass(node, "glyph");
  assert.equal(glyphs.length, 1);
  assert.equal(glyphs[0].getAttribute("data-kind"), "video");
  assert.equal(node.querySelectorAll("img").length, 0);
});

test("a thumbnail that opens the preview is a named control Tab and Enter reach (HT-M14 F4)", () => {
  for (const item of [card({}), card({ media_type: "video", ext: "mov" })]) {
    const opened = [];
    const node = row.buildRow(item, () => {}, (it) => opened.push(it.item_id));
    const [thumb] = byClass(node, "row-thumb");
    assert.equal(thumb.getAttribute("role"), "button");
    assert.equal(thumb.getAttribute("tabindex"), "0");
    assert.equal(thumb.getAttribute("aria-label"), "Show a larger preview of " + item.old_filename);
    let prevented = 0;
    const press = (key) =>
      (thumb.listeners.keydown || []).forEach((handler) =>
        handler({ key, preventDefault: () => (prevented += 1) })
      );
    press("Enter");
    press(" ");
    press("a");
    assert.equal(opened.length, 2, "Enter and Space open the preview; another key does not");
    assert.equal(prevented, 2);
    thumb.click();
    assert.equal(opened.length, 3, "a click still opens it");
  }
  /* With no preview to open, the thumbnail stays a picture, not a control. */
  const [plain] = byClass(row.buildRow(card({}), () => {}), "row-thumb");
  assert.equal(plain.getAttribute("role"), null);
  assert.equal(plain.getAttribute("tabindex"), null);
});

/* HT-M14 fix pass M3514: a thumbnail that fails to load is replaced by the glyph, and the glyph
 * must still open the preview - or the row loses F4's control the moment an image breaks. */
test("a thumbnail that fails to load is replaced by a glyph that still opens the preview", () => {
  const opened = [];
  const item = card({ has_thumb: true });
  const node = row.buildRow(item, () => {}, (it) => opened.push(it.item_id));
  const [image] = node.querySelectorAll("img");
  assert.ok(image, "the row asks for a thumbnail");
  (image.listeners.error || []).forEach((handler) => handler());
  const [glyph] = byClass(node, "row-thumb");
  assert.equal(glyph.querySelectorAll("img").length, 0, "the broken image is gone");
  assert.equal(byClass(glyph, "glyph").length, 1);
  assert.equal(glyph.getAttribute("role"), "button");
  assert.equal(glyph.getAttribute("tabindex"), "0");
  glyph.click();
  assert.deepEqual(opened, [item.item_id]);
});

/* M14 Task 2 review R2 (M2869-M2873): `rowModel` was executed and `buildRow` was not - the
 * row could stop DRAWING its `was` line, its decision word, its label, the look-alike line
 * or W10's note with every check green. Each line's class and text, spelled here from W7,
 * W8, W10 and UX 5 (never read from `ROW_COPY`), and each note gone once decided. */
test("the row draws its was line, label, decision word, look-alike line and note", () => {
  const text = (node, className) => byClass(node, className).map((each) => each.textContent);
  const undecided = row.buildRow(
    card({ note_code: "ocr", attention: true, look_alikes: ["2019/a.jpg"] }),
    () => {},
  );
  assert.deepEqual(text(undecided, "row-was"), ["was IMG_0001.HEIC"]);
  assert.deepEqual(text(undecided, "row-label"), ["High trust"]);
  assert.deepEqual(text(undecided, "row-decision"), ["Not decided"]);
  assert.deepEqual(text(undecided, "row-alike"), [
    "Looks like 1 other photo in this plan: 2019/a.jpg.",
  ]);
  assert.deepEqual(text(undecided, "row-note"), [
    "The date was read from the date printed on the photo. Check it matches.",
  ]);
  const cases = [
    ["approved", "Approved"],
    ["skipped", "Skipped, keeps its name"],
  ];
  assert.equal(cases.length, 2);
  for (const [status, word] of cases) {
    const decided = row.buildRow(card({ status: status, note_code: "ocr" }), () => {});
    assert.deepEqual(text(decided, "row-decision"), [word], status);
    assert.deepEqual(text(decided, "row-note"), [], status + " still shows the note");
  }
  const edited = row.buildRow(card({ edited: true }), () => {});
  assert.deepEqual(text(edited, "row-label"), ["Edited by you"]);
  assert.deepEqual(text(row.buildRow(card({}), () => {}), "row-note"), []);
});

test("the circle's action is reset from approved and approve from proposed or skipped", () => {
  assert.equal(row.circleAction(card({ status: "approved" })), "reset");
  assert.equal(row.circleAction(card({ status: "proposed" })), "approve");
  assert.equal(row.circleAction(card({ status: "skipped" })), "approve");
});

/* --- the list --------------------------------------------------------------------------- */

test("an approved row's circle sends reset through the existing decide", async () => {
  const items = [
    card({ id: 1, status: "approved" }),
    card({ id: 2, status: "proposed" }),
    card({ id: 3, status: "skipped" }),
  ];
  const counts = { total: 3, approved: 1, skipped: 1, remaining: 1, attention: 0 };
  fresh(7, counts);
  serveGroup("2019-07-04", items, {
    decide: () => ({ companions: [], note: null, counts: counts }),
  });
  await list.setView({});
  for (const [id, action] of [
    [1, "reset"],
    [2, "approve"],
    [3, "approve"],
  ]) {
    calls = [];
    await Promise.all(circleOf(id).click());
    const posted = calls.filter((each) => each.path === "api/plan/7/decide");
    assert.equal(posted.length, 1, "row " + id);
    assert.deepEqual(posted[0].body, { action: action, item_ids: [id] }, "row " + id);
  }
});

test("the progress line shows the decision response's counts, not local arithmetic", async () => {
  const items = [card({ id: 1 }), card({ id: 2 })];
  /* Before: 2 of 10 approved. One more approve would make a LOCAL tally 3 of 10; the server
   * answers 6 (other decisions it knows about), and the page must say 6. */
  fresh(7, { total: 10, approved: 2, skipped: 1, remaining: 7, attention: 0 });
  serveGroup("2019-07-04", items, {
    decide: () => ({
      companions: [],
      note: null,
      counts: { total: 10, approved: 6, skipped: 1, remaining: 3, attention: 0 },
    }),
  });
  await list.setView({});
  await Promise.all(circleOf(1).click());
  assert.equal(document.getElementById("progress-approved").textContent, "6 of 10 names approved");
  assert.equal(document.getElementById("progress-rest").textContent, "1 skipped · 3 still to decide");
  assert.equal(document.getElementById("tab-all").textContent, "All 10");
});

test("a decision that widened to a companion repaints the partner's row", async () => {
  const items = [card({ id: 1, unit_size: 2 }), card({ id: 2, unit_size: 2 })];
  const counts = { total: 2, approved: 2, skipped: 0, remaining: 0, attention: 0 };
  fresh(7, counts);
  serveGroup("2019-07-04", items, {
    decide: () => ({ companions: [2], note: "This also approved 1 other file.", counts: counts }),
  });
  await list.setView({});
  await Promise.all(circleOf(1).click());
  assert.equal(rowNode(2).getAttribute("data-status"), "approved");
  assert.equal(circleOf(2).getAttribute("data-action"), "reset");
  assert.equal(document.getElementById("list-note").textContent, "This also approved 1 other file.");
});

test("Show N more pages through a group and shows every id exactly once", async () => {
  const size = list.LIST_FIRST_ROWS + 2 * list.LIST_PAGE + 1;
  const items = [];
  for (let id = 1; id <= size; id += 1) {
    items.push(card({ id: id }));
  }
  fresh(7, { total: size, approved: 0, skipped: 0, remaining: size, attention: 0 });
  serveGroup("e12", items, { decide: () => null });
  await list.setView({});
  const entry = list.LIST.byKey.e12;
  const seen = ["Show " + list.LIST_PAGE + " more"];
  assert.equal(entry.more.textContent, seen[0]);
  let clicks = 0;
  while (!entry.more.hidden) {
    clicks += 1;
    assert.ok(clicks <= 10, "Show N more never finished");
    await Promise.all(entry.more.click());
    if (!entry.more.hidden) {
      seen.push(entry.more.textContent);
    }
  }
  /* first rows, then a full page, then the one left over. */
  assert.deepEqual(seen, [
    "Show " + list.LIST_PAGE + " more",
    "Show " + list.LIST_PAGE + " more",
    "Show 1 more",
  ]);
  const ids = entry.rows.children.map((each) => Number(each.getAttribute("data-item-id")));
  assert.equal(ids.length, size);
  assert.equal(new Set(ids).size, size, "a row was shown twice");
  const offsets = calls
    .filter((each) => each.path.indexOf("list_group=e12") !== -1)
    .map((each) => Number(query(each.path).get("offset")));
  const first = list.LIST_FIRST_ROWS;
  assert.deepEqual(offsets, [0, first, first + list.LIST_PAGE, first + 2 * list.LIST_PAGE]);
});

test("the progress line and the group header read the specs' words", () => {
  const text = list.progressText({ total: 923, approved: 812, skipped: 15, remaining: 96 });
  assert.equal(text.approved, "812 of 923 names approved");
  assert.equal(text.rest, "15 skipped · 96 still to decide");
  assert.equal(list.groupCountText({ total: 12, attention_undecided: 3 }), "12 files · 3 need a look");
  assert.equal(list.groupCountText({ total: 1, attention_undecided: 0 }), "1 file");
  assert.equal(list.showMoreText(20, 20), null);
});

test("the tabs read All N and Needs a look K, K from the server's undecided set", async () => {
  fresh(7, null);
  serveGroup("2019-07-04", [card({ id: 1 })], { decide: () => null, attentionTodo: 4 });
  await list.startList(7, { total: 10, approved: 0, skipped: 0, remaining: 10, attention: 9 });
  await settle();
  assert.equal(document.getElementById("tab-all").textContent, "All 10");
  assert.equal(document.getElementById("tab-attention").textContent, "Needs a look 4");
  /* Step 6b part 2: Accept all's count asks two sized sets of its own (high, medium), so the
   * tab's request is the one sized request that carries `attention`. */
  const sized = calls.filter(
    (each) => query(each.path).get("limit") === "0" && query(each.path).get("attention") !== null,
  );
  assert.equal(sized.length, 1);
  assert.equal(query(sized[0].path).get("attention"), "1");
  assert.equal(query(sized[0].path).get("status"), "proposed");
});

test("the Needs a look view asks every group for attention items only", async () => {
  fresh(7, { total: 1, approved: 0, skipped: 0, remaining: 1, attention: 1 });
  serveGroup("2019-07-04", [card({ id: 1 })], { decide: () => null });
  await list.setView({ mode: "attention" });
  const pages = calls.filter((each) => each.path.indexOf("list_group=") !== -1);
  assert.equal(pages.length, 1);
  assert.equal(query(pages[0].path).get("attention"), "1");
  assert.equal(query(pages[0].path).get("list_group"), "2019-07-04");
});

/* --- the keys (Step 5; U2, U16), spelled from U2's keyboard bullet ---------------------- */

function key(name, target, mods) {
  const event = Object.assign({ key: name, target: target || makeNode("body") }, mods || {});
  event.prevented = false;
  event.preventDefault = () => {
    event.prevented = true;
  };
  return event;
}

const CLOSED = { shortcuts: false, preview: false, editorField: null };

test("each of U2's keys maps to its action in the list and in the preview", () => {
  const inList = [
    ["ArrowUp", "previous"],
    ["ArrowDown", "next"],
    ["x", "approve"],
    ["X", "approve"],
    ["z", "skip"],
    ["c", "edit"],
    [" ", "select"],
    ["Enter", "preview"],
    ["?", "shortcuts"],
    /* `←/→` belong to the preview; in the list they do nothing. */
    ["ArrowLeft", null],
    ["ArrowRight", null],
    ["Escape", null],
    ["q", null],
  ];
  const inPreview = [
    ["ArrowLeft", "previous"],
    ["ArrowRight", "next"],
    ["Enter", "close-preview"],
    ["Escape", "close-preview"],
    ["x", "approve"],
  ];
  assert.equal(inList.length + inPreview.length, 18);
  for (const [name, expected] of inList) {
    assert.equal(list.listKeyAction(key(name), CLOSED), expected, "list " + name);
  }
  for (const [name, expected] of inPreview) {
    const open = Object.assign({}, CLOSED, { preview: true });
    assert.equal(list.listKeyAction(key(name), open), expected, "preview " + name);
  }
  for (const mod of ["ctrlKey", "altKey", "metaKey"]) {
    assert.equal(list.listKeyAction(key("x", null, { [mod]: true }), CLOSED), null, mod);
  }
});

test("X, Z and C stay inert inside the name field and every other field", () => {
  const field = makeNode("input");
  const open = Object.assign({}, CLOSED, { editorField: field });
  for (const tag of ["input", "textarea", "select"]) {
    const target = tag === "input" ? field : makeNode(tag);
    for (const name of ["x", "X", "z", "c", "C", "Enter", "?", "ArrowDown", " "]) {
      assert.equal(list.listKeyAction(key(name, target), open), null, tag + " " + name);
    }
  }
  /* The name field's own Escape is the one key it gives back; another field's is not. */
  assert.equal(list.listKeyAction(key("Escape", field), open), "close-editor");
  assert.equal(list.listKeyAction(key("Escape", makeNode("input")), open), null);
  /* A focused button owns Enter and Space. */
  for (const name of ["Enter", " "]) {
    assert.equal(list.listKeyAction(key(name, makeNode("button")), CLOSED), null, name);
  }
  /* Through the handler too: typing a name posts nothing and takes no key. */
  calls = [];
  for (const name of ["x", "z", "c"]) {
    const event = key(name, field);
    assert.equal(list.onListKey(event), null);
    assert.equal(event.prevented, false);
  }
  assert.equal(calls.length, 0);
});

test("the open shortcut list takes only its own closing keys", () => {
  const open = Object.assign({}, CLOSED, { shortcuts: true });
  for (const name of ["?", "Escape", "Enter"]) {
    assert.equal(list.listKeyAction(key(name), open), "close-shortcuts", name);
  }
  for (const name of ["x", "z", "ArrowDown"]) {
    assert.equal(list.listKeyAction(key(name), open), null, name);
  }
});

function decided() {
  return calls.filter((each) => each.path === "api/plan/7/decide").map((each) => each.body);
}

test("down moves the cursor, X approves and advances, X again resets and stays", async () => {
  const items = [
    card({ id: 1 }),
    card({ id: 2 }),
    card({ id: 3, status: "approved" }),
    card({ id: 4 }),
  ];
  const counts = { total: 4, approved: 1, skipped: 0, remaining: 3, attention: 0 };
  fresh(7, counts);
  list.LIST.cursorId = null;
  serveGroup("2019-07-04", items, {
    decide: () => ({ companions: [], note: null, counts: counts }),
  });
  await list.setView({});
  list.onListKey(key("ArrowDown"));
  assert.equal(list.LIST.cursorId, 1, "the first key lands on the first row");
  assert.equal(rowNode(1).getAttribute("data-cursor"), "true");
  calls = [];
  assert.equal(list.onListKey(key("x")), "approve");
  await list.LIST.keyDone;
  assert.deepEqual(decided(), [{ action: "approve", item_ids: [1] }]);
  /* Advanced to the next UNDECIDED row: 2, past nothing - and never onto the approved 3. */
  assert.equal(list.LIST.cursorId, 2);
  list.onListKey(key("ArrowDown"));
  list.onListKey(key("ArrowDown"));
  list.onListKey(key("ArrowDown"));
  assert.equal(list.LIST.cursorId, 4, "clamped at the last row");
  list.onListKey(key("ArrowUp"));
  assert.equal(list.LIST.cursorId, 3);
  assert.equal(rowNode(3).getAttribute("data-cursor"), "true");
  assert.equal(rowNode(4).getAttribute("data-cursor"), "false");
  calls = [];
  list.onListKey(key("x"));
  await list.LIST.keyDone;
  assert.deepEqual(decided(), [{ action: "reset", item_ids: [3] }]);
  assert.equal(list.LIST.cursorId, 3, "an un-decide stays on its row");
  assert.equal(rowNode(3).getAttribute("data-cursor"), "true", "the repainted row keeps it");
});

test("Z skips, and Z on a skipped row resets", async () => {
  const items = [card({ id: 1 }), card({ id: 2, status: "skipped" })];
  const counts = { total: 2, approved: 0, skipped: 1, remaining: 1, attention: 0 };
  fresh(7, counts);
  serveGroup("2019-07-04", items, {
    decide: () => ({ companions: [], note: null, counts: counts }),
  });
  await list.setView({});
  list.LIST.cursorId = 1;
  calls = [];
  list.onListKey(key("z"));
  await list.LIST.keyDone;
  assert.deepEqual(decided(), [{ action: "skip", item_ids: [1] }]);
  assert.equal(rowNode(1).getAttribute("data-status"), "skipped");
  list.LIST.cursorId = 2;
  calls = [];
  list.onListKey(key("Z"));
  await list.LIST.keyDone;
  assert.deepEqual(decided(), [{ action: "reset", item_ids: [2] }]);
});

/* M14 Task 3 (U3): Space selects the row under the keyboard - it hands the row to the
 * selection's hook, as the row's square does, and takes the key from the browser. */
test("Space hands the row to the selection, and Enter, C and ? hand the row to their surfaces", async () => {
  fresh(7, { total: 1, approved: 0, skipped: 0, remaining: 1, attention: 0 });
  serveGroup("2019-07-04", [card({ id: 5 })], { decide: () => null });
  await list.setView({});
  list.LIST.cursorId = 5;
  const handed = [];
  const saved = list.LIST.hooks;
  list.LIST.hooks = {
    preview: (item) => handed.push(["preview", item.id]),
    edit: (item) => handed.push(["edit", item.id]),
    shortcuts: () => handed.push(["shortcuts"]),
    select: (item) => handed.push(["select", item.id]),
  };
  try {
    calls = [];
    const space = key(" ");
    assert.equal(list.onListKey(space), "select");
    assert.equal(space.prevented, true, "Space is taken from the browser, not left to scroll");
    list.onListKey(key("Enter"));
    list.onListKey(key("c"));
    list.onListKey(key("?"));
    assert.deepEqual(handed, [["select", 5], ["preview", 5], ["edit", 5], ["shortcuts"]]);
    assert.equal(calls.length, 0, "no key but X and Z writes");
  } finally {
    list.LIST.hooks = saved;
  }
});

/* --- Accept all (Step 6a; U2, U3) ------------------------------------------------------- */

test("Accept all reloads the list, and every count it shows is the server's", async () => {
  /* Before: 2 of 10 approved, one group of two undecided rows. Accept all approves 3 and
   * holds 1 Low back; LOCAL arithmetic would say 2 + 3 = 5 approved. The server says 6 -
   * other decisions it knows about - and its reloaded group header says 2 still need a look. */
  fresh(7, { total: 10, approved: 2, skipped: 0, remaining: 8, attention: 3 });
  let listAsked = 0;
  let accepted = false;
  routes = function (pathname, body) {
    if (pathname.endsWith("/decide-filter")) {
      accepted = true;
      return {
        decided: 3,
        excluded_low: 1,
        note: "",
        counts: { total: 10, approved: 6, skipped: 0, remaining: 4, attention: 3 },
      };
    }
    if (pathname.split("?")[0].endsWith("/list")) {
      listAsked += 1;
      return {
        groups: [
          {
            key: "2019-07-04",
            title: "July 4, 2019",
            date_line: "July 4, 2019",
            total: 2,
            undecided: accepted ? 1 : 2,
            attention_undecided: accepted ? 2 : 0,
            all_low: false,
          },
        ],
      };
    }
    const params = query(pathname);
    if (params.get("limit") === "0") {
      return { total: accepted ? 5 : 3, items: [] };
    }
    const status = accepted ? "approved" : "proposed";
    return { total: 2, items: [card({ id: 1, status: status }), card({ id: 2, status: "proposed" })] };
  };
  await list.setView({});
  const before = listAsked;
  calls = [];
  await list.runAcceptAll();
  const posted = calls.filter((each) => each.path === "api/plan/7/decide-filter");
  assert.equal(posted.length, 1);
  assert.deepEqual(posted[0].body, { action: "approve", accept_all: true });
  /* The progress line is the response's: 6, never 5. */
  assert.equal(document.getElementById("progress-approved").textContent, "6 of 10 names approved");
  assert.equal(document.getElementById("progress-rest").textContent, "0 skipped · 4 still to decide");
  /* The groups were asked for again, and drawn from the new answer. */
  assert.equal(listAsked, before + 1, "the list was not reloaded from the server");
  const entry = list.LIST.byKey["2019-07-04"];
  assert.equal(entry.count.textContent, "2 files · 2 need a look");
  assert.equal(rowNode(1).getAttribute("data-status"), "approved");
  /* The Needs a look count is the server's undecided set again. */
  assert.equal(document.getElementById("tab-attention").textContent, "Needs a look 5");
  /* F1 R2's sentence, over the server's numbers. */
  assert.equal(
    document.getElementById("list-note").textContent,
    "Accepted 3. 1 low-confidence name(s) were not accepted - approve them one at a time.",
  );
});

/* --- moved from the card page (Step 6b part 2; UX 5, 5a) -------------------------------- */

const PASS_COMPLETE = "Nothing left undecided in this view — pass complete.";

test("search and sort reach every group's items request, and clear filters drops the search", async () => {
  fresh(7, { total: 1, approved: 0, skipped: 0, remaining: 1, attention: 0 });
  serveGroup("2019-07-04", [card({ id: 1 })], { decide: () => null });
  list.LIST.view = { mode: "all", tier: "", group: "", q: "", sort: "trust_asc", status: "" };
  calls = [];
  await list.setView({ q: "beach" });
  let pages = calls.filter((each) => each.path.indexOf("list_group=") !== -1);
  assert.equal(pages.length, 1);
  assert.equal(query(pages[0].path).get("q"), "beach");
  assert.equal(query(pages[0].path).get("sort"), "trust_asc");
  calls = [];
  await list.setView({ sort: "date" });
  pages = calls.filter((each) => each.path.indexOf("list_group=") !== -1);
  assert.equal(query(pages[0].path).get("sort"), "date");
  assert.equal(query(pages[0].path).get("q"), "beach");
  calls = [];
  await list.clearListFilters();
  pages = calls.filter((each) => each.path.indexOf("list_group=") !== -1);
  assert.equal(query(pages[0].path).get("q"), null, "clear filters left the search on");
  assert.equal(query(pages[0].path).get("sort"), "date", "clear filters moved the sort");
  /* UX 5a: the attention view sorts lowest trust first. */
  calls = [];
  await list.setView({ mode: "attention" });
  pages = calls.filter((each) => each.path.indexOf("list_group=") !== -1);
  assert.equal(query(pages[0].path).get("sort"), "trust_asc");
});

test("a view the server leaves empty shows UX 5's empty state, and one with rows hides it", async () => {
  fresh(7, { total: 1, approved: 0, skipped: 0, remaining: 1, attention: 0 });
  serveGroup("2019-07-04", [], { decide: () => null });
  await list.setView({});
  assert.equal(document.getElementById("empty-state").hidden, false);
  serveGroup("2019-07-04", [card({ id: 1 })], { decide: () => null });
  await list.setView({});
  assert.equal(document.getElementById("empty-state").hidden, true);
});

test("the view's last undecided decision reports the pass complete and leaves the row", async () => {
  const counts = { total: 2, approved: 1, skipped: 0, remaining: 1, attention: 0 };
  fresh(7, counts);
  let left = 1;
  serveGroup("2019-07-04", [card({ id: 1 }), card({ id: 2, status: "approved" })], {
    decide: () => {
      left = 0;
      return { companions: [], note: null, counts: counts };
    },
    get attentionTodo() {
      return left;
    },
  });
  await list.setView({});
  list.LIST.cursorId = 1;
  calls = [];
  list.onListKey(key("x"));
  await list.LIST.keyDone;
  /* Asked the SERVER, for this view's undecided set, before saying so. */
  const asked = calls.filter((each) => query(each.path).get("limit") === "0");
  assert.equal(asked.length, 1);
  assert.equal(query(asked[0].path).get("status"), "proposed");
  assert.equal(list.LIST.cursorId, null, "focus stayed on the row just decided");
  assert.equal(rowNode(1).getAttribute("data-cursor"), "false");
  assert.equal(document.getElementById("list-note").textContent, PASS_COMPLETE);
  /* The repeated X is inert now: nothing is reset. */
  calls = [];
  list.onListKey(key("x"));
  await list.LIST.keyDone;
  assert.deepEqual(decided(), []);
});

test("undecided rows in pages not loaded yet keep the pass open", async () => {
  const counts = { total: 30, approved: 0, skipped: 0, remaining: 29, attention: 0 };
  fresh(7, counts);
  serveGroup("2019-07-04", [card({ id: 1 })], {
    decide: () => ({ companions: [], note: null, counts: counts }),
    attentionTodo: 29,
  });
  await list.setView({});
  list.LIST.cursorId = 1;
  showNoteReset();
  list.onListKey(key("x"));
  await list.LIST.keyDone;
  assert.equal(list.LIST.cursorId, 1);
  assert.notEqual(document.getElementById("list-note").textContent, PASS_COMPLETE);
});

function showNoteReset() {
  document.getElementById("list-note").textContent = "";
  document.getElementById("list-note").hidden = true;
}

test("the done panel shows only when nothing is undecided, from the server's counts", async () => {
  fresh(4, { total: 3, approved: 2, skipped: 0, remaining: 1, attention: 0 });
  list.renderProgress();
  const panel = document.getElementById("done-panel");
  assert.equal(panel.hidden, true);
  list.LIST.counts = { total: 3, approved: 2, skipped: 1, remaining: 0, attention: 0 };
  list.renderProgress();
  assert.equal(panel.hidden, false);
  /* M14 Task 6c (ruling B5): W16's second sentence, in place of "until you run: pam apply N". */
  assert.equal(
    panel.children[0].textContent,
    "Every name in this plan is decided — 2 approved, 1 skipped. Nothing is renamed until you confirm.",
  );
  assert.equal(panel.children.length, 1, "the panel still names a console command");
});

const W3 = "4 names need a look. pam isn't sure about these names. Everything else looks right.";
const W4 = "Nothing needs a look. Rename when you're ready, or keep checking names.";

test("the callout reads W3 with OD-3's sentence, and its button opens the Needs a look tab", async () => {
  fresh(7, null);
  serveGroup("2019-07-04", [card({ id: 1 })], {
    decide: () => null,
    attentionTodo: 4,
    other: 6,
  });
  await list.startList(7, { total: 10, approved: 0, skipped: 0, remaining: 10, attention: 9 });
  await settle();
  assert.equal(document.getElementById("look-callout").hidden, false);
  assert.equal(document.getElementById("look-callout-text").textContent, W3);
  const button = document.getElementById("look-callout-button");
  assert.equal(button.hidden, false);
  assert.equal(button.textContent, "Look at the 4 first");
  /* W3's second button (OD-1), N from `/list`'s top-level `shortcut_count`. */
  const other = document.getElementById("look-callout-other");
  assert.equal(other.hidden, false);
  assert.equal(other.textContent, "Approve the other 6");
  await Promise.all(button.click());
  assert.equal(list.LIST.view.mode, "attention");
  assert.equal(document.getElementById("tab-attention").getAttribute("aria-pressed"), "true");
});

/* W3's singular: spelled here from U8's W3 and the Task 2 ruling, never read from LIST_COPY. */
test("the callout reads W3's singular for one name and its plural for two", () => {
  const counts = { total: 10, approved: 0, skipped: 0, remaining: 10, attention: 2 };
  const one = list.calloutText(counts, 1);
  assert.equal(
    one.text,
    "1 name needs a look. pam isn't sure about these names. Everything else looks right.",
  );
  assert.equal(one.button, "Look at the 1 first");
  const two = list.calloutText(counts, 2);
  assert.equal(
    two.text,
    "2 names need a look. pam isn't sure about these names. Everything else looks right.",
  );
  assert.equal(two.button, "Look at the 2 first");
});

test("the callout reads W4 when nothing undecided needs a look, and is gone when all is decided", () => {
  fresh(7, { total: 10, approved: 3, skipped: 0, remaining: 7, attention: 2 });
  list.LIST.summary = { undecided: 7, attention_undecided: 0, shortcut_count: 7 };
  list.renderProgress();
  assert.equal(document.getElementById("look-callout").hidden, false);
  assert.equal(document.getElementById("look-callout-text").textContent, W4);
  assert.equal(document.getElementById("look-callout-button").hidden, true);
  /* W4 has no button at all - W3's `Approve the other N` included. */
  assert.equal(document.getElementById("look-callout-other").hidden, true);
  list.LIST.counts = { total: 10, approved: 10, skipped: 0, remaining: 0, attention: 2 };
  list.renderProgress();
  assert.equal(document.getElementById("look-callout").hidden, true);
  /* Before the server's undecided count arrives there is no callout at all. */
  list.LIST.counts = { total: 10, approved: 3, skipped: 0, remaining: 7, attention: 2 };
  list.LIST.summary = null;
  list.renderProgress();
  assert.equal(document.getElementById("look-callout").hidden, true);
});

test("the resume banner reads UX 5 rev 8's line and Jump lands on the first undecided row", async () => {
  const items = [
    card({ id: 1, status: "approved" }),
    card({ id: 2, status: "skipped" }),
    card({ id: 3 }),
    card({ id: 4 }),
  ];
  fresh(7, { total: 4, approved: 1, skipped: 1, remaining: 2, attention: 0 });
  serveGroup("2019-07-04", items, { decide: () => null });
  await list.setView({});
  list.renderProgress();
  const banner = document.getElementById("resume-banner");
  assert.equal(banner.hidden, false);
  assert.equal(document.getElementById("resume-text").textContent, "1 approved, 2 remaining");
  await list.jumpToFirstUndecided();
  assert.equal(list.LIST.cursorId, 3);
  assert.equal(banner.hidden, true);
  /* No decision yet: no banner (no greeting, ever). */
  list.LIST.counts = { total: 4, approved: 0, skipped: 0, remaining: 4, attention: 0 };
  list.renderProgress();
  assert.equal(banner.hidden, true);
});

test("Jump with no undecided row loaded narrows the view to the undecided", async () => {
  fresh(7, { total: 30, approved: 1, skipped: 0, remaining: 29, attention: 0 });
  list.LIST.view = { mode: "all", tier: "", group: "", q: "", sort: "trust_asc", status: "" };
  serveGroup("2019-07-04", [card({ id: 1, status: "approved" })], { decide: () => null });
  await list.setView({});
  serveGroup("2019-07-04", [card({ id: 9 })], { decide: () => null });
  calls = [];
  await list.jumpToFirstUndecided();
  const pages = calls.filter((each) => each.path.indexOf("list_group=") !== -1);
  assert.equal(pages.length, 1);
  assert.equal(query(pages[0].path).get("status"), "proposed");
  assert.equal(list.LIST.cursorId, 9);
  calls = [];
  await list.clearListFilters();
  const after = calls.filter((each) => each.path.indexOf("list_group=") !== -1);
  assert.equal(query(after[0].path).get("status"), null, "clear filters kept the undecided view");
});

test("the estimate stays hidden until ten paced decisions and then reads at your pace", async () => {
  assert.equal(list.estimateText(new Array(9).fill(30), 10), null);
  assert.equal(list.estimateText(new Array(10).fill(30), 10), "≈5 min at your pace");
  /* The median: one interruption does not move it. */
  assert.equal(list.estimateText(new Array(9).fill(30).concat([3000]), 10), "≈5 min at your pace");
  assert.equal(list.estimateText(new Array(10).fill(30), 0), null);
  fresh(7, { total: 10, approved: 0, skipped: 0, remaining: 10, attention: 0 });
  list.LIST.pace = new Array(10).fill(30);
  list.renderProgress();
  const estimate = document.getElementById("estimate");
  assert.equal(estimate.hidden, false);
  assert.equal(estimate.textContent, "≈5 min at your pace");
  /* A one-row decision is a pace sample. */
  const counts = { total: 2, approved: 1, skipped: 0, remaining: 1, attention: 0 };
  fresh(7, counts);
  list.LIST.pace = [];
  list.LIST.lastDecisionAt = null;
  serveGroup("2019-07-04", [card({ id: 1 }), card({ id: 2 })], {
    decide: () => ({ companions: [], note: null, counts: counts }),
  });
  await list.setView({});
  await list.decideRow(list.LIST.rows[1].item);
  await list.decideRow(list.LIST.rows[2].item);
  assert.equal(list.LIST.pace.length, 1);
  list.LIST.pace = [];
  list.LIST.lastDecisionAt = null;
});

test("Accept all's count is the plan's undecided high plus medium, from the server", async () => {
  fresh(7, null);
  list.LIST.view = { mode: "attention", tier: "low", group: "", q: "beach", sort: "date", status: "" };
  routes = function (pathname) {
    const params = query(pathname);
    return { total: { high: 7, medium: 5 }[params.get("tier")] || 0, items: [] };
  };
  await list.refreshAcceptAllCount();
  assert.equal(document.getElementById("accept-all-count").textContent, "(12)");
  assert.equal(calls.length, 2);
  for (const each of calls) {
    const params = query(each.path);
    assert.equal(params.get("status"), "proposed");
    assert.equal(params.get("limit"), "0");
    assert.equal(params.get("q"), null, "the count followed the view's search");
    assert.equal(params.get("attention"), null, "the count followed the view's tab");
  }
});

test("a dead server shows the list's error banner and the page goes inert", () => {
  /* The shared scope reproduced: `serverGone` (core) reads the list's element map. */
  global.listElements = list.listElements;
  const banner = document.getElementById("error-banner");
  banner.hidden = true;
  try {
    serverGone();
    assert.equal(banner.hidden, false);
    assert.equal(PAGE.stopped, true);
  } finally {
    PAGE.stopped = false;
  }
});

/* M14 Task 2 review R2 (M2871): `pam-keys.js`'s `onKeyDown` is the ONE place a key stops on a
 * page pam has stopped - `onListKey` and `decideAction` do not check - and nothing executed
 * it, so its guard could go with every check green. Loaded as the page loads it (a classic
 * script in the shared scope), driven with `X` on a row. */
test("a key on a stopped page decides nothing, and the same key decides while it runs", async () => {
  const fs = require("node:fs");
  const vm = require("node:vm");
  vm.runInThisContext(fs.readFileSync(path.join(STATIC_DIR, "pam-keys.js"), "utf-8"));
  global.onListKey = list.onListKey;
  global.listElements = list.listElements;
  const counts = { total: 1, approved: 1, skipped: 0, remaining: 0, attention: 0 };
  fresh(7, { total: 1, approved: 0, skipped: 0, remaining: 1, attention: 0 });
  serveGroup("2019-07-04", [card({ id: 5 })], {
    decide: () => ({ companions: [], note: null, counts: counts }),
  });
  await list.setView({});
  list.LIST.cursorId = 5;
  const decides = () => calls.filter((each) => each.path === "api/plan/7/decide").length;
  /* M14 Task 3 (R2 F3): `decideAction` now refuses a stopped page too, so X alone would pass
   * with this guard gone. Enter writes nothing and reaches no `decideAction`: it is the key
   * only `onKeyDown`'s guard stops. */
  const previewed = [];
  const saved = list.LIST.hooks;
  list.LIST.hooks = Object.assign({}, saved, { preview: (item) => previewed.push(item.id) });
  try {
    try {
      serverGone();
      calls = [];
      onKeyDown(key("x"));
      onKeyDown(key("Enter"));
      await settle();
      assert.equal(decides(), 0, "X decided a row on a stopped page");
      assert.deepEqual(previewed, [], "Enter opened the preview on a stopped page");
    } finally {
      PAGE.stopped = false;
    }
    /* The positive half: the same keys on the same row, the page running, do act. */
    onKeyDown(key("Enter"));
    assert.deepEqual(previewed, [5]);
    onKeyDown(key("x"));
    await settle();
    assert.equal(decides(), 1);
  } finally {
    list.LIST.hooks = saved;
  }
});

/* M14 Task 3 (Task 2 review R2 F3): the decide path refuses a stopped page ITSELF - the
 * circle reaches it with no key, so `onKeyDown`'s guard cannot be the only one. */
test("a stopped page's circle decides nothing, because the decide path checks it too", async () => {
  const counts = { total: 1, approved: 1, skipped: 0, remaining: 0, attention: 0 };
  fresh(7, { total: 1, approved: 0, skipped: 0, remaining: 1, attention: 0 });
  serveGroup("2019-07-04", [card({ id: 5 })], {
    decide: () => ({ companions: [], note: null, counts: counts }),
  });
  await list.setView({});
  const decides = () => calls.filter((each) => each.path === "api/plan/7/decide").length;
  try {
    PAGE.stopped = true;
    calls = [];
    await Promise.all(circleOf(5).click());
    await list.decideAction(list.LIST.rows[5].item, "skip");
    assert.equal(decides(), 0, "a stopped page sent a decision");
  } finally {
    PAGE.stopped = false;
  }
  await Promise.all(circleOf(5).click());
  assert.equal(decides(), 1, "the running page's circle no longer decides");
});

/* --- M14 Task 3 slice 3d: the one-tap shortcuts (OD-1, U3, W3, W12) ---------------------- */

/* Three list groups, each spelled: one with a shortcut, one all low, one with nothing the
 * shortcut would take. The top-level `shortcut_count` is NOT the groups' sum (2 + 0 + 0), so
 * a page that added the groups up instead of showing the server's number reads the wrong N. */
function serveShortcutPlan(state) {
  routes = function (pathname, body) {
    if (pathname.indexOf("/decide-filter") !== -1) {
      state.posted.push(body);
      state.after = true;
      return {
        decided: 2,
        excluded_low: 0,
        held: 0,
        note: "",
        counts: { total: 10, approved: 7, skipped: 0, remaining: 3, attention: 3 },
      };
    }
    if (pathname.split("?")[0].endsWith("/list")) {
      state.lists.push(pathname);
      return {
        groups: [
          {
            key: "e12",
            title: "Beach day",
            date_line: "July 4, 2019",
            total: 3,
            undecided: state.after ? 1 : 3,
            attention_undecided: 1,
            shortcut_count: state.after ? 0 : 2,
            all_low: false,
          },
          {
            key: "2019-07-05",
            title: "July 5, 2019",
            date_line: "July 5, 2019",
            total: 1,
            undecided: 1,
            attention_undecided: 1,
            shortcut_count: 0,
            all_low: true,
          },
          {
            key: "undated",
            title: "No date found",
            date_line: "",
            total: 1,
            undecided: 1,
            attention_undecided: 1,
            shortcut_count: 0,
            all_low: false,
          },
        ],
        undecided: state.after ? 3 : 5,
        attention_undecided: 3,
        shortcut_count: state.after ? 0 : 9,
      };
    }
    if (pathname.endsWith("/groups")) {
      return { groups: [] };
    }
    const params = query(pathname);
    if (params.get("limit") === "0") {
      return { total: 3, items: [] };
    }
    return { total: 0, items: [] };
  };
}

function shortcutOf(key) {
  return byClass(list.LIST.byKey[key].section, "group-shortcut")[0];
}

function lowLineOf(key) {
  return byClass(list.LIST.byKey[key].section, "group-low")[0];
}

test("a group shows Approve N from the server, pam isn't sure of these when all low, and neither at 0", async () => {
  fresh(7, { total: 10, approved: 5, skipped: 0, remaining: 5, attention: 3 });
  const state = { posted: [], lists: [], after: false };
  serveShortcutPlan(state);
  await list.setView({});
  assert.equal(shortcutOf("e12").hidden, false);
  assert.equal(shortcutOf("e12").textContent, "Approve 2");
  assert.equal(lowLineOf("e12").hidden, true);
  /* UX 5 (U3): a group made only of low-trust names - the sentence, never a button. */
  assert.equal(shortcutOf("2019-07-05").hidden, true);
  assert.equal(lowLineOf("2019-07-05").hidden, false);
  assert.equal(lowLineOf("2019-07-05").textContent, "pam isn't sure of these");
  /* `shortcut_count` 0 and not all low: no button and no sentence. */
  assert.equal(shortcutOf("undated").hidden, true);
  assert.equal(lowLineOf("undated").hidden, true);
  /* The callout's `Approve the other N` is the server's 9, not the groups' 2. */
  assert.equal(document.getElementById("look-callout-other").textContent, "Approve the other 9");
});

test("a group's Approve N posts 3b's body with its key and the view, then reloads the list and its counts", async () => {
  fresh(7, { total: 10, approved: 5, skipped: 0, remaining: 5, attention: 3 });
  const state = { posted: [], lists: [], after: false };
  serveShortcutPlan(state);
  await list.setView({ mode: "attention", group: "2019-07", q: "beach", tier: "medium" });
  const asked = state.lists.length;
  await list.runShortcut("e12");
  assert.equal(state.posted.length, 1);
  /* 3b's body, spelled: the shortcut, the key, and the view the list was showing - the
   * date tree's `group` included (the controller's ruling), the sort never. */
  assert.deepEqual(state.posted[0], {
    action: "approve",
    shortcut: true,
    list_group: "e12",
    attention: true,
    tier: "medium",
    group: "2019-07",
    q: "beach",
  });
  /* Every number afterwards is the server's: the progress line from the response, the
   * list asked for again, and the group's button gone because the server now says 0. */
  assert.equal(document.getElementById("progress-approved").textContent, "7 of 10 names approved");
  assert.ok(state.lists.length > asked, "the list was not asked for again");
  assert.equal(shortcutOf("e12").hidden, true);
  assert.equal(document.getElementById("look-callout-other").hidden, true);
  assert.equal(document.getElementById("list-note").textContent, "Approved 2.");
});

test("Approve the other N posts the view with no list group, and /list is asked with the view and never the sort", async () => {
  fresh(7, { total: 10, approved: 5, skipped: 0, remaining: 5, attention: 3 });
  const state = { posted: [], lists: [], after: false };
  serveShortcutPlan(state);
  await list.setView({ group: "2019-07-04", sort: "date" });
  const last = state.lists[state.lists.length - 1];
  const params = query(last);
  assert.deepEqual([...params.keys()].sort(), ["group"]);
  assert.equal(params.get("group"), "2019-07-04");
  /* The button is wired (an earlier check may have wired it too, so every post is checked). */
  await Promise.all(document.getElementById("look-callout-other").click());
  await settle();
  assert.ok(state.posted.length >= 1, "Approve the other N posted nothing");
  for (const body of state.posted) {
    assert.deepEqual(body, { action: "approve", shortcut: true, group: "2019-07-04" });
  }
});

test("on a stopped page a shortcut writes nothing", async () => {
  fresh(7, { total: 10, approved: 5, skipped: 0, remaining: 5, attention: 3 });
  const state = { posted: [], lists: [], after: false };
  serveShortcutPlan(state);
  await list.setView({});
  try {
    PAGE.stopped = true;
    await list.runShortcut("e12");
    await list.runShortcut(null);
    assert.equal(state.posted.length, 0, "a stopped page wrote a shortcut");
  } finally {
    PAGE.stopped = false;
  }
  await list.runShortcut("e12");
  assert.equal(state.posted.length, 1, "the running page's shortcut no longer writes");
});

/* Task 3 review R2 F4: a second click while the first write and its reload are in flight sends
 * nothing - two posts would let the later "Approved 0." overwrite the first report. The flag is
 * released once the first settles, so the button is not dead afterwards. */
test("a double click on a shortcut posts once, and the next click after it settles posts again", async () => {
  fresh(7, { total: 10, approved: 5, skipped: 0, remaining: 5, attention: 3 });
  const state = { posted: [], lists: [], after: false };
  serveShortcutPlan(state);
  await list.setView({});
  const first = list.runShortcut("e12");
  const second = list.runShortcut("e12");
  await Promise.all([first, second]);
  assert.equal(state.posted.length, 1, "a double click posted twice");
  assert.equal(document.getElementById("list-note").textContent, "Approved 2.");
  await list.runShortcut(null);
  assert.equal(state.posted.length, 2, "the shortcut stayed busy after its write settled");
});

/* --- Task 3 review R2 closures (the shortcut buttons as drawn, and their refresh) ------------- */

/* The group's OWN button, clicked: its body carries that group's key (M2954). The checks above
 * call `runShortcut` directly, so a button wired to the wrong key was invisible to them. */
test("a group's drawn Approve N button posts that group's key", async () => {
  fresh(7, { total: 10, approved: 5, skipped: 0, remaining: 5, attention: 3 });
  const state = { posted: [], lists: [], after: false };
  serveShortcutPlan(state);
  await list.setView({});
  shortcutOf("e12").click();
  for (let i = 0; i < 5; i += 1) {
    await settle();
  }
  assert.equal(state.posted.length, 1, "the group's button posted nothing, or twice");
  assert.deepEqual(state.posted[0], { action: "approve", shortcut: true, list_group: "e12" });
});

/* After a decision the headers refresh from a fresh `/list`, and so do the N on each group's
 * button and the callout's `Approve the other N` - a stale N would sit on a button whose write
 * takes a different number (UX 5, "agree by construction"; M2955). */
test("a header refresh repaints each group's Approve N and the callout's N from the fresh /list", async () => {
  fresh(7, { total: 10, approved: 5, skipped: 0, remaining: 5, attention: 3 });
  const state = { posted: [], lists: [], after: false };
  serveShortcutPlan(state);
  await list.setView({});
  assert.equal(shortcutOf("e12").textContent, "Approve 2");
  const asked = state.lists.length;
  state.after = true;
  list.scheduleRefresh();
  await new Promise((resolve) => setTimeout(resolve, 500));
  for (let i = 0; i < 5; i += 1) {
    await settle();
  }
  assert.ok(state.lists.length > asked, "the refresh did not ask /list");
  assert.equal(shortcutOf("e12").hidden, true, "a group kept a stale Approve N");
  assert.equal(document.getElementById("look-callout-other").hidden, true);
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
      /* The message on one line, whitespace collapsed: an assert's diff spans several. */
      const message = String(error && error.message ? error.message : error);
      console.log("  " + message.replace(/\s+/g, " ").slice(0, 300));
    }
  }
  console.log("ran " + (tests.length - failed));
  process.exit(failed === 0 ? 0 : 1);
})();
