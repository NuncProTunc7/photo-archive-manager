/* The review page's trust display and provenance (M14 Task 1): the tier's band and
 * badge, the trust view, the Why? panel's lines and the provenance words beside each
 * token. Moved out of `app.js` unchanged; `pam-core.js` explains the five-script split.
 *
 * NOTHING HERE RECOMPUTES A NAME, A TIER OR A SCORE. `trust_label`, `edited`,
 * `fact_models` and `confidence_note` arrive from the server (spec 8.2 rev 14 / rev 22),
 * and what is left here is presentation only.
 */

"use strict";

/* UX spec 5 rev 8: the bar's COLOUR follows the tier, so it agrees with the tier badge
 * beside it instead of arguing with it. The page holds NO band table - the trust WORD
 * arrives from the server as `trust_label` (spec 8.2 rev 14), because a contract in this
 * file is a contract no tool in this project lints, types, executes or can mutate. What
 * is left here is presentation only: which of style.css's three colours the bar takes. */
var TIER_BANDS = { high: "ok", medium: "warn", low: "danger" };

/* UX spec 5: High = green tick, Medium = amber tilde, Low = red bang - and colour is
 * never the sole signal, so the symbol and the word both travel with the dot. */
var TIER_BADGES = {
  high: { symbol: "✓", word: "High" },
  medium: { symbol: "~", word: "Medium" },
  low: { symbol: "!", word: "Low" },
};

/* UX spec 8: no jargon leaks. `source_table` is a catalog table name; these are the
 * plain-language readings of the ones the planner writes. An unknown value falls
 * through as itself rather than being hidden. */
var SOURCE_WORDS = {
  capture: "read from the photo",
  files: "read from the file",
  planner: "added by pam",
  /* M10 Task 10. `sourceWords` appends the MODEL from `fact_models` after this, so the
   * line reads `Fireworks ← scene, AI enrichment (qwen2.5vl 7b)` - UX spec 5's example
   * sentence, with the model named at review (vision Q3 rev 4). The words are here and
   * the model is not: `fact_models` is a served field (a CARD's, since spec 8.2 rev 22)
   * for spec 8.2's reason. */
  inferred_facts: "AI enrichment",
  /* M11 Task 12 (UX spec 5's Why? panel). A `{people}` token names a person a HUMAN
   * attached to a face - never a model - so it gets words and no model parenthesis. */
  persons: "a person you named",
};

/* --- trust, tiers and provenance ----------------------------------------------------- */

/* UX spec 5: `<label> · <percent>` plus a small filled bar - and rev 8: an EDITED name
 * carries none of it. pam did not derive that name, has no provenance for it, and a
 * score attached to it is a false claim of objectivity (vision principle 2; spec 6.1
 * rev 6 - provenance that does not match the artifact is worse than no provenance). The
 * planner's breakdown is not lost; `whyPanel` carries it, named as pam's proposal.
 *
 * `item.edited` is the SERVER's predicate, read here and by the `↺` control in
 * `buildCard`. Deriving it twice in this file is how the button and the display come to
 * disagree, which is why spec 8.2 rev 14 serves it. */
/* M12 Task 3 (C5): the RULE, as plain data in and plain data out, so `node` executes it
 * (`tests/js/app_logic_test.js`). `trustDisplay` below renders what this returns and
 * decides nothing. */
function trustView(item) {
  if (item.edited) {
    return { edited: true };
  }
  var known = TIER_BADGES[item.tier] || { symbol: "?", word: item.tier };
  return {
    /* The `|| "warn"` is not "the cautious middle colour". `.trust-fill` has NO base
     * background, so an absent `data-band` renders a TRANSPARENT fill - a bar reading
     * empty beside a percentage saying 90%, which is two channels contradicting each
     * other, the exact defect class P2 was. `danger` would be wrong for the opposite
     * reason: red is `low`'s colour and an unknown tier is not low. `plan_items.tier`
     * carries no CHECK constraint, so an unknown value is reachable, not theoretical. */
    band: TIER_BANDS[item.tier] || "warn",
    label: item.trust_label,
    percent: item.trust_score + "%",
    width: Math.max(0, Math.min(100, item.trust_score)) + "%",
    badge: { tier: item.tier, text: known.symbol + " " + known.word },
  };
}

