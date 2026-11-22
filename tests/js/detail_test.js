/*
 * THE LIST'S PREVIEW AND NAME EDITOR, EXECUTED (M14 Task 2 Step 6a; U2, U16, A6).
 *
 * `pam-detail.js` holds the preview overlay and the one name editor, moved off the card grid
 * and onto the list's rows. This driver runs it under a real `node` with `pam-list.js` and
 * `pam-row.js` over a small hand-written DOM and a stub server, wired exactly as `app.js`
 * wires it (`wireDetail()`), and drives it through the list's own key handler `onListKey`.
 * Every expectation is spelled here - from U2's keyboard bullet, U16, A6 and the rename
 * endpoint's body in design 8.2 - and every number from the stub's own answer.
 *
 * WHAT IT PINS: `Enter` opens the row's preview and `←/→` move it; the thumbnail opens it;
 * the preview's Approve goes through `decide`; `C` opens the editor in that row; X, Z and C
 * typed into the name field do nothing (HT-M7's check); a rename posts
 * `{new_filename}` to `item/{id}/rename` for that row's id and repaints the whole unit from
 * the response; a server-adjusted rename keeps the editor open with the server's sentence.
 *
 * `tests/test_webui_detail_page.py` runs this file and reads the names it prints. A planted
 * copy of `pam-detail.js` goes in through `PAM_DETAIL_JS`.
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
    focus() {
      node.focused = true;
    },
    select() {},
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
      byId[id].id = id;
    }
    return byId[id];
  },
  createElement: makeNode,
  querySelectorAll() {
    return [];
  },
};

/* The editor starts in its home, as `index.html` puts it - so "closed" can be checked as
 * "back home". */
const editorHome = makeNode("section");
editorHome.appendChild(document.getElementById("editor"));
document.getElementById("editor").hidden = true;
document.getElementById("overlay").hidden = true;
/* `index.html`'s `#editor-stem` is an `<input>`, and the keys' field guard reads the tag. */
document.getElementById("editor-stem").tagName = "INPUT";

/* The review page's classic scripts share one global scope; node gives each `require` its
 * own, so each earlier script's names go on `global` first - the shared scope reproduced.
 * `trustDisplay` and `whyPanel` are `pam-trust.js`'s DOM functions, which its node seam
 * does not export (`tests/test_app_logic.py` pins that seam by equality); the preview only
 * places what they return, so they are stood in for here with a marked node each. */
const STATIC_DIR = path.join(__dirname, "..", "..", "pam", "webui", "static");
Object.assign(global, require(path.join(STATIC_DIR, "pam-core.js")));
global.trustDisplay = (item) => makeNode("trust-" + item.id);
global.whyPanel = (item) => makeNode("why-" + item.id);
Object.assign(global, require(path.join(STATIC_DIR, "pam-row.js")));
const list = require(path.join(STATIC_DIR, "pam-list.js"));
Object.assign(global, list);
const detail = require(process.env.PAM_DETAIL_JS || path.join(STATIC_DIR, "pam-detail.js"));
/* Wired ONCE, as `app.js`'s `start` wires it: a second call would add every listener twice. */
detail.wireDetail();

/* --- a stub server ---------------------------------------------------------------------- */

let calls = [];
let rename = null;
let restore = null;

function query(pathname) {
  return new URLSearchParams(pathname.split("?")[1] || "");
}

function card(fields) {
  return Object.assign(
    {
      id: 1,
      file_id: 1,
      old_filename: "IMG_0001.HEIC",
      new_filename: "2019-07-04_001.heic",
      generated_filename: "2019-07-04_001.heic",
      media_type: "image",
      ext: "heic",
      tier: "high",
      trust_label: "High trust",
      edited: false,
      noop: false,
      status: "proposed",
      unit_key: "u1",
      unit_size: 1,
      has_thumb: true,
      attention: false,
      note_code: null,
      look_alikes: [],
      look_alikes_more: 0,
    },
    fields,
  );
}

