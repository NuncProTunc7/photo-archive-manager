/*
 * HISTORY, EXECUTED (M14 Task 7; UX 5 W22, X2, U14; ruling B4; the controller's T7-R1..T7-R5).
 *
 * The review page's fifteen classic scripts are loaded into one `vm` context in the page's own
 * order - so `app.js` boots a bound page as it does in a browser - over a small hand-written DOM
 * and a stub `fetch` answering the routes as M14 Tasks 6b and 7 built them (`GET job`,
 * `GET sessions`, `GET session/{id}`, `POST session/{id}/undo`). The page boots on a finished
 * rename (a reload after it: S1P-F2), so it opens on Done, where History is reached (T7-R1).
 *
 * Nothing is read back out of the scripts to build an expectation: W22's words, W20's
 * `Undo this rename`, B4's `See what happened` and the implementer's words pending the design
 * model are spelled here, every number and flag comes from the stub, and "now" is INJECTED
 * (`HISTORY.now`) - a real clock is never read.
 *
 * `tests/test_webui_history_page.py` runs this file and reads the names it prints: every check
 * prints `ok <name>` or `FAIL <name>` and the run ends with `ran <n>`. A planted copy of a script
 * goes in through `PAM_HISTORY_JS` / `PAM_DONE_JS` / `PAM_CONFIRM_JS` / `PAM_WELCOME_JS`.
 */

"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const STATIC_DIR = path.join(__dirname, "..", "..", "pam", "webui", "static");

/* The page's order, spelled from design spec 2's tree and M14 Task 7 - never read off the page. */
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
  "pam-history.js": process.env.PAM_HISTORY_JS,
  "pam-done.js": process.env.PAM_DONE_JS,
  "pam-confirm.js": process.env.PAM_CONFIRM_JS,
  "pam-welcome.js": process.env.PAM_WELCOME_JS,
};

/* --- the copy, spelled from the specs ------------------------------------------------- */

/* W22. */
const TITLE = "History";
const REPORT = "Report";
/* W20's words, which X2 gives the row. */
const UNDO = "Undo this rename";
/* B4 / U22. */
const SEE_WHAT = "See what happened";
/* AM2 (M15 Task 2), verbatim after the count. */
const CLOUD_ONLY =
  " files became cloud-only after they were approved and were not renamed." +
  " Make them available offline and read the folder again.";
/* C6.5 (the maintainer's ruling, 2026-11-16): one file is singular, and a unit of several members
 * adds TOGETHER - spelled from the ruling. */
const CLOUD_ONLY_ONE =
  "1 file became cloud-only after it was approved and was not renamed." +
  " Make it available offline and read the folder again.";
const TOGETHER = " A photo and the files that go with it are skipped together.";
/* W21. */
const EXACTLY = "Your folder is exactly as it was before.";
/* The implementer's words (Task 7), pending the design model. */
const BACK = "Back";
const EMPTY = "pam has not renamed anything in this folder yet.";
/* Done's way back to the list (Task 6c). */
const KEEP_CHECKING = "Keep checking names";
/* The server's sentences, verbatim. */
const CHANGED = "Something changed since you looked. Check the names again.";
const CHOOSE = "Choose a folder first.";

/* --- the server's answers ------------------------------------------------------------- */

/* The session Done shows (the boot's finished rename), and two older rows History lists. */
const DONE_SESSION = "s-20261114-143205";
const OLDER = "s-20261113-235900";
const OLDEST = "s-20261028-091500";
/* A session whose every rename was refused: neither undone nor undoable - the one row
 * where the two flags do not mirror each other. */
const NOTHING = "s-20261001-080000";
/* Nonces a page would be tempted to tidy: sent exactly as received. */
const NONCES = {};
NONCES[DONE_SESSION] = " d0ne_N+/= ";
NONCES[OLDER] = "  older-Nonce==";
NONCES[OLDEST] = "x";

/* "Now": 14 November 2026, 3:00 PM, local - the day DONE_SESSION ran. */
const NOW = new Date(2026, 10, 14, 15, 0, 0);

