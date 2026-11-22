/* WELCOME'S READING STEP (M14 Task 5e; U10, U11, U12, U21, OD-4).
 *
 * Cut out of `pam-welcome.js` before it was written (that file was at 222 executable lines):
 * `Start reading`, the poll, the progress line, `Stop`, the job's own lines and the finish.
 * Welcome's ids are `pam-welcome.js`'s `welcomeElements()` and its words `WELCOME_COPY`; the
 * functions this file calls there (`markSteps`, `clearError`, `onRefusal`, `drawFolder`,
 * `resetChoice`) are looked up when a click or an answer arrives, never at load, so it loads
 * BEFORE that file.
 *
 * WHEN `Start reading` IS OFFERED: only when NO plan is open - a chosen folder, or a bound one
 * the server says has no plan. Never while a plan is open: a read over an open plan supersedes
 * it, and its decisions with it (design 8.2 "Jobs"). `offerReading` is the one place that asks.
 *
 * THE ROUTES are design 8.2's: `POST api/job/read {}` (its answer names the folder and a new
 * binding, which the page adopts - on a chosen folder the read IS the bind), `GET api/job`
 * about once a second, `POST api/job/cancel {}`. Every sentence the job says - `Nothing new to
 * read.`, U21's console line, the stopped line, recovery's line - is the SERVER'S, shown as it
 * came. While pam plans, the counts stay at the scan's last `done == total`; U11 gives that
 * moment no sentence, so none is drawn.
 *
 * The timer is `READING.schedule`, so the node checks drive the poll without real time.
 */

"use strict";

/* --- the copy ------------------------------------------------------------------------- */

/* THE ETA PHRASE - THE CONTROLLER'S WORDING (Task 5e dispatch), pending the design model at
 * the M14 close. W18's mockup line is `748 of 1,204 · about a minute left`. */
var READING_COPY = {
  of: " of ",
  dot: " · ",
  underAMinute: "less than a minute left",
  aMinute: "about a minute left",
  anHour: "about an hour left",
};

/* --- state ---------------------------------------------------------------------------- */

var READING = {
  /* A read is posting or running: a second press posts nothing (Task 3's F4). */
  active: false,
  /* `Stop` was pressed for the read on show. */
  stopping: false,
  /* The poll's timer: `step` runs about a second later. A node check replaces it. */
  schedule: function (step) {
    return setTimeout(step, 1000);
  },
};

/* --- the progress line (pure) --------------------------------------------------------- */

/* Thousands with a comma, spelled here rather than left to `toLocaleString`, whose answer
 * depends on the ICU data the browser (or node) was built with. */
