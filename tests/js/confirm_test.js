/*
 * CONFIRM, THE RENAME, DONE AND UNDO, EXECUTED (M14 Task 6c; U6, U8 W16/W19-W21, U11, U12, U14,
 * U22; the controller's rulings B2-B6).
 *
 * The review page's fifteen classic scripts (History's since M14 Task 7) are loaded into one `vm` context in the page's own
 * order - so `app.js` boots a bound page exactly as it does in a browser - over a small
 * hand-written DOM and a stub `fetch` that answers the routes as M14 Task 6b built them
 * (`GET plan/{id}/confirmation`, `POST plan/{id}/apply`, `GET job`, `GET session/{id}`,
 * `POST session/{id}/undo`) and `/list`'s `renames` (ruling B2). Nothing is read back out of the
 * scripts to build an expectation: every sentence below is spelled from U6, U22 and W16-W21, from
 * the controller's rulings, or from the server's own sentences (`rename_jobs.CHANGED`, the confirm
 * refusals), and every number from the stub's own answer.
 *
 * `tests/test_webui_confirm_page.py` runs this file and reads the names it prints: every check
 * prints `ok <name>` or `FAIL <name>` and the run ends with `ran <n>`. A planted copy of a script
 * goes in through `PAM_CONFIRM_JS` / `PAM_DONE_JS` / `PAM_STOPPED_JS` / `PAM_LIST_JS` / `PAM_KEYS_JS` / `PAM_APP_JS`.
 */

"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const STATIC_DIR = path.join(__dirname, "..", "..", "pam", "webui", "static");

/* The page's order, spelled from design spec 2's tree and M14 Task 6c - never read off the page
 * (`load_order_test.js` holds the page to its order). */
const PAGE_SCRIPTS = [
  "pam-core.js",
  "pam-trust.js",
  "pam-keys.js",
  "pam-row.js",
  "pam-selection.js",
  "pam-list.js",
  "pam-bar.js",
  "pam-detail.js",
  "pam-chooser.js",
  "pam-reading.js",
  "pam-welcome.js",
  "pam-confirm.js",
  "pam-done.js",
  "pam-stopped.js",
  "pam-history.js",
  "app.js",
];
const PLANTED = {
  "pam-confirm.js": process.env.PAM_CONFIRM_JS,
  "pam-done.js": process.env.PAM_DONE_JS,
  "pam-stopped.js": process.env.PAM_STOPPED_JS,
  "pam-list.js": process.env.PAM_LIST_JS,
  "pam-keys.js": process.env.PAM_KEYS_JS,
  "app.js": process.env.PAM_APP_JS,
};

/* --- the copy, spelled from the specs ------------------------------------------------- */

/* W16: the footer's two labels. */
const APPROVE_FIRST = "Approve names to rename them";
/* W19 and U6, verbatim. */
const W19 = {
  title: "Ready to rename",
  inFolder: "In this folder",
  tick: "This is the right folder",
  getNames: "get their new names",
  notApproved: "Not approved, left as they are",
  skippedByYou: "Skipped by you",
  promises: [
    "Nothing is deleted, moved or edited. Only names change.",
    "Every change is written down before it happens.",
    "Undo puts every name back exactly. It never expires.",
  ],
};
/* U22's small line under the undo promise. */
const CAVEAT = "As long as the files aren't renamed outside pam.";
/* The server's sentence for a press with the box unticked (Task 6b), which the page also shows
 * under the off button. */
const TICK_FIRST = 'Tick "This is the right folder" first.';
/* U22's sync line for OneDrive. */
const SYNC_ONEDRIVE = "This folder is in OneDrive. Pause syncing while pam renames, then turn it back on.";
/* U11's window sentence; Step 6's progress lead; ruling B3's for undo. */
const KEEP_OPEN = "Keep this window open or not — pam finishes either way.";
/* W20, W21, U22. */
const CHANGED_MIND = "Changed your mind?";
const UNDO = "Undo this rename";
const EXACTLY = "Your folder is exactly as it was before.";
/* U12: the heading over recovery's own line after a job died. */
const RECOVERED = "pam finished putting things right after it stopped.";
/* C6.2 (the maintainer's ruling, 2026-11-16): a died rename's heading, and the line beneath it when
 * its session can still be undone. */
const PART_WAY = "pam stopped part-way. It has put things right.";
const RENAMED_BEFORE = "Some files were renamed before it stopped. You can put them back.";
/* The way back to the list (the implementer's words, from W4, pending the design model). */
const BACK = "Keep checking names";
/* M14 Y2: Done's link and Welcome's button, the amendment's word and U10's. */
const START_READING = "Start reading";

/* The server's sentences, each verbatim (Task 6b: `jobs.CHANGED`, the busy refusal). */
const CHANGED = "Something changed since you looked. Check the names again.";
const BUSY = "pam is busy renaming. Try again when it finishes.";
/* A job line and a recovery line, as the server would send them. */
const FAILURE_LINE = "IMG_0001.HEIC could not be renamed: the file is open in another program.";
const RECOVERY_LINE = "pam finished tidying up after an interrupted run: 1 file(s) put back.";

/* --- the server's answers ------------------------------------------------------------- */

/* A long archive root: U6 shows it in full, never shortened. */
const ROOT =
  "C:\\Users\\user\\Pictures\\Archive\\Phone backups\\Everything from the old computer\\Sorted";
/* Nonces that a page would be tempted to tidy: sent exactly as received. */
const NONCE = " Qx7_-a9bZ0+/= ";
const SESSION_NONCE = "u-Nd0_8x==";
const SESSION = "s-20261114-101500";

function confirmation(extra) {
  return Object.assign(
    {
      archive_root: ROOT,
      renames: 124,
      not_approved: 4,
      skipped_by_you: 2,
      already_conforming: 9,
      low_confidence: 0,
      sync_provider: null,
      nonce: NONCE,
    },
    extra || {}
  );
}

function job(state, extra) {
  return Object.assign(
    {
      kind: "rename",
      state: state,
      done: 0,
      total: 0,
      eta_s: null,
      lines: [],
      recovery: null,
      session: null,
      result: null,
    },
    extra || {}
  );
}

function renamedJob(result, extra) {
  return job(
    "done",
    Object.assign(
      {
        done: 124,
        total: 124,
        session: SESSION,
        result: Object.assign({ renamed: 124, failed: 0, not_approved: 4, elapsed_s: 3.14 }, result),
      },
      extra || {}
    )
  );
}

