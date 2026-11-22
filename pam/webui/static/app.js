/* The review page (M7 Tasks 11+12). UX spec 5/5a/7 is the design; this is the wiring.
 *
 * PLAIN `fetch()` + DOM. No framework, no build step, no external resource of any kind
 * (spec 8.2 rev 12): no CDN, no font, no scheme anywhere in this file. A page that
 * fetches a font announces the user's review session to a third party, and pam's
 * network canary watches pam's own sockets, not the browser's.
 *
 * EVERY REQUEST PATH IS RELATIVE. Every route lives under the per-session token prefix,
 * and a relative path makes the browser supply that prefix from the address bar - so the
 * token is never written into this file, and neither is the shape of the prefix, which
 * is spec 8.2's "exactly two places" promise held by construction rather than by care.
 *
 * NOTHING HERE RECOMPUTES A NAME, A TIER OR A SCORE. `tokens` and `trust_reasons` are
 * the planner's own documents, served unedited, and this page renders them in the order
 * they arrive. AS OF M8 THIS PAGE DERIVES NOTHING AT ALL: the trust LABEL used to be
 * computed here from three score bands (>=85 / 60-84 / <60), and both the bands and the
 * derivation are gone (UX spec 5 rev 8 / spec 8.2 rev 14). `trust_label` and `edited`
 * arrive from the server, because a contract expressed in THIS file is one no tool in
 * this project lints, types, executes or can mutate - so it cannot be tested and cannot
 * be caught by the mutation gate. What is left here is presentation only.
 *
 * THE ELEMENT QUERIES ARE DELIBERATELY BORING. `tests/test_webui_static.py` extracts
 * every id this file asks for and checks it against an `html.parser` walk of
 * `index.html` - the JS is the query, the HTML parse is the oracle - and its extractor
 * reads exactly two spellings: `document.getElementById("literal")` and
 * `querySelector`/`querySelectorAll` with a quoted literal selector. A template
 * literal, a computed name or a call split across two lines would be invisible to it,
 * so this file uses neither, and the test fails if one appears rather than quietly
 * checking less. THAT IS ALSO WHY CARDS ARE HELD IN A MAP rather than found with a
 * built selector: `querySelector('[data-item-id="' + id + '"]')` is exactly the
 * spelling the extractor cannot read, so the page keeps its own index instead.
 *
 * TASK 12 ADDS EVERY WRITE THIS PAGE MAKES, and two rules govern all of them:
 *   - THE HEADER'S NUMBERS COME BACK IN THE WRITE'S OWN RESPONSE. All four write
 *     endpoints return fresh `counts`, so nothing here re-fetches the summary and
 *     nothing here re-counts a plan the server has just counted.
 *   - A BULK APPROVE REPORTS WHAT IT ACTUALLY DID. `decide-filter` silently excludes
 *     low-tier items and says how many in `excluded_low`; this page renders that number
 *     whenever it is non-zero, because a bulk approve that quietly did less than it said
 *     is the trust contract failing in the direction nobody watches.
 *
 * M14 TASK 1 SPLIT THIS FILE INTO FIVE CLASSIC SCRIPTS, moved and not rewritten, and this
 * one is now the BOOT ONLY. M14 Task 2 added the list's three (`pam-row.js`, `pam-list.js`,
 * `pam-detail.js`) and Step 6b retired the card page, `pam-review.js`, with its grid, its
 * presets' buttons, its selection panel and its header. `index.html` loads the seven in a
 * fixed order - `pam-core.js` (the copy, the request wrapper, the DOM helpers),
 * `pam-trust.js` (the trust display and provenance), `pam-keys.js` (the keyboard), the
 * list's three, then this file - and they share one global scope, so every rule above
 * still holds across all seven exactly as it did inside one. `tests/js/load_order_test.js`
 * loads them in the page's own order and is the check that the order works.
 */

"use strict";

/* --- start ---------------------------------------------------------------------------- */