const ITEMS = [
  card({ id: 10, file_id: 100, old_filename: "IMG_0010.HEIC", new_filename: "2019-07-04_001.heic", unit_key: "a" }),
  card({
    id: 11,
    file_id: 110,
    old_filename: "IMG_0011.HEIC",
    new_filename: "2019-07-04_002.heic",
    unit_key: "b",
    unit_size: 2,
  }),
  card({
    id: 12,
    file_id: 120,
    old_filename: "IMG_0011.MOV",
    new_filename: "2019-07-04_002.mov",
    media_type: "video",
    ext: "mov",
    unit_key: "b",
    unit_size: 2,
  }),
  card({ id: 13, file_id: 130, old_filename: "IMG_0013.HEIC", new_filename: "2019-07-04_003.heic", unit_key: "c" }),
];

/* The items as written above: the rename checks leave `edited` and the names behind them. */
const AS_WRITTEN = ITEMS.map((each) => Object.assign({}, each));
function asWritten() {
  ITEMS.forEach((each, index) => Object.assign(each, AS_WRITTEN[index]));
}

const COUNTS = { total: 4, approved: 0, skipped: 0, remaining: 4, attention: 0 };

global.api = function (pathname, options) {
  const body = options && options.body ? JSON.parse(options.body) : null;
  calls.push({ path: pathname, body: body });
  if (pathname.indexOf("/restore") !== -1) {
    return Promise.resolve(restore(pathname, body));
  }
  if (pathname.indexOf("/rename") !== -1) {
    return Promise.resolve(rename(pathname, body));
  }
  if (pathname.indexOf("/decide") !== -1) {
    return Promise.resolve({ companions: [], note: null, counts: COUNTS });
  }
  if (pathname.split("?")[0].endsWith("/list")) {
    return Promise.resolve({
      groups: [
        {
          key: "2019-07-04",
          title: "July 4, 2019",
          date_line: "July 4, 2019",
          total: ITEMS.length,
          undecided: ITEMS.length,
          attention_undecided: 0,
          all_low: false,
        },
      ],
    });
  }
  const params = query(pathname);
  if (params.get("q") !== null) {
    return Promise.resolve({ total: 2, items: ITEMS.filter((each) => each.unit_key === "b") });
  }
  if (params.get("limit") === "0") {
    return Promise.resolve({ total: 0, items: [] });
  }
  const offset = Number(params.get("offset"));
  const limit = Number(params.get("limit"));
  return Promise.resolve({ total: ITEMS.length, items: ITEMS.slice(offset, offset + limit) });
};

/* A fresh page: the list drawn from the stub, the detail wired exactly as `app.js` wires it,
 * and the preview and the editor closed. */
async function page() {
  clearTimeout(list.LIST.refreshTimer);
  list.LIST.refreshTimer = null;
  detail.closeEditor();
  detail.closePreview();
  list.LIST.planId = 7;
  list.LIST.counts = Object.assign({}, COUNTS);
  list.LIST.cursorId = null;
  list.LIST.view = { mode: "all", tier: "", group: "" };
  for (const item of ITEMS) {
    item.status = "proposed";
  }
  await list.setView({});
  calls = [];
}

function el(id) {
  return document.getElementById(id);
}

function rowNode(itemId) {
  return list.LIST.rows[itemId].node;
}

function key(name, target) {
  const event = { key: name, target: target || makeNode("body") };
  event.prevented = false;
  event.preventDefault = () => {
    event.prevented = true;
  };
  return event;
}

function posts() {
  return calls.filter((each) => each.body !== null);
}

function settle() {
  return new Promise((resolve) => setImmediate(resolve));
}

const tests = [];
function test(name, body) {
  tests.push([name, body]);
}

/* --- the preview (U16) ------------------------------------------------------------------ */