/* M14 Y3: one row of `GET api/sessions` (design 8.2), as the server would send it. */
function sessionRow(session, extra) {
  return Object.assign(
    { session_id: session, time: null, renamed: 124, undone: false, undoable: true, report: true },
    extra || {}
  );
}

/* M14 Y2: a read's snapshot (U11). */
function readJob(state, extra) {
  return job(state, Object.assign({ kind: "read" }, extra || {}));
}

function undoneJob(result) {
  return job("done", {
    kind: "undo",
    done: 124,
    total: 124,
    session: SESSION,
    result: Object.assign({ restored: 124, failed: 0, already_undone: 0 }, result),
  });
}

/* --- a small DOM ---------------------------------------------------------------------- */

function makeNode(tag) {
  const node = {
    tagName: String(tag || "div").toUpperCase(),
    children: [],
    parentNode: null,
    attributes: {},
    listeners: {},
    style: {},
    dataset: {},
    hidden: false,
    disabled: false,
    checked: false,
    className: "",
    textContent: "",
    value: "",
    type: "",
    classList: {
      add() {},
      remove() {},
      toggle() {},
      contains() {
        return false;
      },
    },
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
      node.removeChild(old);
      return node.appendChild(fresh);
    },
    insertBefore(child) {
      return node.appendChild(child);
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
    removeEventListener(name, handler) {
      node.listeners[name] = (node.listeners[name] || []).filter((each) => each !== handler);
    },
    dispatch(name, event) {
      return (node.listeners[name] || []).map((handler) => handler(event));
    },
    click() {
      return node.dispatch("click", {});
    },
    focus() {},
    scrollIntoView() {},
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
  };
  return node;
}

function makeDocument() {
  const byId = {};
  const body = makeNode("body");
  const doc = makeNode("document");
  doc.body = body;
  doc.getElementById = function (id) {
    if (!byId[id]) {
      byId[id] = makeNode("div");
      body.appendChild(byId[id]);
    }
    return byId[id];
  };
  doc.createElement = makeNode;
  return doc;
}

/* --- one bound page ------------------------------------------------------------------- */

/* The routes of a bound server with open plan 7. `opts` sets `/list`'s `renames`, the
 * confirmation's answer, the apply's answer, the jobs the poll is given in turn, and undo's. */
function boundRoutes(opts) {
  const jobs = (opts.jobs || []).slice();
  let lastJob = job("idle", { kind: null });
  /* The boot's own `GET api/job` (S1P-F2's `resumeMoving`, after the list) is answered by
   * `bootJob` - idle unless a check sets it - so `jobs` stays the poll's, in turn. */
  let booted = false;
  /* M14 Y2: every ping after the boot's, in turn (the last repeats) - by default the server's
   * plan is the one just renamed, no longer open. */
  const pings = (opts.pings || [{ plan_id: 7, status: "applied", binding: 1, folder: ROOT }]).slice();
  let pinged = false;
  return function (url) {
    if (url === "api/ping" && !pinged) {
      pinged = true;
      return [200, { plan_id: 7, status: "open" }];
    }
    if (url === "api/ping") {
      return [200, pings.length > 1 ? pings.shift() : pings[0]];
    }
    /* M14 Y3: History's rows after an undo - by default the one session, put back. */
    if (url === "api/sessions") {
      return opts.sessions || [200, { sessions: [sessionRow(SESSION, { undone: true, undoable: false })] }];
    }
    /* M14 Y2: Welcome's top level and the read, as Task 5d serves them. */
    if (url === "api/folders") {
      return [200, { path: null, parent: null, folders: [], drives: ["C:\\"], known: [] }];
    }
    if (url === "api/job/read") {
      return [200, { job: 2, binding: 4, folder: ROOT }];
    }
    if (url === "api/plan/7") {
      return [200, { total: 140, approved: 124, skipped: 2, remaining: 14, attention: 0 }];
    }
    if (url.startsWith("api/plan/7/list")) {
      return [
        200,
        {
          groups: [],
          undecided: 14,
          attention_undecided: 0,
          shortcut_count: 0,
          renames: opts.renames === undefined ? 124 : opts.renames,
        },
      ];
    }
    if (url.startsWith("api/plan/7/items")) {
      return [200, { total: 0, items: [] }];
    }
    if (url === "api/plan/7/groups") {
      return [200, { groups: [] }];
    }
    if (url === "api/plan/7/confirmation") {
      return opts.confirmation || [200, confirmation()];
    }
    if (url === "api/plan/7/apply") {
      return opts.apply || [202, { kind: "rename", state: "running" }];
    }
    if (url === "api/job" && !booted) {
      booted = true;
      return [200, opts.bootJob || job("idle", { kind: null })];
    }
    if (url === "api/job") {
      if (jobs.length > 0) {
        lastJob = jobs.shift();
      }
      return [200, lastJob];
    }
    if (url === "api/session/" + SESSION) {
      return [
        200,
        {
          session_id: SESSION,
          done: 124,
          failed: 0,
          reverted: 0,
          intent: 0,
          undone: 0,
          undoable: 124,
          nonce: SESSION_NONCE,
        },
      ];
    }
    if (url === "api/session/" + SESSION + "/undo") {
      return opts.undo || [202, { kind: "undo", state: "running" }];
    }
    return null;
  };
}

function loadPage(opts) {
  const requests = [];
  const document = makeDocument();
  /* As `index.html` ships them: Welcome, Confirm and the overlay hidden. */
  for (const id of ["welcome", "confirm", "shortcuts", "steps"]) {
    document.getElementById(id).hidden = true;
  }
  const routes = boundRoutes(opts || {});
  const context = vm.createContext({
    document: document,
    console: console,
    URLSearchParams: URLSearchParams,
    setTimeout: setTimeout,
    clearTimeout: clearTimeout,
    fetch: function (url, options) {
      requests.push({
        url: url,
        method: options && options.method ? options.method : "GET",
        body: options && options.body ? JSON.parse(options.body) : null,
        contentType: options && options.headers ? options.headers["Content-Type"] : undefined,
      });
      const answer = routes(url, options);
      if (answer === null) {
        return new Promise(function () {});
      }
      const [status, payload] = answer;
      return Promise.resolve({
        ok: status < 400,
        status: status,
        text: function () {
          return Promise.resolve(payload === undefined ? "" : JSON.stringify(payload));
        },
      });
    },
  });
  context.window = context;
  for (const file of PAGE_SCRIPTS) {
    const source = PLANTED[file] || path.join(STATIC_DIR, file);
    vm.runInContext(fs.readFileSync(source, "utf-8"), context, { filename: file });
  }
  /* The poll's timer, driven by the checks (`tick`), never a real second. */
  const timers = [];
  context.PAM.confirm.CONFIRM.schedule = function (step) {
    timers.push(step);
  };
  return {
    context,
    document,
    requests,
    timers,
    el: (id) => document.getElementById(id),
  };
}