function thousands(count) {
  return String(count).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

function etaPhrase(seconds) {
  if (seconds < 60) {
    return READING_COPY.underAMinute;
  }
  if (seconds < 90) {
    return READING_COPY.aMinute;
  }
  if (seconds < 3570) {  /* from 3570 s the rounded minutes would read 60 */
    return "about " + Math.round(seconds / 60) + " minutes left";
  }
  if (seconds < 5400) {
    return READING_COPY.anHour;
  }
  return "about " + Math.round(seconds / 3600) + " hours left";
}

/* `748 of 1,204 · about a minute left`; the ETA only when the server sent one. */
function progressLine(job) {
  var line = thousands(job.done) + READING_COPY.of + thousands(job.total);
  return job.eta_s === null ? line : line + READING_COPY.dot + etaPhrase(job.eta_s);
}

/* --- drawing -------------------------------------------------------------------------- */

/* The step's resting state: `Start reading` offered - or, while a plan is open, not. */
function offerReading(w) {
  w.progress.hidden = true;
  w.stop.hidden = true;
  w.change.hidden = false;
  if (WELCOME.planId !== null) {
    w.startReading.hidden = true;
    return;
  }
  w.startReading.hidden = false;
  w.startReading.disabled = false;
  markSteps(w, "done", "current", "next");
}

function drawLines(w, lines) {
  clear(w.lines);
  for (var i = 0; i < lines.length; i += 1) {
    w.lines.appendChild(make("p", null, lines[i]));
  }
  w.lines.hidden = lines.length === 0;
}

function showRunning(job) {
  var w = welcomeElements();
  w.startReading.hidden = true;
  w.change.hidden = true;
  w.progress.textContent = progressLine(job);
  w.progress.hidden = false;
  w.stop.hidden = false;
  w.stop.disabled = READING.stopping;
  READING.active = true;
  markSteps(w, "done", "current", "next");
}

/* --- the job -------------------------------------------------------------------------- */

function postJob(route) {
  var request = {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  };
  return api(route, request);
}

function startReading() {
  if (READING.active || PAGE.stopped) {
    return Promise.resolve(null);
  }
  READING.active = true;
  var w = welcomeElements();
  w.startReading.disabled = true;
  /* UX Welcome: `Change` is not offered mid-read - nor while the read posts, which on a chosen
   * folder is the bind (Task 5 review R2-F1). */
  w.change.hidden = true;
  clearError();
  return postJob("api/job/read").then(
    function (body) {
      PAGE.binding = body.binding;
      w.folder.textContent = body.folder;
      w.folder.hidden = false;
      w.recovery.hidden = true;
      w.startReading.hidden = true;
      w.change.hidden = true;
      drawLines(w, []);
      READING.stopping = false;
      return poll();
    },
    function (failure) {
      /* A dead server (`serverGone`) has disabled every control; it stays so. */
      READING.active = false;
      w.startReading.disabled = PAGE.stopped;
      onRefusal(failure);
      return drawWhatIsHeld(failure);
    }
  );
}

/* After a refused read the page draws what the server holds, asked of the ping (Task 5 review
 * R2-F1, R2-F5): on a chosen folder the read IS the bind, and a refused bind leaves the server
 * holding no folder (U10), so none is shown; a bound folder keeps `Start reading` and `Change`.
 * The ping names only a BOUND folder (W23), so a choice kept by a refusal made before the bind
 * (exiftool not found) is drawn as none. The refusal's sentence stays. A dead server has shown
 * U12's banner, and nothing more is asked of it. */
function drawWhatIsHeld(failure) {
  if (!(failure instanceof ApiRefusal)) {
    return null;
  }
  return api("api/ping").then(function (ping) {
    PAGE.binding = ping.binding;
    if (ping.folder) {
      drawFolder(ping.folder, ping.plan_id);
    } else {
      resetChoice();
    }
    return null;
  }, onRefusal);
}

function poll() {
  if (PAGE.stopped) {
    return null;
  }
  return api("api/job").then(onJob, function (failure) {
    /* A network failure has shown U12's banner (`serverGone`); the poll stops either way. */
    onRefusal(failure);
  });
}

function onJob(job) {
  if (job.state === "running") {
    showRunning(job);
    READING.schedule(poll);
    return null;
  }
  return finish(job);
}

function stopReading() {
  if (READING.stopping || PAGE.stopped) {
    return Promise.resolve(null);
  }
  READING.stopping = true;
  welcomeElements().stop.disabled = true;
  return postJob("api/job/cancel").then(null, onRefusal);
}

/* The job left `running`. `done` asks the ping whether a plan is open now: with one, Welcome's
 * own bound-with-plan state (`drawFolder`) enables `Review names`; with none, the lines stay and
 * `Start reading` is offered again. `failed` with recovery is U12's heading and line. */
function finish(job) {
  var w = welcomeElements();
  READING.stopping = false;
  READING.active = false;
  var healed = job.state === "failed" && job.recovery;
  drawLines(w, healed ? [] : job.lines);
  w.recoveryLine.textContent = healed ? job.recovery : "";
  w.recovery.hidden = !healed;
  if (job.state !== "done") {
    offerReading(w);
    return null;
  }
  return api("api/ping").then(function (ping) {
    PAGE.binding = ping.binding;
    drawFolder(ping.folder, ping.plan_id);
    return null;
  }, onRefusal);
}

/* A reload while a read runs: the progress view comes back instead of `Start reading`.
 * `GET api/job` is served unbound, so it is safe before anything else. */
function resumeReading() {
  return api("api/job").then(function (job) {
    /* A `Start reading` pressed before this answer came owns the poll: a second chain would
     * ask every second twice (Task 5 review R2-F6). */
    if (READING.active) {
      return null;
    }
    if (job.state === "running" && job.kind === "read") {
      return onJob(job);
    }
    return null;
  }, onRefusal);
}

/* --- the seams ------------------------------------------------------------------------- */

var READING_SEAM = {
  READING_COPY: READING_COPY,
  READING: READING,
  thousands: thousands,
  etaPhrase: etaPhrase,
  progressLine: progressLine,
  offerReading: offerReading,
  startReading: startReading,
  stopReading: stopReading,
  resumeReading: resumeReading,
};

if (typeof window !== "undefined") {
  window.PAM.reading = READING_SEAM;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = READING_SEAM;
}