test("Enter on a row opens that row's preview", async () => {
  await page();
  list.LIST.cursorId = 11;
  assert.equal(el("overlay").hidden, true, "the preview was open before Enter");
  assert.equal(list.onListKey(key("Enter")), "preview");
  assert.equal(el("overlay").hidden, false, "Enter did not open the preview");
  assert.equal(el("overlay-title").textContent, "IMG_0011.HEIC");
  assert.equal(el("overlay-name").textContent, "→ 2019-07-04_002.heic");
  assert.equal(el("overlay-image").src, "api/thumb/110?variant=full");
  /* `←/→` move the list's row, and the preview follows without closing. */
  list.onListKey(key("ArrowRight"));
  assert.equal(list.LIST.cursorId, 12);
  assert.equal(el("overlay").hidden, false);
  assert.equal(el("overlay-title").textContent, "IMG_0011.MOV");
  list.onListKey(key("ArrowLeft"));
  list.onListKey(key("ArrowLeft"));
  assert.equal(el("overlay-title").textContent, "IMG_0010.HEIC");
  /* Enter again closes it; nothing was written. */
  assert.equal(list.onListKey(key("Enter")), "close-preview");
  assert.equal(el("overlay").hidden, true);
  assert.equal(posts().length, 0);
});

test("the row's thumbnail opens the preview on that row", async () => {
  await page();
  const thumb = byClass(rowNode(13), "row-thumb")[0];
  thumb.click();
  assert.equal(el("overlay").hidden, false);
  assert.equal(list.LIST.cursorId, 13, "the row clicked is the keyboard's row");
  assert.equal(el("overlay-title").textContent, "IMG_0013.HEIC");
});

test("the preview's Approve decides that row through decide and moves to the next undecided", async () => {
  await page();
  list.LIST.cursorId = 10;
  list.onListKey(key("Enter"));
  await Promise.all(el("overlay-approve").click());
  assert.deepEqual(posts(), [{ path: "api/plan/7/decide", body: { action: "approve", item_ids: [10] } }]);
  assert.equal(list.LIST.cursorId, 11);
  assert.equal(el("overlay-title").textContent, "IMG_0011.HEIC", "the preview did not follow");
});

/* --- the editor (A6) ------------------------------------------------------------------- */

test("C opens the editor for that row", async () => {
  await page();
  list.LIST.cursorId = 13;
  assert.equal(list.onListKey(key("c")), "edit");
  assert.equal(el("editor").hidden, false, "C did not open the editor");
  assert.equal(el("editor-stem").value, "2019-07-04_003");
  assert.equal(el("editor-ext").textContent, ".heic");
  assert.equal(el("editor").parentNode, rowNode(13), "the editor is not in that row");
  assert.equal(rowNode(13).getAttribute("data-editing"), "true");
  assert.equal(el("editor-stem").focused, true, "the name field was not given focus");
  assert.equal(list.LIST.hooks.editorField, el("editor-stem"));
});

test("X, Z and C typed into the name field do not act", async () => {
  await page();
  list.LIST.cursorId = 13;
  list.onListKey(key("c"));
  calls = [];
  for (const name of ["x", "X", "z", "Z", "c", "C", "Enter", "ArrowDown"]) {
    const event = key(name, el("editor-stem"));
    assert.equal(list.onListKey(event), null, name);
    assert.equal(event.prevented, false, name);
  }
  assert.equal(calls.length, 0, "a key typed into the name field reached the server");
  assert.equal(list.LIST.cursorId, 13);
  assert.equal(el("editor").hidden, false, "typing closed the editor");
  assert.equal(el("overlay").hidden, true, "typing opened the preview");
  /* The field's own Escape closes the editor and puts it home. */
  assert.equal(list.onListKey(key("Escape", el("editor-stem"))), "close-editor");
  assert.equal(el("editor").hidden, true);
  assert.equal(el("editor").parentNode, editorHome);
});

