/* THE CONFIRM SCREEN AND THE RENAME'S PROGRESS (M14 Task 6c; U6, U8 W16/W19, U11, U14, U22).
 *
 * The page's second trigger of the one rename (U5): the footer's `Rename N files` (W16) opens
 * *Ready to rename* (W19), whose button posts `POST api/plan/{id}/apply {nonce,
 * folder_confirmed: true}` ONCE, and the poll draws `Renaming… 312 of 1,204` until the job
 * ends; `pam-done.js` draws the end. The order on the screen is U6's: the full resolved
 * archive path, never shortened; `This is the right folder`, unticked on arrival; the counts;
 * U22's sync line; the low line; the three reassurances and U22's small line; the button,
 * disabled until the box is ticked.
 *
 * EVERY NUMBER IS THE SERVER'S: the footer's N is `/list`'s top-level `renames` (ruling B2,
 * the confirmation's own count), and the screen's are `GET api/plan/{id}/confirmation`'s. A
 * refusal is the server's sentence, verbatim. The nonce is sent exactly as it came.
 *
 * The functions this file calls in `pam-list.js` (`LIST`, `showListNote`, `refreshHeaders`),
 * `pam-welcome.js` (`reviewParts`) and `pam-done.js` (`finishMoving`) are looked up when a click
 * or an answer arrives, never at load. The timer is `CONFIRM.schedule`, so the node checks
 * drive the poll without real time.
 */

"use strict";

/* --- the copy ------------------------------------------------------------------------- */

var CONFIRM_COPY = {
  /* W16. */
  approveFirst: "Approve names to rename them",
  /* W19, U6. */
  title: "Ready to rename",
  inFolder: "In this folder",
  tick: "This is the right folder",
  getNames: "get their new names",
  /* HT-M14 F5: one file "gets its new name", never "1 file get their new names". */
  getNameOne: "gets its new name",
  notApproved: "Not approved, left as they are",
  skippedByYou: "Skipped by you",
  promises: [
    "Nothing is deleted, moved or edited. Only names change.",
    "Every change is written down before it happens.",
    "Undo puts every name back exactly. It never expires.",
  ],
  tickFirst: 'Tick "This is the right folder" first.',
  /* U22: the sync line, the undo caveat; U6: the low line in U8's words. */
  syncLead: "This folder is in ",
  syncTail: ". Pause syncing while pam renames, then turn it back on.",
  caveat: "As long as the files aren't renamed outside pam.",
  lowTail: " of these names need review.",
  lowTailOne: " of these names needs review.",
  /* Step 6's progress line; ruling B3 for undo's. U11's line about the window. */
  renaming: "Renaming… ",
  puttingBack: "Putting names back… ",
  of: " of ",
  keepOpen: "Keep this window open or not — pam finishes either way.",
  /* The way back to the list: the implementer's words, from W4, pending the design model. */
  back: "Keep checking names",
};

/* --- state ---------------------------------------------------------------------------- */

var CONFIRM = {
  /* The confirmation the screen shows (its nonce, its `skipped_by_you`), or null. */
  facts: null,
  planId: null,
  /* `Rename N files` pressed: its post or its job is in flight, so a second press posts
   * nothing (the nonce is single-use). Cleared only by going back to the list. */
  posting: false,
  /* The poll's timer: `step` runs about a second later. A node check replaces it. */
  schedule: function (step) {
    return setTimeout(step, 1000);
  },
};

function confirmElements() {
  return {
    section: document.getElementById("confirm"),
    title: document.getElementById("confirm-title"),
    ready: document.getElementById("confirm-ready"),
    folderLabel: document.getElementById("confirm-folder-label"),
    folder: document.getElementById("confirm-folder"),
    tick: document.getElementById("confirm-tick"),
    tickLabel: document.getElementById("confirm-tick-label"),
    renames: document.getElementById("confirm-renames"),
    renamesTail: document.getElementById("confirm-renames-tail"),
    notApprovedLabel: document.getElementById("confirm-not-approved-label"),
    notApproved: document.getElementById("confirm-not-approved"),
    skippedLabel: document.getElementById("confirm-skipped-label"),
    skipped: document.getElementById("confirm-skipped"),
    sync: document.getElementById("confirm-sync"),
    low: document.getElementById("confirm-low"),
    promises: document.getElementById("confirm-promises"),
    rename: document.getElementById("confirm-rename"),
    tickFirst: document.getElementById("confirm-tick-first"),
    error: document.getElementById("confirm-error"),
    running: document.getElementById("confirm-running"),
    progress: document.getElementById("confirm-progress"),
    keepOpen: document.getElementById("confirm-keep-open"),
    back: document.getElementById("confirm-back"),
    finish: document.getElementById("finish"),
    history: document.getElementById("history"),
    footerRename: document.getElementById("footer-rename"),
    steps: document.getElementById("steps"),
    stepReview: document.getElementById("step-review"),
    stepRename: document.getElementById("step-rename"),
  };
}

