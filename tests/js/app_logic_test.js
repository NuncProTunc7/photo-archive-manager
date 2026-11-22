/*
 * THE REVIEW PAGE'S SPEC RULES, EXECUTED (M12 Task 3 - `m11.md` condition C5).
 *
 * `pam/webui/static/app.js` is a program nothing in this project ran until M11 Task 12's
 * review gave it a CommonJS seam for ONE function (`tests/js/app_provenance_test.js`).
 * C5's ruling is "capability, not size": nothing is split, and every function that
 * implements a spec rule - the Why? panel's lines, the trust display, the Smart Review
 * banner, the undecided attention count, the preset filter, the count query and the
 * decided/undecided predicate - is now a pure function exported through THAT seam, so a
 * real `node` executes it. The DOM functions render what these return and decide nothing.
 *
 * THE EXPECTATIONS ARE SPELLED FROM THE SPEC, never read back out of `app.js`:
 *   - UX spec 5a's two banner sentences, VERBATIM, singular and plural, and step 3's.
 *   - UX spec 5's Why? panel (`Overall trust 96% — nothing lowered it`) and rev 8's edited
 *     heading (`pam proposed 2026-07-25_001.jpeg — Good · 75% · ! Low`).
 *   - Design spec 6.4 rev 14 / UX 5: the bar's colour follows the TIER and agrees with the
 *     tier badge - read against `style.css`'s `[data-band=…]` and `[data-tier=…]` rules,
 *     an INDEPENDENT artefact, and against UX 5/7's green / amber / red (`--ok`, `--warn`,
 *     `--danger`), never against `TIER_BANDS`.
 *   - Design spec 8.2's Low-tier contract as the page states it: a preset never names
 *     `tier: "low"` and decides only what is undecided (`status: "proposed"`).
 *
 * `tests/test_app_logic.py` runs this file and reads the names it prints. Every test
 * prints `ok <name>` and the run ends with `ran <n>`, so the Python side asserts that a
 * known set actually EXECUTED rather than that the process exited 0.
 */

"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

/* The same one-node document stub `app_provenance_test.js` uses. Since M14 Task 2 Step 6b no
 * script required here reads `document` at load (the card page, whose element map did, is
 * gone); the stub stays as the shared harness. `app.js`, the boot, is never required. */
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

const STATIC_DIR = path.join(__dirname, "..", "..", "pam", "webui", "static");
/* M14 Task 1: the subjects moved, unchanged, out of `app.js` - the trust display and the
 * Why? panel into `pam-trust.js`, the banner, presets, count query and card predicates into
 * `pam-review.js`. `app` is the seams together, so every check below reads exactly as it
 * did. A planted copy of `pam-trust.js` goes in through `PAM_TRUST_JS`. (Step 6b retired
 * `pam-review.js`; its last pure functions had moved to `pam-core.js` and `pam-list.js`.)
 * The browser runs the five review scripts in ONE global scope and node gives each
 * `require` its own, so `pam-core.js`'s names (`COPY` among them) are put on `global` first:
 * the shared scope reproduced. */
Object.assign(global, require(path.join(STATIC_DIR, "pam-core.js")));
/* M14 Task 2 Step 6b: the decision's words and the look-alike line moved, unchanged, into
 * `pam-core.js`, and Accept all's body and sentence into `pam-list.js`; `app` gains those two
 * seams, so every check below still reads `app.<name>`. */
const app = Object.assign(
  {},
  require(path.join(STATIC_DIR, "pam-core.js")),
  require(process.env.PAM_TRUST_JS || path.join(STATIC_DIR, "pam-trust.js")),
  require(path.join(STATIC_DIR, "pam-list.js")),
);

/* The stylesheet is read from the REPOSITORY even when `PAM_TRUST_JS` points at a planted
 * copy: it is the independent artefact the band rule is checked against. */
const STYLE = fs.readFileSync(path.join(STATIC_DIR, "style.css"), "utf-8");

/* The colour variable a `style.css` rule gives, or `null` if the stylesheet has no such
 * rule. A real match over the source, not a guess: a band with no rule renders a
 * TRANSPARENT fill (`.trust-fill` has no base background). */
