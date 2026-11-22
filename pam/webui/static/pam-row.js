/* One row of the review list (M14 Task 2; U2, W7-W10, U15). PURE: a row is a function of
 * ONE served item and nothing else - no page state, no request, no element map. `rowModel`
 * is the row as data, so `node` executes every rule here (`tests/js/list_test.js`), and
 * `buildRow` only draws what `rowModel` returned.
 *
 * NOTHING HERE RECOMPUTES A NAME, A TIER, A SCORE OR A REASON. `trust_label`, `edited`,
 * `noop`, `note_code` and `look_alikes` arrive from the server (design 8.2), and the row's
 * one-sentence note is chosen by U15's REASON CODE - never by matching a reason's text,
 * because a code chosen from copy changes when the copy does (UX 5a rev 6).
 *
 * The approve circle (UX 5 rev 8's toggle): an EMPTY circle approves - from `proposed` and
 * from `skipped` alike, because approving a skip is a correction with a destination - and a
 * FILLED one sends `reset`, back to not decided. Which action a click sends is decided here,
 * from the item's status alone, so it is one rule the test can execute.
 *
 * Calls `pam-core.js`'s `lookAlikeLine` AT CALL TIME (the shared scope of the review page's
 * classic scripts): the look-alike line is UNCHANGED COPY (U2), so it has one author. (It was
 * `pam-review.js`'s until M14 Task 2 Step 6b retired the card page.)
 */

"use strict";

/* The copy a row carries. W7's decision words - the third, `Not decided`, is `pam-core.js`'s
 * `COPY.notDecided`, the ONE constant for the un-decide word, read at call time; W8's `was`
 * line; W10's notes, one per U15 code. `utc_video` and `truncated` have no W10 sentence, so they REUSE shipped wording
 * (the controller's ruling for Task 2): the Why? panel's video sentence and the token
 * chips' "shortened to fit". Both are flagged for HT-M14's wording read. */
var ROW_COPY = {
  /* HT-M14 F4: the thumbnail opens the preview (U16), so it is a control with a name. */
  preview: "Show a larger preview of ",
  approved: "Approved",
  skipped: "Skipped, keeps its name",
  was: "was ",
  approve: "Approve",
  /* W9: the companion kind the SERVER read off the catalog (`unit_kind`, the scanner's group
   * kind). A `sidecar` group, or a unit with no kind, discloses its count alone. */
  kinds: { live_photo: "Live Photo", raw_jpeg: "RAW + JPEG" },
  kindJoin: " · ",
  files: " files",
  /* UX 5 rev 8's `↺`, on an edited row only (M14 Task 2 Step 6b: moved from the card with its
   * copy unchanged - the symbol, its title and its accessible name). */
  restore: "↺",
  restoreTitle: "Put pam's own name back",
  restoreLabel: "Restore pam's name for ",
  /* M14 Task 3 (U3, W15): the square's accessible name - "tap a square to pick names". */
  pick: "Pick ",
  notes: {
    file_mtime: "The date came from when the file was last changed, not from the camera. Check it.",
    ocr: "The date was read from the date printed on the photo. Check it matches.",
    none: "No date found anywhere. You can type one in.",
    utc_video: "Video time is UTC, local date may differ.",
    truncated: "Part of this name was shortened to fit.",
  },
};

/* What each action the circle sends leaves behind, spelled from design 8.2's `decide`. */
var ROW_AFTER = { approve: "approved", skip: "skipped", reset: "proposed" };

/* UX 5 rev 8's toggle: a filled circle un-decides, an empty one approves. */
function circleAction(item) {
  return item.status === "approved" ? "reset" : "approve";
}

function decisionWord(item) {
  if (item.status === "approved") {
    return ROW_COPY.approved;
  }
  if (item.status === "skipped") {
    return ROW_COPY.skipped;
  }
  return COPY.notDecided;
}

/* W10: one sentence on the row WHILE UNDECIDED, from the server's code. An unknown or
 * missing code says nothing rather than guess. */
function rowNote(item) {
  if (item.status !== "proposed" || !item.note_code) {
    return null;
  }
  return Object.prototype.hasOwnProperty.call(ROW_COPY.notes, item.note_code)
    ? ROW_COPY.notes[item.note_code]
    : null;
}

