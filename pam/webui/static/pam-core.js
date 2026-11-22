/* The review page's core (M14 Task 1): the copy, the one request wrapper, the
 * server-gone detector and the two DOM helpers every other review script calls.
 *
 * THE REVIEW PAGE IS SEVEN CLASSIC SCRIPTS, LOADED IN A FIXED ORDER by `index.html`:
 * `pam-core.js`, `pam-trust.js`, `pam-keys.js`, `pam-row.js`, `pam-list.js`,
 * `pam-detail.js`, then `app.js` (boot only; M14 Task 1 split five, Task 2 added the list's
 * three and retired the card page, `pam-review.js`). They are not modules: a browser runs every classic script in ONE global scope,
 * so a function declared here is callable from every later file exactly as it was when
 * all five were one `app.js`. The split moved code and rewrote none of it (M14 Task 1;
 * M7's "revisit `app.js` at ~2000 lines", paid at 2482).
 *
 * THIS FILE CREATES `window.PAM`, and every later file attaches its own part of it
 * without a fallback. That is deliberate: it makes the load ORDER something a run can
 * see. A script loaded before this one fails at its first line of namespace code, and
 * `tests/js/load_order_test.js` loads the page's scripts in `index.html`'s order to
 * watch for exactly that.
 *
 * NO EXTERNAL RESOURCE OF ANY KIND (spec 8.2 rev 12), and every request path is
 * relative - `app.js`'s header says why, for the whole page.
 *
 * NUMBER FORMATTING: the task that split this page names it as core's, and there is none
 * to move - the page writes every count with `+` concatenation where it uses it.
 */

"use strict";

/* --- the copy UX spec 5/5a makes normative ---------------------------------------- */

var COPY = {
  /* U12 (M14 Task 5e; UX 5's fourth state), verbatim: what the page says when pam stops. */
  serverGone: {
    stopped: "pam has stopped.",
    lost: "Nothing is lost: every rename is written down before it happens.",
    restart: "Start pam again and it will put things right before it does anything else.",
  },
  loadFailed: "pam could not finish that. Try again, or check the console pam is running in.",
  /* UX spec 5 rev 8: what a card says in place of a trust display it may not make. */
  editedByYou: "Edited by you",
  proposedLead: "pam proposed ",
  restoreHint: "Press ↺ to put pam's name back.",
  /* UX spec 5 rev 8 / A4: the word the un-decide control carries, in the grid, in the
   * overlay and in the selection panel. ONE constant, because three spellings of the
   * same control is three chances for the page to describe a set the Status filter
   * offers under a name that filter does not use. */
  notDecided: "Not decided",
  /* UX spec 5 rev 11 - THE NO-OP CARD. A member of a companion unit whose proposed name
   * equals its current name is on the page because design spec 7.3 rev 16 makes a plan's
   * unit the set of files `pam scan` grouped, not the subset the plan holds items for.
   * NORMATIVE COPY, verbatim. The predicate is `item.noop`, served by spec 8.2 - not
   * derived here from the two filenames, for the reason `ReviewItem.noop` sets out. */
  noopCard: "This file already has the right name — it is here because pam renames a group together.",
};

/* --- talking to the API ------------------------------------------------------------ */

function ApiRefusal(message, status, payload) {
  this.message = message;
  this.status = status;
  /* M14 Task 3: the refusal's whole body, or null. A 409 from `decide-selection` carries the
   * fresh preview beside `error` (U17), and the bar reads it from here. */
  this.payload = payload === undefined ? null : payload;
}

/* One request. A NETWORK failure and a REFUSAL are different things and the page treats
 * them differently: `fetch` rejecting means the server is gone, which makes the page
 * inert; a 4xx/5xx carries `{error}`, which is a sentence to show beside the control
 * that caused it. Conflating them would make one bad filter look like a dead server. */