function cssVar(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = STYLE.match(
    new RegExp(escaped + "\\s*\\{\\s*background:\\s*var\\(--([a-z-]+)\\);"),
  );
  return match ? match[1] : null;
}

function barColour(band) {
  return cssVar('.trust[data-band="' + band + '"] .trust-fill');
}

function badgeColour(tier) {
  return cssVar('.tier-badge[data-tier="' + tier + '"]::before');
}

/* UX spec 5: High = green, Medium = amber, Low = red. UX spec 7 names those colours
 * `--ok`, `--warn` and `--danger`. Spelled here, from the spec. */
const TIER_COLOURS = { high: "ok", medium: "warn", low: "danger" };

const tests = [];
function test(name, body) {
  tests.push([name, body]);
}

/* --- M14 Task 2 Step 6b: the card page's rules are GONE from the review page ------------- *
 *
 * These five pure functions were the card page's (`pam-review.js`): UX 5a's Smart Review
 * banner and the undecided count it was reached by (U8's W3/W4 callout replaced it, and
 * `tests/js/list_test.js` executes that), and the two presets' scope, their refusal and the
 * count query their buttons carried (U2: the preset BUTTONS leave the page; the preset ROUTE
 * stays, and `tests/test_webui_*.py` still drive it). Seventeen checks of them were retired
 * with the file; each function's checks are rewritten into ONE negative pin below, proving
 * the function is in no review script's seam and defined in none of the scripts the page
 * loads. The names are spelled here, never read from a list in production. */

const PAGE_HTML = fs.readFileSync(path.join(STATIC_DIR, "index.html"), "utf-8");
const PAGE_SCRIPTS = (
  PAGE_HTML.replace(/<!--[\s\S]*?-->/g, "").match(/<script\b[^>]*\bsrc="([^"]+)"/g) || []
).map((tag) => tag.match(/src="([^"]+)"/)[1]);

function assertGoneFromThePage(name) {
  assert.ok(PAGE_SCRIPTS.length >= 7, "index.html lists " + PAGE_SCRIPTS.length + " scripts");
  assert.equal(PAGE_SCRIPTS.includes("pam-review.js"), false, "the card page is loaded again");
  assert.equal(fs.existsSync(path.join(STATIC_DIR, "pam-review.js")), false);
  assert.equal(name in app, false, name + " is on a review script's node seam");
  for (const file of PAGE_SCRIPTS) {
    const code = fs.readFileSync(path.join(STATIC_DIR, file), "utf-8");
    assert.equal(code.includes("function " + name + "("), false, file + " defines " + name);
  }
}

test("the Smart Review banner's sentence is gone from the review page", () => {
  assertGoneFromThePage("smartBannerText");
});

test("the banner's undecided-count fallback is gone from the review page", () => {
  assertGoneFromThePage("attentionTodoOf");
});

test("the presets' scope is gone from the review page", () => {
  assertGoneFromThePage("presetFilter");
});

test("the presets' refusal is gone from the review page", () => {
  assertGoneFromThePage("presetRefusal");
});

test("the card page's count query is gone from the review page", () => {
  assertGoneFromThePage("scopeQuery");
});

/* --- design spec 6.4 rev 14 / UX 5: the trust display ----------------------------------- */

test("each tier's bar takes the colour UX 5 gives it and the badge beside it agrees", () => {
  for (const tier of Object.keys(TIER_COLOURS)) {
    const view = app.trustView({ tier: tier, trust_label: "x", trust_score: 50 });
    assert.equal(barColour(view.band), TIER_COLOURS[tier], "bar colour for " + tier);
    assert.equal(badgeColour(tier), TIER_COLOURS[tier], "badge colour for " + tier);
  }
});

test("an unknown tier's bar is amber, never transparent and never red", () => {
  const view = app.trustView({ tier: "zzz", trust_label: "x", trust_score: 90 });
  assert.equal(barColour(view.band), "warn");
});

test("the label is the server's trust_label and the number is the score", () => {
  const view = app.trustView({ tier: "medium", trust_label: "Good", trust_score: 75 });
  assert.equal(view.label, "Good");
  assert.equal(view.percent, "75%");
  assert.equal(view.width, "75%");
});