/* UX 5 rev 11's no-op sentence, on the card's own predicate: the server's `noop`, AND a
 * group to belong to - the sentence is about a group (M10 Task 1, C1). */
function noopLine(item) {
  return item.noop && item.unit_size > 1 ? COPY.noopCard : null;
}

/* W9: `Live Photo · 2 files`, `RAW + JPEG · 2 files`, else `N files`; nothing for a lone
 * file. The kind is the server's `unit_kind` - NEVER guessed from the files' extensions -
 * and the count is `unit_size`, counted over the whole plan. */
function unitLine(item) {
  if (!(item.unit_size > 1)) {
    return null;
  }
  var files = item.unit_size + ROW_COPY.files;
  var kind = Object.prototype.hasOwnProperty.call(ROW_COPY.kinds, item.unit_kind)
    ? ROW_COPY.kinds[item.unit_kind]
    : null;
  return kind === null ? files : kind + ROW_COPY.kindJoin + files;
}

/* The row as data. Every string the row shows is here. */
function rowModel(item) {
  return {
    id: item.id,
    name: item.new_filename,
    was: ROW_COPY.was + item.old_filename,
    /* UX 5 rev 8: an edited name carries no trust display - the label says whose it is. */
    label: item.edited ? COPY.editedByYou : item.trust_label,
    tier: item.edited ? "" : item.tier,
    decision: decisionWord(item),
    unit: unitLine(item),
    circle: item.status === "approved" ? "filled" : "empty",
    action: circleAction(item),
    noop: noopLine(item),
    alike: lookAlikeLine(item),
    note: rowNote(item),
    video: item.media_type === "video",
    /* THE SERVER'S PREDICATE, NOT A SECOND COPY OF IT (spec 8.2 rev 14): the same
     * `item.edited` the label reads, never derived from the two filenames. */
    restore: item.edited === true,
  };
}

/* UX 5: thumbnails get `alt` = old filename; a video, or a file with no embedded preview,
 * gets the glyph and its extension. The thumb route's 404 IS the glyph signal (design 8.2),
 * so a failed image is replaced by the glyph rather than reported. */
/* HT-M14 F4: a thumbnail that opens the preview is reachable and named like a button - Tab
 * reaches it, Enter or Space opens it, and a screen reader says what it does. Applied to the
 * glyph too, and to the glyph that replaces a failed image, which used to lose the click. */
function previewable(cell, item, onThumb) {
  if (typeof onThumb !== "function") {
    return cell;
  }
  cell.setAttribute("role", "button");
  cell.setAttribute("tabindex", "0");
  cell.setAttribute("aria-label", ROW_COPY.preview + item.old_filename);
  cell.addEventListener("click", function () {
    onThumb(item);
  });
  cell.addEventListener("keydown", function (event) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onThumb(item);
    }
  });
  return cell;
}

function rowGlyph(item, onThumb) {
  var cell = make("span", "row-thumb");
  var glyph = make("span", "glyph");
  glyph.setAttribute("data-kind", item.media_type === "video" ? "video" : "image");
  cell.appendChild(glyph);
  cell.appendChild(make("span", "ext-badge", String(item.ext || "").toUpperCase()));
  return previewable(cell, item, onThumb);
}

function rowThumb(item, onThumb) {
  if (item.media_type === "video" || !item.has_thumb) {
    return rowGlyph(item, onThumb);
  }
  var cell = make("span", "row-thumb");
  var image = document.createElement("img");
  /* RELATIVE, so the browser supplies the token prefix from the address bar. */
  image.src = "api/thumb/" + encodeURIComponent(item.file_id) + "?variant=grid";
  image.alt = item.old_filename;
  image.loading = "lazy";
  if (typeof item.orientation === "number" && item.orientation >= 1 && item.orientation <= 8) {
    image.setAttribute("data-orientation", String(item.orientation));
  }
  image.addEventListener("error", function () {
    if (cell.parentNode) {
      cell.parentNode.replaceChild(rowGlyph(item, onThumb), cell);
    }
  });
  cell.appendChild(image);
  return previewable(cell, item, onThumb);
}