function trustDisplay(item) {
  var wrap = make("div", "trust");
  var view = trustView(item);
  if (view.edited) {
    wrap.setAttribute("data-edited", "true");
    wrap.appendChild(make("span", "trust-edited", COPY.editedByYou));
    return wrap;
  }
  wrap.setAttribute("data-band", view.band);
  wrap.appendChild(make("span", "trust-label", view.label));
  wrap.appendChild(make("span", "trust-sep", "·"));
  wrap.appendChild(make("span", "trust-percent", view.percent));
  var bar = make("span", "trust-bar");
  var fill = make("span", "trust-fill");
  fill.style.width = view.width;
  bar.appendChild(fill);
  wrap.appendChild(bar);
  wrap.appendChild(tierBadge(view.badge));
  return wrap;
}

function tierBadge(known) {
  var badge = make("span", "tier-badge", known.text);
  badge.setAttribute("data-tier", known.tier);
  return badge;
}

function tokenList(item) {
  return Array.isArray(item.tokens) ? item.tokens : [];
}

function reasonList(item) {
  return Array.isArray(item.trust_reasons) ? item.trust_reasons : [];
}

/* THE MODEL COMES FROM THE ITEM, not from page-level state (spec 8.2 rev 22 / A17). Both
 * callers already hold the card they are rendering, and a card that carries its own map
 * cannot lose its model name when the next page arrives. */
function sourceWords(token, item) {
  var table = token.source_table;
  var words = SOURCE_WORDS[table] || table || "source not recorded";
  var name = token.resolved_from || token.token || "part of the name";
  var models = (item && item.fact_models) || {};
  /* FOR AN `inferred_facts` TOKEN ONLY (spec 8.2 rev 22). `fact_models` is keyed by fact id,
   * and a `{people}` token's `source_id` is a PERSON id: on a card holding a caption fact
   * 11 and a person 11, an unguarded lookup names the caption model as the source of
   * somebody's child. `tests/test_webui_api.py` pins this line, since nothing runs it. */
  var model = table === "inferred_facts" ? models[token.source_id] : null;
  return name + ", " + words + (model ? " (" + model + ")" : "");
}

/* UX spec 5's [Why?] breakdown: each line names a value and its source, and the
 * planner's negative reasons appear in the same list.
 *
 * The video sentence UX spec 5 rev 3 asks for - "Video time is UTC, local date may
 * differ" - is NOT written here. `naming/consistency.py` already emits it into
 * `trust_reasons_json` for exactly the items it applies to, so it arrives with the
 * reasons below. Writing a second copy in the page would be this project's recurring
 * mistake: a copy that has to be kept in step with a sentence pam already owns. */
/* UX spec 5 rev 8's heading for an edited item's Why? panel:
 * `pam proposed 2026-07-25_001.jpeg — Good · 75% · ! Low`. The whole trust display the
 * card face may no longer show, in one sentence that says WHOSE name it describes, plus
 * the proposed name itself - so the user can see what `↺` would restore without pressing
 * it. The tier word and symbol come from `TIER_BADGES`, the same table the badge uses. */
function proposedHeading(item) {
  var badge = TIER_BADGES[item.tier] || { symbol: "?", word: item.tier };
  return (
    COPY.proposedLead +
    item.generated_filename +
    " — " +
    item.trust_label +
    " · " +
    item.trust_score +
    "% · " +
    badge.symbol +
    " " +
    badge.word
  );
}

/* M12 Task 3 (C5): the Why? panel's LINES, as plain data, so `node` executes the rule
 * (`tests/js/app_logic_test.js`). `whyPanel` below renders what this returns, in this
 * order, and decides nothing. A field that does not apply is `null`. */
