/*
 * THE REVIEW PAGE'S SCRIPTS, LOADED THE WAY THE PAGE LOADS THEM (M14 Task 1).
 *
 * M14 Task 1 split `app.js` into five classic scripts - `pam-core.js`, `pam-trust.js`,
 * `pam-keys.js`, `pam-review.js`, then `app.js` (the boot); M14 Task 2 added the list's
 * three and retired `pam-review.js` (Step 6b) - and they share ONE global scope
 * and are loaded by `index.html` in a fixed order. Nothing else in this suite can see that
 * order: `node --check` parses each file alone, and the node drivers `require` each file
 * into its own scope. A page whose `<script>` tags were swapped would pass both and load
 * blank in a browser. This file is the one check that runs them as the page does.
 *
 * WHAT IT DOES. It reads the `<script src>` list out of `index.html` and runs each named
 * file, in that order, in ONE `vm` context whose `window` is the context itself - which is
 * what a browser's global object is for classic scripts - with a minimal DOM stub and a
 * `fetch` that records its argument and never settles. `module` is NOT defined, so the
 * page's own bootstrap (`app.js`'s `else start()`) runs exactly as in a browser, up to its
 * first request. Every check prints `ok <name>` or `FAIL <name>`, and the run ends with
 * `ran <n>`; `tests/test_static_syntax.py` asserts the set of names that ran.
 *
 * HOW THE LIST IS READ, AND WHY NOT WITH A REAL PARSER HERE. Node has no HTML parser and
 * this project adds no dependency (design 10), so the `src` list is read with a regex over
 * `<script ... src="...">`. A regex over HTML is only sound for a page whose scripts are
 * all of that one shape, so the page is required FIRST to have no `<script>` carrying
 * inline code (the page has no boot line - `app.js` boots), and to have exactly as many
 * `src` attributes as `<script>` tags. The REAL parse is the Python side's:
 * `tests/test_static_syntax.py` reads the same list with the stdlib's `html.parser` and
 * holds it to the order the spec gives - the real parser is the oracle beside this read.
 *
 * AN EMPTY LIST FAILS; IT NEVER SKIPS. A harness that ran no file would report every
 * later check over nothing (M743's shape), so the first check is that the list is not
 * empty, and the later checks refuse to run green over an empty list.
 */

"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const STATIC_DIR = path.join(__dirname, "..", "..", "pam", "webui", "static");
const PAGE = fs.readFileSync(path.join(STATIC_DIR, "index.html"), "utf-8");

/* The namespace each review script attaches to `window.PAM`, spelled from M14 Task 1's
 * module list - never read from the scripts. `boot` is `app.js`'s. */
const NAMESPACES = {
  "pam-core.js": "core",
  "pam-trust.js": "trust",
  "pam-keys.js": "keys",
  /* M14 Task 2: the list (U2). */
  "pam-row.js": "row",
  "pam-list.js": "list",
  /* M14 Task 3: the selection as data, and its bar (U3, U17). */
  "pam-selection.js": "selection",
  "pam-bar.js": "bar",
  /* M14 Task 2 Step 6a: the preview overlay and the name editor (U16, A6). */
  "pam-detail.js": "detail",
  /* M14 Task 4 Step 7: Welcome (W18, U13). */
  /* M14 Task 5a: Welcome's folder chooser (U13), cut out of `pam-welcome.js`. */
  "pam-chooser.js": "chooser",
  /* M14 Task 5e: Welcome's Reading step (U11), after the chooser and before Welcome. */
  "pam-reading.js": "reading",
  "pam-welcome.js": "welcome",
  /* M14 Task 6c: the Confirm screen, then Done and undo (U6, W19-W21), before the boot. */
  "pam-confirm.js": "confirm",
  "pam-done.js": "done",
  /* M15 Task 5b: a rename that did not run or stopped part-way, cut out of Done. */
  "pam-stopped.js": "stopped",
  /* M14 Task 7: History (W22, X2), after Done, whose undo it reuses. */
  "pam-history.js": "history",
  "app.js": "boot",
};

/* The scripts whose node seam (`module.exports`) must offer exactly what their namespace
 * offers, so the browser's names and node's tested names cannot drift apart. */