/* --- the words (pure) ----------------------------------------------------------------- */

/* Every number is drawn with the Reading step's `thousands` (`pam-reading.js`): one formatter,
 * never `toLocaleString`, whose answer depends on the ICU data the browser was built with. */

/* `124 files`; one file is `1 file` (the implementer's singular, pending the design model). */
function filesText(n) {
  return thousands(n) + (n === 1 ? " file" : " files");
}

/* W16's footer label, and W19's button: `Rename 124 files`. */
function renameLabel(n) {
  return "Rename " + filesText(n);
}

/* `Renaming… 312 of 1,204`; an undo's is `Putting names back… 312 of 1,204` (ruling B3). */
function movingLine(job) {
  var lead = job.kind === "undo" ? CONFIRM_COPY.puttingBack : CONFIRM_COPY.renaming;
  return lead + thousands(job.done) + CONFIRM_COPY.of + thousands(job.total);
}

/* U22: the sync line, only when the server names a provider. */
function syncLine(provider) {
  return provider ? CONFIRM_COPY.syncLead + provider + CONFIRM_COPY.syncTail : null;
}

/* U6 / W14: `N of these names need review.`, only when the batch holds a low name. */
function lowLine(low) {
  if (low <= 0) {
    return null;
  }
  return thousands(low) + (low === 1 ? CONFIRM_COPY.lowTailOne : CONFIRM_COPY.lowTail);
}

/* --- drawing -------------------------------------------------------------------------- */

function drawConfirmCopy(c) {
  c.title.textContent = CONFIRM_COPY.title;
  c.folderLabel.textContent = CONFIRM_COPY.inFolder;
  c.tickLabel.textContent = CONFIRM_COPY.tick;
  c.renamesTail.textContent = CONFIRM_COPY.getNames;
  c.notApprovedLabel.textContent = CONFIRM_COPY.notApproved;
  c.skippedLabel.textContent = CONFIRM_COPY.skippedByYou;
  c.tickFirst.textContent = CONFIRM_COPY.tickFirst;
  c.keepOpen.textContent = CONFIRM_COPY.keepOpen;
  c.back.textContent = CONFIRM_COPY.back;
  clear(c.promises);
  for (var i = 0; i < CONFIRM_COPY.promises.length; i += 1) {
    c.promises.appendChild(make("li", null, CONFIRM_COPY.promises[i]));
  }
  c.promises.appendChild(make("li", "confirm-caveat", CONFIRM_COPY.caveat));
}

/* W16: the footer's button, from `/list`'s `renames`; with none, W16's other label, off. The
 * step pills are shown with it (ruling B6): the list is drawn, so steps 1-3 are true. */
function drawFooter(renames) {
  var c = confirmElements();
  var n = typeof renames === "number" ? renames : 0;
  c.footerRename.textContent = n > 0 ? renameLabel(n) : CONFIRM_COPY.approveFirst;
  c.footerRename.disabled = n === 0 || PAGE.stopped;
  c.steps.hidden = false;
}

/* The screen stands in for the list (Welcome's parts), and the list's keys go quiet. */
function showConfirm(on) {
  var c = confirmElements();
  var parts = reviewParts();
  for (var i = 0; i < parts.length; i += 1) {
    parts[i].hidden = on;
  }
  c.section.hidden = !on;
  PAGE.confirming = on;
  c.stepReview.setAttribute("data-step", on ? "done" : "current");
  c.stepReview.textContent = on ? "3 Review ✓" : "3 Review";
  c.stepRename.setAttribute("data-step", on ? "current" : "next");
}

/* One of the screen's four parts: `ready` (W19), `running` (the progress), `finish`, and
 * `history` (W22, M14 Task 7; reached from `finish` only). */
function showPart(name) {
  var c = confirmElements();
  c.ready.hidden = name !== "ready";
  c.running.hidden = name !== "running";
  c.finish.hidden = name !== "finish";
  c.history.hidden = name !== "history";
  c.back.hidden = name === "running";
}

function showConfirmError(message) {
  var c = confirmElements();
  c.error.textContent = message || "";
  c.error.hidden = !message;
}

function drawConfirm(c, facts) {
  c.folder.textContent = facts.archive_root;
  c.tick.checked = false;
  c.renames.textContent = filesText(facts.renames);
  c.renamesTail.textContent = facts.renames === 1 ? CONFIRM_COPY.getNameOne : CONFIRM_COPY.getNames;
  c.notApproved.textContent = thousands(facts.not_approved);
  c.skipped.textContent = thousands(facts.skipped_by_you);
  var sync = syncLine(facts.sync_provider);
  c.sync.textContent = sync || "";
  c.sync.hidden = sync === null;
  var low = lowLine(facts.low_confidence);
  c.low.textContent = low || "";
  c.low.hidden = low === null;
  c.rename.textContent = renameLabel(facts.renames);
  onTick();
}

/* --- the flow ------------------------------------------------------------------------- */

