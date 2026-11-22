/* The review list's detail surfaces (M14 Task 2 Step 6a; U2, U16, A6): the preview overlay
 * and the one name editor, MOVED out of `pam-review.js` - where they worked on the card
 * grid's index - and now working on THE LIST'S ROWS. Their behaviour, their endpoints and
 * their copy are the card page's, unchanged; what changed is where an item comes from (the
 * list's `LIST.rows` and its keyboard row, `listCurrent()`) and where a write repaints (the
 * list's `repaintRow` and its progress line, from the response's own `counts`).
 *
 * THE PREVIEW (U16) shows the list's keyboard row. It is opened by `Enter` or by the row's
 * thumbnail; `←/→` and its Previous/Next buttons move the list's row (`moveListCursor`) and
 * it follows; its Approve and Skip decide through the list's `decideAction` - the existing
 * `decide` - and move on to the next undecided row, exactly as they did in the grid.
 *
 * THE EDITOR (A6) is ONE element, moved into whichever row - or into the preview - is being
 * edited, so "keys are inert while the name field has focus" has one field to be true about
 * (HT-M7's check; `pam-list.js`'s `listKeyAction` holds it, against `LIST.hooks.editorField`).
 * A rename goes through the existing `POST item/{id}/rename`, and the server renames the
 * companion unit's stem whole (design 7.3); the members it moved come back in `members`.
 *
 * NOTHING READS `document` AT LOAD (the element map is built on first use), so node can
 * require this file. `app.js` calls `wireDetail()`, which also hands the list its hooks.
 * Loaded after `pam-list.js`, whose names it calls AT CALL TIME (the shared scope of the
 * review page's classic scripts) - and `pam-core.js`'s `statusNote`/`isDecided`, which
 * Step 6b moved there from the card page.
 */

"use strict";

/* Spec 8.2's paging bound. Used only to pull one companion unit's members back (see
 * `loadUnitMembers`), never to render a page that size. */
var MAX_LIMIT = 200;

var MEDIA_WORDS = { image: "photo", video: "video", sidecar: "sidecar file" };

var DETAIL = {
  /* Whether the preview is open. The item it shows is always the list's keyboard row. */
  open: false,
  /* The id of the row whose name is being edited, or null. */
  editingId: null,
  /* `unit_key` -> the members the page has seen, so an edit can name the partners'
   * extensions (A6) without asking twice. */
  units: {},
};

/* --- the elements, on first use -------------------------------------------------------- */

var detailEl = null;

function detailElements() {
  if (detailEl === null) {
    detailEl = {
      editor: document.getElementById("editor"),
      editorStem: document.getElementById("editor-stem"),
      editorExt: document.getElementById("editor-ext"),
      editorCancel: document.getElementById("editor-cancel"),
      editorNote: document.getElementById("editor-note"),
      editorError: document.getElementById("editor-error"),
      overlay: document.getElementById("overlay"),
      overlayTitle: document.getElementById("overlay-title"),
      overlayPrev: document.getElementById("overlay-prev"),
      overlayNext: document.getElementById("overlay-next"),
      overlayApprove: document.getElementById("overlay-approve"),
      overlaySkip: document.getElementById("overlay-skip"),
      overlayReset: document.getElementById("overlay-reset"),
      overlayEdit: document.getElementById("overlay-edit"),
      overlayClose: document.getElementById("overlay-close"),
      overlayImage: document.getElementById("overlay-image"),
      overlayGlyph: document.getElementById("overlay-glyph"),
      overlaySide: document.getElementById("overlay-side"),
      overlayName: document.getElementById("overlay-name"),
      overlayTrust: document.getElementById("overlay-trust"),
      overlayMeta: document.getElementById("overlay-meta"),
      overlayWhy: document.getElementById("overlay-why"),
    };
    /* Where `#editor` lives when it is not inside a row or the preview: read once, at first
     * use, which is before the editor has ever been moved. */
    detailEl.editorHome = detailEl.editor.parentNode;
  }
  return detailEl;
}

function detailStopped() {
  var hook = LIST.hooks.stopped;
  return typeof hook === "function" && Boolean(hook());
}