const SEAMED = [
  "pam-core.js",
  "pam-trust.js",
  "pam-row.js",
  "pam-list.js",
  "pam-detail.js",
  "pam-selection.js",
  "pam-bar.js",
  "pam-chooser.js",
  "pam-reading.js",
  "pam-welcome.js",
  "pam-confirm.js",
  "pam-done.js",
  "pam-stopped.js",
  "pam-history.js",
];

const tests = [];
function test(name, body) {
  tests.push([name, body]);
}

/* --- reading the page's script list -------------------------------------------------- */

function htmlWithoutComments(html) {
  return html.replace(/<!--[\s\S]*?-->/g, "");
}

function scriptTags(html) {
  return htmlWithoutComments(html).match(/<script\b[^>]*>[\s\S]*?<\/script\s*>/gi) || [];
}

function inlineCode(tag) {
  return tag.replace(/^<script\b[^>]*>/i, "").replace(/<\/script\s*>$/i, "").trim();
}

function scriptSources(html) {
  const found = [];
  const pattern = /<script\b[^>]*\bsrc\s*=\s*"([^"]+)"[^>]*>/gi;
  let match;
  const text = htmlWithoutComments(html);
  while ((match = pattern.exec(text)) !== null) {
    found.push(match[1]);
  }
  return found;
}

/* --- a minimal DOM ------------------------------------------------------------------- */

function makeNode(tag) {
  const node = {
    tagName: String(tag || "div").toUpperCase(),
    children: [],
    firstChild: null,
    parentNode: null,
    style: {},
    dataset: {},
    hidden: false,
    textContent: "",
    value: "",
    disabled: false,
    classList: {
      add: function () {},
      remove: function () {},
      toggle: function () {},
      contains: function () {
        return false;
      },
    },
    addEventListener: function () {},
    removeEventListener: function () {},
    appendChild: function (child) {
      node.children.push(child);
      child.parentNode = node;
      return child;
    },
    removeChild: function (child) {
      node.children = node.children.filter(function (each) {
        return each !== child;
      });
      return child;
    },
    replaceChild: function (fresh, old) {
      node.removeChild(old);
      return node.appendChild(fresh);
    },
    insertBefore: function (child) {
      return node.appendChild(child);
    },
    setAttribute: function () {},
    removeAttribute: function () {},
    getAttribute: function () {
      return null;
    },
    focus: function () {},
    scrollIntoView: function () {},
    querySelector: function () {
      return null;
    },
    querySelectorAll: function () {
      return [];
    },
  };
  return node;
}

function makeDocument() {
  const body = makeNode("body");
  const byId = {};
  return {
    body: body,
    getElementById: function (id) {
      if (!byId[id]) {
        byId[id] = makeNode("div");
        body.appendChild(byId[id]);
      }
      return byId[id];
    },
    createElement: makeNode,
    querySelector: function () {
      return null;
    },
    querySelectorAll: function () {
      return [];
    },
    addEventListener: function () {},
  };
}

/* One page load: every named file, in the order given, in one context. Returns the
 * context, the requests boot made, and the first error with the file it came from. */
function loadPage(files) {
  const requests = [];
  const context = vm.createContext({
    document: makeDocument(),
    fetch: function (url) {
      requests.push(url);
      return new Promise(function () {});
    },
    console: console,
  });
  context.window = context;
  let failure = null;
  const loaded = [];
  for (const file of files) {
    const code = fs.readFileSync(path.join(STATIC_DIR, file), "utf-8");
    try {
      vm.runInContext(code, context, { filename: file });
      loaded.push(file);
    } catch (error) {
      failure = { file: file, error: error };
      break;
    }
  }
  return { context: context, requests: requests, failure: failure, loaded: loaded };
}

const SOURCES = scriptSources(PAGE);

function requireSources() {
  assert.ok(SOURCES.length > 0, "index.html lists no <script src> - nothing would be checked");
}

/* --- the checks ------------------------------------------------------------------------ */

test("the page lists at least one script, and every script is an external file", function () {
  requireSources();
  const tags = scriptTags(PAGE);
  assert.equal(tags.length, SOURCES.length, "a <script> without a src would escape the read");
  for (const tag of tags) {
    assert.equal(inlineCode(tag), "", "a <script> carries inline code: " + tag);
  }
});

