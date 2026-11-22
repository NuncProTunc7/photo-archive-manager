/* The selection bar (M14 Task 3; U3, U8 W13/W14, U17): `N selected [Clear] [Skip] [Approve]`,
 * and what drives the selection - a row's square, a group's box, `Select all` and `Space`.
 * The selection itself is `pam-selection.js`'s, as data; this file draws it and asks the
 * server what it means.
 *
 * FOUR RULES, each executed by `tests/js/selection_test.js`:
 *   - EVERY NUMBER IS THE SERVER'S. N is the preview's `items` - the selection RESOLVED AND
 *     WIDENED to whole units by `POST /plan/{id}/selection` - and the low line is the
 *     preview's `low`. Neither is counted from the rows this page holds.
 *   - THE COUNT SHOWN IS THE COUNT SENT. The preview is asked for after every change
 *     (debounced); while one is in flight `Approve` is disabled, and `Approve` sends the
 *     selection that preview was for with `expected_low` = the `low` it showed
 *     (`include_low: true` only when that is above 0). The server refuses any other count.
 *   - AFTER APPROVE, U3's SENTENCE IS BUILT FROM THE RESPONSE's `low_included`, never from the
 *     preview; a 409 that carries a fresh preview says W14's "selection changed" sentence and
 *     asks for the preview again.
 *   - EVERY BULK WRITE IS `decide-selection`. The page never enumerates ids to `decide` or
 *     `decide-filter` for a bulk action.
 *
 * Calls `pam-list.js`'s page state and requests AT CALL TIME (the shared scope of the review
 * page's classic scripts); `wireBar` hands the list its hooks. NOTHING READS `document` AT LOAD.
 */

"use strict";

/* U3's two sentences (W14), spelled with their singulars; W13's `selected`; and the 409's
 * sentence (the controller's Task 3 ruling). `approvedPlain`/`skipped` are the bar's own
 * report when no low name was approved - U3 gives a sentence only for the case that has one. */
var BAR_COPY = {
  selected: " selected",
  lowOne: "Includes 1 name that needs review. Approve will approve it too.",
  lowMany: " names that need review. Approve will approve them too.",
  includes: "Includes ",
  approved: "Approved ",
  includingOne: ", including 1 name that needs review. Every rename can be undone later.",
  including: ", including ",
  includingMany: " names that need review. Every rename can be undone later.",
  skipped: "Skipped ",
  changed: "The selection changed. Check the count and try again.",
};

var CONFLICT = 409;

var BAR = {
  /* The last preview the server answered, and the wire selection it was for. */
  preview: null,
  wire: null,
  /* True from a selection change until its preview is answered: `Approve` is disabled. */
  pending: false,
  /* Task 3 review R2 F4: true while a `decide-selection` write and its reload are in flight;
   * a second Approve or Skip in that time sends nothing. */
  writing: false,
  timer: null,
  delay: 250,
  generation: 0,
  previewDone: Promise.resolve(),
  /* The last row a square ticked, for Shift+click's range. */
  anchor: null,
};

/* --- the copy, from the server's numbers --------------------------------------------- */

function barLowText(low) {
  if (!(low > 0)) {
    return null;
  }
  return low === 1 ? BAR_COPY.lowOne : BAR_COPY.includes + low + BAR_COPY.lowMany;
}

/* U3's confirmation, from the RESPONSE: `decided` is M, `low_included` is N. Rule 7's
 * companion `note` follows when the server sent one. */
function barApprovedText(result) {
  var low = result.low_included;
  var said = BAR_COPY.approved + result.decided;
  if (low === 1) {
    said += BAR_COPY.includingOne;
  } else if (low > 1) {
    said += BAR_COPY.including + low + BAR_COPY.includingMany;
  } else {
    said += ".";
  }
  return result.note ? said + " " + result.note : said;
}

function barSkippedText(result) {
  var said = BAR_COPY.skipped + result.decided + ".";
  return result.note ? said + " " + result.note : said;
}

/* The approve body: the previewed selection and the low count the preview showed. */
function barApproveBody(wire, preview) {
  var body = { selection: wire, action: "approve", expected_low: preview.low };
  if (preview.low > 0) {
    body.include_low = true;
  }
  return body;
}

/* --- the page's elements, on first use ------------------------------------------------- */

var barEl = null;