test("a rename sends the existing endpoint's body for that row's id and repaints its unit", async () => {
  await page();
  rename = () => ({
    item_id: 11,
    submitted_filename: "Beach.heic",
    note: null,
    members: [
      { id: 11, new_filename: "Beach.heic", generated_filename: "2019-07-04_002.heic", edited: true, noop: false, status: "approved" },
      { id: 12, new_filename: "Beach.mov", generated_filename: "2019-07-04_002.mov", edited: true, noop: false, status: "approved" },
    ],
    counts: { total: 4, approved: 3, skipped: 0, remaining: 1, attention: 0 },
  });
  list.LIST.cursorId = 11;
  list.onListKey(key("c"));
  await settle();
  el("editor-stem").value = "Beach";
  calls = [];
  const submit = { prevented: false, preventDefault() { submit.prevented = true; } };
  await Promise.all(el("editor").listeners.submit.map((handler) => handler(submit)));
  await settle();
  assert.equal(submit.prevented, true, "the form was allowed to submit itself");
  assert.deepEqual(posts(), [{ path: "api/item/11/rename", body: { new_filename: "Beach.heic" } }]);
  /* The server renamed the unit's stem whole; both rows show what it stored. */
  assert.equal(byClass(rowNode(11), "row-name")[0].textContent, "Beach.heic");
  assert.equal(byClass(rowNode(12), "row-name")[0].textContent, "Beach.mov");
  assert.equal(byClass(rowNode(12), "row-label")[0].textContent, "Edited by you");
  /* The progress line is the response's (local arithmetic would say 2 approved). */
  assert.equal(el("progress-approved").textContent, "3 of 4 names approved");
  assert.equal(el("editor").hidden, true, "no note, and the editor stayed open");
});

test("a rename the server adjusted keeps the editor open with its sentence", async () => {
  await page();
  rename = () => ({
    item_id: 13,
    submitted_filename: "CON.heic",
    note: "pam stored this as CON-x.heic.",
    members: [{ id: 13, new_filename: "CON-x.heic", generated_filename: "2019-07-04_003.heic", edited: true, noop: false, status: "approved" }],
    counts: COUNTS,
  });
  list.LIST.cursorId = 13;
  list.onListKey(key("c"));
  el("editor-stem").value = "CON";
  const submit = { preventDefault() {} };
  await Promise.all(el("editor").listeners.submit.map((handler) => handler(submit)));
  await settle();
  assert.equal(el("editor").hidden, false);
  assert.equal(el("editor-note").textContent, "pam stored this as CON-x.heic.");
  assert.equal(el("editor-stem").value, "CON");
  assert.equal(el("editor").parentNode, rowNode(13), "the editor was left in the replaced row");
});

/* --- putting pam's name back (A6; M14 Task 2 Step 6b: the card's `↺`, now the row's) ------- */

test("the row's restore shows only on an edited row and sends the existing body", async () => {
  const edited = ITEMS[3];
  asWritten();
  try {
    await page();
    for (const each of ITEMS) {
      assert.equal(byClass(rowNode(each.id), "restore").length, 0, "a name pam proposed offers restore");
    }
    edited.edited = true;
    edited.new_filename = "Beach.heic";
    await page();
    assert.equal(byClass(rowNode(10), "restore").length, 0);
    const buttons = byClass(rowNode(13), "restore");
    assert.equal(buttons.length, 1);
    assert.equal(buttons[0].textContent, "↺");
    assert.equal(buttons[0].title, "Put pam's own name back");
    restore = () => ({
      item_id: 13,
      members: [
        { id: 13, new_filename: "2019-07-04_003.heic", generated_filename: "2019-07-04_003.heic", edited: false, noop: false, status: "proposed" },
      ],
      counts: { total: 4, approved: 1, skipped: 0, remaining: 3, attention: 0 },
    });
    await Promise.all(buttons[0].listeners.click.map((handler) => handler({})));
    await settle();
    assert.deepEqual(posts(), [{ path: "api/item/13/restore", body: {} }]);
    assert.equal(byClass(rowNode(13), "row-name")[0].textContent, "2019-07-04_003.heic");
    assert.equal(byClass(rowNode(13), "restore").length, 0, "the repainted row still offers restore");
    /* The progress line is the response's (local arithmetic would say 0 approved). */
    assert.equal(el("progress-approved").textContent, "1 of 4 names approved");
  } finally {
    asWritten();
  }
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
