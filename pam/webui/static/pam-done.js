/* DONE, UNDO, AND A RENAME THAT DID NOT RUN (M14 Task 6c; U8 W20/W21, U11, U12, U14, U22;
 * M14 Task 7: B4's report link and History's undo).
 *
 * `pam-confirm.js`'s poll hands this file the job when it leaves `running`:
 *   - a finished RENAME is *Done* (W20). Its counts are the job's `result` - `{renamed, failed,
 *     not_approved, elapsed_s}` - never the session's journal counts, which do not count a
 *     preflight refusal; `Skipped by you` is the confirmation the page showed. U22: the headline
 *     changes when a name could not be renamed, and is never buried; "with no problems" only
 *     when `failed == 0`; the not-approved line only when it or the skipped count is non-zero.
 *     The job's own lines (each failure's reason) sit under the headline, and under a failure
 *     headline `See what happened` links to the session's report (ruling B4, M14 Task 7): a
 *     plain link to `api/session/{id}/report`, which the server sends as `text/plain`, opened in
 *     a new tab so the page keeps its place.
 *   - `Undo this rename` asks `GET api/session/{id}` for its nonce and posts it as received to
 *     `POST api/session/{id}/undo`, once; the same poll draws `Putting names back…`.
 *   - a finished UNDO is W21: `All 124 names put back` (`1 name put back` for one - M14 Y3) when
 *     every name was put back (`failed == 0` and none was already back); else U22's other
 *     headline. `Your folder is exactly as it was before.` only when every name came back AND no
 *     rename session here can still be undone (M14 Y3: `GET api/sessions`' per-row `undoable`).
 *   - after a rename and after an undo, Done offers `Start reading` (M14 Y2): Welcome's Reading
 *     step for the folder the server holds, where the person starts the read - one read flow.
 *   - a job that did not run, or died, is `pam-stopped.js`'s (`drawRefused`,
 *     `offerUndoAfterDeath`; cut out of this file by M15 Task 5b, before C6.2's edit). `Keep
 *     checking names` goes back to the list.
 *   - M15 Task 5b (the maintainer's C6 ruling, 2026-11-16): C6.1, an undo with names already back says
 *     so after its headline and says `could not be` only when one could not; C6.8(a), a rename
 *     that skipped cloud-only files (`cloud_only > 0`) says its time with no "no problems" tail.
 */

"use strict";

/* --- the copy ------------------------------------------------------------------------- */

var DONE_COPY = {
  /* U22 / ruling B4: under a failure headline, the link to the report. */
  seeWhat: "See what happened",
  /* W20 and U22. */
  renamedTail: " renamed",
  couldNotBe: " could not be.",
  inLead: "In ",
  noProblems: " seconds, with no problems.",
  /* C6.8(a): the time line when cloud-only files were not renamed - nothing failed, but not
   * everything was renamed either. */
  secondsOnly: " seconds.",
  changedMind: "Changed your mind?",
  undo: "Undo this rename",
  /* W21 and U22. */
  all: "All ",
  putBack: " names put back",
  /* M14 Y3: one is singular. */
  putBackOne: " name put back",
  /* C6.1 (owner, 2026-11-16): after the headline, only when `already_undone > 0`. */
  alreadyBack: " were already back under their old names.",
  alreadyBackOne: " was already back under its old name.",
  exactly: "Your folder is exactly as it was before.",
  /* M14 Y2: the amendment's word for Done's way to the Reading step. */
  readAgain: "Start reading",
};

/* --- state ---------------------------------------------------------------------------- */

var DONE = {
  /* The rename session the finished job wrote: what `Undo this rename` puts back. */
  session: null,
  /* `Undo this rename` pressed: a second press posts nothing. */
  undoing: false,
  /* An undo's request is in flight, from Done or History (R7-F3): `undoSession` waits it out
   * rather than reset `undoing` under it. Cleared when `pressUndo`'s promise settles. */
  pressing: false,
  /* M14 Y2: how the Reading step reached from Done opens the plan its read made - a reload,
   * which boots on the server's plan (this page's list was drawn for the renamed plan, and
   * `startList` wires its controls once). A node check replaces it. */
  reload: function () {
    window.location.reload();
  },
};

function doneElements() {
  return {
    headline: document.getElementById("finish-headline"),
    time: document.getElementById("finish-time"),
    left: document.getElementById("finish-left"),
    lines: document.getElementById("finish-lines"),
    reportBox: document.getElementById("finish-report-box"),
    report: document.getElementById("finish-report"),
    undoBox: document.getElementById("finish-undo-box"),
    changed: document.getElementById("finish-changed"),
    undo: document.getElementById("finish-undo"),
    promise: document.getElementById("finish-promise"),
    caveat: document.getElementById("finish-caveat"),
    read: document.getElementById("finish-read"),
    recovery: document.getElementById("finish-recovery"),
    recoveryTitle: document.getElementById("finish-recovery-title"),
    recoveryLine: document.getElementById("finish-recovery-line"),
  };
}

/* --- the words (pure) ----------------------------------------------------------------- */

