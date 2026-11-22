/* WELCOME (M14 Task 4 Step 7, Task 5e; U8's W18, U9, U10, U11, U12, U13; OD-4).
 *
 * The page `pam start` opens when its server holds no folder. `app.js` decides: the ping
 * answers `plan_id: null` on an unbound server (M14 Task 4a), and only then is `startWelcome`
 * called. A `pam review` page always has a plan, never reaches this file, and looks and
 * behaves exactly as it did before Task 4.
 *
 * THE FOLDER STEP IS U13's CHOOSER: the known archives, a browser that lists drives and then
 * folders (`GET api/folders`, read-only on the server; folders only, never files), and a box
 * to paste a path - drawn by `pam-chooser.js` (cut out of this file by M14 Task 5a, R2-F10).
 * Choosing posts `POST api/bind {path}` (M14 Task 4b) and the page draws the server's answer:
 *   - `bound` with a plan: the list opens, through the hook `app.js` hands in - unless
 *     recovery ran at the bind, when its own line is shown under U12's heading first and
 *     `Review names` opens the list;
 *   - `chosen` (a folder pam has never read), or `bound` with no open plan: the folder is
 *     shown and `Start reading` is offered - the Reading step, `pam-reading.js` (M14 Task 5e);
 *   - a refusal: the server's own sentence, verbatim - never one this file builds.
 * A server `pam start PATH` bound to a folder with no open plan answers the ping with that
 * folder, and Welcome opens on it (`drawFolder`); a read still running is resumed.
 *
 * EVERY PATH SHOWN IS ONE THE SERVER SENT (`archive_root`, a listing's `path`, a folder's
 * `name`, a drive, a known archive). The pasted text is only ever the request's body.
 *
 * OD-4 (ADOPTED): W18's last mockup line, about reviewing during a read, is NOT drawn - a
 * plan is built from a finished read, so `Review names` enables only when there is a plan.
 *
 * The element queries are the boring two spellings `tests/test_webui_static.py` reads.
 */

"use strict";

/* --- the copy ------------------------------------------------------------------------- */

var WELCOME_COPY = {
  /* U8 W18, verbatim. */
  title: "Give your photos names that make sense.",
  lead:
    "pam suggests a name for every photo from its date, place and event. " +
    "You approve the names. Nothing changes until you say so.",
  stepFolder: "Folder",
  stepReading: "Reading your photos",
  stepReview: "Review the names",
  promises: [
    "Your photos stay on this computer. Nothing is uploaded.",
    "Only the file names change. Photos are never edited, moved or deleted.",
    "Every rename can be undone, any time.",
  ],
  reviewNames: "Review names",
  /* U10: the control a chosen folder shows. */
  startReading: "Start reading",
  /* M14 Task 5e (U11's cancellation): the controller's word, pending the design model. */
  stop: "Stop",
  /* U12: the heading over recovery's own line after a bind. */
  recovered: "pam finished putting things right after it stopped.",
  /* M14 Task 4 Step 7 (the plan): shown after a choice. */
  change: "Change",
  /* THE IMPLEMENTER'S RULINGS (Task 4d) - no spec gives these words; the design model rules
   * them at the M14 close. */
  known: "Folders pam has read before",
  drives: "Drives",
  up: "Up one folder",
  allDrives: "All drives",
  chooseThis: "Choose this folder",
  paste: "Or paste the full path of a folder",
  pasteChoose: "Choose",
  noFolders: "No folders in here.",
};

/* --- state ---------------------------------------------------------------------------- */

var WELCOME = {
  /* `app.js`'s: called with a plan id to open the list. */
  openList: null,
  planId: null,
  busy: false,
  wired: false,
};