function barElements() {
  if (barEl === null) {
    barEl = {
      bar: document.getElementById("selection-bar"),
      count: document.getElementById("bar-count"),
      low: document.getElementById("bar-low"),
      note: document.getElementById("bar-note"),
      clear: document.getElementById("bar-clear"),
      skip: document.getElementById("bar-skip"),
      approve: document.getElementById("bar-approve"),
      selectAll: document.getElementById("select-all"),
    };
  }
  return barEl;
}

function barNote(message) {
  var note = barElements().note;
  note.textContent = message;
  note.hidden = !message;
}

function barRender() {
  var el = barElements();
  var preview = BAR.preview;
  el.bar.hidden = selIsEmpty();
  el.count.textContent = preview ? preview.items + BAR_COPY.selected : "";
  var low = preview ? barLowText(preview.low) : null;
  el.low.textContent = low || "";
  el.low.hidden = low === null;
  el.approve.disabled = BAR.pending || BAR.writing || preview === null || PAGE.stopped;
  el.selectAll.setAttribute("aria-pressed", SELECTION.all ? "true" : "false");
}

/* Every square and box the list holds, redrawn from the model. */
function barPaint() {
  for (var id in LIST.rows) {
    if (Object.prototype.hasOwnProperty.call(LIST.rows, id)) {
      repaintRow(LIST.rows[id].item.id);
    }
  }
  for (var g = 0; g < LIST.groups.length; g += 1) {
    var entry = LIST.groups[g];
    if (entry.box) {
      var state = selGroupState(barGroup(entry));
      entry.box.setAttribute("data-state", state);
      entry.box.setAttribute("aria-pressed", state === "all" ? "true" : state === "some" ? "mixed" : "false");
    }
  }
}

function barGroup(entry) {
  return {
    key: entry.group.key,
    ids: entry.items.map(function (item) {
      return item.id;
    }),
  };
}

/* --- the preview ------------------------------------------------------------------------ */

/* After every change: redraw, and - unless the selection is empty - ask the server, once the
 * user has stopped for `BAR.delay`. `Approve` is disabled from this moment. */
function barSelectionChanged() {
  barPaint();
  BAR.generation += 1;
  if (BAR.timer !== null) {
    clearTimeout(BAR.timer);
    BAR.timer = null;
  }
  if (selIsEmpty()) {
    BAR.preview = null;
    BAR.wire = null;
    BAR.pending = false;
    barRender();
    BAR.previewDone = Promise.resolve();
    return BAR.previewDone;
  }
  BAR.pending = true;
  barRender();
  var generation = BAR.generation;
  BAR.previewDone = new Promise(function (resolve) {
    BAR.timer = setTimeout(function () {
      BAR.timer = null;
      resolve(barFetchPreview(generation));
    }, BAR.delay);
  });
  return BAR.previewDone;
}

function barFetchPreview(generation) {
  var wire = selToWire(selViewOf(LIST.view));
  return api("api/plan/" + LIST.planId + "/selection", listPost({ selection: wire }))
    .then(function (preview) {
      if (generation !== BAR.generation) {
        return;
      }
      BAR.preview = preview;
      BAR.wire = wire;
      BAR.pending = false;
      barRender();
    })
    .catch(function (failure) {
      if (generation !== BAR.generation || !(failure instanceof ApiRefusal)) {
        return;
      }
      BAR.preview = null;
      BAR.pending = false;
      barRender();
      barNote(failure.message);
    });
}

/* --- the three buttons --------------------------------------------------------------------- */

function barClear() {
  selClear();
  BAR.anchor = null;
  barNote("");
  return barSelectionChanged();
}

/* One bulk write through `decide-selection`; then the selection is cleared and the list
 * refreshes from the server, as `Accept all` does. Resolves with the response, or null. */
function barDecide(body) {
  if (BAR.writing) {
    return Promise.resolve(null);
  }
  BAR.writing = true;
  showListNote("");
  barNote("");
  return api("api/plan/" + LIST.planId + "/decide-selection", listPost(body))
    .then(function (result) {
      if (result.counts) {
        LIST.counts = result.counts;
      }
      selClear();
      BAR.anchor = null;
      barSelectionChanged();
      renderProgress();
      refreshAcceptAllCount();
      return Promise.all([loadGroups(), loadAttentionTodo()]).then(function () {
        return result;
      });
    })
    .catch(function (failure) {
      if (!(failure instanceof ApiRefusal)) {
        return null;
      }
      if (failure.status === CONFLICT && failure.payload && failure.payload.selection) {
        barNote(BAR_COPY.changed);
        barSelectionChanged();
      } else {
        barNote(failure.message);
      }
      return null;
    })
    .then(function (result) {
      BAR.writing = false;
      barRender();
      return result;
    });
}

