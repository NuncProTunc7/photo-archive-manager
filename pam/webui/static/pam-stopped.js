/* A RENAME THAT DID NOT RUN, OR STOPPED PART-WAY (cut out of `pam-done.js` by M15 Task 5b, before
 * its C6.2 edit: `pam-done.js` was at 292 executable lines and carried "the next edit names its
 * cut"; U12, U14, S2-F1, C6.2).
 *
 * `pam-done.js`'s `finishMoving` hands this file a job that left `running` without finishing:
 *   - a job that did not run (`cancelled`: U14's sentence; `failed` with no recovery: a
 *     whole-plan refusal's sentence) shows the job's lines verbatim;
 *   - one that DIED shows recovery's own line. A died RENAME draws C6.2's heading (the maintainer's
 *     ruling, 2026-11-16) - `pam stopped part-way. It has put things right.` - in Done's headline,
 *     in place of U12's; a died undo keeps U12's heading over recovery's line;
 *   - a died rename that renamed files before it died offers W20's undo exactly when the server
 *     says its session can still be undone (S2-F1), and then, under the heading, C6.2's second
 *     line - so the line never promises an undo the server would refuse.
 * It shares the page's one global scope (`drawLine`, `drawJobLines`, `DONE` are `pam-done.js`'s;
 * `api` is `pam-core.js`'s), and is loaded after `pam-done.js`.
 */

"use strict";

/* --- the copy ------------------------------------------------------------------------- */

var STOPPED_COPY = {
  /* U12: the heading over recovery's own line after a job died (a died undo). */
  recovered: "pam finished putting things right after it stopped.",
  /* C6.2 (owner, 2026-11-16): a died rename's heading, and the line beneath it when its session
   * can still be undone. */
  partWay: "pam stopped part-way. It has put things right.",
  renamedBefore: "Some files were renamed before it stopped. You can put them back.",
};

/* --- drawing -------------------------------------------------------------------------- */

/* A job that renamed nothing: its lines verbatim, or recovery's line under its heading. */
function drawRefused(f, job) {
  var healed = job.state === "failed" && Boolean(job.recovery);
  var died = healed && job.kind === "rename";
  drawJobLines(f, healed ? [] : job.lines);
  drawLine(f.headline, died ? STOPPED_COPY.partWay : null);
  drawLine(f.recoveryTitle, died ? null : STOPPED_COPY.recovered);
  f.recoveryLine.textContent = healed ? job.recovery : "";
  f.recovery.hidden = !healed;
}

/* A rename that DIED after it began names its session (S2-F1, Task 6 Stage 2): recovery put
 * back only the unit in flight, so the files renamed before it can still be put back. Undo is
 * offered exactly when the server says that session can be undone (`GET api/session/{id}`'s
 * `undoable`); its words are W20's own, and C6.2's line under the heading goes with it. */
function offerUndoAfterDeath(f, job) {
  if (job.kind !== "rename" || !job.session) {
    return null;
  }
  return api("api/session/" + job.session).then(
    function (facts) {
      DONE.session = job.session;
      DONE.undoing = false;
      f.undo.disabled = false;
      f.undoBox.hidden = !facts.undoable;
      drawLine(f.time, facts.undoable ? STOPPED_COPY.renamedBefore : null);
      return null;
    },
    function () {
      /* A refusal (or U12's banner): the session cannot be offered; nothing more is drawn. */
      return null;
    }
  );
}

/* --- the seams ------------------------------------------------------------------------- */

var STOPPED_SEAM = {
  STOPPED_COPY: STOPPED_COPY,
  drawRefused: drawRefused,
  offerUndoAfterDeath: offerUndoAfterDeath,
};

if (typeof window !== "undefined") {
  window.PAM.stopped = STOPPED_SEAM;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = STOPPED_SEAM;
}