function row(session, time, extra) {
  return Object.assign(
    { session_id: session, time: time, renamed: 124, undone: false, undoable: true, report: true },
    extra || {}
  );
}

/* Newest first, as the server sends them. */
function fourRows() {
  return [
    row(DONE_SESSION, "2026-11-14T14:32:05", { renamed: 124, undone: true, undoable: false }),
    row(OLDER, "2026-11-13T23:59:00", { renamed: 1204, report: false }),
    row(OLDEST, "2026-10-28T09:15:00", { renamed: 3, cloud_only: 2, cloud_only_units: 1 }),
    row(NOTHING, "2026-10-01T08:00:00", { renamed: 0, undoable: false, report: false, cloud_only: 0 }),
  ];
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

function renamedJob(result) {
  return job("done", {
    done: 124,
    total: 124,
    session: DONE_SESSION,
    result: Object.assign({ renamed: 124, failed: 0, not_approved: 0, elapsed_s: 3.1 }, result),
  });
}

function undoneJob(result) {
  return job("done", {
    kind: "undo",
    session: "s-20261114-150000",
    result: Object.assign({ restored: 3, failed: 0, already_undone: 0 }, result),
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

/* --- one bound page, booted on a finished job ------------------------------------------ */

/* The routes of a bound server with open plan 7. `opts.bootJob` is the boot's `GET api/job`;
 * `opts.jobs` the poll's answers in turn; `opts.sessions` History's answer; `opts.undo` the
 * undo post's answer. */
function boundRoutes(opts) {
  const jobs = (opts.jobs || []).slice();
  let lastJob = job("idle", { kind: null });
  let booted = false;
  return function (url) {
    if (url === "api/ping") {
      return [200, opts.ping || { plan_id: 7, status: "open" }];
    }
    if (url === "api/plan/7") {
      return [200, { total: 140, approved: 124, skipped: 2, remaining: 14, attention: 0 }];
    }
    if (url.startsWith("api/plan/7/list")) {
      return [200, { groups: [], undecided: 0, attention_undecided: 0, shortcut_count: 0, renames: 0 }];
    }
    if (url.startsWith("api/plan/7/items")) {
      return [200, { total: 0, items: [] }];
    }
    if (url === "api/plan/7/groups") {
      return [200, { groups: [] }];
    }
    if (url === "api/job" && !booted) {
      booted = true;
      return [200, opts.bootJob || renamedJob({})];
    }
    if (url === "api/job") {
      if (jobs.length > 0) {
        lastJob = jobs.shift();
      }
      return [200, lastJob];
    }
    if (url === "api/sessions") {
      return opts.sessions || [200, { sessions: fourRows() }];
    }
    for (const session of Object.keys(NONCES)) {
      if (url === "api/session/" + session) {
        return [200, { session_id: session, undoable: true, nonce: NONCES[session] }];
      }
      if (url === "api/session/" + session + "/undo") {
        return opts.undo || [200, { job: 2 }];
      }
    }
    return null;
  };
}

function loadPage(opts) {
  const requests = [];
  const document = makeDocument();
  for (const id of ["welcome", "confirm", "shortcuts", "steps", "finish", "history"]) {
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
  const timers = [];
  context.PAM.confirm.CONFIRM.schedule = function (step) {
    timers.push(step);
  };
  /* "Now", injected: a real clock is never read here. */
  context.PAM.history.HISTORY.now = function () {
    return NOW;
  };
  return { context, document, requests, timers, el: (id) => document.getElementById(id) };
}

async function settle() {
  for (let i = 0; i < 10; i += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
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

/* Done, after the boot found the finished job. */
async function donePage(opts) {
  const page = loadPage(opts);
  await settle();
  return page;
}

/* History, opened from Done. */
async function historyPage(opts) {
  const page = await donePage(opts);
  page.el("finish-history").click();
  await settle();
  return page;
}

/* One row's parts, by class. */
function parts(item) {
  const out = {};
  for (const child of item.children) {
    out[child.className] = child;
  }
  return out;
}

function rows(page) {
  return page.el("history-rows").children.map(parts);
}

const tests = [];
function test(name, body) {
  tests.push([name, body]);
}

/* --- T7-R1: reached from Done, and back ------------------------------------------------- */

test("Done after a rename carries History, and History opens from it", async () => {
  const page = await historyPage();
  assert.equal(page.el("finish-history").textContent, TITLE);
  /* One GET for the list header's availability probe (AM3), one for History's rows. */
  assert.equal(gets(page, "api/sessions").length, 2);
  assert.equal(page.el("history").hidden, false);
  assert.equal(page.el("finish").hidden, true, "Done stayed shown under History");
  assert.equal(page.el("history-title").textContent, TITLE);
  assert.equal(page.el("history-back").textContent, BACK);
  assert.equal(page.el("confirm-back").hidden, true, "Keep checking names showed on History");
});

test("Done after an undo carries History too", async () => {
  /* M14 Y3: the folder sentence needs every session here past undoing. */
  const sessions = [200, { sessions: [row(DONE_SESSION, "2026-11-14T14:32:05", { undone: true, undoable: false })] }];
  const page = await donePage({ bootJob: undoneJob(), sessions: sessions });
  assert.equal(shown(page.el("finish-time")), EXACTLY);
  assert.equal(page.el("finish").hidden, false);
  assert.equal(page.el("finish-history").hidden, false);
  assert.equal(page.el("finish-history").textContent, TITLE);
  page.el("finish-history").click();
  await settle();
  assert.equal(page.el("history").hidden, false);
});

test("Back returns to the Done History came from, as it was", async () => {
  const page = await historyPage();
  page.el("history-back").click();
  await settle();
  assert.equal(page.el("history").hidden, true);
  assert.equal(page.el("finish").hidden, false);
  assert.equal(shown(page.el("finish-headline")), "124 files renamed");
  assert.equal(page.el("finish-undo-box").hidden, false, "Done's own Undo went away");
  assert.equal(page.el("confirm-back").hidden, true, "Back un-hid Keep checking names");
  assert.equal(gets(page, "api/sessions").length, 2, "Back asked for the rows again");
});

/* --- W22: the rows ----------------------------------------------------------------------- */

test("the rows are the server's, in its order, with W22's labels from an injected now", async () => {
  const page = await historyPage();
  const drawn = rows(page);
  assert.equal(drawn.length, 4);
  assert.deepEqual(
    drawn.map((each) => each["history-time"].textContent),
    ["Today, 2:32 PM", "Yesterday, 11:59 PM", "October 28, 2026, 9:15 AM", "October 1, 2026, 8:00 AM"]
  );
  assert.equal(page.el("history-empty").hidden, true);
  assert.equal(page.el("history-rows").hidden, false);
});

test("the time label is Today, Yesterday or the date, on both sides of midnight", async () => {
  const page = loadPage();
  const label = page.context.PAM.history.timeLabel;
  /* Just after midnight: the minute before is yesterday; the same day's first minute, today. */
  const justAfter = new Date(2026, 10, 14, 0, 1, 0);
  assert.equal(label("2026-11-13T23:59:00", justAfter), "Yesterday, 11:59 PM");
  assert.equal(label("2026-11-14T00:00:30", justAfter), "Today, 12:00 AM");
  /* Just before midnight: the day's first minute is still today; the day before, yesterday. */
  const justBefore = new Date(2026, 10, 14, 23, 59, 0);
  assert.equal(label("2026-11-14T00:01:00", justBefore), "Today, 12:01 AM");
  assert.equal(label("2026-11-13T12:05:00", justBefore), "Yesterday, 12:05 PM");
  assert.equal(label("2026-11-12T23:59:59", justBefore), "November 12, 2026, 11:59 PM");
  /* Across a month and a year. */
  assert.equal(label("2025-12-31T18:00:00", new Date(2026, 0, 1, 9, 0)), "Yesterday, 6:00 PM");
  assert.equal(label("2025-12-30T18:00:00", new Date(2026, 0, 1, 9, 0)), "December 30, 2025, 6:00 PM");
});

test("a row reads N renamed, or N renamed · undone", async () => {
  const page = await historyPage();
  assert.deepEqual(
    rows(page).map((each) => each["history-count"].textContent),
    ["124 renamed · undone", "1,204 renamed", "3 renamed", "0 renamed"]
  );
});

test("AM2: a row says how many files became cloud-only, only when its report counted any", async () => {
  const page = await historyPage();
  const drawn = rows(page);
  assert.equal(drawn[2]["history-cloud-only"].textContent, "2" + CLOUD_ONLY + TOGETHER);
  for (const index of [0, 1, 3]) {
    assert.equal(drawn[index]["history-cloud-only"], undefined, "a cloud-only line on row " + index);
  }
  const text = page.context.PAM.history.cloudOnlyText;
  assert.equal(text({ cloud_only: 1204, cloud_only_units: 1204 }), "1,204" + CLOUD_ONLY);
  assert.equal(text({ cloud_only: 1205, cloud_only_units: 1204 }), "1,205" + CLOUD_ONLY + TOGETHER);
  assert.equal(text({ cloud_only: 1, cloud_only_units: 1 }), CLOUD_ONLY_ONE);
  assert.equal(text({ cloud_only: 0, cloud_only_units: 0 }), null);
});

test("Undo this rename is drawn exactly when the server says the session can be undone", async () => {
  const page = await historyPage();
  const undos = rows(page).map((each) => (each["history-undo"] ? each["history-undo"].textContent : null));
  assert.deepEqual(undos, [null, UNDO, UNDO, null]);
});

test("Report links to the session's report in a new tab, only when the report exists", async () => {
  const page = await historyPage();
  const drawn = rows(page);
  assert.equal(drawn[0]["history-report"].textContent, REPORT);
  assert.equal(drawn[0]["history-report"].getAttribute("href"), "api/session/" + DONE_SESSION + "/report");
  assert.equal(drawn[0]["history-report"].getAttribute("target"), "_blank");
  assert.equal(drawn[1]["history-report"], undefined, "Report drawn for a session with none");
  assert.equal(drawn[2]["history-report"].getAttribute("href"), "api/session/" + OLDEST + "/report");
  assert.equal(drawn[3]["history-report"], undefined, "Report drawn for a session with none");
});

test("a session with no report says No report, with the server's reason, and links nothing", async () => {
  const page = await historyPage();
  const drawn = rows(page);
  assert.equal(drawn[0]["history-no-report"], undefined, "No report drawn beside a Report link");
  assert.equal(drawn[1]["history-no-report"].textContent, "No report");
  assert.equal(
    drawn[1]["history-no-report"].getAttribute("title"),
    "pam has no report for this session: it was never written, or it was deleted."
  );
  assert.equal(drawn[3]["history-no-report"].textContent, "No report");
});

test("an archive with no sessions says so in one sentence", async () => {
  const page = await historyPage({ sessions: [200, { sessions: [] }] });
  assert.equal(shown(page.el("history-empty")), EMPTY);
  assert.equal(page.el("history-rows").children.length, 0);
  assert.equal(page.el("history-rows").hidden, true);
});

test("a refused listing shows the server's sentence verbatim", async () => {
  const page = await historyPage({ sessions: [409, { error: CHOOSE }] });
  assert.equal(shown(page.el("history-error")), CHOOSE);
  assert.equal(page.el("history-rows").children.length, 0);
});

/* --- X2: Undo from History is Done's undo ------------------------------------------------ */

test("a row's Undo this rename posts once, the nonce as received, then Done after the undo", async () => {
  const page = await historyPage({ jobs: [undoneJob()] });
  const button = rows(page)[1]["history-undo"];
  button.click();
  button.click();
  await settle();
  assert.equal(gets(page, "api/session/" + OLDER).length, 1);
  const sent = posts(page, "api/session/" + OLDER + "/undo");
  assert.equal(sent.length, 1, "the undo was posted " + sent.length + " times");
  assert.deepEqual(sent[0].body, { nonce: NONCES[OLDER] });
  assert.equal(posts(page, "api/session/" + DONE_SESSION + "/undo").length, 0);
  assert.equal(page.el("history").hidden, true);
  /* The poll's first answer is the finished undo: Done after it, W21. */
  assert.equal(page.el("finish").hidden, false);
  assert.equal(shown(page.el("finish-headline")), "All 3 names put back");
  /* M14 Y3: OLDEST can still be undone, so the folder is not as it was before. */
  assert.equal(shown(page.el("finish-time")), null, "the folder sentence with a session still undoable");
});

/* R7-F3 (Task 7 review): Done's Undo, then the same session's History Undo before the first
 * nonce request returns - one GET and one POST, never two. */
test("an undo already in flight from Done is not sent again from History's row", async () => {
  const sessions = [200, { sessions: [row(DONE_SESSION, "2026-11-14T14:32:05")] }];
  const page = await historyPage({ sessions: sessions, jobs: [undoneJob()] });
  page.el("finish-undo").click();
  rows(page)[0]["history-undo"].click();
  await settle();
  assert.equal(gets(page, "api/session/" + DONE_SESSION).length, 1, "the nonce was asked for twice");
  const sent = posts(page, "api/session/" + DONE_SESSION + "/undo");
  assert.equal(sent.length, 1, "the undo was posted " + sent.length + " times");
  assert.deepEqual(sent[0].body, { nonce: NONCES[DONE_SESSION] });
  /* Settled: the in-flight flag does not outlive the undo. */
  assert.equal(page.context.DONE.pressing, false, "the in-flight flag outlived the undo");
});

test("a refused undo from History shows the server's sentence where Done's would", async () => {
  const page = await historyPage({ undo: [409, { error: CHANGED }] });
  rows(page)[2]["history-undo"].click();
  await settle();
  assert.equal(posts(page, "api/session/" + OLDEST + "/undo").length, 1);
  assert.equal(page.el("finish").hidden, false);
  assert.equal(shown(page.el("finish-headline")), null, "the old Done's headline stayed over the refusal");
  assert.deepEqual(page.el("finish-lines").children.map((each) => each.textContent), [CHANGED]);
  assert.equal(page.el("finish-history").hidden, false);
});

/* Task 7 review (M3401, R7-F4): once pam has stopped, a row's Undo does nothing at all - no
 * request, and History stays on screen (not Done's blank finish part). */
test("a row's Undo does nothing once pam has stopped", async () => {
  const page = await historyPage();
  page.context.PAGE.stopped = true;
  rows(page)[1]["history-undo"].click();
  await settle();
  assert.equal(gets(page, "api/session/" + OLDER).length, 0);
  assert.equal(page.el("history").hidden, false, "History left the screen on a stopped page");
  assert.equal(page.el("finish").hidden, true);
});

/* --- B4: See what happened ---------------------------------------------------------------- */

test("B4: See what happened is drawn under a failure headline only, linking to the report", async () => {
  const failed = await donePage({ bootJob: renamedJob({ renamed: 119, failed: 5 }) });
  assert.equal(shown(failed.el("finish-headline")), "119 files renamed, 5 could not be.");
  assert.equal(shown(failed.el("finish-report")), SEE_WHAT);
  assert.equal(failed.el("finish-report-box").hidden, false);
  assert.equal(failed.el("finish-report").getAttribute("href"), "api/session/" + DONE_SESSION + "/report");
  const clean = await donePage();
  assert.equal(clean.el("finish-report-box").hidden, true, "See what happened under no failure");
  const undone = await donePage({ bootJob: undoneJob() });
  assert.equal(undone.el("finish-report-box").hidden, true, "See what happened after an undo");
});

/* --- AM3 / E7: History from the list and from Welcome ------------------------------------- */

const IDLE = job("idle", { kind: null });
const NONE = [200, { sessions: [] }];
const FOLDER = "C:\\Users\\user\\Photos";

/* The bound page on its list: the boot found no job, so nothing stands in for the list. */
async function listPage(opts) {
  const page = loadPage(Object.assign({ bootJob: IDLE }, opts || {}));
  await settle();
  return page;
}

/* A bound server `pam start` began with a folder and no plan: Welcome, its folder named. */
async function welcomePage(opts) {
  const page = loadPage(
    Object.assign({ bootJob: IDLE, ping: { plan_id: null, folder: FOLDER, recovery: null } }, opts || {})
  );
  await settle();
  return page;
}

test("AM3: the list header's History is drawn exactly when the folder has a session", async () => {
  const some = await listPage();
  assert.equal(some.el("list-history").hidden, false, "hidden with sessions");
  assert.equal(some.el("list-history").textContent, TITLE);
  const none = await listPage({ sessions: NONE });
  assert.equal(none.el("list-history").hidden, true, "shown with no sessions");
});

test("AM3: Welcome's History is drawn exactly when the bound folder has a session", async () => {
  const some = await welcomePage();
  assert.equal(some.el("welcome").hidden, false);
  assert.equal(some.el("welcome-history").hidden, false, "hidden with sessions");
  assert.equal(some.el("welcome-history").textContent, TITLE);
  const none = await welcomePage({ sessions: NONE });
  assert.equal(none.el("welcome-history").hidden, true, "shown with no sessions");
  const refused = await welcomePage({ sessions: [409, { error: CHOOSE }] });
  assert.equal(refused.el("welcome-history").hidden, true, "shown over a refused listing");
});

test("E7: History from the list lists the rows, and Back returns to the list", async () => {
  const page = await listPage();
  page.el("list-history").click();
  await settle();
  assert.equal(page.el("history").hidden, false);
  assert.equal(page.el("confirm").hidden, false);
  assert.equal(page.el("main").hidden, true, "the list stayed under History");
  assert.equal(rows(page).length, 4);
  assert.equal(page.el("confirm-back").hidden, true, "Keep checking names showed on History");
  page.el("history-back").click();
  await settle();
  assert.equal(page.el("history").hidden, true);
  assert.equal(page.el("confirm").hidden, true, "Back left the Confirm screen up");
  for (const id of ["header-row", "controls", "main", "footer"]) {
    assert.equal(page.el(id).hidden, false, id + " stayed hidden after Back");
  }
  assert.equal(page.el("finish").hidden, true, "Back went to Done");
});

test("E7: History from Welcome lists the rows, and Back returns to Welcome", async () => {
  const page = await welcomePage();
  page.el("welcome-history").click();
  await settle();
  assert.equal(page.el("history").hidden, false);
  assert.equal(page.el("confirm").hidden, false);
  assert.equal(page.el("welcome").hidden, true, "Welcome stayed under History");
  assert.equal(rows(page).length, 4);
  page.el("history-back").click();
  await settle();
  assert.equal(page.el("history").hidden, true);
  assert.equal(page.el("confirm").hidden, true);
  assert.equal(page.el("welcome").hidden, false, "Back did not return to Welcome");
  assert.equal(page.el("main").hidden, true, "Back showed the list over Welcome");
  assert.equal(page.el("finish").hidden, true, "Back went to Done");
});

test("AM3: choosing another folder takes Welcome's History away", async () => {
  const page = await welcomePage();
  assert.equal(page.el("welcome-history").hidden, false, "not drawn to begin with");
  page.el("welcome-change").click();
  await settle();
  assert.equal(page.el("welcome-history").hidden, true, "History stayed over a folder no longer chosen");
});

test("E7: an undo from Welcome's History reaches Done with no Keep checking names", async () => {
  const page = await welcomePage({ jobs: [undoneJob()] });
  page.el("welcome-history").click();
  await settle();
  rows(page)[1]["history-undo"].click();
  /* Straight after the press, before the undo's answers: pressHistoryUndo's own restore of
   * Keep checking names (Done's finished undo hides it again by itself, so only this window
   * can tell the Welcome origin's rule from the others). */
  assert.equal(page.el("confirm-back").hidden, true, "Keep checking names offered with no list behind Welcome");
  await settle();
  assert.equal(page.el("finish").hidden, false, "Done was not reached after the undo");
  assert.equal(page.el("history").hidden, true);
  assert.equal(page.el("confirm-back").hidden, true, "Keep checking names offered after the undo");
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
      if (process.env.PAM_STACK) console.log(error.stack);
      console.log("  " + message.replace(/\s+/g, " ").slice(0, 300));
    }
  }
  console.log("ran " + (tests.length - failed));
  process.exit(failed === 0 ? 0 : 1);
})();
