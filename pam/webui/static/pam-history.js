/* HISTORY (M14 Task 7; UX 5 W22, X2, U14; the controller's rulings T7-R1..T7-R5).
 *
 * A row per rename session, newest first, as `GET api/sessions` lists them: its time
 * (`Today, 2:32 PM`), `124 renamed` or `124 renamed · undone`, `Report` when the session's report
 * exists, and `Undo this rename` EXACTLY when the server says the session can still be undone
 * (`undoable`: design 7.4's rule, applied by the server to every row - X2). Every number and
 * every flag is the server's; the page decides only the words.
 *
 * REACHED FROM DONE, THE LIST AND WELCOME (T7-R1, widened by M15 Task 3: AM3 / E7). Done's
 * `History` (after a rename, and after an undo), the list header's `History` and Welcome's
 * open it as the Confirm screen's fourth part, and `Back` returns to the page it was opened
 * from, as it was (E7 amends W22's "Back returns to Done"). The list's and Welcome's controls
 * are drawn only when `GET api/sessions` holds at least one session for the bound folder (AM3).
 *
 * ONE UNDO ON THE PAGE, NOT TWO. A row's `Undo this rename` hands its session to `pam-done.js`'s
 * `undoSession`, which is Done's own press: the same `GET api/session/{id}` for the nonce, the
 * same single `POST api/session/{id}/undo` with the nonce as received, the same progress, and
 * Done after the undo. `Report` is a plain link to `api/session/{id}/report` (the text the
 * rename wrote, served as `text/plain`), opened in a new tab so the page keeps its place.
 *
 * THE TIME (T7-R5). A session id's clock is the LOCAL wall time `pam apply` read on this same
 * computer (`datetime.now()`), and the server hands it on as `YYYY-MM-DDTHH:MM:SS` with no zone.
 * The page reads it as fields and compares them with "now"'s local fields: nothing is converted,
 * and `Date` never parses the string. "Now" is `HISTORY.now`, so a check injects it.
 *
 * The functions this file calls in `pam-confirm.js` (`confirmElements`, `showPart`),
 * `pam-done.js` (`undoSession`, `reportPath`), `pam-reading.js` (`thousands`) and `pam-core.js`
 * (`api`, `make`, `clear`) are looked up when a click or an answer arrives, never at load.
 */

"use strict";

/* --- the copy ------------------------------------------------------------------------- */

var HISTORY_COPY = {
  /* W22. */
  title: "History",
  renamedTail: " renamed",
  undoneTail: " · undone",
  report: "Report",
  /* HT-M14 F6: a session with no report file says so rather than drawing nothing. A run pam
   * stopped part-way (Ctrl+C, a crash) never wrote one; recovery puts things right but writes
   * no report. The title is the server's own NO_REPORT sentence (`confirm_api.py`). */
  noReport: "No report",
  noReportWhy: "pam has no report for this session: it was never written, or it was deleted.",
  /* W20's words, which X2 gives the row. */
  undo: "Undo this rename",
  /* AM2 (M15 Task 2), verbatim after the count: files the rename skipped because they became
   * cloud-only. The server reads the counts from the session's report (`cloud_only`, and
   * `cloud_only_units`). C6.5 (owner, 2026-11-16): one file is singular, and when a skipped unit
   * has more than one member (files > units) `cloudOnlyTogether` follows - the console's words. */
  cloudOnly: " files became cloud-only after they were approved and were not renamed. Make them available offline and read the folder again.",
  cloudOnlyOne: "1 file became cloud-only after it was approved and was not renamed. Make it available offline and read the folder again.",
  cloudOnlyTogether: " A photo and the files that go with it are skipped together.",
  /* Done's link to History: W22's own word. */
  link: "History",
  /* AM3 (M15 Task 3): the list header's and Welcome's control carries W22's own word, unchanged
   * (the copy waits for the maintainer's wording read, C6). */
  linkEverywhere: "History",
  /* The implementer's words (Task 7), pending the design model: the way back to Done, the
   * empty sentence, and the labels for yesterday and an older day (W22 spells only today's). */
  back: "Back",
  empty: "pam has not renamed anything in this folder yet.",
  today: "Today, ",
  yesterday: "Yesterday, ",
  months: [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
  ],
};

/* --- state ---------------------------------------------------------------------------- */

var HISTORY = {
  /* Whether Done's `Keep checking names` was hidden when History opened, restored by `Back`. */
  backHidden: false,
  /* E7: the page History was opened from - "done", "list" or "welcome" - which `Back` returns to. */
  origin: "done",
  /* "Now", for the time labels. A node check replaces it; a real clock is never read there. */
  now: function () {
    return new Date();
  },
};