/* W20 / U22: `124 files renamed`, or `119 files renamed, 5 could not be.` */
function renamedHeadline(result) {
  var head = filesText(result.renamed) + DONE_COPY.renamedTail;
  return result.failed > 0 ? head + ", " + thousands(result.failed) + DONE_COPY.couldNotBe : head;
}

/* W20: `In 3.1 seconds, with no problems.` - ONLY when nothing failed (U22); `In 3.1 seconds.`
 * when cloud-only files were not renamed (C6.8(a), AM2's sentence then says why). */
function timeLine(result) {
  if (result.failed !== 0) {
    return null;
  }
  var tail = result.cloud_only > 0 ? DONE_COPY.secondsOnly : DONE_COPY.noProblems;
  return DONE_COPY.inLead + Number(result.elapsed_s).toFixed(1) + tail;
}

/* U22: `4 names were not approved and 0 were skipped; they kept their old names.`, only when
 * either count is non-zero. */
function leftLine(notApproved, skipped) {
  if (notApproved === 0 && skipped === 0) {
    return null;
  }
  /* HT-M14 F5: each count agrees with its own verb, and the tail with the total. */
  return (
    thousands(notApproved) + (notApproved === 1 ? " name was" : " names were") +
    " not approved and " + thousands(skipped) + (skipped === 1 ? " was" : " were") +
    " skipped; " +
    (notApproved + skipped === 1 ? "it kept its old name." : "they kept their old names.")
  );
}

/* `124 names put back`; one is `1 name put back` (M14 Y3). */
function putBackText(restored) {
  return thousands(restored) + (restored === 1 ? DONE_COPY.putBackOne : DONE_COPY.putBack);
}

/* W21 / U22: every name put back -> `All 124 names put back` (`1 name put back` for one) and
 * `whole`; else `121 names put back, 3 could not be.` when some failed, `121 names put back.`
 * when none did, and C6.1's `3 were already back under their old names.` after either when any
 * was (`1 was already back under its old name.`). The folder sentence is not decided here: it
 * needs the folder's other sessions too (`drawFolderSentence`, M14 Y3). */
function undoneText(result) {
  if (result.failed === 0 && result.already_undone === 0) {
    var one = result.restored === 1;
    return {
      headline: (one ? "" : DONE_COPY.all) + putBackText(result.restored),
      whole: true,
    };
  }
  var head = putBackText(result.restored);
  head += result.failed > 0 ? ", " + thousands(result.failed) + DONE_COPY.couldNotBe : ".";
  var back = result.already_undone;
  if (back > 0) {
    head += " " + thousands(back) + (back === 1 ? DONE_COPY.alreadyBackOne : DONE_COPY.alreadyBack);
  }
  return { headline: head, whole: false };
}

/* --- drawing -------------------------------------------------------------------------- */

function drawLine(node, text) {
  node.textContent = text || "";
  node.hidden = !text;
}

function drawJobLines(f, lines) {
  clear(f.lines);
  for (var i = 0; i < lines.length; i += 1) {
    f.lines.appendChild(make("p", null, lines[i]));
  }
  f.lines.hidden = lines.length === 0;
}

/* A clean slate for the finish part: nothing from the last job stays on it. */
function resetFinish(f) {
  drawLine(f.headline, null);
  drawLine(f.time, null);
  drawLine(f.left, null);
  drawJobLines(f, []);
  drawReport(f, null);
  f.undoBox.hidden = true;
  f.recovery.hidden = true;
  f.read.hidden = true;
}

/* A session's report, by its id (Task 7: `GET api/session/{id}/report`). History links here too. */
function reportPath(session) {
  return "api/session/" + encodeURIComponent(session) + "/report";
}

/* B4: `See what happened`, or nothing (`session` null). */
function drawReport(f, session) {
  f.reportBox.hidden = !session;
  if (session) {
    f.report.setAttribute("href", reportPath(session));
  } else {
    f.report.removeAttribute("href");
  }
}

function drawRenamed(f, job) {
  var result = job.result;
  var skipped = CONFIRM.facts ? CONFIRM.facts.skipped_by_you : 0;
  drawLine(f.headline, renamedHeadline(result));
  drawLine(f.time, timeLine(result));
  drawLine(f.left, leftLine(result.not_approved, skipped));
  drawJobLines(f, job.lines);
  drawReport(f, result.failed > 0 ? job.session : null);
  DONE.session = job.session;
  DONE.undoing = false;
  f.undo.disabled = false;
  f.undoBox.hidden = !(result.renamed > 0 && job.session);
}

function drawUndone(f, job) {
  var text = undoneText(job.result);
  drawLine(f.headline, text.headline);
  drawJobLines(f, job.lines);
  return drawFolderSentence(f, text);
}

/* M14 Y3: `Your folder is exactly as it was before.` claims the whole FOLDER, so it is said only
 * when the undone session's every name came back AND no rename session here can still be undone
 * - `GET api/sessions`, whose rows carry design 7.4's rule (`undoable`) per session. Undoing the
 * newer of two renames leaves the older in place, and the headline then stands alone; so does a
 * refused or failed listing. */