function welcomeElements() {
  return {
    welcome: document.getElementById("welcome"),
    title: document.getElementById("welcome-title"),
    lead: document.getElementById("welcome-lead"),
    stepFolder: document.getElementById("welcome-step-folder"),
    stepReading: document.getElementById("welcome-step-reading"),
    stepReview: document.getElementById("welcome-step-review"),
    folderTitle: document.getElementById("welcome-folder-title"),
    readingTitle: document.getElementById("welcome-reading-title"),
    reviewTitle: document.getElementById("welcome-review-title"),
    folder: document.getElementById("welcome-folder"),
    change: document.getElementById("welcome-change"),
    chooser: document.getElementById("welcome-chooser"),
    knownTitle: document.getElementById("welcome-known-title"),
    known: document.getElementById("welcome-known"),
    place: document.getElementById("welcome-place"),
    up: document.getElementById("welcome-up"),
    foldersTitle: document.getElementById("welcome-folders-title"),
    folders: document.getElementById("welcome-folders"),
    chooseThis: document.getElementById("welcome-choose-this"),
    paste: document.getElementById("welcome-paste"),
    pasteLabel: document.getElementById("welcome-paste-label"),
    pastePath: document.getElementById("welcome-paste-path"),
    pasteChoose: document.getElementById("welcome-paste-choose"),
    error: document.getElementById("welcome-error"),
    recovery: document.getElementById("welcome-recovery"),
    recoveryTitle: document.getElementById("welcome-recovery-title"),
    recoveryLine: document.getElementById("welcome-recovery-line"),
    startReading: document.getElementById("welcome-start-reading"),
    progress: document.getElementById("welcome-reading-progress"),
    stop: document.getElementById("welcome-stop"),
    lines: document.getElementById("welcome-reading-lines"),
    review: document.getElementById("welcome-review"),
    promises: document.getElementById("welcome-promises"),
  };
}

/* The review page's parts Welcome stands in for. The dead-server banner lives in the header
 * and stays reachable, so a stopped server still says so on Welcome. */
function reviewParts() {
  return [
    document.getElementById("header-row"),
    document.getElementById("controls"),
    /* HT-M14 F7: `Accept all` sits outside `controls`, and showed on Welcome and Confirm. */
    document.getElementById("presets"),
    document.getElementById("main"),
    document.getElementById("footer"),
  ];
}

function showWelcome(on) {
  var parts = reviewParts();
  for (var i = 0; i < parts.length; i += 1) {
    parts[i].hidden = on;
  }
  welcomeElements().welcome.hidden = !on;
  /* HT-M14 F7: the step pills are shown once a list is drawn (ruling B6) and stayed up when
   * the page went back to Welcome, where no folder step is true yet. */
  if (on) {
    document.getElementById("steps").hidden = true;
  }
}

/* --- drawing -------------------------------------------------------------------------- */

function drawCopy(w) {
  w.title.textContent = WELCOME_COPY.title;
  w.lead.textContent = WELCOME_COPY.lead;
  w.folderTitle.textContent = WELCOME_COPY.stepFolder;
  w.readingTitle.textContent = WELCOME_COPY.stepReading;
  w.reviewTitle.textContent = WELCOME_COPY.stepReview;
  w.change.textContent = WELCOME_COPY.change;
  w.knownTitle.textContent = WELCOME_COPY.known;
  w.chooseThis.textContent = WELCOME_COPY.chooseThis;
  w.pasteLabel.textContent = WELCOME_COPY.paste;
  w.pasteChoose.textContent = WELCOME_COPY.pasteChoose;
  w.recoveryTitle.textContent = WELCOME_COPY.recovered;
  w.startReading.textContent = WELCOME_COPY.startReading;
  w.stop.textContent = WELCOME_COPY.stop;
  w.review.textContent = WELCOME_COPY.reviewNames;
  clear(w.promises);
  for (var i = 0; i < WELCOME_COPY.promises.length; i += 1) {
    w.promises.appendChild(make("li", null, WELCOME_COPY.promises[i]));
  }
}

function markSteps(w, folder, reading, review) {
  w.stepFolder.setAttribute("data-step", folder);
  w.stepReading.setAttribute("data-step", reading);
  w.stepReview.setAttribute("data-step", review);
}

function showError(message) {
  var w = welcomeElements();
  w.error.textContent = message;
  w.error.hidden = false;
}

function clearError() {
  var w = welcomeElements();
  w.error.textContent = "";
  w.error.hidden = true;
}

/* A refusal's sentence is the server's, shown as it came. A network failure has already
 * been reported by `api` (the dead-server banner), so it adds nothing here. */
function onRefusal(failure) {
  if (failure instanceof ApiRefusal) {
    showError(failure.message);
  }
}

/* --- choosing (U10: the bind) --------------------------------------------------------- */

