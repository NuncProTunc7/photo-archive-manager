/* The review list's SELECTION, as data (M14 Task 3; U3, U17). PURE: no DOM, no request -
 * `pam-bar.js` draws it and asks the server what it means.
 *
 * THE STATE IS U17's WIRE FORMAT, FIELD FOR FIELD: `{ids, groups, all, except_ids}`.
 *   - `ids`: rows the user ticked one at a time, taken as named whatever the view (U17).
 *   - `groups`: U25 list-group keys whose box is ticked. A GROUP IS SENT AS ITS KEY, NEVER AS
 *     THE IDS OF THE ROWS THIS PAGE HAPPENS TO HOLD: the page loads a group's first rows and
 *     `Show N more` pages the rest, so enumerating loaded ids would drop every row not loaded
 *     yet. The server resolves the key over the whole plan (`selection.resolve`).
 *   - `all`: `Select all` - the whole view, resolved by the server.
 *   - `except_ids`: a row unticked inside a ticked group (or under `all`). Ticking it again
 *     takes the exception back.
 *
 * WHAT A ROW AND A GROUP ARE HERE: a row is `{id, key}` (its item id and the list group it is
 * drawn in); a group is `{key, ids}`, `ids` being the rows of it this page has LOADED - read
 * only to tell `none` from `some` from `all` and to drop stale exceptions, never sent.
 *
 * `toWire(view)` sends only U17's four view fields; the server refuses anything else (M13 V3).
 */

"use strict";

var SELECTION = { ids: [], groups: [], all: false, except_ids: [] };

/* U17's `view`: the filter the user was looking at, by the names `selection` accepts. */
/* U17's four, plus the date tree's `group` (M14 Task 3's controller ruling, slice 3d). */
var SELECTION_VIEW_FIELDS = ["tier", "status", "attention", "q", "group"];

function selHas(list, value) {
  return list.indexOf(value) !== -1;
}

function selAdd(list, value) {
  if (!selHas(list, value)) {
    list.push(value);
  }
}

function selDrop(list, value) {
  var at = list.indexOf(value);
  if (at !== -1) {
    list.splice(at, 1);
  }
}

function selDropEach(list, values) {
  for (var i = 0; i < values.length; i += 1) {
    selDrop(list, values[i]);
  }
}

/* Whether a list group is selected as a whole: by its own box, or by `Select all`. */
function selCovers(key) {
  return SELECTION.all || selHas(SELECTION.groups, key);
}

/* A row is `all` (ticked) or `none`. Inside a covered group it is ticked unless excepted. */
function selRowState(row) {
  if (selCovers(row.key)) {
    return selHas(SELECTION.except_ids, row.id) ? "none" : "all";
  }
  return selHas(SELECTION.ids, row.id) ? "all" : "none";
}

/* A group is `all` when covered with no loaded row excepted, `some` when covered with an
 * exception or when some of its loaded rows are ticked one at a time, else `none`. Rows ticked
 * one by one never make a group `all`: only its key stands for its unloaded rows. */
function selGroupState(group) {
  var loaded = group.ids || [];
  var list = selCovers(group.key) ? SELECTION.except_ids : SELECTION.ids;
  var marked = false;
  for (var i = 0; i < loaded.length; i += 1) {
    marked = marked || selHas(list, loaded[i]);
  }
  if (selCovers(group.key)) {
    return marked ? "some" : "all";
  }
  return marked ? "some" : "none";
}

function selToggleRow(row) {
  var ticked = selRowState(row) === "all";
  if (selCovers(row.key)) {
    if (ticked) {
      selAdd(SELECTION.except_ids, row.id);
    } else {
      selDrop(SELECTION.except_ids, row.id);
    }
  } else if (ticked) {
    selDrop(SELECTION.ids, row.id);
  } else {
    selAdd(SELECTION.ids, row.id);
  }
}

/* A group's box. Ticking it selects the group BY ITS KEY; unticking it removes the key. Its
 * loaded rows' own ticks and exceptions go either way - the box now speaks for the group.
 *
 * Unticking one group under `Select all`: the rest of the view stays selected, now as every
 * other group's key (`allKeys`, the list's groups) - `all` is the union of the view's groups,
 * because every item is in exactly one list group (U25). */
function selToggleGroup(group, allKeys) {
  var state = selGroupState(group);
  selDropEach(SELECTION.except_ids, group.ids || []);
  selDropEach(SELECTION.ids, group.ids || []);
  if (state === "all") {
    if (SELECTION.all) {
      SELECTION.all = false;
      var keys = allKeys || [];
      for (var i = 0; i < keys.length; i += 1) {
        if (keys[i] !== group.key) {
          selAdd(SELECTION.groups, keys[i]);
        }
      }
    }
    selDrop(SELECTION.groups, group.key);
  } else if (!SELECTION.all) {
    selAdd(SELECTION.groups, group.key);
  }
}

