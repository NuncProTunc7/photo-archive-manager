/*
 * THE REVIEW PAGE'S PROVENANCE LINE, EXECUTED (M11 Task 12 review, finding F3).
 *
 * `pam/webui/static/app.js` is ~2300 lines nothing in this project runs (`m7.md` F4).
 * Task 12 Step 9 fixed a real defect in it - `sourceWords` looked `fact_models` up by
 * `source_id` whatever the source table was, so a card holding caption fact 11 and person
 * 11 would have named a caption model as the source of somebody's child (spec 8.2 rev 22)
 * - and the fix was pinned by ONE regex over the source. M1939 measured what that is
 * worth: breaking the guard AND weakening the regex in the same run left 3620 tests
 * green. A grep cannot survive an edit to the text it greps for, so this file EXECUTES
 * the function instead, on `tests/js/faces_state_test.js`'s precedent.
 *
 * THE EXPECTATIONS ARE SPELLED FROM THE SPEC, never read back out of `app.js`: spec 8.2
 * rev 22 ("a `{people}` token gets NO entry: its `source_table` is `persons`"), UX spec 5's
 * Why? panel sentence, and A14's `source_table='persons'`.
 *
 * `tests/test_app_provenance.py` runs this file and reads the names it prints. Every test
 * prints `ok <name>` and the run ends with `ran <n>`, so the Python side asserts that a
 * known set actually EXECUTED rather than that the process exited 0 - a file whose tests
 * had all been deleted would exit 0 too. That module also plants the M1938 defect in a
 * COPY of `pam-trust.js` (M14 Task 1: `app.js` until then), points this driver at it
 * through `PAM_TRUST_JS` (`PAM_APP_JS` until then), and requires the run to
 * FAIL: a negative control nobody has watched fire is not a control.
 */

"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");

/* The narrowest document stub: one inert node that is its own parent. It was written for
 * `app.js` as one file, whose element map read `document.getElementById` at load. Since M14
 * Task 1 that map is in `pam-review.js` and `pam-trust.js` reads no document at load, so
 * the stub is inert here; it stays so the driver does not depend on which review script
 * holds the element map. */
const NODE = {};
NODE.parentNode = NODE;
NODE.addEventListener = function () {};
NODE.appendChild = function () {};
NODE.style = {};
NODE.classList = {
  add: function () {},
  remove: function () {},
  toggle: function () {},
  contains: function () {
    return false;
  },
};

global.document = {
  getElementById: function () {
    return NODE;
  },
};

/* M14 Task 1: `sourceWords` moved, unchanged, from `app.js` to `pam-trust.js`, and the
 * planted copy is now a copy of THAT file, through `PAM_TRUST_JS`. The browser runs the
 * review page's five classic scripts in one global scope and node gives each `require` its
 * own, so `pam-core.js`'s names (`COPY` among them) are put on `global` first - the shared
 * scope reproduced, never a planted copy: the core is read from the repository. */
const STATIC_DIR = path.join(__dirname, "..", "..", "pam", "webui", "static");
Object.assign(global, require(path.join(STATIC_DIR, "pam-core.js")));
const app = require(process.env.PAM_TRUST_JS || path.join(STATIC_DIR, "pam-trust.js"));

const tests = [];
function test(name, body) {
  tests.push([name, body]);
}

/* A card as `webui/api.py::_card` serves it: `fact_models` keyed by FACT id, holding the
 * caption model - and the COLLISION built on purpose, person 11 beside caption fact 11. */
const COLLIDING_ID = 11;
function card() {
  return { fact_models: { 11: "qwen2.5vl 7b" } };
}

function peopleToken() {
  return {
    token: "people",
    resolved_from: "people",
    source_table: "persons",
    source_id: COLLIDING_ID,
  };
}

function sceneToken() {
  return {
    token: "event-or-scene",
    resolved_from: "scene",
    source_table: "inferred_facts",
    source_id: COLLIDING_ID,
  };
}

test("a person's chip never carries a model, even when a fact shares the id", () => {
  const line = app.sourceWords(peopleToken(), card());
  assert.equal(
    line.includes("qwen2.5vl"),
    false,
    "a caption model was named as the source of a person: " + line,
  );
  assert.equal(line.includes("("), false, "a {people} token drew a model parenthesis");
});

test("the same card still names the model for the AI token beside it", () => {
  /* The other half, and the one that makes the test above a separation rather than a
   * coincidence: the map legitimately holds that id, so a mutant that simply stopped
   * reading `fact_models` at all must fail here. */
  assert.equal(
    app.sourceWords(sceneToken(), card()),
    "scene, AI enrichment (qwen2.5vl 7b)",
  );
});

test("a person's chip reads UX 5's words, not a catalog table name", () => {
  assert.equal(app.sourceWords(peopleToken(), card()), "people, a person you named");
  assert.equal(app.SOURCE_WORDS.persons, "a person you named");
});

test("a token whose source table pam does not know still renders", () => {
  const unknown = { token: "x", resolved_from: "x", source_table: "zzz", source_id: 11 };
  assert.equal(app.sourceWords(unknown, card()), "x, zzz");
});

let failures = 0;
for (const [name, body] of tests) {
  try {
    body();
    console.log("ok " + name);
  } catch (error) {
    failures += 1;
    console.log("FAIL " + name + ": " + error.message);
  }
}
console.log("ran " + tests.length);
process.exit(failures === 0 ? 0 : 1);
