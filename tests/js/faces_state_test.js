/*
 * THE LABELING PAGE'S STATE TRANSITIONS, EXECUTED (M11 Task 10, Step 8 - Blocker 8.4).
 *
 * WHY THIS FILE IS NOT PYTHON. `pam/webui/static/faces.js` is a program, and until this
 * file existed nothing in this project ran one. `node --check` in
 * `tests/test_static_syntax.py` proves a script PARSES - the M783 defect, one unclosed
 * brace over 1862 green tests - and says nothing whatever about whether a transition
 * works. The plan's Blocker 8.4 is exactly that gap: "Syntax validity cannot prove that
 * undo works." So the consequential transitions are pure functions in `faces.js`, and
 * this file executes them under a real `node`.
 *
 * THE INSTRUMENT THAT MATTERS IS THE RECORDING TRANSPORT, and CLAUDE.md's M697 is why. A
 * test that asserts the OUTCOME cannot tell "never did the dangerous thing" from "did it
 * and left no trace". The dangerous thing on this page is WRITING A NAME NOBODY TYPED
 * (vision R6), and a rendered empty field is consistent with a POST that already
 * happened. `calls` below is the watch on the call itself: the load path is required to
 * have issued exactly two GETs and NO POST, which reading any state could not establish.
 *
 * THE EXPECTATIONS ARE SPELLED FROM THE SPECS, never from `faces.js`'s own constants -
 * `repo.ENRICHERS`'s lesson. UX spec 6's done sentence is typed out here in full; the
 * biggest-first order is asserted against a payload this file builds in a known order;
 * the rule 7 sentence is written from spec 6.2 rule 7's requirement (both numbers) rather
 * than from `labelSentence`.
 *
 * `tests/test_faces_page.py` runs this file and reads the names it prints. Every test
 * prints `ok <name>` and the run ends with `ran <n>`, so the Python side can assert that
 * a known set actually EXECUTED rather than that the process happened to exit 0 - a file
 * whose tests were all deleted would exit 0 too.
 */

"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");

/* The shipped script, unless a caller names another copy. THE OVERRIDE IS THE NEGATIVE
 * CONTROL'S SEAM, not a convenience: `tests/test_faces_page.py` plants a defect in a COPY
 * of `faces.js` in a temporary directory and requires this file to fail over it. Without
 * that, "the Node driver passes" would be a claim about a driver nobody has watched fail,
 * which is the shape this project keeps measuring. Nothing on disk is ever touched. */
const faces = require(
  process.env.PAM_FACES_JS ||
    path.join(__dirname, "..", "..", "pam", "webui", "static", "faces.js")
);

/* --- the harness ------------------------------------------------------------------ */

const tests = [];
function test(name, body) {
  tests.push([name, body]);
}

/* --- fixtures: payloads shaped exactly as `faces_api.py` serves them --------------- */

function face(id, filename) {
  return { face_id: id, file_id: id, filename: filename, confidence: 0.9 };
}

/* Three groups in the order `/faces/clusters` serves them: LARGEST FIRST (spec 8.2). */
function clustersPayload() {
  return {
    clusters: [
      {
        cluster_id: 7,
        faces: 40,
        photographs: 31,
        sample: [face(1, "a.HEIC"), face(2, "b.HEIC")],
      },
      { cluster_id: 3, faces: 9, photographs: 9, sample: [face(3, "c.HEIC")] },
      { cluster_id: 5, faces: 2, photographs: 1, sample: [face(4, null)] },
    ],
    offset: 0,
    limit: 50,
    total: 3,
    named: 7,
    remaining: 3,
  };
}

function personsPayload() {
  return {
    persons: [
      {
        id: 1,
        label: "Emma",
        filename_label: null,
        filename_value: "Emma",
        hidden: false,
        faces: 12,
        photographs: 8,
      },
    ],
  };
}

/* A transport that RECORDS every call and answers from a table. The default answers are
 * the read routes; a test that wants a refusal or a dead server overrides them. */
function recorder(overrides) {
  const answers = Object.assign(
    {
      "GET api/faces/clusters": () => clustersPayload(),
      "GET api/faces/persons": () => personsPayload(),
    },
    overrides || {}
  );
  const calls = [];
  function request(method, path, body) {
    calls.push({ method: method, path: path, body: body });
    const key = method + " " + path;
    const answer = answers[key];
    if (answer === undefined) {
      return Promise.reject(
        new faces.FacesRefusal("no stub for " + key + " - the page called a route nobody expected")
      );
    }
    try {
      return Promise.resolve(answer(body));
    } catch (thrown) {
      return Promise.reject(thrown);
    }
  }
  return { request: request, calls: calls };
}

function methods(calls) {
  return calls.map((call) => call.method);
}

/* A clock the tests drive, so no assertion here depends on how fast this machine is.
 * CLAUDE.md's rule, learned at cost in M9: a test that measures a real clock across
 * near-instant fake work is a latent hosted failure. */
function fakeClock(start) {
  let value = start;
  return {
    now: () => value,
    advance: (seconds) => {
      value += seconds;
    },
  };
}

/* --- vision R6: pam never auto-labels, and never proposes a person ---------------- */

test("loading a group issues exactly two GETs and no write at all", async () => {
  const io = recorder();
  const app = faces.createFacesApp({ request: io.request });
  await app.load();
  assert.deepEqual(methods(io.calls), ["GET", "GET"]);
  assert.deepEqual(
    io.calls.map((call) => call.path),
    ["api/faces/clusters", "api/faces/persons"]
  );
  assert.equal(
    io.calls.filter((call) => call.method !== "GET").length,
    0,
    "the page wrote something while merely reading a group"
  );
});

test("the name field is empty even though pam knows people to autocomplete", async () => {
  const io = recorder();
  const app = faces.createFacesApp({ request: io.request });
  await app.load();
  const shown = app.view();
  assert.equal(shown.mode, "cluster");
  assert.equal(shown.nameValue, "", "pam proposed a name for an unnamed group");
  assert.deepEqual(shown.suggestions, ["Emma"], "autocomplete lost its only person");
  assert.equal(
    shown.suggestions.indexOf(shown.nameValue),
    -1,
    "the proposed value is one of the suggestions - that IS auto-labelling"
  );
});

test("a blank name never reaches the socket", async () => {
  const io = recorder();
  const app = faces.createFacesApp({ request: io.request });
  await app.load();
  const before = io.calls.length;
  await app.save("   ");
  assert.equal(io.calls.length, before, "a blank name was posted");
  assert.equal(app.view().requestError, faces.COPY.nameNeeded);
  assert.match(app.view().requestError, /never proposes/);
});

/* --- the one write, and spec 6.2 rule 7 ------------------------------------------- */

test("saving posts exactly one label, carrying the name the user typed", async () => {
  const io = recorder({
    "POST api/faces/cluster/7/label": (body) => ({
      cluster_id: 7,
      person_id: 1,
      label: body.person,
      faces: 40,
      photographs: 31,
    }),
  });
  const app = faces.createFacesApp({ request: io.request });
  await app.load();
  await app.save("  Emma  ");
  const writes = io.calls.filter((call) => call.method === "POST");
  assert.equal(writes.length, 1, "expected exactly one write");
  assert.equal(writes[0].path, "api/faces/cluster/7/label");
  assert.deepEqual(writes[0].body, { person: "Emma" });
});

test("the label note names BOTH counts, which is what rule 7 asks for", () => {
  const note = faces.labelSentence({
    cluster_id: 7,
    label: "Emma",
    faces: 40,
    photographs: 31,
  });
  /* Spelled from spec 6.2 rule 7 + vision R6: how many faces AND how many photographs. */
  assert.ok(note.includes("Emma"), note);
  assert.ok(note.includes("40 faces"), note);
  assert.ok(note.includes("31 photographs"), note);
});

test("the label note is singular where the number is one", () => {
  const note = faces.labelSentence({
    cluster_id: 5,
    label: "Ada",
    faces: 1,
    photographs: 1,
  });
  assert.ok(note.includes("1 face "), note);
  assert.ok(note.includes("1 photograph."), note);
  assert.ok(!note.includes("faces"), note);
});

test("the scope sentence discloses the same two numbers BEFORE the click", () => {
  const sentence = faces.scopeSentence({
    cluster_id: 7,
    faces: 40,
    photographs: 31,
    sample: [],
  });
  assert.ok(sentence.includes("40 faces"), sentence);
  assert.ok(sentence.includes("31 photographs"), sentence);
  assert.ok(sentence.includes("One name will be attached to all of them"), sentence);
});

test("a saved group leaves the queue and both archive counts move", async () => {
  const io = recorder({
    /* The counts are the RESPONSE's (P2): read after the write, taken as given. */
    "POST api/faces/cluster/7/label": () => ({
      cluster_id: 7,
      person_id: 1,
      label: "Emma",
      faces: 40,
      photographs: 31,
      named: 8,
      remaining: 2,
    }),
  });
  const app = faces.createFacesApp({ request: io.request });
  await app.load();
  assert.equal(app.state().named, 7);
  assert.equal(app.state().remaining, 3);
  await app.save("Emma");
  assert.equal(app.state().named, 8);
  assert.equal(app.state().remaining, 2);
  assert.equal(app.view().cluster.cluster_id, 3, "the next group is not on offer");
  assert.ok(app.view().note.includes("31 photographs"), app.view().note);
});