/* `Select all`: on, the whole view (the groups and their exceptions are subsumed; ticked ids
 * stay, being taken as named); off, nothing at all. */
function selToggleAll() {
  if (SELECTION.all) {
    selClear();
    return;
  }
  SELECTION.all = true;
  SELECTION.groups = [];
  SELECTION.except_ids = [];
}

/* Shift+click: the loaded rows between two rows, both ends included, ticked as ids (or, in a
 * covered group, their exceptions taken back). `rows` is the page's loaded order. */
function selSelectRange(rows, fromId, toId) {
  var from = -1;
  var to = -1;
  for (var i = 0; i < rows.length; i += 1) {
    if (rows[i].id === fromId) {
      from = i;
    }
    if (rows[i].id === toId) {
      to = i;
    }
  }
  if (from < 0 || to < 0) {
    from = to;
  }
  if (to < 0) {
    return;
  }
  var low = Math.min(from, to);
  var high = Math.max(from, to);
  for (var at = low; at <= high; at += 1) {
    if (selRowState(rows[at]) !== "all") {
      selToggleRow(rows[at]);
    }
  }
}

function selClear() {
  SELECTION.ids = [];
  SELECTION.groups = [];
  SELECTION.all = false;
  SELECTION.except_ids = [];
}

function selIsEmpty() {
  return !SELECTION.all && SELECTION.groups.length === 0 && SELECTION.ids.length === 0;
}

/* A new view: `groups` and `all` were scoped to the old one, so they go with their
 * exceptions; ticked ids are taken as named whatever the view (U17), so they stay. */
function selViewChanged() {
  SELECTION.groups = [];
  SELECTION.all = false;
  SELECTION.except_ids = [];
}

/* The list's view (`pam-list.js`'s `LIST.view`) as U17's `view`: the `Needs a look` tab is
 * `attention: true`, the Skipped view is `status: "skipped"`. The date tree's `group` IS sent
 * (M14 Task 3's controller ruling: the list under a month shows only that month, so a
 * selection made there covers only that month); the sort is not part of a view. */
function selViewOf(listView) {
  var view = {};
  if (listView.mode === "attention") {
    view.attention = true;
  }
  if (listView.mode === "skipped") {
    view.status = "skipped";
  } else if (listView.status) {
    view.status = listView.status;
  }
  if (listView.tier) {
    view.tier = listView.tier;
  }
  if (listView.q) {
    view.q = listView.q;
  }
  if (listView.group) {
    view.group = listView.group;
  }
  return view;
}

/* U17's `selection`: the view's four fields (empty ones left out), and only the parts the
 * selection holds. Copies, so a later change cannot reach a body already built. */
function selToWire(view) {
  var wire = { view: {} };
  var source = view || {};
  for (var i = 0; i < SELECTION_VIEW_FIELDS.length; i += 1) {
    var name = SELECTION_VIEW_FIELDS[i];
    var value = source[name];
    if (value !== undefined && value !== null && value !== "" && value !== false) {
      wire.view[name] = value;
    }
  }
  if (SELECTION.ids.length > 0) {
    wire.ids = SELECTION.ids.slice();
  }
  if (SELECTION.groups.length > 0) {
    wire.groups = SELECTION.groups.slice();
  }
  if (SELECTION.all) {
    wire.all = true;
  }
  if (SELECTION.except_ids.length > 0 && (SELECTION.all || SELECTION.groups.length > 0)) {
    wire.except_ids = SELECTION.except_ids.slice();
  }
  return wire;
}

/* --- the seams: the page's namespace, and node's -------------------------------------- */

/* No fallback for `window.PAM`, for the reason `pam-trust.js` gives. */
if (typeof window !== "undefined") {
  window.PAM.selection = {
    SELECTION: SELECTION,
    toggleRow: selToggleRow,
    toggleGroup: selToggleGroup,
    toggleAll: selToggleAll,
    selectRange: selSelectRange,
    clear: selClear,
    isEmpty: selIsEmpty,
    viewChanged: selViewChanged,
    viewOf: selViewOf,
    toWire: selToWire,
    rowState: selRowState,
    groupState: selGroupState,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    SELECTION: SELECTION,
    toggleRow: selToggleRow,
    toggleGroup: selToggleGroup,
    toggleAll: selToggleAll,
    selectRange: selSelectRange,
    clear: selClear,
    isEmpty: selIsEmpty,
    viewChanged: selViewChanged,
    viewOf: selViewOf,
    toWire: selToWire,
    rowState: selRowState,
    groupState: selGroupState,
  };
}
