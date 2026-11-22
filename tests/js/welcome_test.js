/*
 * WELCOME, EXECUTED (M14 Task 4 Step 7, Task 5e; U8's W18, U9, U10, U11, U12, U13, U21; OD-4).
 *
 * The review page's twelve classic scripts are loaded into one `vm` context in the page's own
 * order - so `app.js` boots exactly as it does in a browser - over a small hand-written DOM
 * and a stub `fetch` that answers the routes as M14 Task 4a-4c built them. Nothing is read
 * back out of the scripts to build an expectation: every sentence below is spelled from W18,
 * U10, U12 and U13, or from the server's own sentences (`folders.NETWORK_REFUSAL`,
 * `launcher.ANOTHER_PAM`, `folders.PATH_REQUIRED`, `launcher.SEVERAL_OPEN_PLANS`), and every
 * path from the stub's own answer.
 *
 * `tests/test_webui_welcome_page.py` runs this file and reads the names it prints: every
 * check prints `ok <name>` or `FAIL <name>` and the run ends with `ran <n>`. A planted copy of
 * any of five scripts goes in through `PAM_CORE_JS` / `PAM_CHOOSER_JS` / `PAM_READING_JS` /
 * `PAM_WELCOME_JS` / `PAM_APP_JS`.
 *
 * M14 Task 5e: the Reading step (`pam-reading.js`). The poll's timer is replaced at load by one
 * the checks drive (`page.timers`), so no check waits a real second; the job's routes answer as
 * M14 Task 5d built them (`task-5d-report.md`, "The routes").
 */

"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const STATIC_DIR = path.join(__dirname, "..", "..", "pam", "webui", "static");

/* The page's order, spelled from design spec 2's tree and M14 Task 4 - never read off the
 * page (`load_order_test.js` holds the page to its order). */
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
  "pam-core.js": process.env.PAM_CORE_JS,
  "pam-chooser.js": process.env.PAM_CHOOSER_JS,
  "pam-reading.js": process.env.PAM_READING_JS,
  "pam-welcome.js": process.env.PAM_WELCOME_JS,
  "app.js": process.env.PAM_APP_JS,
};

/* --- the copy, spelled from the specs ------------------------------------------------- */

/* U8 W18, verbatim. */
const W18 = {
  title: "Give your photos names that make sense.",
  lead:
    "pam suggests a name for every photo from its date, place and event. You approve the " +
    "names. Nothing changes until you say so.",
  steps: ["Folder", "Reading your photos", "Review the names"],
  promises: [
    "Your photos stay on this computer. Nothing is uploaded.",
    "Only the file names change. Photos are never edited, moved or deleted.",
    "Every rename can be undone, any time.",
  ],
  button: "Review names",
};
/* OD-4 (ADOPTED): the mockup's last line, which the page must NOT draw. */
const OD4 = "You can start reviewing while pam keeps reading.";
/* U10: a chosen folder's control. U12: the heading over recovery's line. */
const START_READING = "Start reading";
const RECOVERED = "pam finished putting things right after it stopped.";
/* M14 Task 4 Step 7 (the plan): the control shown after a choice. */
const CHANGE = "Change";
/* The implementer's rulings for the chooser's own words (Task 4d report; no spec gives them). */
const RULED = {
  allDrives: "All drives",
  up: "Up one folder",
  chooseThis: "Choose this folder",
  noFolders: "No folders in here.",
};
/* The server's sentences, each verbatim (U13; U10; M14 Task 4b's two; paths.CANNOT_SEE_FOLDER). */
const REFUSALS = {
  network: [409, "pam works on folders on this computer. Copy the photos to this computer first."],
  lock: [409, "Another pam is working on this folder. Close it, then try again."],
  path: [400, "path: give the full path of a folder."],
  plans: [409, "pam has more than one open plan for C:\\Users\\user\\Photos (3, 4)."],
};
const CANNOT_SEE =
  "pam cannot see this folder, so it will not touch anything:\n  Z:\\Gone\n" +
  "If the photos are on a drive or a network folder, connect it and run the command again.";
/* A line `run_recovery` prints (M14 Task 4b's spelled test sentence). */
const RECOVERY_LINE = "pam finished tidying up after an interrupted run: 1 rename(s) not started.";

const TOP = {
  path: null,
  parent: null,
  folders: [],
  drives: ["C:\\", "D:\\"],
  known: [
    { path: "C:\\Users\\user\\Pictures\\Archive", reachable: true },
    { path: "E:\\Old", reachable: false },
  ],
};

/* U12, verbatim: what the page says when pam stops (UX 5's fourth state, rev 16). */
const U12 = {
  stopped: "pam has stopped.",
  lost: "Nothing is lost: every rename is written down before it happens.",
  restart: "Start pam again and it will put things right before it does anything else.",
};
/* U11's stopped sentence and U21's console line, and P2's - all the SERVER'S, spelled from
 * U11/U21 and Task 5d's job (`job_api.py`), shown by the page as they came. */
const STOPPED_LINE = "Stopped. pam read 40 of 100; the rest will be read next time.";
const NOTHING_NEW = "Nothing new to read.";
const U21_LINE = "Places and people come from pam enrich, in the console.";
/* M14 Task 5e: `Stop` - the controller's word for U11's cancellation. */
const STOP = "Stop";
/* `GET api/job` with no job (Task 5d), and a read's snapshot. */
const IDLE = { kind: null, state: "idle", done: 0, total: 0, eta_s: null, lines: [], recovery: null };
function jobAt(state, extra) {
  return Object.assign(
    { kind: "read", state: state, done: 0, total: 0, eta_s: null, lines: [], recovery: null },
    extra || {}
  );
}
/* A poll the server never answers: it is gone (a network failure, not a refusal). */
const GONE = "gone";

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
  doc.byId = byId;
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

function allText(node) {
  return [node.textContent].concat(node.children.map(allText)).join("\n");
}

/* --- one page load -------------------------------------------------------------------- */

/* `routes(url, options)` answers `[status, body]`, or null to leave the request pending. */
function loadPage(routes) {
  const requests = [];
  const delays = [];
  const document = makeDocument();
  /* As `index.html` ships it: Welcome hidden, the parts it stands in for shown. */
  document.getElementById("welcome").hidden = true;
  const context = vm.createContext({
    document: document,
    console: console,
    URLSearchParams: URLSearchParams,
    setTimeout: function (step, ms) {
      delays.push(ms);
      return setTimeout(step, ms);
    },
    clearTimeout: clearTimeout,
    fetch: function (url, options) {
      requests.push({
        url: url,
        method: options && options.method ? options.method : "GET",
        body: options && options.body ? JSON.parse(options.body) : null,
        binding: options && options.headers ? options.headers["X-Pam-Binding"] : undefined,
        contentType: options && options.headers ? options.headers["Content-Type"] : undefined,
      });
      const answer = routes(url, options);
      if (answer === null) {
        return new Promise(function () {});
      }
      /* A route may answer LATER: a promise of `[status, body]`, settled when the check says. */
      return Promise.resolve(answer).then(function ([status, payload]) {
        return {
          ok: status < 400,
          status: status,
          text: function () {
            return Promise.resolve(payload === undefined ? "" : JSON.stringify(payload));
          },
        };
      });
    },
  });
  context.window = context;
  for (const file of PAGE_SCRIPTS) {
    const source = PLANTED[file] || path.join(STATIC_DIR, file);
    vm.runInContext(fs.readFileSync(source, "utf-8"), context, { filename: file });
  }
  /* The poll's timer, driven by the checks (`tick`). The page's own is kept to be checked. */
  const timers = [];
  const pageSchedule = context.PAM.reading.READING.schedule;
  context.PAM.reading.READING.schedule = function (step) {
    timers.push(step);
  };
  return {
    context,
    document,
    requests,
    delays,
    timers,
    pageSchedule,
    el: (id) => document.getElementById(id),
  };
}