/* W16's button: the confirmation is asked for NOW (U6), and its refusal is the list's note. */
function openConfirm() {
  if (CONFIRM.posting || PAGE.stopped) {
    return Promise.resolve(null);
  }
  CONFIRM.planId = LIST.planId;
  return api("api/plan/" + CONFIRM.planId + "/confirmation").then(
    function (facts) {
      CONFIRM.facts = facts;
      showConfirmError("");
      drawConfirm(confirmElements(), facts);
      showConfirm(true);
      showPart("ready");
      return facts;
    },
    function (failure) {
      if (failure instanceof ApiRefusal) {
        showListNote(failure.message);
      }
    }
  );
}

/* The box gates the button (U6). */
function onTick() {
  var c = confirmElements();
  c.rename.disabled = !c.tick.checked || CONFIRM.posting || PAGE.stopped;
  c.tickFirst.hidden = Boolean(c.tick.checked);
}

function postJson(body) {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

/* `Rename N files`: the nonce as received and the JSON literal `true`, posted ONCE. */
function pressRename() {
  var c = confirmElements();
  if (CONFIRM.posting || PAGE.stopped || !c.tick.checked || CONFIRM.facts === null) {
    return Promise.resolve(null);
  }
  CONFIRM.posting = true;
  c.rename.disabled = true;
  c.back.hidden = true;
  showConfirmError("");
  var body = { nonce: CONFIRM.facts.nonce, folder_confirmed: true };
  return api("api/plan/" + CONFIRM.planId + "/apply", postJson(body)).then(
    function () {
      return pollMoving();
    },
    function (failure) {
      c.back.hidden = false;
      if (failure instanceof ApiRefusal) {
        showConfirmError(failure.message);
      }
    }
  );
}

function pollMoving() {
  if (PAGE.stopped) {
    return null;
  }
  return api("api/job").then(onMovingJob, function (failure) {
    /* A network failure has shown U12's banner (`serverGone`); the poll stops either way. */
    if (failure instanceof ApiRefusal) {
      showConfirmError(failure.message);
    }
  });
}

function onMovingJob(job) {
  if (job.state === "running") {
    var c = confirmElements();
    showPart("running");
    c.progress.textContent = movingLine(job);
    CONFIRM.schedule(pollMoving);
    return null;
  }
  return finishMoving(job);
}

/* A reload while a rename or an undo runs, or after one ended (Task 6 Stage 1 page review,
 * S1P-F2): the progress view, or the end, comes back instead of the list. U11 tells the user pam
 * finishes whether the window stays open or not, so the page must be able to find the job again;
 * without this a reload drew the list, its footer offering `Approve names` over a plan already
 * renaming. `GET api/job` is served unbound. A read's job is `pam-reading.js`'s. The move is
 * the user's own press from before the reload, so `posting` holds as if it had just been made:
 * the footer cannot open a second Confirm over it. */
function resumeMoving() {
  if (CONFIRM.posting || PAGE.stopped) {
    return Promise.resolve(null);
  }
  return api("api/job").then(
    function (job) {
      if (CONFIRM.posting || (job.kind !== "rename" && job.kind !== "undo")) {
        return null;
      }
      CONFIRM.posting = true;
      CONFIRM.planId = LIST.planId;
      showConfirmError("");
      showConfirm(true);
      return onMovingJob(job);
    },
    function () {
      /* `GET api/job` refuses nothing (it takes no body and no binding); a network failure
       * has shown U12's banner. Either way the list, already drawn, stays as it is. */
      return null;
    }
  );
}

/* Back to the list, which asks the server for its numbers again (the footer's N with them). */
function backToList() {
  CONFIRM.posting = false;
  CONFIRM.facts = null;
  showConfirmError("");
  showConfirm(false);
  return refreshHeaders();
}

/* Wired ONCE by `app.js`'s `start`. `/list`'s summary reaches the footer through the list's
 * `renames` hook. */
function wireConfirm() {
  var c = confirmElements();
  drawConfirmCopy(c);
  c.footerRename.addEventListener("click", openConfirm);
  c.tick.addEventListener("change", onTick);
  c.rename.addEventListener("click", pressRename);
  c.back.addEventListener("click", backToList);
  LIST.hooks.renames = drawFooter;
}

/* --- the seams ------------------------------------------------------------------------- */

var CONFIRM_SEAM = {
  CONFIRM_COPY: CONFIRM_COPY,
  CONFIRM: CONFIRM,
  filesText: filesText,
  renameLabel: renameLabel,
  movingLine: movingLine,
  syncLine: syncLine,
  lowLine: lowLine,
  drawFooter: drawFooter,
  openConfirm: openConfirm,
  pressRename: pressRename,
  pollMoving: pollMoving,
  resumeMoving: resumeMoving,
  backToList: backToList,
  wireConfirm: wireConfirm,
};

if (typeof window !== "undefined") {
  window.PAM.confirm = CONFIRM_SEAM;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = CONFIRM_SEAM;
}