function historyElements() {
  return {
    link: document.getElementById("finish-history"),
    title: document.getElementById("history-title"),
    empty: document.getElementById("history-empty"),
    rows: document.getElementById("history-rows"),
    error: document.getElementById("history-error"),
    back: document.getElementById("history-back"),
    listLink: document.getElementById("list-history"),
    welcomeLink: document.getElementById("welcome-history"),
  };
}

/* --- the words (pure) ----------------------------------------------------------------- */

/* `2:32 PM`; midnight is `12:05 AM`, noon `12:05 PM`. */
function clockText(hour, minute) {
  var h = hour % 12 === 0 ? 12 : hour % 12;
  return h + ":" + (minute < 10 ? "0" : "") + minute + (hour < 12 ? " AM" : " PM");
}

function sameDay(year, month, day, date) {
  return date.getFullYear() === year && date.getMonth() + 1 === month && date.getDate() === day;
}

/* W22: `Today, 2:32 PM`; then `Yesterday, 11:59 PM` and `October 28, 2026, 2:32 PM`. */
function timeLabel(time, now) {
  var at = /^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})$/.exec(time || "");
  if (at === null) {
    return "";
  }
  var year = Number(at[1]);
  var month = Number(at[2]);
  var day = Number(at[3]);
  var clock = clockText(Number(at[4]), Number(at[5]));
  if (sameDay(year, month, day, now)) {
    return HISTORY_COPY.today + clock;
  }
  var yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
  if (sameDay(year, month, day, yesterday)) {
    return HISTORY_COPY.yesterday + clock;
  }
  return HISTORY_COPY.months[month - 1] + " " + day + ", " + year + ", " + clock;
}

/* W22: `124 renamed`, or `124 renamed · undone` when every renamed file was put back. */
function countText(row) {
  return thousands(row.renamed) + HISTORY_COPY.renamedTail + (row.undone ? HISTORY_COPY.undoneTail : "");
}

/* AM2: `2 files became cloud-only ...`, or nothing when the session skipped none; C6.5's singular
 * and its sentence for a unit of several members. */
function cloudOnlyText(row) {
  if (!(row.cloud_only > 0)) {
    return null;
  }
  var text =
    row.cloud_only === 1 ? HISTORY_COPY.cloudOnlyOne : thousands(row.cloud_only) + HISTORY_COPY.cloudOnly;
  return row.cloud_only > row.cloud_only_units ? text + HISTORY_COPY.cloudOnlyTogether : text;
}

/* --- drawing -------------------------------------------------------------------------- */

function drawRow(row, now) {
  var item = make("li", "history-row", null);
  item.appendChild(make("span", "history-time", timeLabel(row.time, now)));
  item.appendChild(make("span", "history-count", countText(row)));
  var cloudOnly = cloudOnlyText(row);
  if (cloudOnly) {
    item.appendChild(make("span", "history-cloud-only", cloudOnly));
  }
  if (row.report) {
    var report = make("a", "history-report", HISTORY_COPY.report);
    report.setAttribute("href", reportPath(row.session_id));
    report.setAttribute("target", "_blank");
    report.setAttribute("rel", "noopener");
    item.appendChild(report);
  } else {
    var none = make("span", "history-no-report", HISTORY_COPY.noReport);
    none.setAttribute("title", HISTORY_COPY.noReportWhy);
    item.appendChild(none);
  }
  if (row.undoable) {
    var undo = make("button", "history-undo", HISTORY_COPY.undo);
    undo.setAttribute("type", "button");
    undo.addEventListener("click", function () {
      return pressHistoryUndo(row.session_id, undo);
    });
    item.appendChild(undo);
  }
  return item;
}

function drawHistory(h, rows) {
  var now = HISTORY.now();
  clear(h.rows);
  for (var i = 0; i < rows.length; i += 1) {
    h.rows.appendChild(drawRow(rows[i], now));
  }
  h.rows.hidden = rows.length === 0;
  h.empty.hidden = rows.length !== 0;
}

function showHistoryError(h, message) {
  h.error.textContent = message || "";
  h.error.hidden = !message;
}

/* Done's `History`: the fourth part, its rows asked for now. */
function openHistory() {
  return openHistoryFrom("done");
}

/* AM3: the list header's `History`. */
function openHistoryFromList() {
  return openHistoryFrom("list");
}

/* AM3: Welcome's `History`. */
function openHistoryFromWelcome() {
  return openHistoryFrom("welcome");
}

/* The screen's fourth part from `origin`. Done is already inside the screen; the list is
 * stood in for by it (`showConfirm`, whose keys-quiet flag comes with it); Welcome is hidden
 * and the screen shown over the same slot. */