function api(path, options) {
  return fetch(path, withBinding(options)).then(
    function (response) {
      return response.text().then(function (text) {
        var payload = null;
        if (text) {
          try {
            payload = JSON.parse(text);
          } catch (badJson) {
            payload = null;
          }
        }
        if (!response.ok) {
          var message = payload && payload.error ? payload.error : COPY.loadFailed;
          throw new ApiRefusal(message, response.status, payload);
        }
        return payload;
      });
    },
    function (networkError) {
      serverGone();
      throw networkError;
    }
  );
}

/* M14 Task 4 review R1-F1: every request names the binding this page was loaded on (`ping` or
 * `bind` gave it), so a page left open on a folder pam has since left is refused rather than
 * writing into the new folder, whose plan and item ids collide with the old one's. */
function withBinding(options) {
  if (PAGE.binding === null || PAGE.binding === undefined) {
    return options;
  }
  var copy = {};
  var key;
  for (key in options || {}) {
    copy[key] = options[key];
  }
  var headers = {};
  for (key in copy.headers || {}) {
    headers[key] = copy.headers[key];
  }
  headers["X-Pam-Binding"] = String(PAGE.binding);
  copy.headers = headers;
  return copy;
}

/* --- the page's stop flag ------------------------------------------------------------ */

/* Spec 8.2's `?sort=` vocabulary. UX spec 5a: lowest trust first in every view, with a
 * toggle to date order for chronological review. M14 Task 2 Step 6b part 2: moved here from
 * `pam-review.js` (retired at Step 6b), whose state and the list's sort toggle both read it. */
var SORT_TRUST = "trust_asc";
var SORT_DATE = "date";

/* Set once, by `serverGone`, and read by every write path and the keyboard: the page goes
 * inert and stays inert (UX 5's fourth state). M14 Task 2 Step 6b: moved here from the card
 * page's `state`, whose other fields retired with the card grid. M14 Task 6c: `confirming` is
 * set while the Confirm screen (`pam-confirm.js`) stands in for the list, whose keys go quiet. */
var PAGE = { stopped: false, binding: null, confirming: false };

/* UX spec 5's fourth state, in U12's words (M14 Task 5e - a poll that fails shows it too).
 * THE PAGE GOES INERT AND DOES NOT RETRY: a retry loop
 * against a server the user has just stopped with Ctrl+C spins the browser and hides
 * the one thing the banner is there to say. Decisions already made are already on
 * disk - they were server-side writes the moment they happened - so nothing is lost. */
function serverGone() {
  if (PAGE.stopped) {
    return;
  }
  PAGE.stopped = true;
  document.getElementById("error-banner-stopped").textContent = COPY.serverGone.stopped;
  document.getElementById("error-banner-lost").textContent = COPY.serverGone.lost;
  document.getElementById("error-banner-restart").textContent = COPY.serverGone.restart;
  /* The list's own reference to the banner (`pam-list.js`, Step 6b part 2). */
  listElements().errorBanner.hidden = false;
  var controls = document.querySelectorAll("button, select, input");
  for (var i = 0; i < controls.length; i += 1) {
    controls[i].disabled = true;
  }
}

/* `api.py` puts the parameter name in front of every 400 so the page knows which control
 * is wrong. That is instruction to the PAGE, not copy for the user - `new_filename` is a
 * column name and UX spec 8 keeps column names out of what a person reads - so the page
 * uses the prefix to place the message and then takes it off. (M14 Task 2 Step 6a: moved
 * here from `pam-review.js`, retired at Step 6b; `pam-detail.js`'s editor uses it.) */
function fieldMessage(message, parameter) {
  var prefix = parameter + ": ";
  return message.indexOf(prefix) === 0 ? message.slice(prefix.length) : message;
}

/* --- the decision's words, and the look-alike line (moved from the card page) --------- */

/* M14 Task 2 Step 6b: moved unchanged from `pam-review.js` when the card page retired.
 * `pam-detail.js`'s preview and `pam-row.js`'s row call them at call time. */