/* FINDING F4's KILLER, AND THE REASON THE NUMBERS BELOW ARE NOT CLUSTER 7's.
 *
 * Every label answer above returns `faces: 40, photographs: 31`, which are cluster 7's
 * LISTING counts to the digit - so `labelSentence(response)` and a `labelSentence` built
 * from the cluster the page was showing produce the identical sentence, and the whole
 * suite stayed green when `afterLabel` was switched to the second one (M1851). Two
 * readings that agree by construction cannot be separated.
 *
 * THE DIVERGENCE IS REAL. `/faces/clusters` reports the repo aggregate; `cluster/{id}/
 * label` reports `touched` and the distinct contents AFTER A11's `[tiers] faces` floor
 * and after reviewed-away faces are excluded. A cluster holding a sub-floor or a
 * previously-ignored face lists more than pam names. So the server here answers 37 across
 * 11 for a group the queue advertised as 40 across 31, and the sentence must carry the
 * server's numbers - spec 6.2 rule 7 is what pam CHANGED, not what it set out to change.
 */
test("the note carries the numbers the SERVER wrote, not the queue's advertisement", async () => {
  const io = recorder({
    "POST api/faces/cluster/7/label": () => ({
      cluster_id: 7,
      person_id: 1,
      label: "Emma",
      faces: 37,
      photographs: 11,
    }),
  });
  const app = faces.createFacesApp({ request: io.request });
  await app.load();
  assert.equal(app.view().scope.includes("40 faces"), true, "the queue advertised 40");
  await app.save("Emma");
  const note = app.view().note;
  assert.ok(note.includes("37 faces"), note);
  assert.ok(note.includes("11 photographs"), note);
  assert.ok(!note.includes("40 faces"), `the note reports the page's belief: ${note}`);
  assert.ok(!note.includes("31 photographs"), `the note reports the page's belief: ${note}`);
});

test("a refusal changes nothing but the sentence - the group stays on offer", async () => {
  const io = recorder({
    "POST api/faces/cluster/7/label": () => {
      throw new faces.FacesRefusal("pam was about to name 40 faces and found 39;"
        + " nothing was changed. Reload the page.");
    },
  });
  const app = faces.createFacesApp({ request: io.request });
  await app.load();
  await app.save("Emma");
  assert.equal(app.view().cluster.cluster_id, 7, "a refused write removed the group anyway");
  assert.equal(app.state().named, 7, "a refused write moved the named count");
  assert.equal(app.state().remaining, 3, "a refused write moved the remaining count");
  assert.equal(app.view().note, "", "a refused write reported something it changed");
  assert.match(app.view().requestError, /nothing was changed/);
});

test("a transport failure makes the page inert and is not a refusal", async () => {
  const io = recorder({
    "GET api/faces/persons": () => {
      throw new Error("socket closed");
    },
  });
  const app = faces.createFacesApp({ request: io.request });
  await app.load();
  assert.equal(app.state().stopped, true, "the dead server did not stop the page");
  assert.equal(app.view().mode, "stopped");
  const after = io.calls.length;
  await app.save("Emma");
  assert.ok(io.calls.length >= after, "sanity");
});

/* --- the queue: biggest first, and Skip is session state -------------------------- */

test("the queue keeps the server's order and the page never re-sorts it", async () => {
  const io = recorder();
  const app = faces.createFacesApp({ request: io.request });
  await app.load();
  /* Spelled from the fixture, which is written LARGEST FIRST as spec 8.2 serves it. */
  assert.deepEqual(
    faces.visibleClusters(app.state()).map((cluster) => cluster.cluster_id),
    [7, 3, 5]
  );
  assert.equal(app.view().cluster.cluster_id, 7, "the biggest group is not offered first");
});

test("skip issues no request, moves on, and is reversible in the same visit", async () => {
  const io = recorder();
  const app = faces.createFacesApp({ request: io.request });
  await app.load();
  const before = io.calls.length;
  app.skip();
  assert.equal(io.calls.length, before, "Skip wrote something - it is session state");
  assert.equal(app.view().cluster.cluster_id, 3);
  app.restoreSkipped();
  assert.equal(app.view().cluster.cluster_id, 7, "a skipped group did not come back");
});

test("skipping every group is NOT the done state", async () => {
  const io = recorder();
  const app = faces.createFacesApp({ request: io.request });
  await app.load();
  app.skip();
  app.skip();
  app.skip();
  assert.equal(app.view().mode, "skipped", "a page of skipped groups reported success");
  assert.equal(app.view().cluster, null);
  assert.equal(app.state().remaining, 3, "skipping moved an archive-wide count");
});

test("an archive nobody has clustered is the empty state, not the done state", () => {
  const state = faces.withClusters(faces.initialState(), {
    clusters: [],
    offset: 0,
    limit: 50,
    total: 0,
    named: 0,
    remaining: 0,
  });
  assert.equal(faces.view(state).mode, "empty");
});

/* FINDING F5's KILLER: the `empty` branch's THIRD OPERAND, `remaining === 0`.
 *
 * `viewMode`'s empty test is `clusters.length === 0 && remaining === 0 && named === 0`,
 * and dropping the middle operand left the whole suite green (M1866). The test above
 * cannot reach it - it sets all three to their zero - and neither can the one below,
 * which moves `named`.
 *
 * The state it misses is ordinary. `/faces/clusters` hides a cluster whose every face
 * sits below A11's `[tiers] faces` floor or has been reviewed away, while `remaining` is
 * the ARCHIVE-WIDE unnamed count. So an archive where `pam faces cluster` HAS run and
 * left four unnamed groups, none of them currently offerable, serves `clusters: []`,
 * `named: 0`, `remaining: 4` - and under the mutant the page tells that user their
 * archive has never been clustered and to go and run the command they just ran.
 */
test("an archive with unnamed faces it cannot offer is NOT the fresh-archive state", () => {
  const state = faces.withClusters(faces.initialState(), {
    clusters: [],
    offset: 0,
    limit: 50,
    total: 0,
    named: 0,
    remaining: 4,
  });
  assert.notEqual(
    faces.view(state).mode,
    "empty",
    "clustering has run: `remaining` is 4, so this archive is not a fresh one"
  );
  assert.equal(faces.view(state).mode, "done");
});

test("an archive whose every group is named IS the done state", () => {
  const state = faces.withClusters(faces.initialState(), {
    clusters: [],
    offset: 0,
    limit: 50,
    total: 0,
    named: 23,
    remaining: 0,
  });
  assert.equal(faces.view(state).mode, "done");
});

/* --- the progress line (UX spec 6) ------------------------------------------------ */

test("the progress line names the two counts UX 6 draws", () => {
  const state = faces.withClusters(faces.initialState(), {
    clusters: [],
    offset: 0,
    limit: 50,
    total: 0,
    named: 7,
    remaining: 16,
  });
  const line = faces.progressLine(state);
  assert.ok(line.includes("7 named"), line);
  assert.ok(line.includes("16 remaining"), line);
  assert.ok(line.includes("est."), line);
});

test("the estimate is seeded, then MEASURED once a group has been handled", () => {
  const seeded = faces.estimateSeconds(10, []);
  assert.equal(seeded, 10 * faces.SECONDS_PER_CLUSTER);
  /* Two groups that each took 60 s: the estimate is about this user, not the constant. */
  const measured = faces.estimateSeconds(10, [60, 60]);
  assert.equal(measured, 600);
  assert.notEqual(measured, seeded, "the measurement did not displace the seed");
});

test("the estimate claims no precision it has not got", () => {
  assert.equal(faces.estimateText(0), "");
  assert.equal(faces.estimateText(-5), "");
  assert.equal(faces.estimateText(30), "est. under a minute");
  assert.equal(faces.estimateText(180), "est. 3 min");
});

test("a saved group's real duration reaches the estimate", async () => {
  const clock = fakeClock(1000);
  const io = recorder({
    "POST api/faces/cluster/7/label": () => ({
      cluster_id: 7,
      person_id: 1,
      label: "Emma",
      faces: 40,
      photographs: 31,
    }),
  });
  const app = faces.createFacesApp({ request: io.request, now: clock.now });
  await app.load();
  clock.advance(45);
  await app.save("Emma");
  assert.deepEqual(app.state().durations, [45]);
  assert.equal(faces.estimateSeconds(2, app.state().durations), 90);
});

test("the position line counts through the queue", async () => {
  const io = recorder();
  const app = faces.createFacesApp({ request: io.request });
  await app.load();
  assert.equal(faces.positionLine(app.state()), "Group 1 of 3");
  app.skip();
  assert.equal(faces.positionLine(app.state()), "Group 2 of 3");
});