function detailPost(body) {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

/* --- thumbnails' glyphs and orientation (the card page's own copies retired at Step 6b) - */

function glyphKind(item) {
  if (item.media_type === "video") {
    return "video";
  }
  if (item.media_type === "image") {
    return "image";
  }
  return "other";
}

/* Spec 8.2: a 404 from the thumb route is NOT an error, it is the signal to draw the
 * glyph. `has_thumb: false` is the cheap half of the same question and avoids the
 * request entirely, and the two variants may legitimately disagree - a file whose only
 * embedded preview is large gives a grid 404 and a working overlay (A2 amendment 1),
 * so a grid failure is never cached as a verdict about the overlay. */
function fillGlyph(cell, item) {
  clear(cell);
  var glyph = make("span", "glyph");
  glyph.setAttribute("data-kind", glyphKind(item));
  cell.appendChild(glyph);
  cell.appendChild(make("span", "ext-badge", String(item.ext || "").toUpperCase()));
  return cell;
}

/* A2: orientation is applied client-side as a CSS transform. `null` means NO TRANSFORM
 * and 0 is not an orientation - the catalog records the EXIF value or nothing, and
 * inventing a rotation for a file that never declared one would rotate the picture the
 * user has been looking at for twenty years. */
function applyOrientation(image, orientation) {
  /* CLEARED FIRST, ALWAYS. The overlay reuses ONE image for every photo the arrows walk
   * through - so an early return would leave the previous photo's rotation on the next
   * one, which is a picture presented wrongly rather than a missing effect. */
  image.removeAttribute("data-orientation");
  if (typeof orientation !== "number" || orientation < 1 || orientation > 8) {
    return;
  }
  image.setAttribute("data-orientation", String(orientation));
}

/* --- the preview overlay: a full review surface (U16) ---------------------------------- */

function previewIsOpen() {
  return DETAIL.open;
}

/* Open the preview on this row: it becomes the list's keyboard row, so `←/→` walk on
 * from it and the decision keys act on it. */
function openPreview(item) {
  if (detailStopped() || !item || !LIST.rows[item.id]) {
    return;
  }
  setListCursor(item.id);
  DETAIL.open = true;
  detailElements().overlay.hidden = false;
  renderPreview();
}

function closePreview() {
  if (!DETAIL.open) {
    return;
  }
  closeEditor();
  DETAIL.open = false;
  detailElements().overlay.hidden = true;
}

function renderPreview() {
  if (!DETAIL.open) {
    return;
  }
  var d = detailElements();
  var item = listCurrent();
  if (!item) {
    closePreview();
    return;
  }
  d.overlayTitle.textContent = item.old_filename;
  d.overlayName.textContent = "→ " + item.new_filename;
  /* UX spec 5 rev 8: only a DECIDED item carries the un-decide control, and the overlay
   * is redrawn on every move, so this is the line that keeps the rule true as the user
   * walks a mixed plan. */
  d.overlayReset.hidden = !isDecided(item);
  clear(d.overlayTrust);
  d.overlayTrust.appendChild(trustDisplay(item));
  clear(d.overlayMeta);
  d.overlayMeta.appendChild(metaPanel(item));
  clear(d.overlayWhy);
  d.overlayWhy.appendChild(whyPanel(item));
  renderPreviewImage(item);
}

/* A2 amendment 1: the list asks for `-ThumbnailImage` alone and the overlay asks for the
 * bigger preview, so a file that drew a GLYPH in the list can show a real picture here.
 * The overlay therefore asks for its own image and treats its own 404 as the glyph
 * signal. */
function renderPreviewImage(item) {
  var d = detailElements();
  if (!item.has_thumb) {
    showPreviewGlyph(item);
    return;
  }
  d.overlayGlyph.hidden = true;
  d.overlayImage.hidden = false;
  d.overlayImage.alt = item.old_filename;
  applyOrientation(d.overlayImage, item.orientation);
  d.overlayImage.src = "api/thumb/" + encodeURIComponent(item.file_id) + "?variant=full";
}

function showPreviewGlyph(item) {
  var d = detailElements();
  d.overlayImage.hidden = true;
  d.overlayImage.removeAttribute("src");
  d.overlayGlyph.hidden = false;
  d.overlayGlyph.className = "thumb-cell overlay-glyph";
  fillGlyph(d.overlayGlyph, item);
}

function metaPanel(item) {
  var list = make("dl");
  addMeta(list, "Now called", item.old_filename);
  addMeta(list, "Will be called", item.new_filename);
  addMeta(list, "Folder", item.rel_dir || "the top of the archive");
  addMeta(list, "Kind", MEDIA_WORDS[item.media_type] || "file");
  addMeta(list, "Date in the name", item.date || "none pam could read");
  addMeta(list, "Decision", statusNote(item));
  if (item.unit_size > 1) {
    addMeta(list, "Renames with", item.unit_size - 1 + " other file(s)");
  }
  if (item.placeholder) {
    addMeta(list, "Note", "This file is not downloaded yet, so pam has not read it.");
  }
  return list;
}

function addMeta(list, label, value) {
  list.appendChild(make("dt", null, label));
  list.appendChild(make("dd", null, value));
}

/* The preview's Approve and Skip: one decision through the list's `decide`, then on to the
 * next undecided row - UX 5's one-keystroke-per-photo review. They do NOT toggle (a button
 * labelled `Approve` that un-approves is a label arguing with its control); the route back
 * to undecided is the `Not decided` button, which stays on its row. */
function previewDecide(action) {
  var item = listCurrent();
  if (!item) {
    return Promise.resolve();
  }
  return decideAction(item, action).then(function () {
    if (action !== "reset") {
      return advanceListCursor();
    }
  });
}

/* --- editing a name (A6) ---------------------------------------------------------------- */

function extOf(filename) {
  var dot = filename.lastIndexOf(".");
  return dot <= 0 ? "" : filename.slice(dot + 1);
}

function stemOf(filename) {
  var dot = filename.lastIndexOf(".");
  return dot <= 0 ? filename : filename.slice(0, dot);
}

function rememberUnitMember(item) {
  var known = DETAIL.units[item.unit_key];
  if (!known) {
    known = {};
    DETAIL.units[item.unit_key] = known;
  }
  known[item.id] = item;
}

function knownPartners(item) {
  var known = DETAIL.units[item.unit_key] || {};
  var partners = [];
  for (var key in known) {
    if (Object.prototype.hasOwnProperty.call(known, key) && known[key].id !== item.id) {
      partners.push(known[key]);
    }
  }
  return partners;
}

/* A6 requires the editor to say that a rename takes the partners with it AND TO NAME
 * THEIR EXTENSIONS, and the row carries `unit_size` but not the partners themselves.
 * They are found through the read API rather than guessed: a unit's members share a
 * stem, and `?q=` matches the proposed name, so one bounded search returns them. The
 * `unit_key` is what actually decides membership - `q` only narrows the search. Every
 * widening of `q` (event labels, places, people) makes this search LESS selective, and the
 * page reads only the first MAX_LIMIT rows - which is why `unitNote` says the weaker thing
 * whenever it has not found every partner. */
function loadUnitMembers(item) {
  var partners = knownPartners(item);
  if (partners.length >= item.unit_size - 1) {
    return Promise.resolve(partners);
  }
  var params = new URLSearchParams();
  params.set("q", stemOf(item.new_filename));
  params.set("limit", String(MAX_LIMIT));
  return api("api/plan/" + LIST.planId + "/items?" + params.toString())
    .then(function (payload) {
      var found = (payload && payload.items) || [];
      for (var i = 0; i < found.length; i += 1) {
        if (found[i].unit_key === item.unit_key) {
          rememberUnitMember(found[i]);
        }
      }
      return knownPartners(item);
    })
    .catch(function () {
      return knownPartners(item);
    });
}

function unitNote(item, partners) {
  if (item.unit_size <= 1) {
    return "";
  }
  var exts = [];
  for (var i = 0; i < partners.length; i += 1) {
    exts.push("." + extOf(partners[i].new_filename));
  }
  if (exts.length !== item.unit_size - 1) {
    /* The partners are real - `unit_size` counted them over the WHOLE plan - but this
     * page has not been able to fetch all of them, so it says the true weaker thing
     * rather than naming extensions it does not have. `!==` rather than `=== 0`: a
     * PARTIAL find is the dangerous case, because naming the one extension that arrived,
     * in a sentence whose shape claims to list them all, tells the user that fewer
     * irreplaceable files are about to be renamed than really are. Under-claiming a rename
     * is the direction this product may never fail in. */
    var others = item.unit_size - 1;
    return (
      "This file was taken together with " +
      others +
      (others === 1 ? " other file" : " other files") +
      ", and saving this name renames " +
      (others === 1 ? "it" : "them") +
      " too."
    );
  }
  var named = exts.length === 1 ? exts[0] : exts.slice(0, -1).join(", ") + " and " + exts[exts.length - 1];
  return "Saving this also renames its " + named + " — they always keep the same name.";
}

function openEditor(item) {
  if (detailStopped() || !item || !LIST.rows[item.id]) {
    return;
  }
  var d = detailElements();
  closeEditor();
  DETAIL.editingId = item.id;
  d.editor.hidden = false;
  d.editorError.hidden = true;
  d.editorError.textContent = "";
  d.editorStem.value = stemOf(item.new_filename);
  d.editorExt.textContent = "." + extOf(item.new_filename);
  d.editorNote.hidden = item.unit_size <= 1;
  d.editorNote.textContent = unitNote(item, knownPartners(item));
  hostEditor(item);
  if (item.unit_size > 1) {
    loadUnitMembers(item).then(function (partners) {
      if (DETAIL.editingId === item.id) {
        d.editorNote.textContent = unitNote(item, partners);
      }
    });
  }
}

/* Put the one editor into whichever surface is showing this item - the preview's side
 * panel when it is open, else the row - and give it focus. Shared by `openEditor` and
 * `showEditorDisclosure` because a rename REPAINTS the row (`repaintRow` swaps the node the
 * editor was appended to), so a disclosure that stays on screen has to be re-hosted against
 * the fresh row rather than left orphaned in the detached one. */
function hostEditor(item) {
  var d = detailElements();
  d.editor.hidden = false;
  var held = LIST.rows[item.id];
  var host = DETAIL.open ? d.overlaySide : held ? held.node : null;
  if (host) {
    host.appendChild(d.editor);
  }
  if (held) {
    held.node.setAttribute("data-editing", "true");
  }
  d.editorStem.focus();
  d.editorStem.select();
}

function closeEditor() {
  if (DETAIL.editingId === null) {
    return;
  }
  var d = detailElements();
  var held = LIST.rows[DETAIL.editingId];
  if (held) {
    held.node.setAttribute("data-editing", "false");
  }
  DETAIL.editingId = null;
  d.editor.hidden = true;
  if (d.editorHome) {
    d.editorHome.appendChild(d.editor);
  }
}

function submitEditor() {
  var d = detailElements();
  var held = DETAIL.editingId === null ? null : LIST.rows[DETAIL.editingId];
  if (!held) {
    closeEditor();
    return Promise.resolve();
  }
  var item = held.item;
  var submitted = d.editorStem.value + "." + extOf(item.new_filename);
  d.editorError.hidden = true;
  return api("api/item/" + item.id + "/rename", detailPost({ new_filename: submitted }))
    .then(function (result) {
      /* SPEC 6.2 RULE 7: pam says what it changed. The server chooses the sentence,
       * because only the server knows WHICH of 6.2's rules fired; no sentence means
       * nothing was adjusted, and the editor closes. */
      absorbUnit(result);
      /* THE RESPONSE'S COUNTS, NEVER A LOCAL TALLY: a rename approves its whole unit. */
      if (result.counts) {
        LIST.counts = result.counts;
      }
      renderProgress();
      scheduleRefresh();
      if (result.note) {
        showEditorDisclosure(result);
      } else {
        closeEditor();
      }
    })
    .catch(function (failure) {
      if (failure instanceof ApiRefusal) {
        /* UX spec 1.4's shape, AT THE FIELD: the server's own sentence already says what
         * happened, what it means and what to do. */
        d.editorError.hidden = false;
        d.editorError.textContent = fieldMessage(failure.message, "new_filename");
        d.editorStem.focus();
      }
    });
}

/* Spec 6.2 rule 7's sentence, AT THE FIELD (amendment A5). The write has already landed,
 * so the editor stays open only to say what pam stored and why; the field shows the
 * SUBMITTED stem, read back from the response, and the sentence replaces the companion
 * note rather than joining it. */
function showEditorDisclosure(result) {
  var d = detailElements();
  var held = LIST.rows[result.item_id];
  if (!held) {
    closeEditor();
    return;
  }
  var item = held.item;
  DETAIL.editingId = item.id;
  d.editorError.hidden = true;
  d.editorError.textContent = "";
  d.editorStem.value = stemOf(result.submitted_filename);
  d.editorExt.textContent = "." + extOf(item.new_filename);
  d.editorNote.hidden = false;
  d.editorNote.textContent = result.note;
  hostEditor(item);
}

/* The response is the page's evidence that the write landed - it is read back from the
 * catalog, not assembled from the request - so the page renders THAT, including a stem
 * the server sanitized into something other than what was typed. Every member the write
 * moved is repainted, the partners included. */
function absorbUnit(result) {
  var members = result.members || [];
  for (var i = 0; i < members.length; i += 1) {
    var member = members[i];
    var held = LIST.rows[member.id];
    if (!held) {
      continue;
    }
    var item = held.item;
    item.new_filename = member.new_filename;
    item.generated_filename = member.generated_filename;
    /* COPIED, NOT RE-DERIVED: `edited` and `noop` change only through a rename or a
     * restore, which are exactly the two writes that land here (UX 5 rev 8, rev 11). */
    item.edited = member.edited;
    item.noop = member.noop;
    item.status = member.status;
    rememberUnitMember(item);
    repaintRow(member.id);
  }
  renderPreview();
}

/* --- putting pam's name back (A6) ----------------------------------------------------- */

/* UX 5 rev 8's `↺` on an edited row (M14 Task 2 Step 6b: moved from the card page's
 * `restoreUnit`, with its endpoint and body unchanged). The server puts pam's name back on the
 * whole unit and answers with the members it moved - so the page repaints THOSE, as a rename
 * does - and the progress line is the response's `counts`, never a local tally. A refusal is
 * the server's own sentence, on the list's note line. */
function restoreRow(item) {
  if (detailStopped()) {
    return Promise.resolve();
  }
  showListNote("");
  return api("api/item/" + item.id + "/restore", detailPost({}))
    .then(function (result) {
      absorbUnit(result);
      if (result.counts) {
        LIST.counts = result.counts;
      }
      renderProgress();
      scheduleRefresh();
    })
    .catch(function (failure) {
      if (failure instanceof ApiRefusal) {
        showListNote(failure.message);
      }
    });
}

/* --- wiring ----------------------------------------------------------------------------- */

/* The listeners, and the hooks the list's keys hand a row to (`Enter`, `C`, `←/→` in the
 * preview, the name field's Escape). Called once by `app.js`'s `start`. */
function wireDetail() {
  var d = detailElements();
  d.editor.addEventListener("submit", function (event) {
    event.preventDefault();
    submitEditor();
  });
  d.editorCancel.addEventListener("click", closeEditor);
  d.overlayClose.addEventListener("click", closePreview);
  d.overlayPrev.addEventListener("click", function () {
    moveListCursor(-1);
  });
  d.overlayNext.addEventListener("click", function () {
    moveListCursor(1);
  });
  d.overlayApprove.addEventListener("click", function () {
    return previewDecide("approve");
  });
  d.overlaySkip.addEventListener("click", function () {
    return previewDecide("skip");
  });
  d.overlayReset.addEventListener("click", function () {
    return previewDecide("reset");
  });
  d.overlayEdit.addEventListener("click", function () {
    openEditor(listCurrent());
  });
  d.overlayImage.addEventListener("error", function () {
    var item = listCurrent();
    if (item) {
      showPreviewGlyph(item);
    }
  });
  LIST.hooks.preview = openPreview;
  LIST.hooks.previewOpen = previewIsOpen;
  LIST.hooks.previewMoved = renderPreview;
  LIST.hooks.closePreview = closePreview;
  LIST.hooks.edit = openEditor;
  LIST.hooks.restore = restoreRow;
  LIST.hooks.editorField = d.editorStem;
  LIST.hooks.closeEditor = closeEditor;
}

/* --- the seams: the page's namespace, and node's -------------------------------------- */

if (typeof window !== "undefined") {
  window.PAM.detail = {
    DETAIL: DETAIL,
    MEDIA_WORDS: MEDIA_WORDS,
    extOf: extOf,
    stemOf: stemOf,
    unitNote: unitNote,
    glyphKind: glyphKind,
    metaPanel: metaPanel,
    openPreview: openPreview,
    closePreview: closePreview,
    openEditor: openEditor,
    closeEditor: closeEditor,
    submitEditor: submitEditor,
    restoreRow: restoreRow,
    wireDetail: wireDetail,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    DETAIL: DETAIL,
    MEDIA_WORDS: MEDIA_WORDS,
    extOf: extOf,
    stemOf: stemOf,
    unitNote: unitNote,
    glyphKind: glyphKind,
    metaPanel: metaPanel,
    openPreview: openPreview,
    closePreview: closePreview,
    openEditor: openEditor,
    closeEditor: closeEditor,
    submitEditor: submitEditor,
    restoreRow: restoreRow,
    wireDetail: wireDetail,
  };
}