async function settle() {
  for (let i = 0; i < 10; i += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}

/* One waiting poll, run. */
async function tick(page) {
  assert.equal(page.timers.length, 1, "the page holds " + page.timers.length + " waiting polls");
  page.timers.shift()();
  await settle();
}

function posts(page, url) {
  return page.requests.filter((each) => each.method === "POST" && each.url === url);
}

function gets(page, url) {
  return page.requests.filter((each) => each.method === "GET" && each.url === url);
}

function shown(node) {
  return node.hidden ? null : node.textContent;
}

function lines(node) {
  return node.hidden ? [] : node.children.map((each) => each.textContent);
}

/* A bound page, its list drawn. */
async function listPage(opts) {
  const page = loadPage(opts);
  await settle();
  return page;
}

/* Ready to rename, opened from the footer. */
async function readyPage(opts) {
  const page = await listPage(opts);
  page.el("footer-rename").click();
  await settle();
  return page;
}

function tickBox(page, on) {
  page.el("confirm-tick").checked = on;
  page.el("confirm-tick").dispatch("change", {});
}

/* Ready to rename, ticked and pressed: the post is answered and the poll has run once. */
async function pressedPage(opts) {
  const page = await readyPage(opts);
  tickBox(page, true);
  page.el("confirm-rename").click();
  await settle();
  return page;
}

/* A rename that has finished as `final` (the poll's first answer). */
async function finishedPage(final, opts) {
  return pressedPage(Object.assign({ jobs: [final] }, opts || {}));
}

const tests = [];
function test(name, body) {
  tests.push([name, body]);
}

/* --- W16: the footer --------------------------------------------------------------------- */

test("the footer's button is Rename N files from /list's renames, with the step pills shown", async () => {
  const page = await listPage({ renames: 1204 });
  assert.equal(page.el("footer-rename").textContent, "Rename 1,204 files");
  assert.equal(page.el("footer-rename").disabled, false);
  assert.equal(page.el("steps").hidden, false, "ruling B6: the step pills stayed hidden");
  assert.equal(page.el("confirm").hidden, true, "Confirm was open before the button was pressed");
});

test("the footer with nothing to rename says Approve names to rename them and is off", async () => {
  const page = await listPage({ renames: 0 });
  assert.equal(page.el("footer-rename").textContent, APPROVE_FIRST);
  assert.equal(page.el("footer-rename").disabled, true);
});

/* --- W19 / U6: Ready to rename ----------------------------------------------------------- */

test("Ready to rename shows the server's folder in full, its counts and U6's copy, unticked", async () => {
  const page = await readyPage();
  assert.equal(gets(page, "api/plan/7/confirmation").length, 1);
  assert.equal(page.el("confirm").hidden, false);
  for (const id of ["header-row", "controls", "main", "footer"]) {
    assert.equal(page.el(id).hidden, true, id + " stayed shown under Confirm");
  }
  assert.equal(page.el("confirm-title").textContent, W19.title);
  assert.equal(page.el("confirm-folder-label").textContent, W19.inFolder);
  assert.equal(page.el("confirm-folder").textContent, ROOT);
  assert.equal(page.el("confirm-tick-label").textContent, W19.tick);
  assert.equal(page.el("confirm-tick").checked, false);
  assert.equal(page.el("confirm-renames").textContent, "124 files");
  assert.equal(page.el("confirm-renames-tail").textContent, W19.getNames);
  assert.equal(page.el("confirm-not-approved-label").textContent, W19.notApproved);
  assert.equal(page.el("confirm-not-approved").textContent, "4");
  assert.equal(page.el("confirm-skipped-label").textContent, W19.skippedByYou);
  assert.equal(page.el("confirm-skipped").textContent, "2");
  assert.deepEqual(
    page.el("confirm-promises").children.map((each) => each.textContent),
    W19.promises.concat([CAVEAT])
  );
  assert.equal(page.el("confirm-rename").textContent, "Rename 124 files");
  assert.equal(page.el("confirm-rename").disabled, true);
  assert.equal(shown(page.el("confirm-tick-first")), TICK_FIRST);
  assert.equal(shown(page.el("confirm-back")), BACK);
  assert.equal(page.el("confirm-ready").hidden, false);
  assert.equal(page.el("confirm-running").hidden, true);
  assert.equal(page.el("finish").hidden, true);
});

test("the tick box gates Rename N files and the sentence under it", async () => {
  const page = await readyPage();
  tickBox(page, true);
  assert.equal(page.el("confirm-rename").disabled, false);
  assert.equal(page.el("confirm-tick-first").hidden, true);
  tickBox(page, false);
  assert.equal(page.el("confirm-rename").disabled, true);
  assert.equal(shown(page.el("confirm-tick-first")), TICK_FIRST);
});

test("the box is unticked on arrival, even after going back with it ticked", async () => {
  const page = await readyPage();
  tickBox(page, true);
  page.el("confirm-back").click();
  await settle();
  page.el("footer-rename").click();
  await settle();
  assert.equal(gets(page, "api/plan/7/confirmation").length, 2, "Confirm did not ask again");
  assert.equal(page.el("confirm-tick").checked, false);
  assert.equal(page.el("confirm-rename").disabled, true);
});

test("Rename N files posts once on a double press, the nonce as received and folder_confirmed true, as JSON", async () => {
  const page = await readyPage({ jobs: [job("running", { done: 1, total: 124 })] });
  tickBox(page, true);
  page.el("confirm-rename").click();
  page.el("confirm-rename").click();
  await settle();
  const sent = posts(page, "api/plan/7/apply");
  assert.equal(sent.length, 1, "the rename was posted " + sent.length + " times");
  assert.deepEqual(sent[0].body, { nonce: NONCE, folder_confirmed: true });
  assert.equal(sent[0].contentType, "application/json");
  assert.equal(page.el("confirm-rename").disabled, true);
});

test("an unticked press posts nothing", async () => {
  const page = await readyPage();
  page.el("confirm-rename").click();
  await settle();
  assert.equal(posts(page, "api/plan/7/apply").length, 0);
  assert.equal(page.el("confirm-ready").hidden, false);
});

test("the sync line is drawn only when the server names a provider", async () => {
  const withSync = await readyPage({ confirmation: [200, confirmation({ sync_provider: "OneDrive" })] });
  assert.equal(shown(withSync.el("confirm-sync")), SYNC_ONEDRIVE);
  const without = await readyPage();
  assert.equal(without.el("confirm-sync").hidden, true);
  assert.equal(without.el("confirm-sync").textContent, "");
});

test("one file gets its new name, in the singular (HT-M14 F5)", async () => {
  const one = await readyPage({ confirmation: [200, confirmation({ renames: 1 })] });
  assert.equal(one.el("confirm-renames").textContent, "1 file");
  assert.equal(one.el("confirm-renames-tail").textContent, "gets its new name");
  const many = await readyPage({ confirmation: [200, confirmation({ renames: 2 })] });
  assert.equal(many.el("confirm-renames-tail").textContent, W19.getNames);
});

test("the low line is drawn only when the batch holds a low name", async () => {
  const one = await readyPage({ confirmation: [200, confirmation({ low_confidence: 1 })] });
  /* HT-M14 F5: one name NEEDS review. */
  assert.equal(shown(one.el("confirm-low")), "1 of these names needs review.");
  const many = await readyPage({ confirmation: [200, confirmation({ low_confidence: 1204 })] });
  assert.equal(shown(many.el("confirm-low")), "1,204 of these names need review.");
  const none = await readyPage();
  assert.equal(none.el("confirm-low").hidden, true);
});

test("a refused rename shows the server's sentence verbatim and offers the way back", async () => {
  const page = await pressedPage({ apply: [409, { error: CHANGED }] });
  assert.equal(shown(page.el("confirm-error")), CHANGED);
  assert.equal(shown(page.el("confirm-back")), BACK);
  /* The one `GET api/job` is the boot's (S1P-F2), before anything was pressed. */
  assert.equal(gets(page, "api/job").length, 1, "a refused rename was polled");
  assert.equal(page.el("confirm-running").hidden, true);
});

test("a refused confirmation shows the server's sentence on the list and opens no screen", async () => {
  const page = await readyPage({ confirmation: [409, { error: BUSY }] });
  assert.equal(shown(page.el("list-note")), BUSY);
  assert.equal(page.el("confirm").hidden, true);
  assert.equal(page.el("main").hidden, false);
});

/* --- the rename's progress (Step 6; U11) --------------------------------------------- */

test("the progress line counts with thousands and keeps the window sentence, the way back hidden", async () => {
  const page = await pressedPage({
    jobs: [job("running", { done: 312, total: 1204 }), job("running", { done: 1000, total: 1204 })],
  });
  assert.equal(page.el("confirm-running").hidden, false);
  assert.equal(page.el("confirm-ready").hidden, true);
  assert.equal(page.el("confirm-progress").textContent, "Renaming… 312 of 1,204");
  assert.equal(page.el("confirm-keep-open").textContent, KEEP_OPEN);
  assert.equal(page.el("confirm-back").hidden, true);
  await tick(page);
  assert.equal(page.el("confirm-progress").textContent, "Renaming… 1,000 of 1,204");
  assert.equal(page.timers.length, 1);
});

/* --- W20 / U22: Done ------------------------------------------------------------------- */

test("Done with no failure: the headline, the time with no problems, the left line and Undo", async () => {
  const page = await finishedPage(renamedJob({}));
  assert.equal(page.el("finish").hidden, false);
  assert.equal(page.el("confirm-running").hidden, true);
  assert.equal(shown(page.el("finish-headline")), "124 files renamed");
  assert.equal(shown(page.el("finish-time")), "In 3.1 seconds, with no problems.");
  assert.equal(
    shown(page.el("finish-left")),
    "4 names were not approved and 2 were skipped; they kept their old names."
  );
  assert.deepEqual(lines(page.el("finish-lines")), []);
  assert.equal(page.el("finish-undo-box").hidden, false);
  assert.equal(page.el("finish-changed").textContent, CHANGED_MIND);
  assert.equal(page.el("finish-undo").textContent, UNDO);
  assert.equal(page.el("finish-promise").textContent, W19.promises[2]);
  assert.equal(page.el("finish-caveat").textContent, CAVEAT);
  assert.equal(page.el("finish-recovery").hidden, true);
  assert.equal(page.timers.length, 0, "a finished rename was polled again");
  /* The plan is renamed: going back to its list would offer W16's `Approve names` over it. */
  assert.equal(page.el("confirm-back").hidden, true, "the way back was offered after Done");
});

test("Done with one failure: the headline says so, no time line, the job's lines under it", async () => {
  const page = await finishedPage(renamedJob({ renamed: 123, failed: 1 }, { lines: [FAILURE_LINE] }));
  assert.equal(shown(page.el("finish-headline")), "123 files renamed, 1 could not be.");
  assert.equal(page.el("finish-time").hidden, true, "a rename with a failure said no problems");
  assert.deepEqual(lines(page.el("finish-lines")), [FAILURE_LINE]);
  assert.equal(page.el("finish-undo-box").hidden, false);
});

/* C6.8(a) (the maintainer's ruling, 2026-11-16): files skipped because they became cloud-only were not
 * renamed, so the time line drops "with no problems" - AM2's sentence, the job's line, says why. */
test("C6.8(a): a rename that skipped cloud-only files says its time with no tail", async () => {
  const skipped = await finishedPage(renamedJob({ cloud_only: 2 }, { lines: ["(AM2's sentence)"] }));
  assert.equal(shown(skipped.el("finish-time")), "In 3.1 seconds.");
  assert.deepEqual(lines(skipped.el("finish-lines")), ["(AM2's sentence)"]);
  const none = await finishedPage(renamedJob({ cloud_only: 0 }));
  assert.equal(shown(none.el("finish-time")), "In 3.1 seconds, with no problems.");
});

test("the left line at each boundary: not approved 0, skipped 0, both 0", async () => {
  const skippedOnly = await finishedPage(renamedJob({ not_approved: 0 }));
  assert.equal(
    shown(skippedOnly.el("finish-left")),
    "0 names were not approved and 2 were skipped; they kept their old names."
  );
  const notApprovedOnly = await finishedPage(renamedJob({}), {
    confirmation: [200, confirmation({ skipped_by_you: 0 })],
  });
  assert.equal(
    shown(notApprovedOnly.el("finish-left")),
    "4 names were not approved and 0 were skipped; they kept their old names."
  );
  /* HT-M14 F5: a count of one takes the singular, and the tail follows the total. */
  const oneSkipped = await finishedPage(renamedJob({ not_approved: 0 }), {
    confirmation: [200, confirmation({ not_approved: 0, skipped_by_you: 1 })],
  });
  assert.equal(
    shown(oneSkipped.el("finish-left")),
    "0 names were not approved and 1 was skipped; it kept its old name."
  );
  const oneEach = await finishedPage(renamedJob({ not_approved: 1 }), {
    confirmation: [200, confirmation({ not_approved: 1, skipped_by_you: 1 })],
  });
  assert.equal(
    shown(oneEach.el("finish-left")),
    "1 name was not approved and 1 was skipped; they kept their old names."
  );
  const neither = await finishedPage(renamedJob({ not_approved: 0 }), {
    confirmation: [200, confirmation({ not_approved: 0, skipped_by_you: 0 })],
  });
  assert.equal(neither.el("finish-left").hidden, true);
  assert.equal(neither.el("finish-left").textContent, "");
});

/* --- W21 / U22: undo ------------------------------------------------------------------- */

async function undoPressed(jobsAfter) {
  const page = await finishedPage(renamedJob({}));
  /* The undo's jobs follow the rename's on the same poll. */
  page.context.PAM.confirm.CONFIRM.schedule = function (step) {
    page.timers.push(step);
  };
  const more = boundRoutes({ jobs: jobsAfter });
  /* This page has booted already: spend the fresh routes' boot answer, so `jobsAfter` is the
   * undo poll's, in turn. */
  more("api/job");
  const fetchBefore = page.context.fetch;
  page.context.fetch = function (url, options) {
    if (url === "api/job") {
      page.requests.push({ url: url, method: "GET", body: null, contentType: undefined });
      const [status, payload] = more(url, options);
      return Promise.resolve({
        ok: status < 400,
        status: status,
        text: () => Promise.resolve(JSON.stringify(payload)),
      });
    }
    return fetchBefore(url, options);
  };
  page.el("finish-undo").click();
  page.el("finish-undo").click();
  await settle();
  return page;
}

test("Undo this rename asks for the session's nonce and posts it as received, once", async () => {
  const page = await undoPressed([job("running", { kind: "undo", done: 1, total: 124 })]);
  assert.equal(gets(page, "api/session/" + SESSION).length, 1);
  const sent = posts(page, "api/session/" + SESSION + "/undo");
  assert.equal(sent.length, 1, "the undo was posted " + sent.length + " times");
  assert.deepEqual(sent[0].body, { nonce: SESSION_NONCE });
  assert.equal(sent[0].contentType, "application/json");
});

test("the undo's progress line is Putting names back", async () => {
  const page = await undoPressed([job("running", { kind: "undo", done: 312, total: 1204 })]);
  assert.equal(page.el("confirm-running").hidden, false);
  assert.equal(page.el("confirm-progress").textContent, "Putting names back… 312 of 1,204");
});

test("an undo that put every name back says All N names put back and the folder sentence", async () => {
  const page = await undoPressed([undoneJob({ restored: 1204 })]);
  assert.equal(shown(page.el("finish-headline")), "All 1,204 names put back");
  assert.equal(shown(page.el("finish-time")), EXACTLY);
  assert.equal(page.el("finish-undo-box").hidden, true, "Undo was offered after an undo");
});

test("an undo that put back only some says how many could not be, and no folder sentence", async () => {
  const page = await undoPressed([undoneJob({ restored: 121, failed: 3 })]);
  assert.equal(shown(page.el("finish-headline")), "121 names put back, 3 could not be.");
  assert.equal(page.el("finish-time").hidden, true, "a partial undo said the folder is as it was");
});

test("an undo where some names were already back makes no folder claim", async () => {
  const page = await undoPressed([undoneJob({ restored: 120, already_undone: 4 })]);
  assert.ok(!shown(page.el("finish-headline")).startsWith("All "), "a partial undo said All");
  assert.equal(page.el("finish-time").hidden, true);
});

/* C6.1 (the maintainer's ruling, 2026-11-16): `121 names put back. 3 were already back under their old
 * names.` - `could not be` only when a name could not be put back; one is singular. */
test("C6.1: names already back are said after the headline, and could not be only when one failed", async () => {
  const back = await undoPressed([undoneJob({ restored: 121, already_undone: 3 })]);
  assert.equal(
    shown(back.el("finish-headline")),
    "121 names put back. 3 were already back under their old names."
  );
  const one = await undoPressed([undoneJob({ restored: 121, already_undone: 1 })]);
  assert.equal(shown(one.el("finish-headline")), "121 names put back. 1 was already back under its old name.");
  const both = await undoPressed([undoneJob({ restored: 1, failed: 2, already_undone: 1_204 })]);
  assert.equal(
    shown(both.el("finish-headline")),
    "1 name put back, 2 could not be. 1,204 were already back under their old names."
  );
});

/* --- M14 Y3: the folder sentence only when it is true ----------------------------------- */

/* An undo pressed and finished, with `GET api/sessions` answering `sessions` afterwards. */
async function undoneWith(result, sessions) {
  const page = await finishedPage(renamedJob({}), { sessions: sessions });
  page.context.PAM.confirm.CONFIRM.schedule = function (step) {
    page.timers.push(step);
  };
  answer(page, "api/job", 200, undoneJob(result));
  /* The boot's History probe (M15 Task 3, AM3) is not Done's own question after an undo. */
  page.sessionsBefore = gets(page, "api/sessions").length;
  page.el("finish-undo").click();
  await settle();
  return page;
}

test("an undo of the folder's only rename says the folder is exactly as it was", async () => {
  const only = [200, { sessions: [sessionRow(SESSION, { undone: true, undoable: false })] }];
  const page = await undoneWith({}, only);
  assert.equal(
    gets(page, "api/sessions").length,
    page.sessionsBefore + 1,
    "the folder's sessions were not asked for"
  );
  assert.equal(shown(page.el("finish-headline")), "All 124 names put back");
  assert.equal(shown(page.el("finish-time")), EXACTLY);
});

test("an undo of the newer of two renames says its names came back and nothing about the folder", async () => {
  const two = [
    200,
    {
      sessions: [
        sessionRow(SESSION, { undone: true, undoable: false }),
        sessionRow("s-20261113-090000", { renamed: 3 }),
      ],
    },
  ];
  const page = await undoneWith({}, two);
  assert.equal(shown(page.el("finish-headline")), "All 124 names put back");
  assert.equal(page.el("finish-time").hidden, true, "the folder was said to be as it was");
  /* A refused listing proves nothing about the folder either. */
  const refused = await undoneWith({}, [409, { error: CHANGED }]);
  assert.equal(shown(refused.el("finish-headline")), "All 124 names put back");
  assert.equal(refused.el("finish-time").hidden, true, "a refused listing was read as an empty one");
});

/* Task 9Y review (M3469): the undone session is the OLDER of two, and only the NEWER - the first
 * row, since the server sends them newest first - can still be undone. A rule that read every row
 * but the first said the folder was as it was. */
test("an undo of the older of two renames, the newer still standing, says nothing about the folder", async () => {
  const two = [
    200,
    {
      sessions: [
        sessionRow("s-20261115-090000", { renamed: 3 }),
        sessionRow(SESSION, { undone: true, undoable: false }),
      ],
    },
  ];
  const page = await undoneWith({}, two);
  assert.equal(shown(page.el("finish-headline")), "All 124 names put back");
  assert.equal(page.el("finish-time").hidden, true, "the folder was said to be as it was");
});

test("one name put back is singular, with every name back and with some not", async () => {
  const whole = await undoneWith({ restored: 1 });
  assert.equal(shown(whole.el("finish-headline")), "1 name put back");
  assert.equal(shown(whole.el("finish-time")), EXACTLY);
  const part = await undoneWith({ restored: 1, failed: 2 });
  assert.equal(shown(part.el("finish-headline")), "1 name put back, 2 could not be.");
  assert.equal(part.el("finish-time").hidden, true);
});

/* --- U14 / U12: a rename that did not run ------------------------------------------- */

test("a cancelled rename shows the job's sentence verbatim and no headline", async () => {
  const page = await finishedPage(job("cancelled", { lines: [CHANGED] }));
  assert.equal(page.el("finish").hidden, false);
  assert.deepEqual(lines(page.el("finish-lines")), [CHANGED]);
  assert.equal(page.el("finish-headline").hidden, true);
  assert.equal(page.el("finish-undo-box").hidden, true);
  assert.equal(page.el("finish-recovery").hidden, true);
  assert.equal(shown(page.el("confirm-back")), BACK);
});

test("a rename that died shows C6.2's heading and recovery's own line", async () => {
  const page = await finishedPage(job("failed", { recovery: RECOVERY_LINE, lines: ["ignored"] }));
  assert.equal(page.el("finish-recovery").hidden, false);
  assert.equal(shown(page.el("finish-headline")), PART_WAY);
  assert.equal(page.el("finish-recovery-title").hidden, true, "U12's heading under C6.2's");
  assert.equal(page.el("finish-recovery-line").textContent, RECOVERY_LINE);
  assert.deepEqual(lines(page.el("finish-lines")), []);
  /* It names no session, so no Undo and no line promising one. */
  assert.equal(page.el("finish-time").hidden, true);
});

test("an undo that died keeps U12's heading over recovery's line, and no headline", async () => {
  const page = await finishedPage(
    job("failed", { kind: "undo", recovery: RECOVERY_LINE, session: SESSION })
  );
  assert.equal(page.el("finish-recovery").hidden, false);
  assert.equal(shown(page.el("finish-recovery-title")), RECOVERED);
  assert.equal(page.el("finish-recovery-line").textContent, RECOVERY_LINE);
  assert.equal(page.el("finish-headline").hidden, true);
  assert.equal(page.el("finish-time").hidden, true);
});

/* S2-F1 (Task 6 Stage 2, the controller's fix): a rename that died names its session. */
function diedRename(extra) {
  return job("failed", Object.assign({ recovery: RECOVERY_LINE, session: SESSION }, extra || {}));
}

test("a rename that died after renaming offers Undo when its session can still be undone", async () => {
  const page = await finishedPage(diedRename());
  assert.equal(page.el("finish-recovery-line").textContent, RECOVERY_LINE);
  /* C6.2: the second line, exactly when the server says the session can be undone. */
  assert.equal(shown(page.el("finish-headline")), PART_WAY);
  assert.equal(shown(page.el("finish-time")), RENAMED_BEFORE);
  assert.equal(gets(page, "api/session/" + SESSION).length, 1);
  assert.equal(page.el("finish-undo-box").hidden, false, "no Undo after a rename that died");
  assert.equal(page.el("finish-undo").textContent, UNDO);
  page.el("finish-undo").click();
  page.el("finish-undo").click();
  await settle();
  const sent = posts(page, "api/session/" + SESSION + "/undo");
  assert.equal(sent.length, 1, "the undo was posted " + sent.length + " times");
  assert.deepEqual(sent[0].body, { nonce: SESSION_NONCE });
});

test("a died job offers no Undo when its session cannot be undone, names none, or was an undo", async () => {
  /* The server says the session cannot be undone (recovery put everything back). */
  const spent = await readyPage({ jobs: [diedRename()] });
  answer(spent, "api/session/" + SESSION, 200, { session_id: SESSION, undoable: false, nonce: "x" });
  tickBox(spent, true);
  spent.el("confirm-rename").click();
  await settle();
  assert.equal(gets(spent, "api/session/" + SESSION).length, 1);
  assert.equal(spent.el("finish-undo-box").hidden, true, "Undo over a session with nothing to undo");
  assert.equal(shown(spent.el("finish-headline")), PART_WAY);
  assert.equal(spent.el("finish-time").hidden, true, "C6.2's line promised an undo the server refuses");
  /* A died rename that names no session, and a died undo: nothing is asked for. */
  for (const final of [diedRename({ session: null }), diedRename({ kind: "undo" })]) {
    const page = await finishedPage(final);
    assert.equal(page.el("finish-undo-box").hidden, true, "Undo after a died " + final.kind);
    assert.equal(
      page.requests.filter((each) => each.url.indexOf("api/session/") === 0).length,
      0,
      "a died " + final.kind + " asked for a session"
    );
  }
});

/* --- the page around the screen -------------------------------------------------------- */

function press(page, key) {
  page.document.dispatch("keydown", { key: key, target: page.document.body, preventDefault() {} });
}

test("the list's keys are off while the screen is open and back on after Keep checking names", async () => {
  const page = await readyPage();
  press(page, "?");
  assert.equal(page.el("shortcuts").hidden, true, "a list key worked under Confirm");
  page.el("confirm-back").click();
  await settle();
  press(page, "?");
  assert.equal(page.el("shortcuts").hidden, false, "the list's keys stayed off after going back");
});

test("the step pills mark Review done and Rename current, and go back with the list", async () => {
  const page = await readyPage();
  assert.equal(page.el("step-review").getAttribute("data-step"), "done");
  assert.equal(page.el("step-review").textContent, "3 Review ✓");
  assert.equal(page.el("step-rename").getAttribute("data-step"), "current");
  page.el("confirm-back").click();
  await settle();
  assert.equal(page.el("step-review").getAttribute("data-step"), "current");
  assert.equal(page.el("step-review").textContent, "3 Review");
  assert.equal(page.el("step-rename").getAttribute("data-step"), "next");
});

test("Keep checking names goes back to the list and asks for its numbers again", async () => {
  const page = await readyPage();
  const before = page.requests.filter((each) => each.url.startsWith("api/plan/7/list")).length;
  page.el("confirm-back").click();
  await settle();
  assert.equal(page.el("confirm").hidden, true);
  for (const id of ["header-row", "controls", "main", "footer"]) {
    assert.equal(page.el(id).hidden, false, id + " stayed hidden after going back");
  }
  const after = page.requests.filter((each) => each.url.startsWith("api/plan/7/list")).length;
  assert.equal(after, before + 1, "the list's numbers were not asked for again");
});

/* --- the Stage 1 page review's checks (M3335, M3340, M3347, M3352) ----------------------- */

/* One answer for `url`, put in front of the page's stub. */
function answer(page, url, status, payload) {
  const before = page.context.fetch;
  page.context.fetch = function (asked, options) {
    if (asked === url) {
      page.requests.push({
        url: asked,
        method: options && options.method ? options.method : "GET",
        body: options && options.body ? JSON.parse(options.body) : null,
        contentType: options && options.headers ? options.headers["Content-Type"] : undefined,
      });
      return Promise.resolve({
        ok: status < 400,
        status: status,
        text: () => Promise.resolve(JSON.stringify(payload)),
      });
    }
    return before(asked, options);
  };
}

test("Confirm shows and sends the newest confirmation, never an earlier one", async () => {
  const page = await readyPage();
  page.el("confirm-back").click();
  await settle();
  answer(page, "api/plan/7/confirmation", 200, confirmation({ renames: 3, nonce: "n-2nd_x" }));
  page.el("footer-rename").click();
  await settle();
  assert.equal(page.el("confirm-renames").textContent, "3 files");
  assert.equal(page.el("confirm-rename").textContent, "Rename 3 files");
  tickBox(page, true);
  page.el("confirm-rename").click();
  await settle();
  const sent = posts(page, "api/plan/7/apply");
  assert.equal(sent.length, 1);
  assert.deepEqual(sent[0].body, { nonce: "n-2nd_x", folder_confirmed: true });
});

test("Rename N files stays off after a refused rename, however the box is ticked", async () => {
  const page = await pressedPage({ apply: [409, { error: CHANGED }] });
  tickBox(page, false);
  tickBox(page, true);
  assert.equal(page.el("confirm-rename").disabled, true, "a spent nonce's button came back on");
  page.el("confirm-rename").click();
  await settle();
  assert.equal(posts(page, "api/plan/7/apply").length, 1);
});

test("Done offers no Undo when nothing was renamed", async () => {
  const page = await finishedPage(
    renamedJob({ renamed: 0, failed: 124 }, { lines: [FAILURE_LINE] })
  );
  assert.equal(shown(page.el("finish-headline")), "0 files renamed, 124 could not be.");
  assert.equal(page.el("finish-undo-box").hidden, true, "Undo was offered over no rename");
});

test("a refused undo shows the server's sentence verbatim", async () => {
  const page = await finishedPage(renamedJob({}));
  answer(page, "api/session/" + SESSION + "/undo", 409, { error: CHANGED });
  page.el("finish-undo").click();
  await settle();
  assert.equal(posts(page, "api/session/" + SESSION + "/undo").length, 1);
  assert.deepEqual(lines(page.el("finish-lines")), [CHANGED]);
});

/* --- S1P-F2 (Task 6 Stage 1 page review): a reload finds the rename again ------------------ */

test("a reload during a rename brings back its progress, not the list", async () => {
  const page = await listPage({
    bootJob: job("running", { done: 312, total: 1204 }),
    jobs: [job("running", { done: 1000, total: 1204 })],
  });
  assert.equal(page.el("confirm").hidden, false, "the reload drew the list over a running rename");
  assert.equal(page.el("welcome").hidden, true);
  assert.equal(page.el("confirm-running").hidden, false);
  assert.equal(page.el("confirm-ready").hidden, true);
  assert.equal(page.el("confirm-progress").textContent, "Renaming… 312 of 1,204");
  assert.equal(page.el("confirm-keep-open").textContent, KEEP_OPEN);
  assert.equal(page.el("confirm-back").hidden, true);
  await tick(page);
  assert.equal(page.el("confirm-progress").textContent, "Renaming… 1,000 of 1,204");
  /* The press before the reload owns the move: the footer opens no second Confirm. */
  page.el("footer-rename").click();
  await settle();
  assert.equal(gets(page, "api/plan/7/confirmation").length, 0, "a second Confirm was asked for");
});

test("a reload after a rename finished shows Done with its Undo", async () => {
  const page = await listPage({ bootJob: renamedJob({}) });
  assert.equal(page.el("confirm").hidden, false);
  assert.equal(page.el("finish").hidden, false);
  assert.equal(shown(page.el("finish-headline")), "124 files renamed");
  assert.equal(page.el("finish-undo-box").hidden, false);
  assert.equal(page.el("confirm-back").hidden, true);
  assert.equal(page.timers.length, 0, "a finished rename was polled again");
});

test("a reload during an undo brings back Putting names back", async () => {
  const page = await listPage({ bootJob: job("running", { kind: "undo", done: 5, total: 1204 }) });
  assert.equal(page.el("confirm-running").hidden, false);
  assert.equal(page.el("confirm-progress").textContent, "Putting names back… 5 of 1,204");
});

test("a reload with no rename or undo leaves the list as it was", async () => {
  for (const boot of [job("idle", { kind: null }), job("running", { kind: "read", done: 3, total: 9 })]) {
    const page = await listPage({ bootJob: boot });
    assert.equal(page.el("confirm").hidden, true, "Confirm opened over a " + boot.kind + " job");
    assert.equal(page.timers.length, 0);
    assert.equal(gets(page, "api/job").length, 1);
  }
});

/* --- M14 Y2: Done offers Start reading ------------------------------------------------- */

/* `GET api/job` answers `list` in turn from now on (the last repeats). */
function jobsInTurn(page, list) {
  const left = list.slice();
  const before = page.context.fetch;
  page.context.fetch = function (url, options) {
    if (url === "api/job") {
      page.requests.push({ url: url, method: "GET", body: null, contentType: undefined });
      const next = left.length > 1 ? left.shift() : left[0];
      return Promise.resolve({
        ok: true,
        status: 200,
        text: () => Promise.resolve(JSON.stringify(next)),
      });
    }
    return before(url, options);
  };
}

/* A request that is never answered: it stays in flight. */
function hold(page, url) {
  const before = page.context.fetch;
  page.context.fetch = function (asked, options) {
    if (asked === url) {
      page.requests.push({ url: asked, method: "GET", body: null, contentType: undefined });
      return new Promise(function () {});
    }
    return before(asked, options);
  };
}

/* The Reading step's poll timer, driven by the check like Confirm's. */
function readingTimer(page) {
  page.context.PAM.reading.READING.schedule = function (step) {
    page.timers.push(step);
  };
}

function assertReadingStep(page) {
  assert.equal(page.el("confirm").hidden, true, "Done stayed over the Reading step");
  assert.equal(page.el("welcome").hidden, false, "the Reading step is not shown");
  assert.equal(page.el("welcome-folder").textContent, ROOT);
  assert.equal(shown(page.el("welcome-start-reading")), START_READING);
  assert.equal(page.el("welcome-review").disabled, true, "the renamed plan was offered for review");
  assert.equal(posts(page, "api/job/read").length, 0, "Done's link started a read by itself");
}

test("after a rename Done offers Start reading, which opens the Reading step on the server's folder", async () => {
  const page = await finishedPage(renamedJob({}));
  assert.equal(shown(page.el("finish-read")), START_READING);
  page.el("finish-read").click();
  await settle();
  assertReadingStep(page);
});

test("after an undo Done offers Start reading too", async () => {
  const page = await undoneWith({});
  assert.equal(shown(page.el("finish-headline")), "All 124 names put back");
  assert.equal(shown(page.el("finish-read")), START_READING);
  page.el("finish-read").click();
  await settle();
  assertReadingStep(page);
});

test("Done's Start reading reads through the Reading step, once, and Review names opens the plan the read made", async () => {
  const newPlan = { plan_id: 9, status: "open", binding: 4, folder: ROOT };
  const page = await finishedPage(renamedJob({}), {
    pings: [{ plan_id: 7, status: "applied", binding: 1, folder: ROOT }, newPlan],
  });
  readingTimer(page);
  let reloads = 0;
  page.context.DONE.reload = function () {
    reloads += 1;
  };
  /* What the server answers: the finished rename until the read starts, then the read. */
  jobsInTurn(page, [
    renamedJob({}),
    readJob("running", { done: 3, total: 9 }),
    readJob("done", { done: 9, total: 9, lines: ["Nothing new to read."] }),
  ]);
  page.el("finish-read").click();
  await settle();
  page.el("welcome-start-reading").click();
  page.el("welcome-start-reading").click();
  await settle();
  assert.equal(posts(page, "api/job/read").length, 1, "the read was posted more than once");
  assert.equal(page.el("welcome-reading-progress").textContent, "3 of 9");
  await tick(page);
  assert.equal(page.el("welcome-review").disabled, false, "the read's plan cannot be reviewed");
  assert.equal(reloads, 0, "the page reloaded before Review names was pressed");
  page.el("welcome-review").click();
  await settle();
  assert.equal(reloads, 1, "Review names did not open the new plan");
  assert.equal(
    page.requests.filter((each) => each.url.indexOf("api/plan/9") === 0).length,
    0,
    "this page drew the new plan's list over its own"
  );
});

test("Done's Start reading does nothing while an undo is being asked for", async () => {
  const page = await finishedPage(renamedJob({}));
  hold(page, "api/session/" + SESSION);
  page.el("finish-undo").click();
  page.el("finish-read").click();
  await settle();
  assert.equal(gets(page, "api/ping").length, 1, "Start reading went ahead under an undo");
  assert.equal(page.el("welcome").hidden, true);
});

test("a rename that did not run offers no Start reading", async () => {
  const page = await finishedPage(job("cancelled", { lines: [CHANGED] }));
  assert.equal(page.el("finish-read").hidden, true, "Start reading after a cancelled rename");
  page.el("finish-read").click();
  await settle();
  assert.equal(gets(page, "api/ping").length, 1);
  assert.equal(page.el("welcome").hidden, true);
});

test("Done offers no Start reading once pam has stopped", async () => {
  const page = await finishedPage(renamedJob({}));
  page.context.serverGone();
  page.el("finish-read").click();
  await settle();
  assert.equal(gets(page, "api/ping").length, 1, "a stopped page asked the server");
  assert.equal(page.el("welcome").hidden, true);
  /* A Done drawn after pam stopped does not offer it at all. */
  page.context.PAM.done.finishMoving(renamedJob({}));
  assert.equal(page.el("finish-read").hidden, true, "Start reading on a stopped page");
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