/* --- crops (spec 8.2, I4) --------------------------------------------------------- */

test("expanding swaps the server's sample for every face in the group", async () => {
  const io = recorder({
    "GET api/faces/cluster/7": () => ({
      cluster_id: 7,
      faces: [face(1, "a.HEIC"), face(2, "b.HEIC"), face(9, "z.HEIC")],
      photographs: 3,
    }),
  });
  const app = faces.createFacesApp({ request: io.request });
  await app.load();
  assert.equal(app.view().faces.length, 2, "the sample is not what is drawn first");
  await app.expand();
  assert.deepEqual(
    app.view().faces.map((f) => f.face_id),
    [1, 2, 9]
  );
});

test("a crop path is RELATIVE and carries the face id", () => {
  const url = faces.cropPath(face(42, "x.HEIC"));
  assert.equal(url, "api/faces/crop/42");
  assert.ok(!url.startsWith("/"), "a root-relative path drops the session token prefix");
  assert.ok(!url.includes("://"), "a crop must never be fetched from another origin");
});

test("a crop's alt names the photograph, and says something when it cannot", () => {
  assert.equal(faces.cropAlt(face(1, "IMG_0001.HEIC")), "Face detected in IMG_0001.HEIC");
  assert.equal(faces.cropAlt(face(1, null)), "A detected face");
});

/* --- the done sentence, spelled from UX spec 6 ------------------------------------ */

test("the done sentence is UX 6's, word for word", () => {
  assert.equal(
    faces.COPY.done,
    "All clusters handled. People will appear in filenames on your next pam plan."
  );
});

/* --- the pure transitions, driven DIRECTLY ---------------------------------------- */

/* A SECOND PATH TO THE SAME STATE, and that is the point. Every test above reaches these
 * functions through `createFacesApp`, so a transition could be wrong in a way the
 * controller's own sequencing happens to hide. Driving them by hand and requiring the two
 * paths to agree is the independent second reading this project asks for - and it is what
 * makes every exported transition EXECUTED rather than merely exported. */
test("the pure transitions compose to exactly the state the controller reaches", () => {
  const labelResponse = {
    cluster_id: 7,
    person_id: 1,
    label: "Emma",
    faces: 40,
    photographs: 31,
    named: 8,
    remaining: 2,
  };
  let state = faces.initialState();
  state = faces.withClusters(state, clustersPayload());
  state = faces.withPersons(state, personsPayload());
  assert.equal(faces.currentCluster(state).cluster_id, 7);

  state = faces.withExpanded(state, {
    cluster_id: 7,
    faces: [face(1, "a.HEIC"), face(2, "b.HEIC"), face(9, "z.HEIC")],
    photographs: 3,
  });
  assert.equal(faces.view(state).faces.length, 3);

  state = faces.afterLabel(state, labelResponse, 45);
  assert.equal(state.named, 8);
  assert.equal(state.remaining, 2);
  assert.equal(state.expanded, null, "the expansion outlived the group it belonged to");
  assert.deepEqual(state.durations, [45]);
  assert.ok(state.note.includes("31 photographs"), state.note);

  state = faces.skipCurrent(state);
  assert.equal(faces.currentCluster(state).cluster_id, 5);
  state = faces.restoreSkipped(state);
  assert.equal(faces.currentCluster(state).cluster_id, 3);

  state = faces.afterRefusal(state, "pam refused that");
  assert.equal(state.requestError, "pam refused that");
  assert.equal(state.note, "", "a refusal left a success sentence standing");
  assert.equal(faces.currentCluster(state).cluster_id, 3, "a refusal moved the queue");

  state = faces.afterServerGone(state);
  assert.equal(faces.view(state).mode, "stopped");
});

test("a refusal with no sentence still says something", () => {
  const state = faces.afterRefusal(faces.initialState(), "");
  assert.equal(state.requestError, faces.COPY.loadFailed);
  assert.ok(state.requestError.length > 0);
});

/* --- Task 11 Step 3: Ignore, and the Ignored faces view that makes it reversible ---- */

/* A server that changes its answer: the queue BEFORE and AFTER a write, so a reload that
 * silently re-read the old list would put the group back and fail here. */
function queueAfter(removedId, named, remaining) {
  const payload = clustersPayload();
  payload.clusters = payload.clusters.filter((c) => c.cluster_id !== removedId);
  payload.total = payload.clusters.length;
  payload.named = named;
  payload.remaining = remaining;
  return payload;
}

function posts(calls) {
  return calls.filter((call) => call.method === "POST");
}

test("ignore posts dismiss exactly once and the group leaves the queue with the server's counts", async () => {
  let dismissed = false;
  const io = recorder({
    "GET api/faces/clusters": () => (dismissed ? queueAfter(7, 7, 2) : clustersPayload()),
    "POST api/faces/cluster/7/dismiss": () => {
      dismissed = true;
      /* NOT cluster 7's listing counts (40 across 31): the server reports what it wrote. */
      return { cluster_id: 7, faces: 37, photographs: 11, people: 0 };
    },
  });
  const app = faces.createFacesApp({ request: io.request });
  await app.load();
  await app.ignore();
  const writes = posts(io.calls);
  assert.equal(writes.length, 1, "Ignore must be exactly one write");
  assert.equal(writes[0].path, "api/faces/cluster/7/dismiss");
  assert.equal(app.view().cluster.cluster_id, 3, "the ignored group is still on offer");
  assert.equal(app.state().remaining, 2);
  assert.equal(app.state().named, 7, "Ignore is not naming");
  const note = app.view().note;
  assert.ok(note.includes("37 faces"), note);
  assert.ok(note.includes("11 photographs"), note);
  assert.ok(!note.includes("40 faces"), `the note reports the page's belief: ${note}`);
  assert.ok(note.includes("Ignored faces"), `the note does not say where to find them: ${note}`);
  assert.deepEqual(
    io.calls.slice(-2).map((call) => call.method + " " + call.path),
    ["POST api/faces/cluster/7/dismiss", "GET api/faces/clusters"],
    "the queue was not re-read from the server after Ignore"
  );
});

function ignoredPayload() {
  return {
    faces: [
      Object.assign(face(21, "p.HEIC"), { review_state: "dismissed" }),
      Object.assign(face(22, "q.HEIC"), { review_state: "dismissed" }),
      Object.assign(face(23, "r.HEIC"), { review_state: "not_a_face" }),
    ],
    counts: { not_a_face: 1, dismissed: 2 },
  };
}

test("the ignored list renders both kinds with their counts", async () => {
  const io = recorder({ "GET api/faces/ignored": () => ignoredPayload() });
  const app = faces.createFacesApp({ request: io.request });
  await app.load();
  await app.openIgnored();
  assert.deepEqual(posts(io.calls), [], "opening the Ignored faces view wrote something");
  const shown = app.view();
  assert.equal(shown.mode, "ignored");
  assert.deepEqual(
    shown.ignored.dismissed.map((f) => f.face_id),
    [21, 22]
  );
  assert.deepEqual(
    shown.ignored.notAFace.map((f) => f.face_id),
    [23]
  );
  assert.equal(shown.ignored.dismissedCount, 2);
  assert.equal(shown.ignored.notAFaceCount, 1);
  assert.equal(shown.ignored.empty, false);
});

test("an empty ignored list says so rather than drawing nothing", async () => {
  const io = recorder({
    "GET api/faces/ignored": () => ({ faces: [], counts: { not_a_face: 0, dismissed: 0 } }),
  });
  const app = faces.createFacesApp({ request: io.request });
  await app.load();
  await app.openIgnored();
  assert.equal(app.view().ignored.empty, true);
  assert.ok(faces.COPY.nothingIgnored.includes("brought back"), faces.COPY.nothingIgnored);
});

test("restore posts a cleared state for exactly that face and the list follows the server", async () => {
  let restored = false;
  const io = recorder({
    "GET api/faces/ignored": () => {
      if (!restored) {
        return ignoredPayload();
      }
      const after = ignoredPayload();
      after.faces = after.faces.filter((f) => f.face_id !== 22);
      after.counts = { not_a_face: 1, dismissed: 1 };
      return after;
    },
    "POST api/faces/faces/state": () => {
      restored = true;
      return { review_state: null, faces: 1, photographs: 1, people: 0 };
    },
  });
  const app = faces.createFacesApp({ request: io.request });
  await app.load();
  await app.openIgnored();
  await app.restore(22);
  const writes = posts(io.calls);
  assert.equal(writes.length, 1, "Restore must be exactly one write");
  assert.equal(writes[0].path, "api/faces/faces/state");
  assert.deepEqual(writes[0].body, { face_ids: [22], review_state: null });
  const shown = app.view().ignored;
  assert.deepEqual(
    shown.dismissed.map((f) => f.face_id),
    [21],
    "the restored face is still listed"
  );
  assert.equal(shown.dismissedCount, 1, "the count did not move with the server's answer");
  assert.equal(shown.notAFaceCount, 1);
  assert.ok(app.view().note.includes("1 face"), app.view().note);
});