/* One row's nodes. `onCircle(item)`, `onThumb(item)` and `onRestore(item)` are the page's; the
 * row decides nothing itself. `onThumb` (Step 6a, U16: the thumbnail opens the preview) and
 * `onRestore` (Step 6b: the card's `↺`, now the row's) are optional.
 *
 * `square` (M14 Task 3, U3) is optional too: `{state, onToggle(event)}`, the selection's
 * `none`/`all` for this row and the page's handler. The row draws it and holds no selection. */
function buildRow(item, onCircle, onThumb, onRestore, square) {
  var model = rowModel(item);
  var row = make("div", "row");
  row.setAttribute("data-item-id", String(model.id));
  row.setAttribute("data-status", item.status);

  if (square) {
    var box = make("button", "row-square");
    box.type = "button";
    box.setAttribute("data-state", square.state);
    box.setAttribute("aria-pressed", square.state === "all" ? "true" : "false");
    box.setAttribute("aria-label", ROW_COPY.pick + item.old_filename);
    box.addEventListener("click", function (event) {
      square.onToggle(event);
    });
    row.appendChild(box);
  }

  var circle = make("button", "row-circle");
  circle.type = "button";
  circle.setAttribute("data-state", model.circle);
  circle.setAttribute("data-action", model.action);
  circle.setAttribute("aria-pressed", model.circle === "filled" ? "true" : "false");
  circle.setAttribute(
    "aria-label",
    (model.action === "reset" ? COPY.notDecided : ROW_COPY.approve) + " — " + item.old_filename
  );
  circle.addEventListener("click", function () {
    onCircle(item);
  });
  row.appendChild(circle);
  row.appendChild(rowThumb(item, onThumb));

  var body = make("div", "row-body");
  body.appendChild(make("div", "row-name", model.name));
  /* Beside the name it would put back, never inside it: the name's text stays the name. */
  if (model.restore && typeof onRestore === "function") {
    var restore = make("button", "restore", ROW_COPY.restore);
    restore.type = "button";
    restore.title = ROW_COPY.restoreTitle;
    restore.setAttribute("aria-label", ROW_COPY.restoreLabel + item.old_filename);
    restore.addEventListener("click", function () {
      onRestore(item);
    });
    body.appendChild(restore);
  }
  body.appendChild(make("div", "row-was", model.was));
  var meta = make("div", "row-meta");
  var label = make("span", "row-label", model.label);
  label.setAttribute("data-tier", model.tier);
  meta.appendChild(label);
  if (model.unit !== null) {
    meta.appendChild(make("span", "row-unit", model.unit));
  }
  meta.appendChild(make("span", "row-decision", model.decision));
  body.appendChild(meta);
  var lines = [
    ["row-noop", model.noop],
    ["row-alike", model.alike],
    ["row-note", model.note],
  ];
  for (var i = 0; i < lines.length; i += 1) {
    if (lines[i][1] !== null) {
      body.appendChild(make("p", lines[i][0], lines[i][1]));
    }
  }
  row.appendChild(body);
  return row;
}

/* --- the seams: the page's namespace, and node's -------------------------------------- */

/* No fallback for `window.PAM`, for the reason `pam-trust.js` gives. */
if (typeof window !== "undefined") {
  window.PAM.row = {
    ROW_COPY: ROW_COPY,
    ROW_AFTER: ROW_AFTER,
    circleAction: circleAction,
    decisionWord: decisionWord,
    unitLine: unitLine,
    rowNote: rowNote,
    rowModel: rowModel,
    buildRow: buildRow,
  };
}

/* NOTHING HERE READS `document` AT LOAD, so node can require this file; `tests/js/list_test.js`
 * puts `pam-core.js`'s names on `global` first - the shared scope. */
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    ROW_COPY: ROW_COPY,
    ROW_AFTER: ROW_AFTER,
    circleAction: circleAction,
    decisionWord: decisionWord,
    unitLine: unitLine,
    rowNote: rowNote,
    rowModel: rowModel,
    buildRow: buildRow,
  };
}