function whyLines(item) {
  var lines = [];
  var tokens = tokenList(item);
  for (var i = 0; i < tokens.length; i += 1) {
    var token = tokens[i];
    if (!token || typeof token !== "object") {
      continue;
    }
    var source = sourceWords(token, item);
    lines.push({
      value: token.value || "",
      source: token.truncated ? source + " (shortened to fit)" : source,
    });
  }
  var reasons = reasonList(item);
  var reasonTexts = [];
  for (var r = 0; r < reasons.length; r += 1) {
    reasonTexts.push(String(reasons[r]));
  }
  return {
    proposed: item.edited ? proposedHeading(item) : null,
    tokens: lines,
    reasons: reasonTexts,
    total:
      reasons.length === 0
        ? "Overall trust " + item.trust_score + "% — nothing lowered it"
        : "Overall trust " + item.trust_score + "%",
    /* Vision Q3 rev 4's second half, rendered VERBATIM: where the model behind an
     * AI-derived token emits no confidence, the server says so and names the tier that
     * produced. The page holds no band table and composes no sentence - `webui/provenance.py`
     * owns the copy, for the reason `trust_label` is a response field. */
    confidence: item.confidence_note ? item.confidence_note : null,
    /* UX spec 5 rev 8's last line for an edited item. The `↺` control lives on the card
     * face, and the Why? panel is where the user learns what it would do - so the sentence
     * that names it belongs here, at the end of the breakdown it would restore. */
    restore: item.edited ? COPY.restoreHint : null,
  };
}

function whyPanel(item) {
  var panel = make("div", "why");
  var why = whyLines(item);
  if (why.proposed !== null) {
    panel.appendChild(make("p", "why-proposed", why.proposed));
  }
  var list = make("ul");
  for (var i = 0; i < why.tokens.length; i += 1) {
    var line = make("li");
    line.appendChild(make("span", "why-value", why.tokens[i].value));
    line.appendChild(make("span", "why-arrow", "←"));
    line.appendChild(make("span", "why-source", why.tokens[i].source));
    list.appendChild(line);
  }
  for (var r = 0; r < why.reasons.length; r += 1) {
    var reasonLine = make("li");
    reasonLine.appendChild(make("span", "why-source", why.reasons[r]));
    list.appendChild(reasonLine);
  }
  panel.appendChild(list);
  panel.appendChild(make("p", "why-total", why.total));
  if (why.confidence !== null) {
    panel.appendChild(make("p", "why-confidence", why.confidence));
  }
  if (why.restore !== null) {
    panel.appendChild(make("p", "why-restore", why.restore));
  }
  return panel;
}

/* --- the seams: the page's namespace, and node's -------------------------------------- */

/* `window.PAM` is created by `pam-core.js`, which `index.html` loads before this file. No
 * fallback on purpose: loaded out of order, this line fails, and the load-order harness
 * sees it (`tests/js/load_order_test.js`). */
if (typeof window !== "undefined") {
  window.PAM.trust = {
    SOURCE_WORDS: SOURCE_WORDS,
    sourceWords: sourceWords,
    whyLines: whyLines,
    trustView: trustView,
  };
}

/* THE COMMONJS SEAM `tests/js/app_provenance_test.js` and `app_logic_test.js` REACH
 * THROUGH (M11 Task 12 review, F3; M12 Task 3, C5). A grep cannot survive an edit to the
 * text it greps for, so a real `node` EXECUTES these. Keyed on `module`: in a browser it
 * is undefined and nothing here runs. */
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    SOURCE_WORDS: SOURCE_WORDS,
    sourceWords: sourceWords,
    /* M12 Task 3 (C5): every function that implements a spec rule, executed by
     * `tests/js/app_logic_test.js`. */
    whyLines: whyLines,
    trustView: trustView,
  };
}