test("leaving the Ignored faces view re-reads the queue, because a restored face rejoins it", async () => {
  const io = recorder({ "GET api/faces/ignored": () => ignoredPayload() });
  const app = faces.createFacesApp({ request: io.request });
  await app.load();
  await app.openIgnored();
  await app.closeIgnored();
  assert.equal(app.view().mode, "cluster");
  assert.equal(io.calls[io.calls.length - 1].path, "api/faces/clusters");
  assert.deepEqual(posts(io.calls), []);
});

/* --- Task 11 Step 4: Not the same person - preview, confirm, undo ------------------ */

function separatedPreview() {
  return {
    cluster_id: 7,
    separated: true,
    epsilon: 0.3,
    groups: [
      { face_ids: [1, 2, 5], faces: 3, photographs: 3 },
      { face_ids: [6, 8], faces: 2, photographs: 1 },
    ],
    unclustered: { face_ids: [9], faces: 1, photographs: 1 },
    token: "preview-token-7",
    message: "server words",
  };
}

function splitServer(overrides) {
  const table = Object.assign(
    {
      preview: () => separatedPreview(),
      confirm: () => ({
        cluster_id: 7,
        cluster_ids: [40, 41],
        faces: 6,
        photographs: 4,
        people: 0,
        unclustered: 1,
        undo: "undo-token-7",
      }),
      undo: () => ({ cluster_id: 7, faces: 6, photographs: 4, people: 0 }),
    },
    overrides || {}
  );
  return (body) => table[body.action](body);
}

test("not the same person issues a preview and never a confirm", async () => {
  const io = recorder({ "POST api/faces/cluster/7/split": splitServer() });
  const app = faces.createFacesApp({ request: io.request });
  await app.load();
  await app.splitPreview();
  const writes = posts(io.calls);
  assert.equal(writes.length, 1, "the click issued more than the preview");
  assert.deepEqual(writes[0].body, { action: "preview" }, "the click did not ask for a preview");
  assert.equal(
    writes.filter((call) => call.body && call.body.action === "confirm").length,
    0,
    "a split was confirmed without the user seeing the proposal"
  );
  const split = app.view().split;
  assert.equal(split.canConfirm, true);
  assert.deepEqual(
    split.groups.map((g) => g.faces.map((f) => f.face_id)),
    [
      [1, 2, 5],
      [6, 8],
    ]
  );
  assert.equal(split.groups[0].caption, "3 faces across 3 photographs");
  assert.ok(split.message.includes("6 faces"), split.message);
  assert.ok(split.message.includes("2 groups"), split.message);
  assert.ok(split.message.includes("1 face would be left out"), split.message);
  assert.equal(app.view().cluster.cluster_id, 7, "a preview moved the queue");
});

test("a preview that will not separate offers nothing to confirm and issues nothing further", async () => {
  const io = recorder({
    "POST api/faces/cluster/7/split": splitServer({
      preview: () => ({
        cluster_id: 7,
        separated: false,
        epsilon: 0.1,
        groups: [],
        unclustered: { face_ids: [], faces: 0, photographs: 0 },
        token: null,
        message: "pam could not separate these 40 faces.",
      }),
    }),
  });
  const app = faces.createFacesApp({ request: io.request });
  await app.load();
  await app.splitPreview();
  const split = app.view().split;
  assert.equal(split.canConfirm, false, "the page offered to confirm a split pam refused");
  assert.deepEqual(split.groups, []);
  assert.match(split.message, /could not tell these faces apart/);
  assert.match(split.message, /Nothing was changed/);
  const before = io.calls.length;
  await app.confirmSplit();
  assert.equal(io.calls.length, before, "a confirm went out for a split the server would not make");
});

test("confirm posts the preview's own token to the same group", async () => {
  const io = recorder({ "POST api/faces/cluster/7/split": splitServer() });
  const app = faces.createFacesApp({ request: io.request });
  await app.load();
  await app.splitPreview();
  await app.confirmSplit();
  const writes = posts(io.calls);
  assert.equal(writes.length, 2);
  assert.equal(writes[1].path, "api/faces/cluster/7/split", "the confirm went to another group");
  assert.deepEqual(writes[1].body, { action: "confirm", token: "preview-token-7" });
  assert.equal(app.view().split, null, "the proposal outlived its confirmation");
  const note = app.view().note;
  assert.ok(note.includes("6 faces across 4 photographs"), note);
  assert.ok(note.includes("2 groups"), note);
  assert.equal(app.view().undoOffered, true, "a confirmed split offered no undo");
  assert.equal(io.calls[io.calls.length - 1].path, "api/faces/clusters", "the queue was not re-read");
});

test("undo after a split posts the confirm's undo token to the same group", async () => {
  const io = recorder({ "POST api/faces/cluster/7/split": splitServer() });
  const app = faces.createFacesApp({ request: io.request });
  await app.load();
  await app.splitPreview();
  await app.confirmSplit();
  await app.undo();
  const writes = posts(io.calls);
  assert.equal(writes.length, 3);
  assert.equal(writes[2].path, "api/faces/cluster/7/split");
  assert.deepEqual(writes[2].body, { action: "undo", token: "undo-token-7" });
  assert.equal(app.view().undoOffered, false, "the undo is still offered after it ran");
  assert.ok(app.view().note.includes("back together"), app.view().note);
  assert.equal(io.calls[io.calls.length - 1].path, "api/faces/clusters", "the queue was not re-read");
  const after = io.calls.length;
  await app.undo();
  assert.equal(io.calls.length, after, "a second undo went out - that is a stack");
});

test("cancelling a preview issues no request", async () => {
  const io = recorder({ "POST api/faces/cluster/7/split": splitServer() });
  const app = faces.createFacesApp({ request: io.request });
  await app.load();
  await app.splitPreview();
  const before = io.calls.length;
  app.cancelSplit();
  assert.equal(io.calls.length, before, "cancelling a proposal wrote something");
  assert.equal(app.view().split, null);
  assert.equal(app.view().cluster.cluster_id, 7);
  await app.confirmSplit();
  assert.equal(io.calls.length, before, "a cancelled proposal could still be confirmed");
});

/* --- Task 11 Step 5: the one undo after a label ------------------------------------ */

function labelServer() {
  return {
    "POST api/faces/cluster/7/label": (body) => ({
      cluster_id: 7,
      person_id: 1,
      label: body.person,
      faces: 3,
      photographs: 2,
      face_ids: [1, 2, 11],
    }),
    "POST api/faces/cluster/3/label": (body) => ({
      cluster_id: 3,
      person_id: 2,
      label: body.person,
      faces: 2,
      photographs: 2,
      face_ids: [3, 12],
    }),
    "POST api/faces/faces/assign": (body) => ({
      person_id: null,
      label: null,
      faces: body.face_ids.length,
      photographs: 2,
      people: 1,
    }),
  };
}

test("undo after a label posts exactly that label's faces with no person", async () => {
  const io = recorder(labelServer());
  const app = faces.createFacesApp({ request: io.request });
  await app.load();
  await app.save("Emma");
  assert.equal(app.view().undoOffered, true, "a saved name offered no undo");
  await app.undo();
  const writes = posts(io.calls);
  assert.equal(writes.length, 2);
  assert.equal(writes[1].path, "api/faces/faces/assign");
  assert.deepEqual(writes[1].body, { face_ids: [1, 2, 11], person: null });
  assert.equal(app.view().undoOffered, false);
  assert.ok(app.view().note.includes("3 faces"), app.view().note);
  assert.equal(io.calls[io.calls.length - 1].path, "api/faces/clusters", "the queue was not re-read");
});

test("after a second label only the latest label is undoable", async () => {
  const io = recorder(labelServer());
  const app = faces.createFacesApp({ request: io.request });
  await app.load();
  await app.save("Emma");
  await app.save("Ada");
  assert.deepEqual(app.state().undo.face_ids, [3, 12], "the offer is not the latest label's");
  await app.undo();
  const undos = posts(io.calls).filter((call) => call.path === "api/faces/faces/assign");
  assert.equal(undos.length, 1, "more than one undo went out");
  assert.deepEqual(undos[0].body, { face_ids: [3, 12], person: null });
  const after = io.calls.length;
  await app.undo();
  assert.equal(io.calls.length, after, "the FIRST label became undoable once the second was undone");
});

test("any other action withdraws the undo offer", async () => {
  /* Save on 7, skip 3, bring it back, save on 3, then Ignore lands on 5. */
  const io = recorder(
    Object.assign(labelServer(), {
      "POST api/faces/cluster/5/dismiss": () => ({
        cluster_id: 5,
        faces: 2,
        photographs: 1,
        people: 0,
      }),
    })
  );
  const app = faces.createFacesApp({ request: io.request });
  await app.load();
  await app.save("Emma");
  app.skip();
  assert.equal(app.view().undoOffered, false, "Skip left the label's undo standing");
  const before = io.calls.length;
  await app.undo();
  assert.equal(io.calls.length, before, "an undo went out after the user moved on");
  app.restoreSkipped();
  await app.save("Emma");
  await app.ignore();
  assert.equal(app.state().undo, null, "Ignore left the label's undo standing");
});