function drawFolderSentence(f, text) {
  if (!text.whole) {
    return null;
  }
  return api("api/sessions").then(
    function (answer) {
      var rows = answer.sessions || [];
      for (var i = 0; i < rows.length; i += 1) {
        if (rows[i].undoable) {
          return null;
        }
      }
      drawLine(f.time, DONE_COPY.exactly);
      return null;
    },
    function () {
      return null;
    }
  );
}

/* M14 Y2: after a rename and after an undo Done offers `Start reading` - never on a stopped page. */
function offerReadAgain(f) {
  f.read.hidden = PAGE.stopped;
}

/* Done's `Start reading` (M14 Y2): Welcome's Reading step for the folder the server holds, through
 * `app.js`'s `welcomeFirst` (the list's keys go quiet), and the person presses `Start reading`
 * THERE - `pam-reading.js`'s one read flow, never a second. The renamed plan is not open, so
 * Welcome offers the read; its `Review names` reloads the page (`DONE.reload`). Nothing while an
 * undo is being asked for, nor once pam has stopped. */
function pressReadAgain() {
  var f = doneElements();
  if (PAGE.stopped || DONE.pressing || f.read.hidden) {
    return Promise.resolve(null);
  }
  return api("api/ping").then(
    function (ping) {
      PAGE.binding = ping.binding;
      showConfirm(false);
      var open = ping.status === "open" ? ping.plan_id : null;
      return welcomeFirst({ folder: ping.folder, plan_id: open, recovery: null }, function () {
        DONE.reload();
        return Promise.resolve(null);
      });
    },
    function () {
      /* A network failure has shown U12's banner; the ping refuses nothing else. */
      return null;
    }
  );
}

/* The job left `running` (`pam-confirm.js`'s poll). */
function finishMoving(job) {
  var f = doneElements();
  resetFinish(f);
  if (job.state === "done" && job.result) {
    var after = null;
    if (job.kind === "undo") {
      after = drawUndone(f, job);
    } else {
      drawRenamed(f, job);
    }
    offerReadAgain(f);
    showPart("finish");
    confirmElements().back.hidden = true;
    return after;
  }
  drawRefused(f, job);
  showPart("finish");
  return offerUndoAfterDeath(f, job);
}

/* `Undo this rename`: the session's nonce, asked for now, posted once as it came (U14). */
function pressUndo() {
  var f = doneElements();
  if (DONE.undoing || PAGE.stopped || !DONE.session) {
    return Promise.resolve(null);
  }
  DONE.undoing = true;
  DONE.pressing = true;
  f.undo.disabled = true;
  var path = "api/session/" + DONE.session;
  var settled = function (value) {
    DONE.pressing = false;
    return value;
  };
  return api(path)
    .then(function (facts) {
      return api(path + "/undo", postJson({ nonce: facts.nonce }));
    })
    .then(
      function () {
        return pollMoving();
      },
      function (failure) {
        if (failure instanceof ApiRefusal) {
          drawJobLines(f, [failure.message]);
        }
      }
    )
    .then(settled, function (failure) {
      settled(null);
      throw failure;
    });
}

/* History's `Undo this rename` (M14 Task 7, X2): Done's own undo, for the row's session - not a
 * second flow. The session becomes Done's, the finish part is cleared and shown (a refusal's
 * sentence lands where Done's would), and `pressUndo` asks for the nonce and posts it. */
function undoSession(session) {
  if (DONE.pressing) {
    return Promise.resolve(null);
  }
  var f = doneElements();
  resetFinish(f);
  showPart("finish");
  DONE.session = session;
  DONE.undoing = false;
  f.undo.disabled = false;
  return pressUndo();
}

/* Wired ONCE by `app.js`'s `start`, after `wireConfirm`. */
function wireDone() {
  var f = doneElements();
  f.changed.textContent = DONE_COPY.changedMind;
  f.undo.textContent = DONE_COPY.undo;
  f.promise.textContent = CONFIRM_COPY.promises[2];
  f.caveat.textContent = CONFIRM_COPY.caveat;
  f.report.textContent = DONE_COPY.seeWhat;
  f.read.textContent = DONE_COPY.readAgain;
  f.undo.addEventListener("click", pressUndo);
  f.read.addEventListener("click", pressReadAgain);
}

/* --- the seams ------------------------------------------------------------------------- */

var DONE_SEAM = {
  DONE_COPY: DONE_COPY,
  DONE: DONE,
  renamedHeadline: renamedHeadline,
  timeLine: timeLine,
  leftLine: leftLine,
  undoneText: undoneText,
  putBackText: putBackText,
  finishMoving: finishMoving,
  pressUndo: pressUndo,
  pressReadAgain: pressReadAgain,
  reportPath: reportPath,
  undoSession: undoSession,
  wireDone: wireDone,
};

if (typeof window !== "undefined") {
  window.PAM.done = DONE_SEAM;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = DONE_SEAM;
}
