/* The review page's keyboard (M14 Task 1): the shortcut overlay's toggle and the one
 * key handler, moved out of `app.js` unchanged. `pam-core.js` explains the split.
 *
 * `onListKey` lives in `pam-list.js`, loaded AFTER this file. That is sound for classic
 * scripts: the
 * handler only runs on a key press, long after every script has loaded, and nothing here
 * runs at load except the namespace line.
 */

"use strict";

/* --- the shortcut overlay, and the keyboard ------------------------------------------ */

/* M14 Task 2 Step 6b: the card page's element map went with it, so the overlay is looked up
 * here, at call time. */
function toggleShortcuts() {
  var shortcuts = document.getElementById("shortcuts");
  shortcuts.hidden = !shortcuts.hidden;
}

function shortcutsOpen() {
  return !document.getElementById("shortcuts").hidden;
}

/* M14 TASK 2 STEP 5: THE KEYS ARE THE LIST'S. U2's set - `↑/↓`, `X`, `Z`, `C`, `Space`
 * (select, since M14 Task 3), `Enter`, `?`, and `←/→` inside the preview - is decided by
 * `pam-list.js`'s `listKeyAction` (pure, executed under node by `tests/js/list_test.js`)
 * and performed by its `onListKey`, which also keeps every key inert while a field has
 * focus. This handler is the document's listener and only stops a page pam has stopped. */
function onKeyDown(event) {
  if (PAGE.stopped || PAGE.confirming) {
    return;
  }
  onListKey(event);
}

/* --- the seam: the page's namespace ---------------------------------------------------- */

/* No node seam: no function here is pure - both read and write the page. The namespace
 * line has no fallback, for the reason `pam-trust.js` gives. */
if (typeof window !== "undefined") {
  window.PAM.keys = {
    toggleShortcuts: toggleShortcuts,
    shortcutsOpen: shortcutsOpen,
    onKeyDown: onKeyDown,
  };
}