test("a response without face ids offers no undo rather than guessing", () => {
  let state = faces.withClusters(faces.initialState(), clustersPayload());
  state = faces.afterLabel(
    state,
    { cluster_id: 7, person_id: 1, label: "Emma", faces: 40, photographs: 31 },
    5
  );
  assert.equal(state.undo, null);
});

/* --- refusals, for every Task 11 write --------------------------------------------- */

test("a refusal to ignore, split or undo changes nothing but the sentence", async () => {
  const refuse = (text) => () => {
    throw new faces.FacesRefusal(text);
  };
  /* Ignore refused. */
  let io = recorder({ "POST api/faces/cluster/7/dismiss": refuse("pam changed nothing. Named.") });
  let app = faces.createFacesApp({ request: io.request });
  await app.load();
  await app.ignore();
  assert.equal(app.view().cluster.cluster_id, 7, "a refused Ignore removed the group anyway");
  assert.equal(app.state().remaining, 3);
  assert.equal(app.view().note, "");
  assert.match(app.view().requestError, /changed nothing/);
  assert.equal(io.calls[io.calls.length - 1].method, "POST", "a refused Ignore still re-read");

  /* Confirm refused: the proposal stays, still confirmable, and no undo appears. */
  io = recorder({
    "POST api/faces/cluster/7/split": splitServer({
      confirm: refuse("pam changed nothing. This group has changed."),
    }),
  });
  app = faces.createFacesApp({ request: io.request });
  await app.load();
  await app.splitPreview();
  await app.confirmSplit();
  assert.notEqual(app.view().split, null, "a refused confirm threw the proposal away");
  assert.equal(app.state().undo, null, "a refused confirm offered an undo");
  assert.equal(app.view().cluster.cluster_id, 7);
  assert.match(app.view().requestError, /has changed/);

  /* Undo refused: the offer stays, and the queue is not touched. */
  const server = labelServer();
  server["POST api/faces/faces/assign"] = refuse("pam changed nothing. Moved since.");
  io = recorder(server);
  app = faces.createFacesApp({ request: io.request });
  await app.load();
  await app.save("Emma");
  const named = app.state().named;
  await app.undo();
  assert.deepEqual(app.state().undo.face_ids, [1, 2, 11], "a refused undo withdrew the offer");
  assert.equal(app.state().named, named);
  assert.match(app.view().requestError, /Moved since/);

  /* Restore refused: the face stays listed. */
  io = recorder({
    "GET api/faces/ignored": () => ignoredPayload(),
    "POST api/faces/faces/state": refuse("pam changed nothing. Unknown face."),
  });
  app = faces.createFacesApp({ request: io.request });
  await app.load();
  await app.openIgnored();
  await app.restore(22);
  assert.deepEqual(
    app.view().ignored.dismissed.map((f) => f.face_id),
    [21, 22],
    "a refused restore removed the face anyway"
  );
  assert.match(app.view().requestError, /Unknown face/);
});

/* --- the Task 11 transitions, driven DIRECTLY -------------------------------------- */

test("the correction transitions compose without the controller", () => {
  let state = faces.withClusters(faces.initialState(), clustersPayload());

  state = faces.afterIgnore(state, {
    cluster_id: 7,
    faces: 37,
    photographs: 11,
    people: 0,
    named: 7,
    remaining: 2,
  });
  assert.equal(faces.currentCluster(state).cluster_id, 3);
  assert.equal(state.remaining, 2);
  assert.equal(state.note, faces.ignoreSentence({ faces: 37, photographs: 11 }));

  state = faces.withSplitPreview(state, separatedPreview(), 3);
  assert.equal(state.split.cluster_id, 3, "the proposal forgot which group it was posted to");
  assert.equal(state.split.token, "preview-token-7");
  assert.ok(faces.splitPreviewSentence(state.split).includes("2 groups"));
  state = faces.cancelSplit(state);
  assert.equal(state.split, null);

  state = faces.withSplitPreview(state, separatedPreview(), 3);
  state = faces.afterSplitConfirm(state, {
    cluster_id: 3,
    cluster_ids: [40, 41],
    faces: 6,
    photographs: 4,
    people: 0,
    unclustered: 2,
    undo: "u",
  });
  assert.deepEqual(state.undo, { kind: "split", cluster_id: 3, token: "u" });
  assert.equal(
    state.note,
    faces.splitSentence({ cluster_ids: [40, 41], faces: 6, photographs: 4, unclustered: 2 })
  );
  assert.ok(state.note.includes("2 faces were left out"), state.note);
  state = faces.afterSplitUndo(state, { faces: 6, photographs: 4, people: 0 });
  assert.equal(state.undo, null);

  state = faces.afterLabel(
    state,
    { cluster_id: 3, person_id: 1, label: "Ada", faces: 9, photographs: 9, face_ids: [3] },
    4
  );
  assert.deepEqual(state.undo.face_ids, [3]);
  state = faces.afterLabelUndo(state, { faces: 1, photographs: 1, people: 1 });
  assert.equal(state.undo, null);
  assert.ok(state.note.includes("1 face across 1 photograph"), state.note);

  state = faces.showIgnored(state);
  assert.equal(faces.view(state).mode, "ignored");
  state = faces.withIgnored(state, ignoredPayload());
  state = faces.afterRestore(state, { faces: 1, photographs: 1, people: 0 }, 23);
  assert.deepEqual(
    state.ignored.faces.map((f) => f.face_id),
    [21, 22]
  );
  state = faces.closeIgnored(state);
  assert.equal(state.ignoredOpen, false);
  assert.equal(state.ignored, null);
});

/* --- Task 11 Step 2: Manage people ------------------------------------------------- */

/* Three people as `/faces/persons` serves them: Emma, Em (the same person arriving as a
 * second name - the merge case clarification 3 calls normal) and a HIDDEN Kofi with a
 * filename override, so a field that pre-filled from `filename_label` would be visible. */
function peoplePayload() {
  return {
    persons: [
      {
        id: 1,
        label: "Emma",
        filename_label: null,
        filename_value: "Emma",
        hidden: false,
        faces: 12,
        photographs: 8,
      },
      {
        id: 2,
        label: "Em",
        filename_label: null,
        filename_value: "Em",
        hidden: false,
        faces: 3,
        photographs: 2,
      },
      {
        id: 3,
        label: "Kofi",
        filename_label: "KofiM",
        filename_value: "KofiM",
        hidden: true,
        faces: 5,
        photographs: 5,
      },
    ],
  };
}

/* `GET faces/person/{id}` (owner ruling 4): the person row, and their faces with
 * `below_floor`. Face 32 is below the floor and still this person's. */
function personPayload(id) {
  return {
    person: peoplePayload().persons.find((person) => person.id === id),
    faces: [
      Object.assign(face(31, "e1.HEIC"), { below_floor: false }),
      Object.assign(face(32, "e2.HEIC"), { below_floor: true }),
      Object.assign(face(33, "e3.HEIC"), { below_floor: false }),
    ],
  };
}

function peopleServer(overrides) {
  return recorder(
    Object.assign(
      {
        "GET api/faces/persons": () => peoplePayload(),
        "GET api/faces/person/1": () => personPayload(1),
        "GET api/faces/person/2": () => personPayload(2),
        "GET api/faces/person/3": () => personPayload(3),
      },
      overrides || {}
    )
  );
}

/* Load the page, open Manage people, open one person. */
async function openPerson(io, personId) {
  const app = faces.createFacesApp({ request: io.request });
  await app.load();
  await app.openPeople();
  await app.expandPerson(personId);
  return app;
}

/* A person update's answer: NOT the list's counts (Em is 3 across 2), so a sentence built
 * from the page's belief rather than the server's is visible. */
function updated(person) {
  return () => ({ person: person, faces: 4, photographs: 3, people: 1 });
}

function emWith(fields) {
  return Object.assign({}, peoplePayload().persons[1], fields);
}

test("manage people list and expand issue only GETs", async () => {
  const io = peopleServer();
  const app = faces.createFacesApp({ request: io.request });
  await app.load();
  const before = io.calls.length;
  await app.openPeople();
  await app.expandPerson(2);
  assert.deepEqual(posts(io.calls), [], "looking at people wrote something");
  assert.deepEqual(
    io.calls.slice(before).map((call) => call.method + " " + call.path),
    ["GET api/faces/persons", "GET api/faces/person/2"]
  );
  const people = app.view().people;
  assert.equal(app.view().mode, "people");
  assert.deepEqual(
    people.persons.map((person) => person.label),
    ["Emma", "Em", "Kofi"]
  );
  assert.equal(people.persons[1].caption, "3 faces across 2 photographs");
  assert.ok(people.persons[2].flag.includes("Hidden"), "a hidden person is not flagged");
  assert.equal(people.persons[0].flag, "");
  assert.deepEqual(
    people.panel.faces.map((f) => [f.face_id, f.below_floor]),
    [
      [31, false],
      [32, true],
      [33, false],
    ]
  );
});