test("the bar's width is clamped to the bar while the number is not", () => {
  const over = app.trustView({ tier: "high", trust_label: "High trust", trust_score: 130 });
  assert.equal(over.width, "100%");
  assert.equal(over.percent, "130%");
  const under = app.trustView({ tier: "low", trust_label: "Needs review", trust_score: -5 });
  assert.equal(under.width, "0%");
});

test("the badge carries UX 5's symbol and word for every tier", () => {
  assert.deepEqual(app.trustView({ tier: "high", trust_score: 1 }).badge, {
    tier: "high",
    text: "✓ High",
  });
  assert.deepEqual(app.trustView({ tier: "medium", trust_score: 1 }).badge, {
    tier: "medium",
    text: "~ Medium",
  });
  assert.deepEqual(app.trustView({ tier: "low", trust_score: 1 }).badge, {
    tier: "low",
    text: "! Low",
  });
});

test("an edited name carries no label, no score, no bar and no badge", () => {
  /* UX 5 rev 8: pam did not derive that name, so a score attached to it is a false claim. */
  assert.deepEqual(
    app.trustView({ edited: true, tier: "high", trust_label: "High trust", trust_score: 96 }),
    { edited: true },
  );
});

/* --- UX spec 5: the Why? panel --------------------------------------------------------- */

function sceneCard(extra) {
  return Object.assign(
    {
      tier: "high",
      trust_label: "High trust",
      trust_score: 96,
      generated_filename: "2019-07-04_Fireworks_001.jpg",
      tokens: [
        { token: "date", resolved_from: "date", value: "2019-07-04", source_table: "capture" },
        {
          token: "event-or-scene",
          resolved_from: "scene",
          value: "Fireworks",
          source_table: "inferred_facts",
          source_id: 11,
        },
      ],
      trust_reasons: [],
      fact_models: { 11: "qwen2.5vl 7b" },
    },
    extra || {},
  );
}

test("an edited item's Why? panel leads with UX 5 rev 8's proposed heading", () => {
  const why = app.whyLines({
    edited: true,
    tier: "low",
    trust_label: "Good",
    trust_score: 75,
    generated_filename: "2026-07-25_001.jpeg",
    tokens: [],
    trust_reasons: ["x"],
  });
  assert.equal(why.proposed, "pam proposed 2026-07-25_001.jpeg — Good · 75% · ! Low");
  assert.equal(why.restore, "Press ↺ to put pam's name back.");
});

test("a name pam proposed has no proposed heading and no restore line", () => {
  const why = app.whyLines(sceneCard());
  assert.equal(why.proposed, null);
  assert.equal(why.restore, null);
});

test("each line names the value and its source, in the planner's order", () => {
  assert.deepEqual(app.whyLines(sceneCard()).tokens, [
    { value: "2019-07-04", source: "date, read from the photo" },
    { value: "Fireworks", source: "scene, AI enrichment (qwen2.5vl 7b)" },
  ]);
});

test("with no reason the total says nothing lowered it", () => {
  assert.equal(app.whyLines(sceneCard()).total, "Overall trust 96% — nothing lowered it");
});

test("negative reasons appear in the same list and the total stops claiming nothing", () => {
  const why = app.whyLines(
    sceneCard({ trust_score: 71, trust_reasons: ["Date read from file modification time −25"] }),
  );
  assert.deepEqual(why.reasons, ["Date read from file modification time −25"]);
  assert.equal(why.total, "Overall trust 71%");
});

test("a truncated token's source says it was shortened to fit", () => {
  const card = sceneCard();
  card.tokens[1].truncated = true;
  const lines = app.whyLines(card).tokens;
  assert.equal(lines[1].source, "scene, AI enrichment (qwen2.5vl 7b) (shortened to fit)");
  assert.equal(lines[0].source, "date, read from the photo");
});

test("a person's Why? line never names the model of a fact that shares its id", () => {
  const card = sceneCard();
  card.tokens.push({
    token: "people",
    resolved_from: "people",
    value: "Emma",
    source_table: "persons",
    source_id: 11,
  });
  assert.deepEqual(app.whyLines(card).tokens[2], {
    value: "Emma",
    source: "people, a person you named",
  });
});