/* Spec 8.2's three decide actions, and the status each one leaves behind. The page maps
 * them because the decide response carries fresh COUNTS but not fresh rows - and the map
 * is sound rather than optimistic: a decide is all-or-nothing across its ids, so a
 * response that was not a refusal means every id it named now holds this status. */
var STATUS_AFTER = { approve: "approved", skip: "skipped", reset: "proposed" };

function statusNote(item) {
  if (item.status === "approved") {
    return "Approved";
  }
  if (item.status === "skipped") {
    return "Skipped";
  }
  return "Not decided yet";
}

/* M13 amendment H3 (UX 5, design 8.2): the card's look-alike line, or null for none.
 *
 * The paths and the remainder are the SERVER'S (`look_alikes`, at most five, and
 * `look_alikes_more`); this only says them. The count is every other look-alike, listed
 * or not, and "photo" is singular only for exactly one. No link, no filter and no action
 * (H3, vision A10): a sentence, rendered as text, and nothing else. */
function lookAlikeLine(item) {
  var shown = item.look_alikes || [];
  if (shown.length === 0) {
    return null;
  }
  var more = item.look_alikes_more || 0;
  var count = shown.length + more;
  var parts = shown.slice();
  if (more > 0) {
    parts.push("and " + more + " more");
  }
  return (
    "Looks like " + count + (count === 1 ? " other photo" : " other photos") +
    " in this plan: " + parts.join(", ") + "."
  );
}

/* Spec 8.2's `decide` leaves one of three statuses behind, and exactly two of them are
 * a DECISION. Read off `STATUS_AFTER` rather than spelled again, so a card can never
 * offer to un-decide a status no action produced. */
function isDecided(item) {
  return item.status === STATUS_AFTER.approve || item.status === STATUS_AFTER.skip;
}

/* --- small DOM helpers -------------------------------------------------------------- */

/* `textContent` everywhere, never `innerHTML`: filenames are archive data and a page
 * that pastes archive data into markup is a page whose behaviour depends on what a
 * camera happened to write. */
function make(tag, className, text) {
  var node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined && text !== null) {
    node.textContent = text;
  }
  return node;
}

function clear(node) {
  while (node.firstChild) {
    node.removeChild(node.firstChild);
  }
}

/* --- the seams: the page's namespace, and node's -------------------------------------- */

/* Created HERE and nowhere else; see the header. */
if (typeof window !== "undefined") {
  window.PAM = {};
  window.PAM.core = {
    COPY: COPY,
    ApiRefusal: ApiRefusal,
    api: api,
    serverGone: serverGone,
    fieldMessage: fieldMessage,
    make: make,
    clear: clear,
    PAGE: PAGE,
    SORT_TRUST: SORT_TRUST,
    SORT_DATE: SORT_DATE,
    STATUS_AFTER: STATUS_AFTER,
    statusNote: statusNote,
    lookAlikeLine: lookAlikeLine,
    isDecided: isDecided,
  };
}

/* NODE GIVES EACH `require` ITS OWN SCOPE, where the browser gives these scripts one. The
 * node drivers (`tests/js/app_logic_test.js`, `app_provenance_test.js`) put these names
 * on `global` before requiring a later script, which is the shared scope reproduced:
 * `pam-trust.js` reads `COPY` at CALL time, as it always did inside one file. Nothing
 * here runs at load, so requiring this file needs no document. */
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    COPY: COPY,
    ApiRefusal: ApiRefusal,
    api: api,
    serverGone: serverGone,
    fieldMessage: fieldMessage,
    make: make,
    clear: clear,
    PAGE: PAGE,
    SORT_TRUST: SORT_TRUST,
    SORT_DATE: SORT_DATE,
    STATUS_AFTER: STATUS_AFTER,
    statusNote: statusNote,
    lookAlikeLine: lookAlikeLine,
    isDecided: isDecided,
  };
}