test("the rename, filename and move fields start empty for every person", async () => {
  const io = peopleServer();
  const app = await openPerson(io, 3);
  const panel = app.view().people.panel;
  assert.equal(panel.title, "Kofi");
  assert.equal(panel.renameValue, "", "pam typed a name into Rename");
  assert.equal(panel.filenameValue, "", "pam pre-filled the filename name from the catalog");
  assert.equal(panel.moveValue, "", "pam proposed a person to move faces to");
  /* Kofi is HIDDEN, and P2's O4 renders no filename line for a hidden person (M12 Task 5);
   * the unhidden half of that rule is its own test below. */
  assert.equal(panel.filenameLine, null, "a hidden person's panel still names a filename");
  assert.equal(panel.hideLabel, "Unhide");
});

test("rename posts exactly the typed label and nothing else", async () => {
  const io = peopleServer({ "POST api/faces/person/2": updated(emWith({ label: "Person C" })) });
  const app = await openPerson(io, 2);
  await app.renamePerson("  Person C  ");
  const writes = posts(io.calls);
  assert.equal(writes.length, 1, "rename must be exactly one write");
  assert.equal(writes[0].path, "api/faces/person/2");
  assert.deepEqual(writes[0].body, { label: "Person C" });
  const note = app.view().note;
  assert.ok(note.includes("Person C"), note);
  assert.ok(note.includes("4 faces across 3 photographs"), `not the server's numbers: ${note}`);
  assert.deepEqual(
    io.calls.slice(-2).map((call) => call.method + " " + call.path),
    ["GET api/faces/persons", "GET api/faces/person/2"],
    "the people list and the open person were not re-read after the write"
  );
  const before = io.calls.length;
  await app.renamePerson("   ");
  assert.equal(io.calls.length, before, "a blank rename was posted");
  assert.equal(app.view().requestError, faces.COPY.renameNeeded);
});

test("setting the filename name posts the typed string and clearing it posts null, never an empty string", async () => {
  const io = peopleServer({
    "POST api/faces/person/2": (body) =>
      updated(emWith({ filename_label: body.filename_label }))(),
  });
  const app = await openPerson(io, 2);
  await app.setFilenameLabel(" EmM ");
  await app.clearFilenameLabel();
  const writes = posts(io.calls);
  assert.equal(writes.length, 2);
  assert.deepEqual(writes[0].body, { filename_label: "EmM" });
  assert.deepEqual(writes[1].body, { filename_label: null });
  assert.notEqual(writes[1].body.filename_label, "", "clearing posted an empty string");
  const before = io.calls.length;
  await app.setFilenameLabel("  ");
  assert.equal(io.calls.length, before, "a blank filename name was posted");
  assert.equal(app.view().requestError, faces.COPY.filenameNeeded);
});

test("hide posts hidden true and nothing else, and unhide posts false", async () => {
  const io = peopleServer({
    "POST api/faces/person/2": (body) => updated(emWith({ hidden: body.hidden }))(),
  });
  const app = await openPerson(io, 2);
  await app.setHidden(true);
  await app.setHidden(false);
  const writes = posts(io.calls);
  assert.equal(writes.length, 2);
  assert.deepEqual(writes[0].body, { hidden: true });
  assert.deepEqual(Object.keys(writes[0].body), ["hidden"], "hiding touched another field");
  assert.deepEqual(writes[1].body, { hidden: false });
  assert.ok(app.view().note.includes("shown again"), app.view().note);
});

test("unassign posts a null person for exactly the selected faces", async () => {
  const io = peopleServer({
    "POST api/faces/faces/assign": () => ({
      person_id: null,
      label: null,
      faces: 2,
      photographs: 1,
      people: 1,
    }),
  });
  const app = await openPerson(io, 2);
  app.toggleFace(31);
  app.toggleFace(32);
  app.toggleFace(31);
  app.toggleFace(33);
  app.toggleFace(999);
  assert.deepEqual(app.state().selected, [32, 33]);
  await app.unassignFaces();
  const writes = posts(io.calls);
  assert.equal(writes.length, 1);
  assert.equal(writes[0].path, "api/faces/faces/assign");
  assert.deepEqual(writes[0].body, { face_ids: [32, 33], person: null });
  assert.ok(app.view().note.includes("2 faces across 1 photograph"), app.view().note);
  assert.deepEqual(app.state().selected, [], "the selection outlived the write");
});

test("move posts the typed name for the selected faces", async () => {
  const io = peopleServer({
    "POST api/faces/faces/assign": (body) => ({
      person_id: 3,
      label: "Kofi",
      faces: body.face_ids.length,
      photographs: 1,
      people: 2,
    }),
  });
  const app = await openPerson(io, 2);
  app.toggleFace(31);
  const before = io.calls.length;
  await app.moveFaces("   ");
  assert.equal(io.calls.length, before, "a blank name was posted as a move");
  await app.moveFaces(" kofi ");
  const writes = posts(io.calls);
  assert.equal(writes.length, 1);
  assert.deepEqual(writes[0].body, { face_ids: [31], person: "kofi" });
  assert.ok(app.view().note.includes("to Kofi"), app.view().note);
});

test("not a face posts not_a_face, never dismissed", async () => {
  const io = peopleServer({
    "POST api/faces/faces/state": (body) => ({
      review_state: body.review_state,
      faces: 1,
      photographs: 1,
      people: 1,
    }),
  });
  const app = await openPerson(io, 2);
  app.toggleFace(32);
  await app.markNotAFace();
  const writes = posts(io.calls);
  assert.equal(writes.length, 1);
  assert.equal(writes[0].path, "api/faces/faces/state");
  /* Spelled from A18's vocabulary, not from faces.js's constant. */
  assert.equal(writes[0].body.review_state, "not_a_face");
  assert.notEqual(writes[0].body.review_state, "dismissed", "Not a face was sent as Ignore");
  assert.deepEqual(writes[0].body, { face_ids: [32], review_state: "not_a_face" });
  assert.ok(app.view().note.includes("Ignored faces"), app.view().note);
});

test("a manage action with no faces selected issues no request", async () => {
  const io = peopleServer();
  const app = await openPerson(io, 2);
  const before = io.calls.length;
  await app.unassignFaces();
  await app.moveFaces("Kofi");
  await app.markNotAFace();
  assert.equal(io.calls.length, before, "a correction went out with no faces selected");
  assert.equal(app.view().requestError, faces.COPY.selectFaces);
});

test("merge names both people and their counts and issues no request before confirm", async () => {
  const io = peopleServer();
  const app = await openPerson(io, 2);
  const before = io.calls.length;
  app.proposeMerge("1");
  assert.equal(io.calls.length, before, "choosing who to merge into sent something");
  const merge = app.view().people.merge;
  assert.notEqual(merge, null, "no confirmation was shown");
  assert.ok(merge.message.includes("Merge Em into Emma"), merge.message);
  assert.ok(merge.message.includes("3 faces across 2 photographs"), merge.message);
  assert.ok(merge.message.includes("12 faces across 8 photographs"), merge.message);
  assert.ok(merge.message.includes("no longer exist"), merge.message);
  app.proposeMerge("2");
  assert.equal(app.view().requestError, faces.COPY.pickMerge, "a merge into oneself was offered");
  assert.equal(io.calls.length, before);
});

test("cancelling a merge issues no request", async () => {
  const io = peopleServer();
  const app = await openPerson(io, 2);
  app.proposeMerge("1");
  const before = io.calls.length;
  app.cancelMerge();
  assert.equal(io.calls.length, before, "cancelling a merge sent something");
  assert.equal(app.view().people.merge, null);
  await app.confirmMerge();
  assert.equal(io.calls.length, before, "a cancelled merge could still be confirmed");
});

test("confirming a merge posts the surviving id to the merged person's path", async () => {
  let merged = false;
  const message = "Moved 3 faces from Em to Emma. Em no longer exists as a separate person.";
  const io = peopleServer({
    "GET api/faces/persons": () => {
      const payload = peoplePayload();
      if (merged) {
        payload.persons = payload.persons.filter((person) => person.id !== 2);
      }
      return payload;
    },
    "POST api/faces/person/2/merge": () => {
      merged = true;
      return {
        merged: { id: 2, label: "Em" },
        into: { id: 1, label: "Emma" },
        faces: 3,
        photographs: 2,
        people: 2,
        message: message,
      };
    },
  });
  const app = await openPerson(io, 2);
  app.proposeMerge("1");
  await app.confirmMerge();
  const writes = posts(io.calls);
  assert.equal(writes.length, 1, "a merge must be exactly one write");
  assert.equal(writes[0].path, "api/faces/person/2/merge", "the merge went to the wrong person");
  assert.deepEqual(writes[0].body, { into: 1 });
  assert.equal(app.view().note, message, "the note is not the server's sentence");
  assert.equal(app.view().people.panel, null, "the merged person is still open");
  assert.deepEqual(
    app.view().people.persons.map((person) => person.label),
    ["Emma", "Kofi"]
  );
  assert.equal(io.calls[io.calls.length - 1].path, "api/faces/persons", "the list was not re-read");
});