function openHistoryFrom(origin) {
  var c = confirmElements();
  var h = historyElements();
  HISTORY.origin = origin;
  HISTORY.backHidden = c.back.hidden;
  if (origin === "list") {
    showConfirmError("");
    showConfirm(true);
  } else if (origin === "welcome") {
    showConfirmError("");
    document.getElementById("welcome").hidden = true;
    c.section.hidden = false;
  }
  clear(h.rows);
  h.rows.hidden = true;
  h.empty.hidden = true;
  showHistoryError(h, "");
  showPart("history");
  /* History's own `Back` is the way out; Done's `Keep checking names` waits for its return. */
  c.back.hidden = true;
  return api("api/sessions").then(
    function (answer) {
      drawHistory(h, answer.sessions);
      return null;
    },
    function (failure) {
      /* A refusal's sentence, verbatim; a network failure has shown U12's banner. */
      if (failure instanceof ApiRefusal) {
        showHistoryError(h, failure.message);
      }
      return null;
    }
  );
}

/* `Back`: the Done History came from, as it was. */
function backToDone() {
  showPart("finish");
  confirmElements().back.hidden = HISTORY.backHidden;
}

/* `Back` (E7): the page History was opened from, as it was. */
function backFromHistory() {
  if (HISTORY.origin === "list") {
    /* History's part goes with it; the screen's own `ready` part is what Confirm reopens on. */
    showPart("ready");
    confirmElements().back.hidden = HISTORY.backHidden;
    showConfirm(false);
    return null;
  }
  if (HISTORY.origin === "welcome") {
    showPart("ready");
    confirmElements().back.hidden = HISTORY.backHidden;
    confirmElements().section.hidden = true;
    document.getElementById("welcome").hidden = false;
    return null;
  }
  return backToDone();
}

/* AM3: the two controls are shown exactly when the bound folder has a rename session. A refusal
 * (no folder yet, on Welcome) or a failure leaves them hidden: there is nothing to open. */
function drawHistoryLinks(any) {
  var h = historyElements();
  h.listLink.hidden = !any;
  h.welcomeLink.hidden = !any;
}

function refreshHistoryLinks() {
  return api("api/sessions").then(
    function (answer) {
      drawHistoryLinks(Boolean(answer && answer.sessions && answer.sessions.length > 0));
      return null;
    },
    function () {
      drawHistoryLinks(false);
      return null;
    }
  );
}

/* A row's `Undo this rename`: Done's undo for that session, once - a second press, from this
 * row, another row or Done, waits on Done's own in-flight flag (`DONE.pressing`, R7-F3). */
function pressHistoryUndo(session, button) {
  if (PAGE.stopped) {
    return Promise.resolve(null);
  }
  button.disabled = true;
  var pending = undoSession(session);
  /* `undoSession` showed the finish part; Done's `Keep checking names` keeps the state it had. */
  confirmElements().back.hidden = HISTORY.backHidden;
  /* From Welcome there is no list behind Done to keep checking. */
  if (HISTORY.origin === "welcome") {
    confirmElements().back.hidden = true;
  }
  return pending;
}

/* Wired ONCE by `app.js`'s `start`, after `wireDone`. */
function wireHistory() {
  var h = historyElements();
  h.link.textContent = HISTORY_COPY.link;
  h.title.textContent = HISTORY_COPY.title;
  h.empty.textContent = HISTORY_COPY.empty;
  h.back.textContent = HISTORY_COPY.back;
  h.listLink.textContent = HISTORY_COPY.linkEverywhere;
  h.welcomeLink.textContent = HISTORY_COPY.linkEverywhere;
  h.link.addEventListener("click", openHistory);
  h.listLink.addEventListener("click", openHistoryFromList);
  h.welcomeLink.addEventListener("click", openHistoryFromWelcome);
  h.back.addEventListener("click", backFromHistory);
}

/* --- the seams ------------------------------------------------------------------------- */

var HISTORY_SEAM = {
  HISTORY_COPY: HISTORY_COPY,
  HISTORY: HISTORY,
  clockText: clockText,
  timeLabel: timeLabel,
  countText: countText,
  cloudOnlyText: cloudOnlyText,
  openHistory: openHistory,
  backToDone: backToDone,
  backFromHistory: backFromHistory,
  openHistoryFromList: openHistoryFromList,
  openHistoryFromWelcome: openHistoryFromWelcome,
  refreshHistoryLinks: refreshHistoryLinks,
  drawHistoryLinks: drawHistoryLinks,
  pressHistoryUndo: pressHistoryUndo,
  wireHistory: wireHistory,
};

if (typeof window !== "undefined") {
  window.PAM.history = HISTORY_SEAM;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = HISTORY_SEAM;
}