async function settle() {
  for (let i = 0; i < 8; i += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}

/* An unbound server: the ping says no plan, the chooser answers, bind answers `bind`. */
function unbound(bind, extra) {
  return function (url, options) {
    if (url === "api/ping") {
      return [200, { plan_id: null, status: null }];
    }
    if (url === "api/folders") {
      return [200, TOP];
    }
    if (url === "api/bind") {
      return bind(options);
    }
    const answer = extra ? extra(url, options) : null;
    /* M14 Task 5e: Welcome asks for the job once at load (a read may be running). */
    return answer === null && url === "api/job" ? [200, IDLE] : answer;
  };
}

function urls(page) {
  return page.requests.map((each) => each.url);
}

function binds(page) {
  return page.requests.filter((each) => each.url === "api/bind");
}

function buttonsIn(node) {
  return node.children.map((item) => item.children[0]).filter(Boolean);
}

async function welcomePage(bind, extra) {
  const page = loadPage(unbound(bind || (() => null), extra));
  await settle();
  return page;
}

async function choose(page, path) {
  page.el("welcome-paste-path").value = path;
  page.el("welcome-paste").dispatch("submit", { preventDefault() {} });
  await settle();
}

function assertChooserShowsNoFolder(page) {
  assert.equal(page.el("welcome-folder").hidden, true, "a folder is shown");
  assert.equal(page.el("welcome-change").hidden, true, "Change is shown with no choice");
  assert.equal(page.el("welcome-chooser").hidden, false, "the chooser is hidden");
  assert.equal(page.el("welcome-review").disabled, true, "Review names is on with no plan");
}

/* --- the checks ------------------------------------------------------------------------ */

const tests = [];
function test(name, body) {
  tests.push([name, body]);
}

test("an unbound server's page shows Welcome with W18's copy, never asks for a plan, and asks for the job once", async () => {
  const page = await welcomePage();
  assert.equal(page.el("welcome").hidden, false, "Welcome is hidden on an unbound server");
  for (const id of ["header-row", "controls", "presets", "main", "footer"]) {
    assert.equal(page.el(id).hidden, true, id + " is still shown on Welcome");
  }
  assert.equal(page.el("welcome-title").textContent, W18.title);
  assert.equal(page.el("welcome-lead").textContent, W18.lead);
  assert.deepEqual(
    [
      page.el("welcome-folder-title").textContent,
      page.el("welcome-reading-title").textContent,
      page.el("welcome-review-title").textContent,
    ],
    W18.steps
  );
  assert.deepEqual(
    page.el("welcome-promises").children.map((each) => each.textContent),
    W18.promises
  );
  assert.equal(page.el("welcome-review").textContent, W18.button);
  assert.equal(page.el("welcome-review").disabled, true, "Review names is on before a choice");
  assert.equal(page.el("welcome-start-reading").hidden, true);
  /* M14 Task 5e: the job is asked once, at load - a read may be running (a reload). */
  assert.deepEqual(urls(page), ["api/ping", "api/folders", "api/job"]);
  assert.equal((page.document.listeners.keydown || []).length, 0, "the list's keys stay on");
});

test("Welcome hides the step pills a drawn list had shown (HT-M14 F7)", async () => {
  const page = await welcomePage();
  page.el("steps").hidden = false; /* what `drawFooter` leaves after a list was drawn */
  page.context.showWelcome(true);
  assert.equal(page.el("steps").hidden, true, "the step pills stayed up on Welcome");
  assert.equal(page.el("presets").hidden, true, "Accept all stayed up on Welcome");
});

test("a bound server's page opens the list and never Welcome", async () => {
  const page = loadPage(function (url) {
    if (url === "api/ping") {
      return [200, { plan_id: 7, status: "open" }];
    }
    return null;
  });
  await settle();
  assert.equal(page.el("welcome").hidden, true, "Welcome was shown on a bound page");
  /* `api/sessions` is the list header's History probe (M15 Task 3, AM3). */
  assert.deepEqual(urls(page), ["api/ping", "api/sessions", "api/plan/7"]);
  for (const id of ["header-row", "controls", "presets", "main", "footer"]) {
    assert.equal(page.el(id).hidden, false, id + " was hidden on a bound page");
  }
  assert.equal(page.el("plan-title").textContent, "pam review — Plan 7");
  assert.equal((page.document.listeners.keydown || []).length, 1, "the list's keys are off");
});

test("the known archives and the drives are the server's, and an unreachable archive is not offered", async () => {
  const page = await welcomePage();
  const known = buttonsIn(page.el("welcome-known"));
  assert.deepEqual(
    known.map((each) => each.textContent),
    TOP.known.map((each) => each.path)
  );
  assert.deepEqual(
    known.map((each) => each.disabled),
    [false, true]
  );
  assert.deepEqual(
    buttonsIn(page.el("welcome-folders")).map((each) => each.textContent),
    TOP.drives
  );
});

test("a known archive binds its own path", async () => {
  const page = await welcomePage(() => [409, { error: REFUSALS.lock[1] }]);
  buttonsIn(page.el("welcome-known"))[0].click();
  await settle();
  assert.deepEqual(
    binds(page).map((each) => each.body),
    [{ path: "C:\\Users\\user\\Pictures\\Archive" }]
  );
});

test("a drive lists its folders, a folder its own, Up goes back, and Choose this folder binds the listing", async () => {
  const listings = {
    "C:\\": { path: "C:\\", parent: null, folders: [{ name: "Photos", path: "C:\\Photos" }] },
    "C:\\Photos": { path: "C:\\Photos", parent: "C:\\", folders: [] },
  };
  const page = await welcomePage(
    () => [200, { state: "chosen", archive_root: "C:\\Photos", plan_id: null, recovery: null }],
    function (url) {
      const asked = new URLSearchParams(url.split("?")[1] || "").get("path");
      return url.indexOf("api/folders?") === 0 ? [200, { ...listings[asked], drives: null, known: null }] : null;
    }
  );
  buttonsIn(page.el("welcome-folders"))[0].click();
  await settle();
  assert.equal(page.requests[page.requests.length - 1].url, "api/folders?path=C%3A%5C");
  assert.equal(page.el("welcome-place").textContent, "C:\\");
  assert.equal(page.el("welcome-up").textContent, RULED.allDrives);
  assert.equal(buttonsIn(page.el("welcome-known")).length, 0, "the known archives stay in a listing");
  const photos = buttonsIn(page.el("welcome-folders"));
  assert.deepEqual(photos.map((each) => each.textContent), ["Photos"]);
  photos[0].click();
  await settle();
  assert.equal(page.el("welcome-place").textContent, "C:\\Photos");
  assert.equal(page.el("welcome-up").textContent, RULED.up);
  assert.deepEqual(
    page.el("welcome-folders").children.map((each) => each.textContent),
    [RULED.noFolders]
  );
  assert.equal(page.el("welcome-choose-this").textContent, RULED.chooseThis);
  page.el("welcome-up").click();
  await settle();
  assert.equal(page.requests[page.requests.length - 1].url, "api/folders?path=C%3A%5C");
  page.el("welcome-up").click();
  await settle();
  assert.equal(page.requests[page.requests.length - 1].url, "api/folders");
  buttonsIn(page.el("welcome-folders"))[0].click();
  await settle();
  buttonsIn(page.el("welcome-folders"))[0].click();
  await settle();
  page.el("welcome-choose-this").click();
  await settle();
  assert.deepEqual(binds(page).map((each) => each.body), [{ path: "C:\\Photos" }]);
});

test("a bind to a folder with a plan opens the list", async () => {
  const page = await welcomePage(() => [
    200,
    { state: "bound", archive_root: "C:\\Users\\user\\Photos", plan_id: 3, recovery: null },
  ]);
  await choose(page, "C:\\Users\\user\\Photos");
  assert.equal(page.el("welcome").hidden, true, "Welcome stayed after a bind with a plan");
  for (const id of ["header-row", "controls", "presets", "main", "footer"]) {
    assert.equal(page.el(id).hidden, false, id + " stayed hidden");
  }
  assert.equal(page.requests[page.requests.length - 1].url, "api/plan/3");
  assert.equal(page.el("plan-title").textContent, "pam review — Plan 3");
  assert.equal((page.document.listeners.keydown || []).length, 1, "the list's keys are off");
});

test("every request names the binding the page last learned: the ping's, then the bind's", async () => {
  /* R1-F1: a page left open on a folder pam has since left must be refused, so every request the
   * page makes carries the number `ping` or `bind` gave it, as `X-Pam-Binding`. */
  const page = loadPage(function (url, options) {
    if (url === "api/ping") {
      return [200, { plan_id: null, status: null, binding: 4 }];
    }
    if (url === "api/folders") {
      return [200, TOP];
    }
    if (url === "api/bind") {
      return [200, { state: "bound", archive_root: "D:/Photos", plan_id: 3, recovery: null, binding: 7 }];
    }
    return null;
  });
  await settle();
  assert.equal(page.requests[0].url, "api/ping");
  assert.equal(page.requests[0].binding, undefined, "the ping named a binding before it had one");
  await choose(page, "D:/Photos");
  assert.equal(binds(page)[0].binding, "4", "the bind did not name the ping's binding");
  const after = page.requests.slice(page.requests.indexOf(binds(page)[0]) + 1);
  assert.ok(after.length > 0, "nothing was asked after the bind");
  for (const each of after) {
    assert.equal(each.binding, "7", each.url + " did not name the bind's binding");
  }
});

test("a bind that ran recovery shows U12's heading and recovery's own line, then Review names opens the list", async () => {
  const page = await welcomePage(() => [
    200,
    { state: "bound", archive_root: "C:\\Users\\user\\Photos", plan_id: 3, recovery: RECOVERY_LINE },
  ]);
  await choose(page, "C:\\Users\\user\\Photos");
  assert.equal(page.el("welcome").hidden, false, "the list replaced recovery's line unread");
  assert.equal(page.el("welcome-recovery").hidden, false);
  assert.equal(page.el("welcome-recovery-title").textContent, RECOVERED);
  assert.equal(page.el("welcome-recovery-line").textContent, RECOVERY_LINE);
  assert.ok(!urls(page).includes("api/plan/3"), "the list was opened before recovery was read");
  assert.equal(page.el("welcome-review").disabled, false, "Review names is off with a plan");
  page.el("welcome-review").click();
  await settle();
  assert.equal(page.el("welcome").hidden, true);
  assert.equal(page.requests[page.requests.length - 1].url, "api/plan/3");
});

test("a never-read folder is chosen: the server's path, Start reading offered, Review names off", async () => {
  const page = await welcomePage(() => [
    200,
    { state: "chosen", archive_root: "D:\\Scans", plan_id: null, recovery: null },
  ]);
  await choose(page, "d:/scans");
  assert.equal(page.el("welcome-folder").textContent, "D:\\Scans", "a path the server did not send");
  assert.equal(page.el("welcome-folder").hidden, false);
  assert.equal(page.el("welcome-change").hidden, false);
  assert.equal(page.el("welcome-change").textContent, CHANGE);
  assert.equal(page.el("welcome-chooser").hidden, true);
  assert.equal(page.el("welcome-start-reading").textContent, START_READING);
  assert.equal(page.el("welcome-start-reading").hidden, false);
  /* M14 Task 5e: ACTIVE - no plan is open, so a read supersedes nothing. */
  assert.equal(page.el("welcome-start-reading").disabled, false, "Start reading is inactive");
  assert.equal(page.el("welcome-review").disabled, true, "Review names is on with no plan");
  assert.equal(page.el("welcome-recovery").hidden, true);
  assert.deepEqual(
    urls(page).filter((each) => each.indexOf("api/job") === 0),
    ["api/job"],
    "a choice started a read, or asked for the job again"
  );
  assert.ok(!urls(page).some((each) => each.indexOf("api/plan") === 0), "Welcome asked for a plan");
});

test("a read folder with no open plan offers Start reading and opens no list", async () => {
  const page = await welcomePage(() => [
    200,
    { state: "bound", archive_root: "D:\\Scans", plan_id: null, recovery: null },
  ]);
  await choose(page, "D:\\Scans");
  assert.equal(page.el("welcome").hidden, false);
  assert.equal(page.el("welcome-start-reading").hidden, false);
  assert.equal(page.el("welcome-start-reading").disabled, false);
  assert.equal(page.el("welcome-review").disabled, true);
  assert.ok(!urls(page).some((each) => each.indexOf("api/plan") === 0), "a list with no plan");
});

test("each bind refusal shows the server's sentence verbatim, and no folder", async () => {
  /* Four refusals, counted from U13, U10 and Task 4b's two - an emptied table must not pass. */
  let seen = 0;
  for (const [name, [status, sentence]] of Object.entries(REFUSALS)) {
    seen += 1;
    const page = await welcomePage(() => [status, { error: sentence }]);
    await choose(page, "C:\\Users\\user\\Photos");
    assert.equal(page.el("welcome-error").textContent, sentence, name);
    assert.equal(page.el("welcome-error").hidden, false, name);
    assertChooserShowsNoFolder(page);
    assert.equal(page.el("welcome").hidden, false, name);
  }
  assert.equal(seen, 4, "the refusal table went quiet");
});

test("a refused bind after a choice takes the shown folder away", async () => {
  let answer = [200, { state: "chosen", archive_root: "D:\\Scans", plan_id: null, recovery: null }];
  const page = await welcomePage(() => answer);
  await choose(page, "D:\\Scans");
  assert.equal(page.el("welcome-folder").hidden, false);
  answer = [409, { error: REFUSALS.lock[1] }];
  page.el("welcome-change").click();
  await settle();
  await choose(page, "C:\\Users\\user\\Photos");
  assert.equal(page.el("welcome-error").textContent, REFUSALS.lock[1]);
  assertChooserShowsNoFolder(page);
  assert.equal(page.el("welcome-start-reading").hidden, true);
  /* R2 (M3038): `Change` above has already taken the folder away, so the refusal's own reset is
   * built by hand - a folder on show, then a refused bind with no `Change` between them. */
  answer = [200, { state: "chosen", archive_root: "D:\\Scans", plan_id: null, recovery: null }];
  await choose(page, "D:\\Scans");
  assert.equal(page.el("welcome-folder").hidden, false);
  answer = [409, { error: REFUSALS.lock[1] }];
  await page.context.PAM.welcome.chooseFolder("C:\\Users\\user\\Photos");
  await settle();
  assertChooserShowsNoFolder(page);
  assert.equal(page.el("welcome-start-reading").hidden, true);
});

test("a listing refusal shows the server's sentence verbatim", async () => {
  const page = await welcomePage(null, function (url) {
    return url.indexOf("api/folders?") === 0 ? [409, { error: CANNOT_SEE }] : null;
  });
  buttonsIn(page.el("welcome-folders"))[1].click();
  await settle();
  assert.equal(page.el("welcome-error").textContent, CANNOT_SEE);
  assert.equal(page.el("welcome-error").hidden, false);
});

test("the paste box posts the path as typed, once per choice", async () => {
  const page = await welcomePage(() => [
    200,
    { state: "chosen", archive_root: "D:\\Scans", plan_id: null, recovery: null },
  ]);
  let prevented = false;
  page.el("welcome-paste-path").value = "  d:/scans  ";
  page.el("welcome-paste").dispatch("submit", {
    preventDefault() {
      prevented = true;
    },
  });
  await settle();
  assert.equal(prevented, true, "the form's submit navigated the page");
  assert.deepEqual(binds(page).map((each) => each.body), [{ path: "  d:/scans  " }]);
  assert.equal(binds(page)[0].method, "POST");
});

test("Change goes back to the chooser and asks for the folders again", async () => {
  const page = await welcomePage(() => [
    200,
    { state: "chosen", archive_root: "D:\\Scans", plan_id: null, recovery: null },
  ]);
  await choose(page, "D:\\Scans");
  const asked = urls(page).filter((each) => each === "api/folders").length;
  page.el("welcome-change").click();
  await settle();
  assertChooserShowsNoFolder(page);
  assert.equal(page.el("welcome-start-reading").hidden, true);
  assert.equal(urls(page).filter((each) => each === "api/folders").length, asked + 1);
  assert.equal(binds(page).length, 1, "Change posted a bind");
});

test("OD-4's sentence is drawn nowhere, through every state", async () => {
  const states = [
    { state: "chosen", archive_root: "D:\\Scans", plan_id: null, recovery: null },
    { state: "bound", archive_root: "D:\\Scans", plan_id: 3, recovery: RECOVERY_LINE },
  ];
  for (const body of states) {
    const page = await welcomePage(() => [200, body]);
    await choose(page, "D:\\Scans");
    assert.ok(!allText(page.document.body).includes(OD4), "OD-4's sentence is drawn");
    assert.ok(allText(page.document.body).includes(W18.title), "the walk saw nothing");
  }
});

/* --- M14 Task 4 review R2's closures (M3029, M3031, M3032, M3047, M3048, M3051) ---------- */

test("a double submit of the paste box posts one bind", async () => {
  /* The bind stays pending, as a slow one does: the second submit lands while it is in flight. */
  const page = await welcomePage(() => null);
  page.el("welcome-paste-path").value = "D:\\Scans";
  page.el("welcome-paste").dispatch("submit", { preventDefault() {} });
  page.el("welcome-paste").dispatch("submit", { preventDefault() {} });
  buttonsIn(page.el("welcome-known"))[0].click();
  await settle();
  assert.deepEqual(binds(page).map((each) => each.body), [{ path: "D:\\Scans" }]);
});

test("a refusal leaves the screen when the next listing or choice succeeds", async () => {
  let answer = [409, { error: REFUSALS.lock[1] }];
  const page = await welcomePage(
    () => answer,
    function (url) {
      if (url === "api/folders?path=C%3A%5C") {
        return [409, { error: CANNOT_SEE }];
      }
      return url.indexOf("api/folders?") === 0
        ? [200, { path: "D:\\", parent: null, folders: [], drives: null, known: null }]
        : null;
    }
  );
  buttonsIn(page.el("welcome-folders"))[0].click();
  await settle();
  assert.equal(page.el("welcome-error").textContent, CANNOT_SEE);
  buttonsIn(page.el("welcome-folders"))[1].click();
  await settle();
  assert.equal(page.el("welcome-place").textContent, "D:\\");
  assert.equal(page.el("welcome-error").hidden, true, "a listing's refusal outlived the next listing");
  await choose(page, "C:\\Users\\user\\Photos");
  assert.equal(page.el("welcome-error").textContent, REFUSALS.lock[1]);
  answer = [200, { state: "chosen", archive_root: "D:\\Scans", plan_id: null, recovery: null }];
  await choose(page, "D:\\Scans");
  assert.equal(page.el("welcome-folder").textContent, "D:\\Scans");
  assert.equal(page.el("welcome-error").hidden, true, "a bind's refusal outlived the next bind");
});

test("a bind whose plan is then refused shows the server's sentence on the list", async () => {
  const sentence = "pam is working on another folder now. Reload the page.";
  const page = await welcomePage(
    () => [200, { state: "bound", archive_root: "C:\\Users\\user\\Photos", plan_id: 3, recovery: null }],
    (url) => (url === "api/plan/3" ? [409, { error: sentence }] : null)
  );
  await choose(page, "C:\\Users\\user\\Photos");
  assert.equal(page.el("welcome").hidden, true);
  assert.equal(page.el("list-note").textContent, sentence);
  assert.equal(page.el("list-note").hidden, false, "the plan's refusal was not shown");
});

test("the binding header is added to a copy: the caller's own headers are kept and left untouched", async () => {
  const page = loadPage(function (url) {
    if (url === "api/ping") {
      return [200, { plan_id: null, status: null, binding: 4 }];
    }
    if (url === "api/bind") {
      return [200, { state: "chosen", archive_root: "D:\\Scans", plan_id: null, recovery: null, binding: 4 }];
    }
    return url === "api/folders" ? [200, TOP] : [200, {}];
  });
  await settle();
  await choose(page, "D:\\Scans");
  /* R2 (M3049): with a binding known, every option the caller set still goes out - the method,
   * the body and the Content-Type. The list's own drivers never learn a binding, so this check
   * is the only one that sends a write through `withBinding`'s copy. */
  assert.equal(binds(page)[0].method, "POST", "the bind went out as a GET");
  assert.deepEqual(binds(page)[0].body, { path: "D:\\Scans" }, "the bind lost its body");
  assert.equal(binds(page)[0].contentType, "application/json", "the bind lost its Content-Type");
  assert.equal(binds(page)[0].binding, "4");
  const shared = { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" };
  await page.context.api("api/elsewhere", shared);
  assert.deepEqual(Object.keys(shared.headers), ["Content-Type"], "the caller's headers were changed");
  const sent = page.requests[page.requests.length - 1];
  assert.equal(sent.binding, "4");
  assert.equal(sent.method, "POST");
  assert.equal(sent.contentType, "application/json");
});

/* --- M14 Task 5a (R2-F1): a listing that answers after a newer one -------------------- */

test("a folder listing that answers after a newer one is dropped: the folder asked for last stays", async () => {
  /* R2-F1, built as R2 built it: C:\ is asked first and answers slowly, D:\ is asked second
   * and answers at once. When C:\ answers - with folders, and again with a refusal - the page
   * still shows D:\, its folders and no error, and Choose this folder binds D:\. */
  const slow = [
    [200, { path: "C:\\", parent: null, folders: [{ name: "Photos", path: "C:\Photos" }], drives: null, known: null }],
    [409, { error: CANNOT_SEE }],
  ];
  for (const late of slow) {
    let release;
    const pending = new Promise((resolve) => {
      release = resolve;
    });
    const page = await welcomePage(
      () => [200, { state: "chosen", archive_root: "D:\\", plan_id: null, recovery: null }],
      function (url) {
        if (url === "api/folders?path=C%3A%5C") {
          return pending;
        }
        return url === "api/folders?path=D%3A%5C"
          ? [200, { path: "D:\\", parent: null, folders: [{ name: "Scans", path: "D:\Scans" }], drives: null, known: null }]
          : null;
      }
    );
    const drives = buttonsIn(page.el("welcome-folders"));
    drives[0].click();
    drives[1].click();
    await settle();
    assert.equal(page.el("welcome-place").textContent, "D:\\");
    release(late);
    await settle();
    assert.equal(page.el("welcome-place").textContent, "D:\\", "the slow listing replaced the newer one");
    assert.deepEqual(
      buttonsIn(page.el("welcome-folders")).map((each) => each.textContent),
      ["Scans"],
      "the slow listing's folders were drawn"
    );
    assert.equal(page.el("welcome-error").hidden, true, "the slow listing's refusal was shown");
    page.el("welcome-choose-this").click();
    await settle();
    assert.deepEqual(binds(page).map((each) => each.body), [{ path: "D:\\" }]);
  }
});

/* --- M14 Task 5e: the Reading step (U10, U11, U12, U21) --------------------------------- */

const CHOSEN_SCANS = {
  state: "chosen",
  archive_root: "D:\\Scans",
  plan_id: null,
  recovery: null,
  binding: 2,
  folder: null,
};

/* A page whose job routes answer as the check says. `jobs` is the sequence `GET api/job`
 * answers, one per ask (the last repeats; `GONE` is a network failure). `ping` is the boot's
 * answer and `after` every later ping's. `read` answers `POST api/job/read` (a promise of one
 * holds it in flight); `cancel` answers `POST api/job/cancel`. */
async function readingPage(opts) {
  const jobs = (opts.jobs || [IDLE]).slice();
  let pinged = false;
  const page = loadPage(function (url) {
    if (url === "api/ping") {
      const first = !pinged;
      pinged = true;
      return [200, first ? opts.ping || { plan_id: null, status: null, binding: 1, folder: null } : opts.after];
    }
    if (url === "api/folders") {
      return opts.folders === GONE ? Promise.reject(new TypeError("Failed to fetch")) : [200, TOP];
    }
    if (url === "api/bind") {
      return opts.bind ? [200, opts.bind] : null;
    }
    if (url === "api/job") {
      const next = jobs.length > 1 ? jobs.shift() : jobs[0];
      if (next === GONE) {
        return Promise.reject(new TypeError("Failed to fetch"));
      }
      /* Task 5 review R2: `{refuse: [status, body]}` is a poll the server refuses; `{held: p}`
       * is one answered when the check settles `p` (R2-F6's slow reload). */
      if (next.held) {
        return next.held;
      }
      return next.refuse ? next.refuse : [200, next];
    }
    if (url === "api/job/read") {
      if (typeof opts.read === "function") {
        return opts.read();
      }
      return opts.read === undefined ? [200, { job: 1, binding: 3, folder: "D:\\Scans" }] : opts.read;
    }
    if (url === "api/job/cancel") {
      return opts.cancel === GONE ? Promise.reject(new TypeError("Failed to fetch")) : [200, { stopping: true }];
    }
    return null;
  });
  /* As `index.html` ships it: the dead-server banner hidden. */
  page.el("error-banner").hidden = true;
  await settle();
  return page;
}

/* A chosen folder, then `Start reading` pressed and answered. */
async function readingStarted(opts) {
  const page = await readingPage(Object.assign({ bind: CHOSEN_SCANS }, opts));
  await choose(page, "D:\\Scans");
  page.el("welcome-start-reading").click();
  await settle();
  return page;
}

/* One beat of the poll's timer: the step it holds runs, and its answer is drawn. */
async function tick(page) {
  assert.equal(page.timers.length, 1, "the poll did not wait for its next ask");
  page.timers.shift()();
  await settle();
}

function posts(page, url) {
  return page.requests.filter((each) => each.url === url && each.method === "POST");
}

function jobAsks(page) {
  return page.requests.filter((each) => each.url === "api/job");
}

function assertReadingOffered(page) {
  assert.equal(page.el("welcome-start-reading").hidden, false, "Start reading is not offered");
  assert.equal(page.el("welcome-start-reading").disabled, false, "Start reading is inactive");
  assert.equal(page.el("welcome-start-reading").textContent, START_READING);
  assert.equal(page.el("welcome-stop").hidden, true, "Stop outlived the read");
  assert.equal(page.el("welcome-reading-progress").hidden, true, "the progress line outlived the read");
  assert.equal(page.el("welcome-change").hidden, false, "Change is not offered after the read");
}

function shownLines(page) {
  return page.el("welcome-reading-lines").hidden
    ? []
    : page.el("welcome-reading-lines").children.map((each) => each.textContent);
}

test("Start reading posts once on a double click, adopts the answer's binding and folder, and polls about once a second", async () => {
  let release;
  const read = new Promise((resolve) => {
    release = resolve;
  });
  const page = await readingPage({
    bind: CHOSEN_SCANS,
    read: read,
    jobs: [IDLE, jobAt("running", { done: 19, total: 100 })],
  });
  await choose(page, "D:\\Scans");
  page.el("welcome-start-reading").click();
  page.el("welcome-start-reading").click();
  await settle();
  assert.equal(posts(page, "api/job/read").length, 1, "a double click posted two reads");
  assert.deepEqual(posts(page, "api/job/read")[0].body, {});
  assert.equal(posts(page, "api/job/read")[0].binding, "2", "the read did not name the bind's binding");
  assert.equal(page.el("welcome-start-reading").disabled, true, "Start reading is on while it posts");
  /* The answer names the folder as the server holds it - here spelled apart from the choice's,
   * so the line can only be the answer's. */
  release([200, { job: 1, binding: 3, folder: "D:\\Scans\\Photos" }]);
  await settle();
  assert.equal(page.el("welcome-folder").textContent, "D:\\Scans\\Photos");
  const asked = jobAsks(page);
  assert.equal(asked.length, 2, "the read was not followed by a poll");
  assert.equal(asked[1].binding, "3", "the poll did not name the read's binding");
  assert.equal(page.el("welcome-reading-progress").textContent, "19 of 100");
  assert.equal(page.timers.length, 1, "the poll did not schedule its next ask");
  page.el("welcome-start-reading").click();
  await settle();
  assert.equal(posts(page, "api/job/read").length, 1, "a press during the read posted again");
  /* The page's own timer waits about a second (1000 ms) between asks. */
  page.pageSchedule(function () {});
  assert.equal(page.delays[page.delays.length - 1], 1000);
});

test("the progress line at a running snapshot, with and without an ETA: 748 of 1,204 · about a minute left, 19 of 100", async () => {
  const page = await readingStarted({
    jobs: [
      IDLE,
      jobAt("running", { done: 19, total: 100, eta_s: null }),
      jobAt("running", { done: 748, total: 1204, eta_s: 75.0 }),
      jobAt("running", { done: 1204, total: 1234567, eta_s: 4000.0 }),
      jobAt("running", { done: 1204, total: 1204, eta_s: null }),
    ],
  });
  const line = () => page.el("welcome-reading-progress").textContent;
  assert.equal(line(), "19 of 100");
  assert.equal(page.el("welcome-reading-progress").hidden, false);
  assert.equal(page.el("welcome-start-reading").hidden, true, "Start reading is offered mid-read");
  assert.equal(page.el("welcome-change").hidden, true, "Change is offered mid-read");
  assert.equal(page.el("welcome-stop").hidden, false);
  assert.equal(page.el("welcome-stop").textContent, STOP);
  await tick(page);
  assert.equal(line(), "748 of 1,204 · about a minute left");
  await tick(page);
  assert.equal(line(), "1,204 of 1,234,567 · about an hour left");
  /* While pam plans, the counts stay at the scan's last `done == total`; U11 gives no sentence. */
  await tick(page);
  assert.equal(line(), "1,204 of 1,204");
});

test("every ETA boundary: 59, 60, 89, 90, 3569, 3570, 5399 and 5400 seconds", async () => {
  const page = await readingPage({});
  const phrase = page.context.PAM.reading.etaPhrase;
  const boundaries = [
    [0, "less than a minute left"],
    [59, "less than a minute left"],
    [60, "about a minute left"],
    [89, "about a minute left"],
    [90, "about 2 minutes left"],
    [3569, "about 59 minutes left"],
    [3570, "about an hour left"],
    [3600, "about an hour left"],
    [5399, "about an hour left"],
    [5400, "about 2 hours left"],
    /* Task 5 review R2 (M3147): 7300 s is 2.03 hours - rounded, not rounded up. */
    [7300, "about 2 hours left"],
    [9000, "about 3 hours left"],
  ];
  assert.equal(boundaries.length, 12, "the boundary table went quiet");
  for (const [seconds, words] of boundaries) {
    assert.equal(phrase(seconds), words, seconds + " seconds");
  }
});

test("Stop posts cancel once, and the cancelled read shows the job's line and offers Start reading again", async () => {
  const page = await readingStarted({
    jobs: [
      IDLE,
      jobAt("running", { done: 40, total: 100 }),
      jobAt("running", { done: 40, total: 100 }),
      jobAt("cancelled", { done: 40, total: 100, lines: [STOPPED_LINE] }),
    ],
  });
  page.el("welcome-stop").click();
  page.el("welcome-stop").click();
  await settle();
  assert.equal(posts(page, "api/job/cancel").length, 1, "Stop posted twice");
  assert.deepEqual(posts(page, "api/job/cancel")[0].body, {});
  assert.equal(posts(page, "api/job/cancel")[0].binding, "3");
  assert.equal(page.el("welcome-stop").disabled, true, "Stop is on after it was pressed");
  await tick(page);
  assert.equal(page.el("welcome-stop").disabled, true, "a running answer turned Stop back on");
  await tick(page);
  assert.deepEqual(shownLines(page), [STOPPED_LINE]);
  assertReadingOffered(page);
  assert.equal(page.timers.length, 0, "the poll went on after the read stopped");
});

test("a read that ends with an open plan enables Review names, which opens the list", async () => {
  const page = await readingStarted({
    jobs: [IDLE, jobAt("done", { done: 3, total: 3, lines: [] })],
    after: { plan_id: 9, status: "open", binding: 3, folder: "D:\\Scans" },
  });
  assert.equal(page.el("welcome").hidden, false, "the list opened over the job's lines");
  assert.equal(page.el("welcome-review").disabled, false, "Review names is off with a plan");
  assert.equal(page.el("welcome-stop").hidden, true);
  assert.equal(page.el("welcome-reading-progress").hidden, true);
  assert.equal(page.timers.length, 0, "the poll went on after the read finished");
  page.el("welcome-review").click();
  await settle();
  assert.equal(page.el("welcome").hidden, true);
  assert.equal(page.requests[page.requests.length - 1].url, "api/plan/9");
});

test("a read that ends with no plan keeps Start reading and shows the job's lines, U21's verbatim", async () => {
  const page = await readingStarted({
    jobs: [IDLE, jobAt("done", { lines: [NOTHING_NEW, U21_LINE] })],
    after: { plan_id: null, status: null, binding: 3, folder: "D:\\Scans" },
  });
  assert.deepEqual(shownLines(page), [NOTHING_NEW, U21_LINE]);
  assertReadingOffered(page);
  assert.equal(page.el("welcome-review").disabled, true, "Review names is on with no plan");
  assert.ok(!urls(page).some((each) => each.indexOf("api/plan") === 0), "a list with no plan");
});

test("a failed read with recovery shows U12's heading and recovery's own line, and offers Start reading again", async () => {
  const page = await readingStarted({
    jobs: [IDLE, jobAt("failed", { lines: ["pam could not finish reading."], recovery: RECOVERY_LINE })],
  });
  assert.equal(page.el("welcome-recovery").hidden, false);
  assert.equal(page.el("welcome-recovery-title").textContent, RECOVERED);
  assert.equal(page.el("welcome-recovery-line").textContent, RECOVERY_LINE);
  assertReadingOffered(page);
});

test("a failed read with no recovery shows the job's lines and offers Start reading again", async () => {
  const sentence = "pam could not finish reading.";
  const page = await readingStarted({ jobs: [IDLE, jobAt("failed", { lines: [sentence] })] });
  assert.deepEqual(shownLines(page), [sentence]);
  assert.equal(page.el("welcome-recovery").hidden, true);
  assertReadingOffered(page);
});

test("a failed poll shows U12's banner, all three sentences, and stops polling", async () => {
  const page = await readingStarted({
    jobs: [IDLE, jobAt("running", { done: 5, total: 100 }), GONE],
  });
  assert.equal(page.el("error-banner").hidden, true, "the banner is up while pam runs");
  await tick(page);
  assert.equal(page.el("error-banner").hidden, false, "a dead server showed no banner");
  assert.deepEqual(
    [
      page.el("error-banner-stopped").textContent,
      page.el("error-banner-lost").textContent,
      page.el("error-banner-restart").textContent,
    ],
    [U12.stopped, U12.lost, U12.restart]
  );
  assert.equal(page.context.PAM.core.PAGE.stopped, true);
  assert.equal(page.timers.length, 0, "the poll went on asking a dead server");
  const asked = jobAsks(page).length;
  page.el("welcome-stop").click();
  page.el("welcome-start-reading").click();
  await settle();
  assert.equal(jobAsks(page).length, asked, "a dead page asked again");
  assert.equal(posts(page, "api/job/cancel").length, 0, "a dead page posted a cancel");
});

test("Start reading is not offered while a plan is open: after a bind that ran recovery, and after a read whose ping names a plan", async () => {
  const bound = await readingPage({
    bind: { state: "bound", archive_root: "D:\\Scans", plan_id: 3, recovery: RECOVERY_LINE, binding: 2, folder: "D:\\Scans" },
  });
  await choose(bound, "D:\\Scans");
  assert.equal(bound.el("welcome").hidden, false);
  assert.equal(bound.el("welcome-start-reading").hidden, true, "Start reading offered over an open plan");
  const read = await readingStarted({
    jobs: [IDLE, jobAt("done", { lines: [NOTHING_NEW] })],
    after: { plan_id: 9, status: "open", binding: 3, folder: "D:\\Scans" },
  });
  assert.equal(read.el("welcome-review").disabled, false);
  assert.equal(read.el("welcome-start-reading").hidden, true, "Start reading offered over an open plan");
  assert.deepEqual(shownLines(read), [NOTHING_NEW]);
});

test("a bound folder with no open plan shows the ping's folder and offers Start reading", async () => {
  const page = await readingPage({
    ping: { plan_id: null, status: null, binding: 5, folder: "C:\\Users\\user\\Photos" },
  });
  assert.equal(page.el("welcome").hidden, false);
  assert.equal(page.el("welcome-folder").textContent, "C:\\Users\\user\\Photos");
  assert.equal(page.el("welcome-folder").hidden, false);
  assert.equal(page.el("welcome-chooser").hidden, true);
  assertReadingOffered(page);
  assert.equal(page.el("welcome-review").disabled, true);
  page.el("welcome-start-reading").click();
  await settle();
  assert.equal(posts(page, "api/job/read").length, 1);
  assert.equal(posts(page, "api/job/read")[0].binding, "5");
});

/* M14 Task 6 Stage 2, S2-F2: `pam start PATH` binds (and recovers) before the page exists, and
 * the page boots from the ping, never posting `bind` - so the ping carries recovery's line. */
test("a start that ran recovery shows U12's heading and recovery's own line on the ping's folder", async () => {
  const page = await readingPage({
    ping: {
      plan_id: null,
      status: null,
      binding: 5,
      folder: "C:\\Users\\user\\Photos",
      recovery: RECOVERY_LINE,
    },
  });
  assert.equal(page.el("welcome").hidden, false);
  assert.equal(page.el("welcome-recovery").hidden, false, "recovery's line was not shown");
  assert.equal(page.el("welcome-recovery-title").textContent, RECOVERED);
  assert.equal(page.el("welcome-recovery-line").textContent, RECOVERY_LINE);
  const quiet = await readingPage({
    ping: { plan_id: null, status: null, binding: 5, folder: "C:\\Users\\user\\Photos", recovery: null },
  });
  assert.equal(quiet.el("welcome-recovery").hidden, true, "U12's heading over no recovery");
});

test("a start that ran recovery with an open plan waits on Welcome, then Review names opens the list", async () => {
  const page = await readingPage({
    ping: {
      plan_id: 3,
      status: "open",
      binding: 5,
      folder: "C:\\Users\\user\\Photos",
      recovery: RECOVERY_LINE,
    },
  });
  assert.equal(page.el("welcome").hidden, false, "the list replaced recovery's line unread");
  assert.equal(page.el("welcome-recovery-line").textContent, RECOVERY_LINE);
  assert.ok(!urls(page).includes("api/plan/3"), "the list was opened before recovery was read");
  assert.equal(page.el("welcome-review").disabled, false, "Review names is off with a plan");
  page.el("welcome-review").click();
  await settle();
  assert.equal(page.requests[page.requests.length - 1].url, "api/plan/3");
});

test("a reload during a running read resumes the progress view instead of Start reading", async () => {
  const page = await readingPage({
    ping: { plan_id: null, status: null, binding: 6, folder: "C:\\Users\\user\\Photos" },
    jobs: [jobAt("running", { done: 748, total: 1204, eta_s: 75.0 })],
  });
  assert.equal(page.el("welcome-folder").textContent, "C:\\Users\\user\\Photos");
  assert.equal(page.el("welcome-start-reading").hidden, true, "Start reading offered over a running read");
  assert.equal(page.el("welcome-reading-progress").hidden, false);
  assert.equal(page.el("welcome-reading-progress").textContent, "748 of 1,204 · about a minute left");
  assert.equal(page.el("welcome-stop").hidden, false);
  /* Task 5 review R2 (M3143): UX's Welcome section - `Change` is not offered mid-read. */
  assert.equal(page.el("welcome-change").hidden, true, "Change is offered over a resumed read");
  assert.equal(page.timers.length, 1, "the resumed read is not polled");
  assert.equal(posts(page, "api/job/read").length, 0, "a reload started a read");
  /* A running job that is not a read (Task 6's) is not this step's: Start reading stays. */
  const other = await readingPage({
    ping: { plan_id: null, status: null, binding: 6, folder: "C:\\Users\\user\\Photos" },
    jobs: [jobAt("running", { kind: "rename", done: 1, total: 9 })],
  });
  assertReadingOffered(other);
  assert.equal(other.timers.length, 0);
});

/* A bound folder with no open plan, as the ping names it (W23). */
const BOUND_PHOTOS = { plan_id: null, status: null, binding: 5, folder: "C:\\Users\\user\\Photos" };
/* `server.FOLDER_CHANGED`, verbatim: a request whose binding is not the server's (design 8.2). */
const FOLDER_CHANGED = "pam is working on another folder now. Reload the page.";
/* A server that holds no folder: the ping after a refused bind (U10). */
const HOLDS_NOTHING = { plan_id: null, status: null, binding: 2, folder: null };

test("a refused Start reading on a bound folder shows the server's sentence and offers the button again", async () => {
  /* Task 5 review R2-F1/F5: the page draws what the ping says the server holds - here the
   * folder it is bound to, so `Start reading` and `Change` come back. */
  const page = await readingPage({
    ping: BOUND_PHOTOS,
    after: BOUND_PHOTOS,
    read: [409, { error: REFUSALS.plans[1] }],
  });
  page.el("welcome-start-reading").click();
  await settle();
  assert.equal(page.el("welcome-error").textContent, REFUSALS.plans[1]);
  assert.equal(page.el("welcome-error").hidden, false);
  assert.equal(page.el("welcome-folder").textContent, "C:\\Users\\user\\Photos");
  assert.equal(page.el("welcome-folder").hidden, false);
  assertReadingOffered(page);
  assert.equal(jobAsks(page).length, 1, "a refused read was polled");
  /* Task 5 review R2 (M3126): the button offered again WORKS - a second press posts again. */
  page.el("welcome-start-reading").click();
  await settle();
  assert.equal(posts(page, "api/job/read").length, 2, "a refused read left Start reading dead");
});

test("a refused Start reading on a chosen folder shows the server's sentence and the chooser, no folder", async () => {
  /* Task 5 review R2-F5: on a chosen folder the read IS the bind, and a refused bind leaves the
   * server holding no folder (U10) - so the page shows none, as Welcome's own refused bind does. */
  const page = await readingStarted({ read: [409, { error: REFUSALS.lock[1] }], after: HOLDS_NOTHING });
  assert.equal(page.el("welcome-error").textContent, REFUSALS.lock[1]);
  assert.equal(page.el("welcome-error").hidden, false, "the refusal's sentence went with the folder");
  assertChooserShowsNoFolder(page);
  assert.equal(page.el("welcome-start-reading").hidden, true, "Start reading offered with no folder held");
  assert.equal(page.el("welcome-folder").textContent, "", "the withdrawn folder is still named");
  assert.equal(jobAsks(page).length, 1, "a refused read was polled");
});

test("Change is not offered while Start reading posts, and comes back when the server keeps the folder", async () => {
  /* Task 5 review R2-F1: UX Welcome - `Change` is not offered mid-read; a read held in flight
   * is mid-read (on a chosen folder it is the bind). */
  let answer;
  const held = new Promise((resolve) => {
    answer = resolve;
  });
  const page = await readingPage({ ping: BOUND_PHOTOS, after: BOUND_PHOTOS, read: () => held });
  assert.equal(page.el("welcome-change").hidden, false, "Change is not offered before the read");
  page.el("welcome-start-reading").click();
  await settle();
  assert.equal(page.el("welcome-change").hidden, true, "Change is offered while the read posts");
  answer([409, { error: REFUSALS.plans[1] }]);
  await settle();
  assert.equal(page.el("welcome-change").hidden, false, "Change did not come back after the refusal");
});

test("a Start reading that meets a dead server asks it nothing more", async () => {
  /* M3185: U12 - once pam has stopped the page asks nothing; a read whose POST finds the
   * server gone shows the banner and does not ask the ping what it holds. The ping WOULD answer
   * (`after`), so a page that asks it fails on the count below, not on a missing answer (M3185-R
   * was killed by a crash on a null ping instead - a kill for the wrong reason). */
  const page = await readingStarted({
    read: () => Promise.reject(new TypeError("Failed to fetch")),
    after: HOLDS_NOTHING,
  });
  assert.equal(page.el("error-banner").hidden, false, "U12's banner is not shown");
  const read = page.requests.findIndex((each) => each.url === "api/job/read");
  assert.notEqual(read, -1, "the read was not posted");
  assert.deepEqual(page.requests.slice(read + 1).map((each) => each.url), [], "a dead server was asked again");
});

test("a read refused because another folder was chosen draws that folder and its binding", async () => {
  /* M3187: `pam is working on another folder now` - the page draws the folder the ping names
   * and adopts the ping's binding, so the next press is not refused the same way forever. */
  const page = await readingPage({
    ping: BOUND_PHOTOS,
    after: { plan_id: null, status: null, binding: 9, folder: "D:\\Other" },
    read: [409, { error: FOLDER_CHANGED }],
  });
  page.el("welcome-start-reading").click();
  await settle();
  assert.equal(page.el("welcome-error").textContent, FOLDER_CHANGED);
  assert.equal(page.el("welcome-folder").textContent, "D:\\Other");
  assertReadingOffered(page);
  page.el("welcome-start-reading").click();
  await settle();
  const sent = posts(page, "api/job/read").map((each) => each.binding);
  assert.deepEqual(sent, ["5", "9"], "the second press did not carry the ping's binding");
});

test("a read refused while a plan has opened offers Review names, not Start reading", async () => {
  /* M3189 (5d concern 1): a ping that names an open plan - a console `pam plan` meanwhile - is
   * drawn as Welcome's bound-with-plan state; `Start reading` is never offered over it. */
  const page = await readingPage({
    ping: BOUND_PHOTOS,
    after: { plan_id: 7, status: "open", binding: 5, folder: "C:\\Users\\user\\Photos" },
    read: [409, { error: REFUSALS.plans[1] }],
  });
  page.el("welcome-start-reading").click();
  await settle();
  assert.equal(page.el("welcome-error").textContent, REFUSALS.plans[1]);
  assert.equal(page.el("welcome-start-reading").hidden, true, "Start reading offered over an open plan");
  assert.equal(page.el("welcome-review").disabled, false, "Review names is off with a plan open");
});

test("a reload's late answer about a running read starts no second poll once Start reading was pressed", async () => {
  /* Task 5 review R2-F6: the resume's `GET api/job` answers after a press has started the read
   * and its poll; that answer must not start a second chain asking every second twice. */
  let answer;
  const late = new Promise((resolve) => {
    answer = resolve;
  });
  const page = await readingPage({
    ping: BOUND_PHOTOS,
    jobs: [{ held: late }, jobAt("running", { done: 1, total: 9 })],
  });
  page.el("welcome-start-reading").click();
  await settle();
  assert.equal(posts(page, "api/job/read").length, 1);
  assert.equal(page.timers.length, 1, "the read's own poll is not waiting");
  answer([200, jobAt("running", { done: 1, total: 9 })]);
  await settle();
  assert.equal(page.timers.length, 1, "the reload's late answer started a second poll");
  await tick(page);
  assert.equal(page.timers.length, 1, "two polls are waiting after a beat");
});

/* --- M14 Task 5 review R2: the gaps its mutation pass found -------------------------------- */

test("an estimate of 0 seconds still reads less than a minute left", async () => {
  /* M3122: `eta_s: 0` is an estimate (the read's last seconds), not a missing one. */
  const page = await readingStarted({
    jobs: [IDLE, jobAt("running", { done: 1203, total: 1204, eta_s: 0 })],
  });
  assert.equal(page.el("welcome-reading-progress").textContent, "1,203 of 1,204 · less than a minute left");
});

test("a read after a read starts clean: the last one's lines and U12's heading go, and each press posts once", async () => {
  /* M3123, M3124, M3125, M3136: a stopped read, then a read that fails with recovery, then one
   * that finishes - each press after an ending posts, and nothing from an earlier read stays. */
  const page = await readingStarted({
    jobs: [
      IDLE,
      jobAt("cancelled", { lines: [STOPPED_LINE] }),
      jobAt("running", { done: 1, total: 9 }),
      jobAt("failed", { lines: ["pam could not finish reading."], recovery: RECOVERY_LINE }),
      jobAt("running", { done: 2, total: 9 }),
      jobAt("done", { lines: [NOTHING_NEW] }),
    ],
    after: { plan_id: null, status: null, binding: 3, folder: "D:\\Scans" },
  });
  assert.deepEqual(shownLines(page), [STOPPED_LINE]);
  page.el("welcome-start-reading").click();
  await settle();
  assert.equal(posts(page, "api/job/read").length, 2, "a press after a stopped read posted nothing");
  assert.deepEqual(shownLines(page), [], "the stopped read's line stayed over the next read");
  await tick(page);
  assert.equal(page.el("welcome-recovery").hidden, false);
  page.el("welcome-start-reading").click();
  await settle();
  assert.equal(posts(page, "api/job/read").length, 3, "a press after a failed read posted nothing");
  assert.equal(page.el("welcome-recovery").hidden, true, "U12's heading stayed over the next read");
  await tick(page);
  assert.deepEqual(shownLines(page), [NOTHING_NEW], "an earlier read's lines were drawn again");
});

test("a poll the server refuses shows its sentence and asks no more", async () => {
  /* M3132: the server's sentence, as it came - never a progress line left frozen in silence. */
  const sentence = "pam is working on another folder now. Reload the page.";
  const page = await readingStarted({
    jobs: [IDLE, jobAt("running", { done: 4, total: 9 }), { refuse: [409, { error: sentence }] }],
  });
  await tick(page);
  assert.equal(page.el("welcome-error").textContent, sentence);
  assert.equal(page.el("welcome-error").hidden, false);
  assert.equal(page.timers.length, 0, "a refused poll went on asking");
});

test("once pam has stopped the page asks nothing more: a poll already waiting, and Start reading", async () => {
  /* M3131: Stop's cancel finds the server gone while the next poll waits - it never runs. */
  const page = await readingStarted({
    jobs: [IDLE, jobAt("running", { done: 4, total: 9 })],
    cancel: GONE,
  });
  page.el("welcome-stop").click();
  await settle();
  assert.equal(page.context.PAM.core.PAGE.stopped, true);
  const asked = jobAsks(page).length;
  await tick(page);
  assert.equal(jobAsks(page).length, asked, "a waiting poll asked a dead server");
  /* M3130: a page that died with Start reading offered (here the listing found pam gone). */
  const offered = await readingPage({
    ping: { plan_id: null, status: null, binding: 5, folder: "C:\\Users\\user\\Photos" },
    folders: GONE,
  });
  assert.equal(offered.context.PAM.core.PAGE.stopped, true);
  assert.equal(offered.el("welcome-start-reading").hidden, false);
  offered.el("welcome-start-reading").click();
  await settle();
  assert.equal(posts(offered, "api/job/read").length, 0, "a dead page posted a read");
});

test("a reload after a read has ended offers Start reading and draws none of its lines", async () => {
  /* M3141: only a RUNNING read is resumed (the 5e dispatch); a finished one is not redrawn. */
  const page = await readingPage({
    ping: { plan_id: null, status: null, binding: 6, folder: "C:\\Users\\user\\Photos" },
    jobs: [jobAt("cancelled", { lines: [STOPPED_LINE] })],
  });
  assertReadingOffered(page);
  assert.deepEqual(shownLines(page), []);
  assert.equal(page.timers.length, 0);
  assert.equal(urls(page).filter((each) => each === "api/ping").length, 1, "a finished read was finished again");
});

test("Change after a finished read takes the read's lines away with the folder", async () => {
  /* M3153: the chooser never shows lines a read of the folder it left said. */
  const page = await readingStarted({
    jobs: [IDLE, jobAt("done", { lines: [NOTHING_NEW, U21_LINE] })],
    after: { plan_id: null, status: null, binding: 3, folder: "D:\\Scans" },
  });
  assert.deepEqual(shownLines(page), [NOTHING_NEW, U21_LINE]);
  page.el("welcome-change").click();
  await settle();
  assert.equal(page.el("welcome-chooser").hidden, false);
  assert.deepEqual(shownLines(page), [], "the old folder's lines stayed under the chooser");
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