test("a refused rename shows the server's sentence and changes nothing else", async () => {
  const refusal =
    "pam changed nothing. Emma is already a person; to make these faces Emma's, merge the" +
    " two people instead of renaming.";
  const io = peopleServer({
    "POST api/faces/person/2": () => {
      throw new faces.FacesRefusal(refusal);
    },
  });
  const app = await openPerson(io, 2);
  app.toggleFace(33);
  const before = app.state();
  await app.renamePerson("emma");
  const after = app.state();
  assert.equal(after.requestError, refusal);
  assert.equal(after.note, "");
  assert.equal(after.managed, before.managed, "a refused rename re-read or replaced the person");
  assert.equal(after.persons, before.persons, "a refused rename moved the list");
  assert.deepEqual(after.selected, [33], "a refused rename threw the selection away");
  assert.equal(io.calls[io.calls.length - 1].method, "POST", "a refused rename still reloaded");
  assert.equal(app.view().people.panel.key, faces.view(before).people.panel.key);
});

test("leaving Manage people re-reads the queue and writes nothing", async () => {
  const io = peopleServer({ "GET api/faces/ignored": () => ignoredPayload() });
  const app = await openPerson(io, 2);
  await app.closePeople();
  assert.equal(app.view().mode, "cluster");
  assert.equal(app.view().people, null);
  assert.equal(io.calls[io.calls.length - 1].path, "api/faces/clusters");
  await app.openPeople();
  await app.openIgnored();
  assert.equal(app.view().mode, "ignored", "Manage people outranked the view just opened");
  assert.equal(app.state().peopleOpen, false);
  assert.deepEqual(posts(io.calls), []);
});

test("the manage people transitions compose without the controller", () => {
  const internal = /cluster|epsilon|review_state|person_id/;
  let state = faces.withClusters(faces.initialState(), clustersPayload());
  state = faces.showPeople(state);
  state = faces.withPersons(state, peoplePayload());
  state = faces.withManagedPerson(state, personPayload(2));
  assert.equal(state.managed.version, 1);
  state = faces.toggleFaceSelection(state, 31);
  state = faces.toggleFaceSelection(state, 404);
  assert.deepEqual(state.selected, [31]);
  const shown = faces.peopleView(state);
  assert.equal(shown.panel.selectedCount, 1);
  assert.deepEqual(
    shown.panel.mergeTargets.map((t) => t.id),
    [1, 3]
  );

  const response = { person: emWith({ label: "Person C" }), faces: 4, photographs: 3, people: 1 };
  const notes = [
    faces.afterPersonUpdate(state, response, "label").note,
    faces.afterPersonUpdate(state, { ...response, person: emWith({ filename_value: "EmM" }) }, "filename").note,
    faces.afterPersonUpdate(state, { ...response, person: emWith({ hidden: true }) }, "hidden").note,
    faces.afterFacesCorrected(state, { label: null, faces: 1, photographs: 1 }, "unassign").note,
    faces.afterFacesCorrected(state, { label: "Kofi", faces: 2, photographs: 2 }, "move").note,
    faces.afterFacesCorrected(state, { faces: 1, photographs: 1 }, "not_a_face").note,
  ];
  assert.ok(notes[0].startsWith("Renamed to Person C"), notes[0]);
  assert.ok(notes[1].includes("EmM"), notes[1]);
  assert.ok(notes[2].startsWith("Hid Em"), notes[2]);
  assert.ok(notes[3].startsWith("Took the name off 1 face"), notes[3]);
  assert.ok(notes[4].includes("to Kofi"), notes[4]);
  assert.ok(notes[5].includes("not a face"), notes[5]);
  assert.deepEqual(faces.afterPersonUpdate(state, response, "label").selected, []);

  state = faces.proposeMerge(state, 2, 1);
  const sentence = faces.mergeSentence(state.mergePending);
  assert.ok(sentence.includes("Em") && sentence.includes("Emma"), sentence);
  notes.push(sentence);
  for (const note of notes) {
    assert.doesNotMatch(note, internal, `internal vocabulary reached the user: ${note}`);
  }
  assert.equal(faces.cancelMerge(state).mergePending, null);
  assert.equal(faces.proposeMerge(state, 2, 2).requestError, faces.COPY.pickMerge);

  state = faces.afterMerge(state, { message: "Moved 3 faces from Em to Emma." });
  assert.equal(state.managed, null);
  assert.equal(state.note, "Moved 3 faces from Em to Emma.");
  state = faces.withManagedPerson(state, personPayload(1));
  state = faces.closePeople(state);
  assert.equal(state.peopleOpen, false);
  assert.equal(state.managed, null);
  assert.equal(faces.peopleView(state), null);
});

/* --- M12 Task 5 (C8 / P2): what the faces page says, spelled from P2 ----------------- */

/* EVERY LITERAL BELOW IS TYPED FROM P2 (the M12 plan amendments, applied to UX 6 and
 * design 8.2 in the same commit), never built from `faces.js`'s own helpers. */

function doneWith(named, hidden, floor) {
  return faces.withClusters(faces.initialState(), {
    clusters: [],
    offset: 0,
    limit: 50,
    total: 0,
    named: named,
    remaining: 0,
    hidden_below_floor: hidden,
    floor: floor,
  });
}

test("the done state counts the groups the floor is hiding, in P2's words", () => {
  const plural = faces.view(doneWith(4, 2, 0.4));
  assert.equal(plural.mode, "done");
  assert.equal(
    plural.doneNote,
    "2 more groups hold only faces below the detection threshold ([tiers] faces floor = 0.4) and are not shown."
  );
  const single = faces.view(doneWith(4, 1, 0.35));
  assert.equal(
    single.doneNote,
    "1 more group holds only faces below the detection threshold ([tiers] faces floor = 0.35) and is not shown."
  );
  const none = faces.view(doneWith(4, 0, 0.4));
  assert.equal(none.mode, "done");
  assert.equal(none.doneNote, "", "a done state with nothing hidden gained a sentence");
  assert.equal(faces.hiddenBelowFloorSentence(0, 0.4), "");
});

test("an archive whose every group is below the floor is done with the sentence, not empty", () => {
  /* F5's own scenario: clustering ran, nothing is named, nothing is offerable. The empty
   * state would tell this user to run the command they have already run. */
  const shown = faces.view(doneWith(0, 3, 0.4));
  assert.equal(shown.mode, "done");
  assert.equal(
    shown.doneNote,
    "3 more groups hold only faces below the detection threshold ([tiers] faces floor = 0.4) and are not shown."
  );
});

test("the hidden-groups sentence is only ever the done state's", () => {
  const state = faces.withClusters(
    faces.initialState(),
    Object.assign(clustersPayload(), { hidden_below_floor: 2, floor: 0.4 })
  );
  assert.equal(faces.view(state).mode, "cluster");
  assert.equal(faces.view(state).doneNote, "");
});

test("the done sentence is read from the server's field on load", async () => {
  const io = recorder({
    "GET api/faces/clusters": () => ({
      clusters: [],
      offset: 0,
      limit: 50,
      total: 0,
      named: 5,
      remaining: 0,
      hidden_below_floor: 1,
      floor: 0.4,
    }),
  });
  const app = faces.createFacesApp({ request: io.request });
  await app.load();
  assert.equal(
    app.view().doneNote,
    "1 more group holds only faces below the detection threshold ([tiers] faces floor = 0.4) and is not shown."
  );
});

test("a hidden person's panel renders NO filename line and says so in P2's words", async () => {
  /* The ABSENCE is the assertion (M8's partition lesson): a test that the hidden sentence
   * is present would survive the old filename line coming back beside it. */
  const io = peopleServer();
  const app = await openPerson(io, 3);
  const panel = app.view().people.panel;
  assert.equal(panel.filenameLine, null);
  assert.equal(
    panel.summary,
    "5 faces across 5 photographs. Hidden - pam will not put this name in any filename. Unhide to use it again."
  );
  const everything = JSON.stringify(panel);
  assert.ok(!everything.includes("Used in filenames"), everything);
  assert.ok(!everything.includes("KofiM"), `the hidden person's filename name is shown: ${everything}`);
});

test("an unhidden person's panel is unchanged: counts, then its filename line", async () => {
  const io = peopleServer();
  const app = await openPerson(io, 2);
  const panel = app.view().people.panel;
  assert.equal(panel.summary, "3 faces across 2 photographs.");
  assert.equal(panel.filenameLine, "Used in filenames as: Em");
});

test("a restored face going back to a named person says so, singular and plural", () => {
  assert.equal(
    faces.restoreSentence({ faces: 1, photographs: 1, person_labels: ["Emma"] }),
    "Brought back 1 face from 1 photograph. It'll be added back to Emma."
  );
  assert.equal(
    faces.restoreSentence({ faces: 3, photographs: 2, person_labels: ["Emma"] }),
    "Brought back 3 faces from 2 photographs. They'll be added back to Emma."
  );
});

