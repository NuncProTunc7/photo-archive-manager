/* THE FOLDER CHOOSER (M14 Task 4 Step 7, U13; cut out of `pam-welcome.js` by M14 Task 5a,
 * R2-F10).
 *
 * Welcome's Folder step: the known archives, a browser that lists drives and then folders
 * (`GET api/folders`, read-only on the server; folders only, never files), a way Up, and a box
 * to paste a path. Choosing - a known archive, `Choose this folder`, or the paste box - is
 * `pam-welcome.js`'s `chooseFolder`, which posts the bind and draws its answer; a refusal is
 * shown by its `onRefusal`, and its `clearError` takes the last one away. Those are looked up
 * when a click or an answer arrives, never at load, so this file loads BEFORE `pam-welcome.js`.
 *
 * EVERY PATH SHOWN IS ONE THE SERVER SENT (a listing's `path`, a folder's `name`, a drive, a
 * known archive). The pasted text is only ever the request's body.
 *
 * The element queries are `pam-welcome.js`'s `welcomeElements()`, and the words its
 * `WELCOME_COPY`: the page has one list of Welcome's ids and one of its words.
 */

"use strict";

/* --- state ---------------------------------------------------------------------------- */

var CHOOSER = {
  /* The listing on show: its own path, and its parent (null at a drive's root). */
  listed: null,
  parent: null,
  /* R2-F1 (the `BAR.generation` pattern): each listing request is numbered, and an answer -
   * or a refusal - that is not the latest request's is dropped, so a slow folder that answers
   * after a newer one never replaces the folder the user asked for last. */
  listing: 0,
};

/* --- drawing -------------------------------------------------------------------------- */

function folderButton(text, onClick) {
  var button = make("button", "welcome-pick", text);
  button.type = "button";
  button.addEventListener("click", onClick);
  var item = make("li");
  item.appendChild(button);
  return item;
}

/* The top level: the known archives and the drives (U13). */
function drawTop(body) {
  var w = welcomeElements();
  w.place.hidden = true;
  w.up.hidden = true;
  w.chooseThis.hidden = true;
  clear(w.known);
  var known = body.known || [];
  w.knownTitle.hidden = known.length === 0;
  for (var i = 0; i < known.length; i += 1) {
    var row = folderButton(known[i].path, clickTo(chooseFolder, known[i].path));
    /* An archive pam cannot see now is listed, not offered. */
    row.firstChild.disabled = !known[i].reachable;
    w.known.appendChild(row);
  }
  w.foldersTitle.textContent = WELCOME_COPY.drives;
  w.foldersTitle.hidden = (body.drives || []).length === 0;
  clear(w.folders);
  var drives = body.drives || [];
  for (var j = 0; j < drives.length; j += 1) {
    w.folders.appendChild(folderButton(drives[j], clickTo(showListing, drives[j])));
  }
}

/* One folder's folders, a way up, and the choice of the folder itself. */
function drawListing(body) {
  var w = welcomeElements();
  w.knownTitle.hidden = true;
  clear(w.known);
  w.place.textContent = body.path;
  w.place.hidden = false;
  w.up.textContent = body.parent === null ? WELCOME_COPY.allDrives : WELCOME_COPY.up;
  w.up.hidden = false;
  CHOOSER.parent = body.parent;
  w.foldersTitle.hidden = true;
  clear(w.folders);
  var folders = body.folders || [];
  if (folders.length === 0) {
    w.folders.appendChild(make("li", "welcome-empty", WELCOME_COPY.noFolders));
  }
  for (var i = 0; i < folders.length; i += 1) {
    w.folders.appendChild(folderButton(folders[i].name, clickTo(showListing, folders[i].path)));
  }
  w.chooseThis.hidden = false;
  CHOOSER.listed = body.path;
}

function showTop() {
  return listFolders("api/folders", drawTop);
}

function showListing(path) {
  var query = new URLSearchParams({ path: path }).toString();
  return listFolders("api/folders?" + query, drawListing);
}

/* One listing request; its answer is drawn, and its refusal shown, only while it is the latest. */
function listFolders(url, draw) {
  clearError();
  CHOOSER.listing += 1;
  var generation = CHOOSER.listing;
  return api(url).then(
    function (body) {
      if (generation === CHOOSER.listing) {
        draw(body);
      }
    },
    function (failure) {
      if (generation === CHOOSER.listing) {
        onRefusal(failure);
      }
    }
  );
}

/* A click handler that calls `action(path)`: a listing, or a choice. */
function clickTo(action, path) {
  return function () {
    return action(path);
  };
}

/* --- the controls ---------------------------------------------------------------------- */

function onUp() {
  return CHOOSER.parent === null ? showTop() : showListing(CHOOSER.parent);
}

function chooseListed() {
  return chooseFolder(CHOOSER.listed);
}

function onPaste(event) {
  if (event) {
    event.preventDefault();
  }
  return chooseFolder(welcomeElements().pastePath.value);
}

/* --- the seams ------------------------------------------------------------------------- */

var CHOOSER_SEAM = {
  CHOOSER: CHOOSER,
  showTop: showTop,
  showListing: showListing,
};

if (typeof window !== "undefined") {
  window.PAM.chooser = CHOOSER_SEAM;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = CHOOSER_SEAM;
}