test("the confidence note is the server's sentence verbatim, and absent when it is", () => {
  const note = "The captioner reports no confidence, so this name is at most Good.";
  assert.equal(app.whyLines(sceneCard({ confidence_note: note })).confidence, note);
  assert.equal(app.whyLines(sceneCard()).confidence, null);
});

test("a malformed token list renders no lines rather than failing", () => {
  assert.deepEqual(app.whyLines(sceneCard({ tokens: null })).tokens, []);
  assert.deepEqual(app.whyLines(sceneCard({ tokens: [null, 3, "x"] })).tokens, []);
});

/* --- M13 Task 2 (F1 R2): Accept all ------------------------------------------------------ */

function view(extra) {
  return Object.assign({ group: "", tier: "", status: "", attention: false, q: "" }, extra);
}

/* F1 R2: "every item in the plan" - the body carries the action and the flag and NOTHING
 * of the view, compared to a literal written here. The page hands it the view it is
 * showing, so a scope that leaked a filter field has one to leak - every field set. */
test("Accept all's body is exactly the action and the flag, whatever the view", () => {
  const busy = view({ group: "2019-07", tier: "high", status: "skipped", attention: true, q: "x" });
  for (const filters of [view(), busy]) {
    assert.deepEqual(app.acceptAllScope(filters), { action: "approve", accept_all: true });
  }
});

test("Accept all's report names the low count only when there is one", () => {
  assert.equal(
    app.acceptAllReport({ decided: 397, excluded_low: 2, note: "" }),
    "Accepted 397. 2 low-confidence name(s) were not accepted - approve them one at a time.",
  );
  assert.equal(app.acceptAllReport({ decided: 5, excluded_low: 0, note: "" }), "Accepted 5.");
  assert.equal(
    app.acceptAllReport({ decided: 5, excluded_low: 0, note: "Also decided 1 other file." }),
    "Accepted 5. Also decided 1 other file.",
  );
});

/* --- UX spec 5 rev 8: decided and undecided --------------------------------------------- */

test("approved and skipped are decisions and nothing else is", () => {
  assert.equal(app.isDecided({ status: "approved" }), true);
  assert.equal(app.isDecided({ status: "skipped" }), true);
  assert.equal(app.isDecided({ status: "proposed" }), false);
  assert.equal(app.isDecided({ status: "applied" }), false);
});

test("the status note names the decision, and anything else reads as not decided", () => {
  assert.equal(app.statusNote({ status: "approved" }), "Approved");
  assert.equal(app.statusNote({ status: "skipped" }), "Skipped");
  assert.equal(app.statusNote({ status: "proposed" }), "Not decided yet");
});

/* --- M13 amendment H3: the card's look-alike line --------------------------------------- */

/* Spelled from H3's sentence, "Looks like N other photo(s) in this plan: a, b, and N more.",
 * read as its singular and its plural - never from `lookAlikeLine`'s own source. */
test("the look-alike line names one other photo in the singular", () => {
  assert.equal(
    app.lookAlikeLine({ look_alikes: ["2019/a.jpg"], look_alikes_more: 0 }),
    "Looks like 1 other photo in this plan: 2019/a.jpg.",
  );
});

test("the look-alike line names several in the plural and counts the rest", () => {
  assert.equal(
    app.lookAlikeLine({ look_alikes: ["a.jpg", "b.jpg"], look_alikes_more: 0 }),
    "Looks like 2 other photos in this plan: a.jpg, b.jpg.",
  );
  assert.equal(
    app.lookAlikeLine({
      look_alikes: ["a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg"],
      look_alikes_more: 2,
    }),
    "Looks like 7 other photos in this plan: a.jpg, b.jpg, c.jpg, d.jpg, e.jpg, and 2 more.",
  );
});

test("a card with no look-alikes has no line", () => {
  assert.equal(app.lookAlikeLine({ look_alikes: [], look_alikes_more: 0 }), null);
  assert.equal(app.lookAlikeLine({}), null);
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