test("every script the page lists runs in the page's order without an error", function () {
  requireSources();
  const page = loadPage(SOURCES);
  if (page.failure) {
    assert.fail(page.failure.file + " failed at load: " + page.failure.error);
  }
  /* WHAT RAN, NOT ONLY THAT A CHECK RAN (M14 Task 1 review, F-1-1 / M2743): the names of the
   * checks that passed say nothing about what they passed over, so a harness that returned
   * early on an empty list printed seven `ok`s over nothing. The Python half holds this line
   * to its own spelled order. */
  console.log("loaded " + page.loaded.join(","));
});

test("each script attaches its own part of the page's namespace", function () {
  requireSources();
  const page = loadPage(SOURCES);
  assert.equal(page.failure, null);
  const PAM = page.context.PAM;
  assert.ok(PAM && typeof PAM === "object", "window.PAM was never created");
  assert.deepEqual(Object.keys(PAM).sort(), Object.values(NAMESPACES).sort());
  assert.equal(typeof PAM.boot, "function", "PAM.boot is not a function");
  for (const name of ["core", "trust", "keys", "row", "list", "detail", "selection", "bar", "chooser", "reading", "welcome"]) {
    assert.ok(Object.keys(PAM[name]).length > 0, "PAM." + name + " is empty");
  }
});

test("the page boots, and its first request is the ping", function () {
  requireSources();
  const page = loadPage(SOURCES);
  assert.equal(page.failure, null);
  assert.deepEqual(page.requests, ["api/ping"]);
});

test("each namespace offers exactly what that script's node seam exports", function () {
  requireSources();
  const page = loadPage(SOURCES);
  assert.equal(page.failure, null);
  global.document = makeDocument();
  /* The shared scope reproduced, as every other driver does: since M14 Task 2 Step 6b part 2
   * `pam-list.js` reads core's `SORT_TRUST` at load. */
  Object.assign(global, require(path.join(STATIC_DIR, "pam-core.js")));
  /* M14 Task 5 review R2 (M3167): every script that HAS a node seam is compared - read off the
   * files, so a seam dropped from SEAMED is red rather than silently uncompared. */
  for (const file of Object.keys(NAMESPACES)) {
    const source = fs.readFileSync(path.join(STATIC_DIR, file), "utf-8");
    /* `app.js` exports the boot, a function, not a namespace of names (`PAM.boot`). */
    if (source.indexOf("module.exports") !== -1 && NAMESPACES[file] !== "boot") {
      assert.ok(SEAMED.includes(file), file + " has a node seam that SEAMED does not compare");
    }
  }
  for (const file of SEAMED) {
    const seam = require(path.join(STATIC_DIR, file));
    const namespace = page.context.PAM[NAMESPACES[file]];
    assert.deepEqual(Object.keys(namespace).sort(), Object.keys(seam).sort(), file);
  }
});

/* THE NEGATIVE CONTROL, RUN: the harness can see the defect it exists for. A script moved
 * above `pam-core.js` must fail at load, and must be named as the file that failed. M14
 * Task 2 Step 6b: the planted script is `pam-list.js` (it reads core's `SORT_TRUST` and
 * attaches to `window.PAM` at load), since `pam-review.js`, the old subject, is gone. */
test("a review script loaded before pam-core.js fails at load, and is named", function () {
  const swapped = ["pam-list.js", "pam-core.js", "pam-trust.js", "pam-keys.js", "app.js"];
  const page = loadPage(swapped);
  assert.ok(page.failure, "loading pam-list.js before pam-core.js raised nothing");
  assert.equal(page.failure.file, "pam-list.js");
});

test("the script reader sees a missing src and inline code", function () {
  assert.deepEqual(scriptSources('<script src="a.js"></script><!-- <script src="b.js"> -->'), [
    "a.js",
  ]);
  assert.equal(scriptTags("<script>start();</script>").length, 1);
  assert.equal(inlineCode("<script>start();</script>"), "start();");
  assert.deepEqual(scriptSources("<script>start();</script>"), []);
});

let failed = 0;
for (const [name, body] of tests) {
  try {
    body();
    console.log("ok " + name);
  } catch (error) {
    failed += 1;
    console.log("FAIL " + name);
    console.log("  " + String(error && error.message ? error.message : error).split("\n")[0]);
  }
}
console.log("ran " + (tests.length - failed));
process.exit(failed === 0 ? 0 : 1);