test("a restored face with no person is offered again with its group", () => {
  assert.equal(
    faces.restoreSentence({ faces: 2, photographs: 2, person_labels: [null] }),
    "Brought back 2 faces from 2 photographs. pam will offer them again with their group."
  );
});

test("a restore spanning more than one destination names no one", () => {
  const expected =
    "Brought back 2 faces from 2 photographs. Each goes back where it was before it was marked.";
  assert.equal(
    faces.restoreSentence({ faces: 2, photographs: 2, person_labels: ["Emma", null] }),
    expected
  );
  assert.equal(
    faces.restoreSentence({ faces: 2, photographs: 2, person_labels: ["Emma", "Kofi"] }),
    expected
  );
});

test("restore's note is chosen from the server's person_labels", async () => {
  const io = recorder({
    "GET api/faces/ignored": () => ignoredPayload(),
    "POST api/faces/faces/state": () => ({
      review_state: null,
      faces: 1,
      photographs: 1,
      people: 1,
      person_labels: ["zzprobe"],
      named: 15,
      remaining: 0,
    }),
  });
  const app = faces.createFacesApp({ request: io.request });
  await app.load();
  await app.openIgnored();
  await app.restore(23);
  assert.equal(
    app.view().note,
    "Brought back 1 face from 1 photograph. It'll be added back to zzprobe."
  );
});

test("a face checkbox is named by its position and photograph, never its id", async () => {
  const io = peopleServer();
  const app = await openPerson(io, 2);
  const names = app.view().people.panel.faces.map((f) => f.accessibleName);
  assert.deepEqual(names, [
    "Face 1 of 3 in e1.HEIC",
    "Face 2 of 3 in e2.HEIC",
    "Face 3 of 3 in e3.HEIC",
  ]);
  for (const name of names) {
    assert.doesNotMatch(name, /3[123]/, `a raw face id reached an accessible name: ${name}`);
  }
  /* A face whose photograph pam cannot point at keeps its position (P2 names no fallback;
   * this is the page's own wording, pinned so a raw id can never stand in for it). */
  assert.equal(faces.faceCheckboxName(2, 5, null), "Face 2 of 5");
});

/* THE COUNTER (P2, HT-M11 Leg B). One transition per success path P2 lists. Every response
 * carries counts NO local arithmetic could reach from 7 named / 3 remaining, so a counter
 * moved by the page instead of by the server is a visible difference. */
const MID = "·";

function counted(state) {
  return faces
    .progressLine(state)
    .split(" " + MID + " ")
    .slice(0, 2)
    .join(" " + MID + " ");
}

function atSeven() {
  return faces.withClusters(faces.initialState(), clustersPayload());
}

test("the counter after a label is the server's", () => {
  const state = faces.afterLabel(
    atSeven(),
    { cluster_id: 7, label: "Emma", faces: 40, photographs: 31, named: 6, remaining: 1 },
    3
  );
  assert.equal(counted(state), "6 named " + MID + " 1 remaining");
});

test("the counter after unassigning faces is the server's", () => {
  const state = faces.afterFacesCorrected(
    atSeven(),
    { label: null, faces: 1, photographs: 1, named: 7, remaining: 4 },
    "unassign"
  );
  assert.equal(counted(state), "7 named " + MID + " 4 remaining");
});

test("the counter after moving faces is the server's", () => {
  const state = faces.afterFacesCorrected(
    atSeven(),
    { label: "Kofi", faces: 2, photographs: 2, named: 8, remaining: 3 },
    "move"
  );
  assert.equal(counted(state), "8 named " + MID + " 3 remaining");
});

test("the counter after a merge is the server's - a merge changes two people's counts", () => {
  const state = faces.afterMerge(atSeven(), {
    message: "Moved 3 faces from Em to Emma.",
    faces: 3,
    photographs: 2,
    named: 5,
    remaining: 3,
  });
  assert.equal(counted(state), "5 named " + MID + " 3 remaining");
});

test("the counter after an ignore is the server's", () => {
  const state = faces.afterIgnore(atSeven(), {
    cluster_id: 7,
    faces: 37,
    photographs: 11,
    named: 7,
    remaining: 1,
  });
  assert.equal(counted(state), "7 named " + MID + " 1 remaining");
});

test("the counter after a restore is the server's", () => {
  const state = faces.afterRestore(
    atSeven(),
    { faces: 1, photographs: 1, person_labels: [null], named: 7, remaining: 4 },
    23
  );
  assert.equal(counted(state), "7 named " + MID + " 4 remaining");
});

test("a write that reports no counts leaves the counter where it was - no arithmetic", () => {
  const state = faces.afterLabel(
    atSeven(),
    { cluster_id: 7, label: "Emma", faces: 40, photographs: 31 },
    3
  );
  assert.equal(counted(state), "7 named " + MID + " 3 remaining");
});

/* Task 5 review, F2 (M2065/M2066): `withProgress` needs BOTH numbers before it takes
 * either. A half response must not leave one count undefined on the screen. */
test("a response carrying only one of the two counts leaves both where they were", () => {
  for (const half of [{ named: 9 }, { remaining: 0 }]) {
    const state = faces.afterIgnore(
      atSeven(),
      Object.assign({ cluster_id: 7, faces: 37, photographs: 11 }, half)
    );
    assert.equal(counted(state), "7 named " + MID + " 3 remaining", JSON.stringify(half));
  }
});

test("a manage-people write moves the counter from its response through the controller", async () => {
  const io = peopleServer({
    "POST api/faces/faces/assign": () => ({
      person_id: null,
      label: null,
      faces: 1,
      photographs: 1,
      people: 1,
      named: 9,
      remaining: 6,
    }),
  });
  const app = await openPerson(io, 2);
  app.toggleFace(31);
  await app.unassignFaces();
  assert.equal(counted(app.state()), "9 named " + MID + " 6 remaining");
});

/* --- M14 review R1: the binding, on every request ----------------------------------- */

/* A recording `fetch`: answers `api/ping` with the binding given, every other path with `{}`,
 * and keeps every call. The header is the property; the response is irrelevant to it. */
function bindingFetch(binding) {
  const calls = [];
  const fetchFn = (path, options) => {
    calls.push({ path, options });
    const body = path === "api/ping" ? { binding, plan_id: null } : {};
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve(body),
      text: () => Promise.resolve(JSON.stringify(body)),
    });
  };
  return { calls, fetchFn };
}

test("every request names the binding the page was loaded on, writes included", async () => {
  const io = bindingFetch(3);
  const request = faces.bindingRequest(io.fetchFn);
  await request("GET", "api/faces/clusters", null);
  await request("POST", "api/faces/faces/state", { face_ids: [1], state: "not_a_face" });
  assert.deepEqual(
    io.calls.map((c) => c.path),
    ["api/ping", "api/faces/clusters", "api/faces/faces/state"]
  );
  for (const call of io.calls.slice(1)) {
    assert.equal(call.options.headers["X-Pam-Binding"], "3", call.path);
  }
  assert.equal(io.calls[2].options.method, "POST");
  assert.equal(io.calls[2].options.body, JSON.stringify({ face_ids: [1], state: "not_a_face" }));
});

test("the page pings once, at load, and never adopts a later binding", async () => {
  const io = bindingFetch(0);
  const request = faces.bindingRequest(io.fetchFn);
  await request("GET", "api/faces/persons", null);
  await request("POST", "api/faces/person/2", { label: "Person A" });
  assert.equal(io.calls.filter((c) => c.path === "api/ping").length, 1);
  assert.equal(io.calls[2].options.headers["X-Pam-Binding"], "0");
});

test("the page's own transport is the browser's fetch through the binding", async () => {
  const io = bindingFetch(5);
  const saved = global.fetch;
  global.fetch = io.fetchFn;
  try {
    await faces.pageRequest()("POST", "api/faces/faces/state", { face_ids: [1], state: "not_a_face" });
  } finally {
    global.fetch = saved;
  }
  assert.deepEqual(io.calls.map((c) => c.path), ["api/ping", "api/faces/faces/state"]);
  assert.equal(io.calls[1].options.headers["X-Pam-Binding"], "5");
});

test("a ping that fails sends no header rather than a made-up one", async () => {
  const calls = [];
  const fetchFn = (path, options) => {
    calls.push({ path, options });
    if (path === "api/ping") {
      return Promise.reject(new Error("gone"));
    }
    return Promise.resolve({ ok: true, text: () => Promise.resolve("{}") });
  };
  await faces.bindingRequest(fetchFn)("GET", "api/faces/clusters", null);
  assert.equal("X-Pam-Binding" in calls[1].options.headers, false);
});

/* --- run -------------------------------------------------------------------------- */

(async () => {
  for (const [name, body] of tests) {
    await body();
    console.log("ok " + name);
  }
  console.log("ran " + tests.length);
})().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