function chooseFolder(path) {
  if (WELCOME.busy || PAGE.stopped) {
    return Promise.resolve(null);
  }
  WELCOME.busy = true;
  clearError();
  var request = {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: path }),
  };
  return api("api/bind", request)
    .then(onBound, function (failure) {
      /* A refused bind leaves the server holding no folder (U10), so none is shown. */
      resetChoice();
      onRefusal(failure);
    })
    .then(function () {
      WELCOME.busy = false;
    });
}

function onBound(body) {
  var w = welcomeElements();
  PAGE.binding = body.binding;
  w.recoveryLine.textContent = body.recovery || "";
  w.recovery.hidden = !body.recovery;
  var planned = body.state === "bound" && body.plan_id !== null;
  drawFolder(body.archive_root, planned ? body.plan_id : null);
  if (!planned) {
    return null;
  }
  /* Recovery's line is read before the list replaces this page; `Review names` opens it. */
  return body.recovery ? null : openTheList();
}

/* A folder the server holds (a bind's `archive_root`, a ping's `folder`) and its plan, or
 * null. With a plan `Review names` is on; with none `Start reading` is offered. A finished
 * read (`pam-reading.js`) draws its state through here too. */
function drawFolder(folder, planId) {
  var w = welcomeElements();
  w.folder.textContent = folder;
  w.folder.hidden = false;
  w.chooser.hidden = true;
  WELCOME.planId = planId;
  w.review.disabled = planId === null;
  offerReading(w);
  if (planId !== null) {
    markSteps(w, "done", "done", "current");
  }
  /* M15 Task 3 (AM3): the folder is bound, so its History can be asked for. */
  if (typeof refreshHistoryLinks === "function") {
    refreshHistoryLinks();
  }
}

function resetChoice() {
  var w = welcomeElements();
  /* AM3: no folder is chosen, so there is no History to offer. */
  if (typeof drawHistoryLinks === "function") {
    drawHistoryLinks(false);
  }
  WELCOME.planId = null;
  w.folder.textContent = "";
  w.folder.hidden = true;
  w.change.hidden = true;
  w.chooser.hidden = false;
  w.recovery.hidden = true;
  w.startReading.hidden = true;
  w.progress.hidden = true;
  w.stop.hidden = true;
  w.lines.hidden = true;
  w.review.disabled = true;
  markSteps(w, "current", "next", "next");
}

function openTheList() {
  if (WELCOME.planId === null || PAGE.stopped) {
    return null;
  }
  showWelcome(false);
  return WELCOME.openList(WELCOME.planId);
}

function onChange() {
  resetChoice();
  return showTop();
}

function wireWelcome(w) {
  if (WELCOME.wired) {
    return;
  }
  WELCOME.wired = true;
  w.change.addEventListener("click", onChange);
  w.up.addEventListener("click", onUp);
  w.chooseThis.addEventListener("click", chooseListed);
  w.paste.addEventListener("submit", onPaste);
  w.review.addEventListener("click", openTheList);
  w.startReading.addEventListener("click", startReading);
  w.stop.addEventListener("click", stopReading);
}

/* --- start ---------------------------------------------------------------------------- */

/* `openList(planId)` is `app.js`'s way into the list; `ping` is the boot's answer: no plan, or
 * a plan with recovery's line (S2-F2). A bound server names its `folder`, and recovery's line
 * from its bind is shown under U12's heading, as `onBound` shows a bind's - with a plan,
 * `Review names` opens the list once the line is read. Returns the top level's and the job's
 * requests. */
function startWelcome(openList, ping) {
  WELCOME.openList = openList;
  var w = welcomeElements();
  drawCopy(w);
  wireWelcome(w);
  resetChoice();
  if (ping.folder) {
    drawFolder(ping.folder, ping.plan_id);
    w.recoveryLine.textContent = ping.recovery || "";
    w.recovery.hidden = !ping.recovery;
  }
  showWelcome(true);
  return Promise.all([showTop(), resumeReading()]);
}

/* --- the seams ------------------------------------------------------------------------- */

var WELCOME_SEAM = {
  WELCOME_COPY: WELCOME_COPY,
  WELCOME: WELCOME,
  startWelcome: startWelcome,
  chooseFolder: chooseFolder,
};

if (typeof window !== "undefined") {
  window.PAM.welcome = WELCOME_SEAM;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = WELCOME_SEAM;
}