function barApprove() {
  if (PAGE.stopped || BAR.pending || BAR.preview === null) {
    return Promise.resolve(null);
  }
  return barDecide(barApproveBody(BAR.wire, BAR.preview)).then(function (result) {
    if (result) {
      showListNote(barApprovedText(result));
    }
    return result;
  });
}

/* Skip needs no low acknowledgement (U17), and sends none - the server refuses one. */
function barSkip() {
  if (PAGE.stopped || selIsEmpty()) {
    return Promise.resolve(null);
  }
  var body = { selection: selToWire(selViewOf(LIST.view)), action: "skip" };
  return barDecide(body).then(function (result) {
    if (result) {
      showListNote(barSkippedText(result));
    }
    return result;
  });
}

/* --- what drives the model -------------------------------------------------------------- */

/* A row's square (or `Space` on the focused row). Shift+click ticks the loaded rows from the
 * last ticked row to this one. */
function barToggleRow(item, key, shift) {
  if (PAGE.stopped) {
    return Promise.resolve();
  }
  if (shift && BAR.anchor !== null) {
    var rows = listOrder().map(function (each) {
      return { id: each.id, key: LIST.rows[each.id] ? LIST.rows[each.id].key : null };
    });
    selSelectRange(rows, BAR.anchor, item.id);
  } else {
    selToggleRow({ id: item.id, key: key });
  }
  BAR.anchor = item.id;
  barNote("");
  return barSelectionChanged();
}

function barToggleGroup(key) {
  var entry = LIST.byKey[key];
  if (PAGE.stopped || !entry) {
    return Promise.resolve();
  }
  var keys = LIST.groups.map(function (each) {
    return each.group.key;
  });
  selToggleGroup(barGroup(entry), keys);
  barNote("");
  return barSelectionChanged();
}

function barToggleAll() {
  if (PAGE.stopped) {
    return Promise.resolve();
  }
  selToggleAll();
  barNote("");
  return barSelectionChanged();
}

/* The hooks the list calls, and the bar's own buttons. `app.js` calls this once. */
function wireBar() {
  var el = barElements();
  LIST.hooks.rowState = function (row) {
    return selRowState(row);
  };
  LIST.hooks.toggleRow = function (pick) {
    return barToggleRow(pick.item, pick.key, pick.shift);
  };
  LIST.hooks.toggleGroup = barToggleGroup;
  LIST.hooks.select = function (item) {
    var held = LIST.rows[item.id];
    return barToggleRow(item, held ? held.key : null, false);
  };
  LIST.hooks.viewChanged = function () {
    selViewChanged();
    BAR.anchor = null;
    return barSelectionChanged();
  };
  el.clear.addEventListener("click", barClear);
  el.skip.addEventListener("click", barSkip);
  el.approve.addEventListener("click", barApprove);
  el.selectAll.addEventListener("click", barToggleAll);
  barRender();
}

/* --- the seams: the page's namespace, and node's -------------------------------------- */

if (typeof window !== "undefined") {
  window.PAM.bar = {
    BAR: BAR,
    BAR_COPY: BAR_COPY,
    barLowText: barLowText,
    barApprovedText: barApprovedText,
    barApproveBody: barApproveBody,
    barSelectionChanged: barSelectionChanged,
    barApprove: barApprove,
    barSkip: barSkip,
    barClear: barClear,
    barToggleRow: barToggleRow,
    barToggleGroup: barToggleGroup,
    barToggleAll: barToggleAll,
    wireBar: wireBar,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    BAR: BAR,
    BAR_COPY: BAR_COPY,
    barLowText: barLowText,
    barApprovedText: barApprovedText,
    barApproveBody: barApproveBody,
    barSelectionChanged: barSelectionChanged,
    barApprove: barApprove,
    barSkip: barSkip,
    barClear: barClear,
    barToggleRow: barToggleRow,
    barToggleGroup: barToggleGroup,
    barToggleAll: barToggleAll,
    wireBar: wireBar,
  };
}