/* `api/ping` is the bootstrap and the only reason it still exists: the page is served
 * from a fixed path and has to be TOLD which plan this session is reviewing, because
 * every other endpoint carries the plan id in its own path. */
/* M14 TASK 2: THE LIST IS THE PAGE. `startList` (`pam-list.js`) draws the groups, the rows,
 * the progress line, the tabs and every state; the preview (`Enter`, the thumbnail) and the
 * name editor (`C`) are `pam-detail.js`'s, and `wireDetail` hands the list their hooks -
 * including the name field the keys go inert in. What is left here is the plan's title, the
 * shortcut overlay and the one key listener. */
function start() {
  var shortcutsClose = document.getElementById("shortcuts-close");
  shortcutsClose.addEventListener("click", toggleShortcuts);
  document.addEventListener("keydown", onKeyDown);
  LIST.hooks.shortcuts = toggleShortcuts;
  LIST.hooks.shortcutsOpen = shortcutsOpen;
  wireDetail();
  /* M14 Task 3 (U3): the selection bar, and the hooks its squares, boxes and Space use. */
  wireBar();
  /* M14 Task 6c (W16, W19-W21): the footer's `Rename N files`, Confirm, Done and undo. */
  wireConfirm();
  wireDone();
  /* M14 Task 7 (W22, X2): History, reached from Done. */
  wireHistory();
  LIST.hooks.stopped = function () {
    return PAGE.stopped;
  };
  return api("api/ping")
    .then(function (ping) {
      PAGE.binding = ping.binding;
      /* M14 TASK 4 (W18, U10): a server `pam start` began with no folder answers the ping
       * with no plan, and the page is Welcome (`pam-welcome.js`). Its keys are a browser's
       * own - the list's key listener would take Enter and Space from its buttons - so the
       * listener goes until Welcome hands over to the list. `pam review` always has a plan.
       * M14 Task 5e: a bound server with no plan is Welcome too, on its `folder`. */
      if (ping.plan_id === null) {
        return welcomeFirst(ping);
      }
      /* Task 6 S2-F2: a bound folder whose bind ran recovery opens on Welcome too, so U12's
       * line is read before the list replaces it (`onBound`'s rule for a bind from the page). */
      if (ping.folder && ping.recovery) {
        return welcomeFirst(ping);
      }
      /* Task 6 S1P-F2: a reload during (or after) a page rename or undo shows it again. After
       * the list, so the screen stands in for a drawn list - and after a refused list too,
       * since a plan being renamed may no longer be one the list serves. */
      return openList(ping.plan_id).catch(onListRefusal).then(resumeMoving);
    })
    .catch(onListRefusal);
}

/* Welcome, with the list's key listener off until Welcome hands over to the list. `open` is how
 * it hands over: `openList`, or - from Done's `Start reading` (M14 Y2, `pam-done.js`) - a reload,
 * because this page's list was already drawn for the plan just renamed. */
function welcomeFirst(ping, open) {
  var hand = open || openList;
  document.removeEventListener("keydown", onKeyDown);
  return startWelcome(function (planId) {
    document.addEventListener("keydown", onKeyDown);
    return hand(planId).catch(onListRefusal);
  }, ping);
}

function openList(planId) {
  document.getElementById("plan-title").textContent = "pam review — Plan " + planId;
  /* M15 Task 3 (AM3): the list header's `History`, when the bound folder has a session. */
  refreshHistoryLinks();
  return api("api/plan/" + planId).then(function (summary) {
    return startList(planId, summary);
  });
}

function onListRefusal(failure) {
  if (failure instanceof ApiRefusal) {
    showListNote(failure.message);
  }
}

/* --- the seams ------------------------------------------------------------------------- */

/* `window.PAM` is created by `pam-core.js`; this file loads last. */
if (typeof window !== "undefined") {
  window.PAM.boot = start;
}

/* KEYED ON `module`, NOT ON `document`: a test may stub `document`, so a document check
 * cannot tell a browser from one. In a browser `module` is undefined and the page starts
 * exactly as it always has; under node nothing starts. */
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    boot: start,
  };
} else {
  start();
}
