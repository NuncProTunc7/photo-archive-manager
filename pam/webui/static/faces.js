/*
 * pam faces label - the labeling page's script. M11 Task 10, Steps 6, 7 and 8; Task 11,
 * Steps 2, 3, 4, 5 and 6.
 *
 * WHAT THIS FILE IS. UX spec 6's workflow - one group at a time, biggest first, five face
 * crops, autocomplete from the people pam already knows, Save name / Not the same person /
 * Skip / Ignore, the progress line and the done state - plus Task 11's corrections: the
 * Ignored faces view that makes Ignore reversible, the split's preview / confirm / undo,
 * the one immediate undo after a name is saved, and the Manage people view where a name
 * already given is repaired (rename, filename name, hide, take faces off, move them, mark
 * them not a face, and a merge that names both people before anything is sent).
 *
 * THE SHAPE, AND IT IS THE WHOLE OF STEP 8 (the plan's Blocker 8.4). This project has
 * measured twice that nothing reads its JavaScript: one unclosed brace left 1862 tests
 * green over a page a browser refuses to parse (M783), and `node --check` closed only the
 * SYNTAX half of that. **Syntax validity cannot prove that a state transition works.** So
 * everything consequential here is a PURE FUNCTION over a plain state object, and the
 * controller that sequences them (`createFacesApp`) takes its transport as a PARAMETER.
 * `tests/js/faces_state_test.js` runs both under a real `node`, with a recording
 * transport, and `tests/test_faces_page.py` drives that. Only `paint()` and
 * `initFacesPage()` touch a document, and they are the thin part on purpose.
 *
 * WHY THE TRANSPORT IS INJECTED RATHER THAN `fetch` BEING STUBBED. CLAUDE.md's M697: a
 * test that asserts the OUTCOME cannot tell "never did the dangerous thing" from "did it
 * and left no trace". The dangerous things on this page are WRITING A NAME NOBODY TYPED
 * (vision R6) and CONFIRMING A SPLIT NOBODY SAW, and the only way to watch either is to
 * watch the CALL. A recording transport is that instrument: the Node tests assert that
 * loading a group issues exactly two GETs and no POST at all, and that `Not the same
 * person` issues a preview and never a confirm.
 *
 * VISION R6, RESTATED AS TWO RULES THIS FILE KEEPS. **pam never auto-labels**: the name
 * field starts empty for every group and no code path here writes a value into it - and
 * the same holds for Manage people's rename, filename and move fields, which the view
 * resets to the empty string and nothing else.
 * **pam never suggests a person for an unnamed cluster**: `/faces/persons` feeds the
 * `<datalist>` so that `Emma` and `emma` do not become two people, and that is all it
 * feeds. A name pam proposed is a name a tired user accepts.
 *
 * SKIP AND IGNORE ARE DIFFERENT THINGS. Skip is SESSION state in this tab, held in
 * `state.skipped`, written nowhere, and gone on reload - which is why the page offers a
 * way to bring skipped groups back rather than reporting a job finished. Ignore is the
 * persisted one (`cluster/{id}/dismiss`), and it is only honest because the Ignored faces
 * view lists every face it set aside with a Restore beside each: I3 forbids a one-way door,
 * and a door is one-way when nobody can FIND the way back.
 *
 * ONE UNDO, NEVER A STACK (Task 11 Step 5, and the maintainer's "server memory" ruling for
 * split). `state.undo` holds at most one offer - the most recent label or the most recent
 * split - and any other action withdraws it. A second label REPLACES the first label's
 * offer; it does not queue behind it, so the undo button can only ever reverse the write
 * the user has just watched happen.
 *
 * RELATIVE PATHS ONLY. Every route lives under the per-session token prefix the browser
 * supplies from the address bar; a root-relative path parses, looks right, drops that
 * prefix and 404s. The token is never written into a shipped asset - spec 8.2 puts it in
 * exactly two places, the printed URL and the address bar, and neither is a file.
 */

"use strict";

/* --- copy, spelled from UX spec 6 and UX spec 8's tone ----------------------------- */

var COPY = {
  done: "All clusters handled. People will appear in filenames on your next pam plan.",
  nameNeeded: "Type a name first. pam never proposes one for you.",
  loadFailed: "pam could not read that. Reload the page to try again.",
  allSkipped: "Every remaining group is skipped for this visit. Nothing has been lost.",
  notSeparated:
    "pam could not tell these faces apart, so it has not proposed a split. Nothing was" +
    " changed. If they really are different people, Skip this group for now.",
  nothingIgnored:
    "Nothing is ignored. Faces you ignore while naming appear here, and each one can be" +
    " brought back.",
  renameNeeded: "Type the new name first. Nothing was changed.",
  filenameNeeded:
    "Type the name to use in filenames first, or choose Stop using it. Nothing was changed.",
  selectFaces: "Select one or more faces first. Nothing was changed.",
  pickMerge: "Choose the person to merge into first. Nothing was changed.",
};

/* The seed for UX spec 6's "est. 3 min", used until the session has measured anything.
 *
 * M9's A2 IS THE REASON THIS IS NOT THE WHOLE ESTIMATE. Spec 8.1's ETA was implemented as
 * a fixed per-item constant and displayed 87.5 s for a run that took 0.08 s - a 1093x
 * overstatement, and an estimate that is wrong by three orders of magnitude is worse than
 * no estimate. So the constant seeds the FIRST group only; from the second onward the
 * page uses the mean of how long this user actually took, which is the one number that is
 * about this archive and this person rather than about a guess.
 */
var SECONDS_PER_CLUSTER = 12;

/* How many measured groups the mean is taken over. Bounded so that a long interruption
 * early in the session does not follow the estimate around for the rest of it. */
var MAX_SAMPLES = 10;

/* A18's two `review_state` values, as the Ignored faces view groups them. The WIRE words;
 * the user reads "Ignored" and "Not a face" (UX spec 8: no jargon leaks). */
var DISMISSED = "dismissed";
var NOT_A_FACE = "not_a_face";

/* --- pure state: every consequential transition on this page ---------------------- */

/* The page before anything has been read. `loaded` is false rather than the clusters
 * being empty, because "pam has read nothing yet" and "pam has no groups" are different
 * states and UX spec 5's four-state rule forbids drawing the second over the first. */
function initialState() {
  return {
    loaded: false,
    stopped: false,
    clusters: [],
    persons: [],
    skipped: [],
    expanded: null,
    named: 0,
    remaining: 0,
    hiddenBelowFloor: 0,
    floor: null,
    durations: [],
    note: "",
    requestError: "",
    undo: null,
    split: null,
    ignoredOpen: false,
    ignored: null,
    peopleOpen: false,
    managed: null,
    selected: [],
    mergePending: null,
  };
}

/* `GET api/faces/clusters`. THE ORDER IS THE SERVER'S AND IS NOT RE-SORTED HERE: spec 8.2
 * orders unnamed clusters largest first because UX spec 6 wants the highest naming payoff
 * first, and a page that sorted them again would be a second opinion about a product
 * decision. `named` and `remaining` are about the whole archive, not about this page. */
function withClusters(state, payload) {
  var next = copyState(state);
  next.clusters = payload.clusters.slice();
  next.named = payload.named;
  next.remaining = payload.remaining;
  next.hiddenBelowFloor = payload.hidden_below_floor || 0;
  next.floor = payload.floor === undefined ? null : payload.floor;
  next.loaded = true;
  return next;
}

/* P2 (M12 Task 5), HT-M11 Leg B: "N named - M remaining" did not move after a save. EVERY
 * write the page makes answers with the two archive-wide counts, read after the write, and
 * they are taken AS GIVEN - never computed here. A merge, a move or a restore into a named
 * person changes them in ways no arithmetic on this page can see. A response without them
 * leaves the counter where it was rather than guessing. */
function withProgress(state, response) {
  var next = copyState(state);
  if (typeof response.named === "number" && typeof response.remaining === "number") {
    next.named = response.named;
    next.remaining = response.remaining;
  }
  return next;
}

/* `GET api/faces/persons`. AUTOCOMPLETE ONLY - see this file's header. */
function withPersons(state, payload) {
  var next = copyState(state);
  next.persons = payload.persons.slice();
  return next;
}

/* The groups still on offer in this visit: the server's order, minus the skipped ones. */
function visibleClusters(state) {
  var out = [];
  for (var i = 0; i < state.clusters.length; i += 1) {
    if (state.skipped.indexOf(state.clusters[i].cluster_id) === -1) {
      out.push(state.clusters[i]);
    }
  }
  return out;
}

/* The one group being asked about, or null. Always the FIRST still on offer, so handling
 * one group moves to the next without a cursor that can drift out of the list. */
function currentCluster(state) {
  var visible = visibleClusters(state);
  return visible.length ? visible[0] : null;
}

/* Every action that is not an undo withdraws the undo offer and any open split proposal:
 * the offer is for the write the user is still looking at, and they have moved on. */
function movedOn(state) {
  var next = copyState(state);
  next.undo = null;
  next.split = null;
  next.note = "";
  next.requestError = "";
  return next;
}

/* Skip. SESSION STATE, WRITTEN NOWHERE, and deliberately so (UX spec 6). It issues no
 * request, which is the difference the page has to make legible: reload and it is back. */
function skipCurrent(state) {
  var cluster = currentCluster(state);
  if (cluster === null) {
    return state;
  }
  var next = movedOn(state);
  next.skipped = state.skipped.concat([cluster.cluster_id]);
  next.expanded = null;
  return next;
}

/* Bring every skipped group back. The counterpart of Skip being session state: without
 * it, skipping the last group would leave a page with nothing on it and no way back. */
function restoreSkipped(state) {
  var next = movedOn(state);
  next.skipped = [];
  return next;
}

/* `GET api/faces/cluster/{id}` landed: every face in the open group, so a user can look
 * before attaching one name to all of them (spec 8.2). */
function withExpanded(state, payload) {
  var next = copyState(state);
  next.expanded = { cluster_id: payload.cluster_id, faces: payload.faces.slice() };
  return next;
}

/* Remove one group from the queue after a write that took it off the server's list. The
 * counts are NOT touched here: they follow the write's response (`withProgress`, P2). */
function withoutCluster(state, clusterId) {
  var next = movedOn(state);
  next.clusters = state.clusters.filter(function (cluster) {
    return cluster.cluster_id !== clusterId;
  });
  next.expanded = null;
  return next;
}

/* `POST api/faces/cluster/{id}/label` landed.
 *
 * THE GROUP LEAVES THE QUEUE, and the two archive-wide counts move with it. `seconds` is
 * how long this group actually took and feeds the estimate; it is a measurement, not a
 * setting. The note is spec 6.2 rule 7's sentence - pam says what it changed - and it is
 * built from the RESPONSE, never from what the page believed it was about to do.
 *
 * THE UNDO OFFER CARRIES THE RESPONSE'S OWN `face_ids` (Step 5): exactly the faces this
 * write touched, never the group's listing. A response without them gets no offer - an
 * undo that has to guess which faces to unname is not an undo.
 */
function afterLabel(state, response, seconds) {
  var next = withProgress(withoutCluster(state, response.cluster_id), response);
  next.durations = state.durations.concat([seconds]).slice(-MAX_SAMPLES);
  next.note = labelSentence(response);
  var ids = response.face_ids;
  next.undo =
    ids && ids.length
      ? {
          kind: "label",
          cluster_id: response.cluster_id,
          label: response.label,
          face_ids: ids.slice(),
        }
      : null;
  return next;
}

/* `POST api/faces/cluster/{id}/dismiss` landed - UX spec 6's Ignore. The group leaves the
 * queue and the note names what was set aside and where to find it again (I3). */
function afterIgnore(state, response) {
  var next = withProgress(withoutCluster(state, response.cluster_id), response);
  next.note = ignoreSentence(response);
  return next;
}

/* `split` with `action: "preview"` landed. NOTHING HAS BEEN WRITTEN: the proposal is held
 * so the user can look at it, and the confirm control exists only when the server handed
 * out a token. The cluster id is the one the preview was POSTED to, so a confirm cannot go
 * anywhere else. */
function withSplitPreview(state, response, clusterId) {
  var next = movedOn(state);
  next.split = {
    cluster_id: clusterId,
    separated: response.separated === true && typeof response.token === "string",
    groups: (response.groups || []).slice(),
    unclustered: response.unclustered || { face_ids: [], faces: 0, photographs: 0 },
    token: typeof response.token === "string" ? response.token : null,
  };
  return next;
}

/* Keep them together. No request - nothing was written by the preview. */
function cancelSplit(state) {
  var next = copyState(state);
  next.split = null;
  next.requestError = "";
  return next;
}

/* `split` with `action: "confirm"` landed. The new undo offer is the split's, carrying the
 * server's undo token and the SAME cluster id the preview and the confirm went to. */
function afterSplitConfirm(state, response) {
  var clusterId = state.split ? state.split.cluster_id : response.cluster_id;
  var next = movedOn(state);
  next.expanded = null;
  next.note = splitSentence(response);
  next.undo =
    typeof response.undo === "string"
      ? { kind: "split", cluster_id: clusterId, token: response.undo }
      : null;
  return next;
}

/* `split` with `action: "undo"` landed. */
function afterSplitUndo(state, response) {
  var next = movedOn(state);
  next.note =
    "Put " +
    plural(response.faces, "face", "faces") +
    " across " +
    plural(response.photographs, "photograph", "photographs") +
    " back together as one group.";
  return next;
}

/* `faces/assign` with `person: null` landed - the label's undo. */
function afterLabelUndo(state, response) {
  var next = movedOn(state);
  next.note =
    "Took the name off " +
    plural(response.faces, "face", "faces") +
    " across " +
    plural(response.photographs, "photograph", "photographs") +
    ". The group will be offered again.";
  return next;
}

/* Open the Ignored faces view. Moving to it is moving on: the undo offer is withdrawn. */
function showIgnored(state) {
  var next = leavePeople(movedOn(state));
  next.ignoredOpen = true;
  return next;
}

/* `GET api/faces/ignored`. The counts are the SERVER's, so a restore moves them only when
 * the server says it did. */
function withIgnored(state, payload) {
  var next = copyState(state);
  next.ignored = { faces: payload.faces.slice(), counts: payload.counts };
  return next;
}

/* `faces/state` with `review_state: null` landed for one face. It leaves the list at
 * once; the Ignored view's counts follow from the next `GET api/faces/ignored`, and the
 * progress line from this response (P2). The sentence says where the face actually went
 * (P2's O8) - `restoreSentence`. */
function afterRestore(state, response, faceId) {
  var next = withProgress(state, response);
  if (state.ignored) {
    next.ignored = {
      faces: state.ignored.faces.filter(function (face) {
        return face.face_id !== faceId;
      }),
      counts: state.ignored.counts,
    };
  }
  next.note = restoreSentence(response);
  next.requestError = "";
  return next;
}

/* Back to naming. */
function closeIgnored(state) {
  var next = movedOn(state);
  next.ignoredOpen = false;
  next.ignored = null;
  return next;
}

/* --- Task 11 Step 2: Manage people -------------------------------------------------- */

/* The people view's own fields, emptied. Shared by opening the Ignored faces view and by
 * going back to naming, so no path out of the view leaves a half-open merge behind. */
function leavePeople(state) {
  var next = copyState(state);
  next.peopleOpen = false;
  next.managed = null;
  next.selected = [];
  next.mergePending = null;
  return next;
}

/* Open Manage people. Moving to it is moving on: the undo offer is withdrawn. */
function showPeople(state) {
  var next = movedOn(state);
  next.ignoredOpen = false;
  next.ignored = null;
  next.peopleOpen = true;
  return next;
}

/* Back to naming. */
function closePeople(state) {
  return leavePeople(movedOn(state));
}

/* `GET api/faces/person/{id}` landed - or `null`, which closes the open person. The
 * `version` moves on every load, so the page knows to redraw the faces and empty the
 * fields: a reload after a write is a fresh look at that person. Selection is cleared,
 * because a selection is about faces the user was looking at, and they have been re-read. */
function withManagedPerson(state, payload) {
  var next = copyState(state);
  next.selected = [];
  next.mergePending = null;
  if (payload === null) {
    next.managed = null;
    return next;
  }
  next.managed = {
    person: payload.person,
    faces: payload.faces.slice(),
    version: (state.managed ? state.managed.version : 0) + 1,
  };
  return next;
}

/* Select or unselect one face of the open person. No request. */
function toggleFaceSelection(state, faceId) {
  if (state.managed === null) {
    return state;
  }
  var known = state.managed.faces.some(function (face) {
    return face.face_id === faceId;
  });
  if (!known) {
    return state;
  }
  var next = copyState(state);
  next.selected =
    state.selected.indexOf(faceId) === -1
      ? state.selected.concat([faceId])
      : state.selected.filter(function (id) {
          return id !== faceId;
        });
  return next;
}

/* A write landed: the sentence is spec 6.2 rule 7's, built from the SERVER's answer, and
 * the selection and any merge proposal are spent. The list reload follows. */
function corrected(state, note) {
  var next = copyState(state);
  next.note = note;
  next.requestError = "";
  next.selected = [];
  next.mergePending = null;
  return next;
}

/* `POST api/faces/person/{id}` landed. `change` names what was asked for - "label",
 * "filename" or "hidden" - and every number in the sentence is the response's. */
function afterPersonUpdate(state, response, change) {
  var person = response.person;
  var scope =
    plural(response.faces, "face", "faces") +
    " across " +
    plural(response.photographs, "photograph", "photographs");
  var note;
  if (change === "label") {
    note = "Renamed to " + person.label + ". " + scope + " carry this name.";
  } else if (change === "filename") {
    note =
      "Filenames will use " +
      person.filename_value +
      " for " +
      person.label +
      ", on " +
      scope +
      ".";
  } else if (person.hidden) {
    note =
      "Hid " + person.label + ". " + scope + " will not put this name in filenames.";
  } else {
    note =
      person.label + " is shown again. " + scope + " can put this name in filenames.";
  }
  return corrected(state, note);
}

/* `faces/assign` or `faces/state` landed from Manage people. `change` is "unassign",
 * "move" or "not_a_face"; the counts are the server's. */
function afterFacesCorrected(state, response, change) {
  var scope =
    plural(response.faces, "face", "faces") +
    " across " +
    plural(response.photographs, "photograph", "photographs");
  var note;
  if (change === "move") {
    note = "Moved " + scope + " to " + response.label + ".";
  } else if (change === "not_a_face") {
    note =
      "Marked " +
      scope +
      " as not a face. Bring any of them back from Ignored faces.";
  } else {
    note = "Took the name off " + scope + ".";
  }
  return withProgress(corrected(state, note), response);
}

/* Merge, STEP ONE: a proposal and NOTHING SENT. Both people are looked up in the list the
 * page is showing, so the confirmation names them and their counts before any request. */
function proposeMerge(state, sourceId, intoId) {
  var source = personById(state, sourceId);
  var into = personById(state, intoId);
  if (source === null || into === null || source.id === into.id) {
    return afterRefusal(state, COPY.pickMerge);
  }
  var next = copyState(state);
  next.mergePending = { source: source, into: into };
  next.requestError = "";
  next.note = "";
  return next;
}

/* Keep them separate. No request - the proposal wrote nothing. */
function cancelMerge(state) {
  var next = copyState(state);
  next.mergePending = null;
  next.requestError = "";
  return next;
}

/* Merge landed. The note is the SERVER's own sentence, which names both people; the merged
 * person no longer exists, so nothing stays open. */
function afterMerge(state, response) {
  var next = withProgress(corrected(state, response.message), response);
  next.managed = null;
  return next;
}

function personById(state, id) {
  for (var i = 0; i < state.persons.length; i += 1) {
    if (state.persons[i].id === id) {
      return state.persons[i];
    }
  }
  return null;
}

/* What a merge WOULD do, said before anything is sent (Step 6). Both names, both sets of
 * counts, and the destructive half in plain words. */
function mergeSentence(pending) {
  var source = pending.source;
  var into = pending.into;
  return (
    "Merge " +
    source.label +
    " into " +
    into.label +
    "? " +
    plural(source.faces, "face", "faces") +
    " across " +
    plural(source.photographs, "photograph", "photographs") +
    " will move from " +
    source.label +
    " to " +
    into.label +
    ", who has " +
    plural(into.faces, "face", "faces") +
    " across " +
    plural(into.photographs, "photograph", "photographs") +
    ". " +
    source.label +
    " will no longer exist as a separate person, and a merge has no undo."
  );
}

/* A refusal the browser is meant to read: a 400 or a 409 carrying `{error}`. NOTHING ELSE
 * IN THE STATE MOVES. A 409 from `label` means the server refused and changed nothing, so
 * a page that removed the group anyway would be showing a write that did not happen. The
 * undo offer and an open split proposal survive too: the refused call changed neither. */
function afterRefusal(state, message) {
  var next = copyState(state);
  next.requestError = message || COPY.loadFailed;
  next.note = "";
  return next;
}

/* The server is gone (Ctrl+C in the terminal). UX spec 5's fourth state: the page goes
 * INERT and does not retry - a retry loop against a stopped server spins the browser and
 * hides the one thing the banner is there to say. */
function afterServerGone(state) {
  var next = copyState(state);
  next.stopped = true;
  return next;
}

/* --- the sentences ---------------------------------------------------------------- */

/* SPEC 6.2 RULE 7, and vision R6's reason for it: one click attached a name to every face
 * in the group, and "this is the action a user is most likely to regret". BOTH numbers,
 * because forty faces across three photographs and forty faces across forty photographs
 * are very different mistakes to have just made. Built from the response's own counts. */
function labelSentence(response) {
  return (
    "Named " +
    response.label +
    " on " +
    plural(response.faces, "face", "faces") +
    " across " +
    plural(response.photographs, "photograph", "photographs") +
    "."
  );
}

/* P2's O8: a restore says WHERE THE FACES WENT, read off the response's `person_labels`
 * (the distinct labels of the restored faces' people, `null` for a face with none).
 * "pam will offer it again with its group" was false for a face whose person was named -
 * HT-M11 watched one reattach straight to its person - so the named case is the maintainer's
 * own wording, and a restore spanning more than one destination names nobody. */
function restoreSentence(response) {
  var labels = response.person_labels || [null];
  var counts =
    "Brought back " +
    plural(response.faces, "face", "faces") +
    " from " +
    plural(response.photographs, "photograph", "photographs") +
    ".";
  if (labels.length > 1) {
    return counts + " Each goes back where it was before it was marked.";
  }
  if (labels[0] === null) {
    return counts + " pam will offer them again with their group.";
  }
  return (
    counts +
    (response.faces === 1 ? " It'll" : " They'll") +
    " be added back to " +
    labels[0] +
    "."
  );
}

/* P2's F5: the done state over groups the floor is hiding. Vision principle 9 forbids
 * dropping work invisibly, so the sentence counts them and names the threshold. */
function hiddenBelowFloorSentence(count, floor) {
  if (!count) {
    return "";
  }
  var threshold = " the detection threshold ([tiers] faces floor = " + floor + ")";
  return count === 1
    ? "1 more group holds only faces below" + threshold + " and is not shown."
    : count + " more groups hold only faces below" + threshold + " and are not shown.";
}

/* Rule 7 again, for Ignore: the server's counts, and where the faces went. */
function ignoreSentence(response) {
  return (
    "Ignored " +
    plural(response.faces, "face", "faces") +
    " across " +
    plural(response.photographs, "photograph", "photographs") +
    ". pam will stop offering them; bring any of them back from Ignored faces."
  );
}

/* What a PROPOSAL would do, said before anything is written. A proposal the server would
 * not make says so in plain words and offers nothing to confirm. */
function splitPreviewSentence(split) {
  if (!split.separated) {
    return COPY.notSeparated;
  }
  var total = 0;
  for (var i = 0; i < split.groups.length; i += 1) {
    total += split.groups[i].faces;
  }
  total += split.unclustered.faces;
  var sentence =
    "pam would split these " +
    plural(total, "face", "faces") +
    " into " +
    split.groups.length +
    " groups.";
  if (split.unclustered.faces > 0) {
    sentence +=
      " " +
      plural(split.unclustered.faces, "face", "faces") +
      " would be left out of every group.";
  }
  return sentence + " Nothing changes until you confirm.";
}

/* Rule 7 for a confirmed split, from the confirm response's own counts. */
function splitSentence(response) {
  var sentence =
    "Split " +
    plural(response.faces, "face", "faces") +
    " across " +
    plural(response.photographs, "photograph", "photographs") +
    " into " +
    response.cluster_ids.length +
    " groups. pam will offer each group separately.";
  if (response.unclustered > 0) {
    sentence +=
      " " + plural(response.unclustered, "face was", "faces were") + " left out of every group.";
  }
  return sentence;
}

/* The same disclosure BEFORE the click, which is the half that can still prevent the
 * regret. UX spec 6 draws "… 48 photos"; the scope sentence is the reason it is there. */
function scopeSentence(cluster) {
  return (
    plural(cluster.faces, "face", "faces") +
    " across " +
    plural(cluster.photographs, "photograph", "photographs") +
    ". One name will be attached to all of them."
  );
}

function plural(count, one, many) {
  return count + " " + (count === 1 ? one : many);
}

/* UX spec 6's progress line: "7 named · 16 remaining · est. 3 min". The counts are the
 * server's, so skipping a group does not make the progress bar move - a skipped group is
 * still an unnamed cluster in the archive, and saying otherwise would be a lie told by
 * arithmetic. */
function progressLine(state) {
  var parts = [state.named + " named", state.remaining + " remaining"];
  var estimate = estimateText(estimateSeconds(state.remaining, state.durations));
  if (estimate) {
    parts.push(estimate);
  }
  return parts.join(" · ");
}

/* Measured where there is a measurement, seeded where there is not yet one (A2). */
function estimateSeconds(remaining, durations) {
  if (remaining <= 0) {
    return 0;
  }
  var per = SECONDS_PER_CLUSTER;
  if (durations.length) {
    var total = 0;
    for (var i = 0; i < durations.length; i += 1) {
      total += durations[i];
    }
    per = total / durations.length;
  }
  return remaining * per;
}

/* NO FALSE PRECISION. Under a minute says so rather than claiming "est. 0 min", and
 * nothing left gets no estimate at all rather than "est. 0 min" beside a done panel. */
function estimateText(seconds) {
  if (seconds <= 0) {
    return "";
  }
  if (seconds < 60) {
    return "est. under a minute";
  }
  return "est. " + Math.round(seconds / 60) + " min";
}

/* UX spec 6's "Cluster 7 of 23 unnamed". Counted over what the page is OFFERING, so the
 * number the user reads matches the queue they are actually moving through. */
function positionLine(state) {
  var visible = visibleClusters(state);
  if (!visible.length) {
    return "";
  }
  return "Group " + (state.clusters.length - visible.length + 1) + " of " + state.clusters.length;
}

/* --- the view: one plain object describing what the page should show -------------- */

/* FIVE MODES AND THEY ARE NOT INTERCHANGEABLE. `empty` is a fresh archive where
 * `pam faces cluster` has not run - reporting "All clusters handled" over it would
 * summarize a job that never started as a success, which vision principle 9 forbids.
 * `skipped` is every remaining group set aside in THIS VISIT, which is not done either.
 * `done` is the real one. `ignored` is the Ignored faces view. `stopped` outranks all of
 * them: a dead server knows nothing.
 *
 * `nameValue` IS ALWAYS THE EMPTY STRING AND THAT IS THE POINT (vision R6). It is in the
 * view so that a change making pam propose a name is a visible, testable difference here
 * rather than a line of DOM code nobody executes.
 */
function view(state) {
  var cluster = currentCluster(state);
  var mode = viewMode(state, cluster);
  return {
    mode: mode,
    cluster: cluster,
    faces: faceStrip(state, cluster),
    nameValue: "",
    suggestions: state.persons.map(function (person) {
      return person.label;
    }),
    position: positionLine(state),
    scope: cluster ? scopeSentence(cluster) : "",
    progress: progressLine(state),
    doneNote: mode === "done" ? hiddenBelowFloorSentence(state.hiddenBelowFloor, state.floor) : "",
    note: state.note,
    requestError: state.requestError,
    skippedCount: state.skipped.length,
    undoOffered:
      state.undo !== null && mode !== "ignored" && mode !== "people" && mode !== "stopped",
    split: splitView(state, cluster),
    ignored: ignoredView(state),
    people: peopleView(state),
  };
}

function viewMode(state, cluster) {
  if (state.stopped) {
    return "stopped";
  }
  if (state.ignoredOpen) {
    return "ignored";
  }
  if (state.peopleOpen) {
    return "people";
  }
  if (!state.loaded) {
    return "loading";
  }
  if (cluster !== null) {
    return "cluster";
  }
  if (state.skipped.length) {
    return "skipped";
  }
  /* F5: groups the floor hides are groups pam made, so this is not a fresh archive. */
  if (
    state.clusters.length === 0 &&
    state.remaining === 0 &&
    state.named === 0 &&
    !state.hiddenBelowFloor
  ) {
    return "empty";
  }
  return "done";
}

/* The proposal for the group on screen, or null. `canConfirm` is true only for a proposal
 * the server made AND tokened - the page never offers to confirm a split it was refused. */
function splitView(state, cluster) {
  var split = state.split;
  if (split === null || cluster === null || split.cluster_id !== cluster.cluster_id) {
    return null;
  }
  return {
    message: splitPreviewSentence(split),
    canConfirm: split.separated,
    groups: split.groups.map(function (group) {
      return {
        caption:
          plural(group.faces, "face", "faces") +
          " across " +
          plural(group.photographs, "photograph", "photographs"),
        faces: group.face_ids.map(function (id) {
          return { face_id: id, filename: null };
        }),
      };
    }),
  };
}

/* The Ignored faces view, split by kind, with the SERVER's count for each. */
function ignoredView(state) {
  if (!state.ignoredOpen || state.ignored === null) {
    return null;
  }
  var faces = state.ignored.faces;
  var counts = state.ignored.counts || {};
  return {
    dismissed: faces.filter(function (face) {
      return face.review_state === DISMISSED;
    }),
    notAFace: faces.filter(function (face) {
      return face.review_state === NOT_A_FACE;
    }),
    dismissedCount: counts[DISMISSED] || 0,
    notAFaceCount: counts[NOT_A_FACE] || 0,
    empty: faces.length === 0,
  };
}

/* Manage people, as the page draws it, or null when the view is closed.
 *
 * `renameValue`, `filenameValue` and `moveValue` ARE ALWAYS THE EMPTY STRING (vision R6),
 * for `nameValue`'s reason: a change that made pam type a name into one of these boxes is
 * a visible, testable difference here rather than a line of DOM code nobody executes.
 * `key` changes on every load of the open person, and it is when the page empties the
 * fields and redraws the faces - so selecting a face does not wipe a name being typed. */
function peopleView(state) {
  if (!state.peopleOpen) {
    return null;
  }
  var managed = state.managed;
  var openId = managed ? managed.person.id : null;
  return {
    empty: state.persons.length === 0,
    persons: state.persons.map(function (person) {
      return {
        id: person.id,
        label: person.label,
        caption:
          plural(person.faces, "face", "faces") +
          " across " +
          plural(person.photographs, "photograph", "photographs"),
        flag: person.hidden ? "Hidden - not used in filenames" : "",
        open: person.id === openId,
      };
    }),
    panel: managed === null ? null : personPanel(state, managed),
    merge: state.mergePending === null ? null : { message: mergeSentence(state.mergePending) },
  };
}

function personPanel(state, managed) {
  var person = managed.person;
  return {
    key: person.id + ":" + managed.version,
    id: person.id,
    title: person.label,
    summary:
      plural(person.faces, "face", "faces") +
      " across " +
      plural(person.photographs, "photograph", "photographs") +
      (person.hidden
        ? ". Hidden - pam will not put this name in any filename. Unhide to use it again."
        : "."),
    /* P2's O4: a hidden person's panel does NOT say what filenames would use - HT-M11's
     * first-time reader could not reconcile the two lines. `null` means not rendered. */
    filenameLine: person.hidden ? null : "Used in filenames as: " + person.filename_value,
    hidden: person.hidden,
    hideLabel: person.hidden ? "Unhide" : "Hide",
    faces: managed.faces.map(function (face, index) {
      return {
        face_id: face.face_id,
        /* P2's O5: a checkbox is named by its position and its photograph, never its id. */
        accessibleName: faceCheckboxName(index + 1, managed.faces.length, face.filename),
        filename: face.filename,
        below_floor: face.below_floor === true,
        selected: state.selected.indexOf(face.face_id) !== -1,
      };
    }),
    selectedCount: state.selected.length,
    renameValue: "",
    filenameValue: "",
    moveValue: "",
    mergeTargets: state.persons
      .filter(function (other) {
        return other.id !== person.id;
      })
      .map(function (other) {
        return { id: other.id, label: other.label };
      }),
  };
}

/* P2's O5: `Face {i} of {n} in {filename}`. A face whose photograph has no file row pam
 * can point at has no filename to name; it keeps its position rather than a raw id. */
function faceCheckboxName(position, total, filename) {
  var name = "Face " + position + " of " + total;
  return filename ? name + " in " + filename : name;
}

/* The faces to draw: the server's spread sample until the user expands, then every face
 * in the group. The sample is chosen server-side for detection quality and spread (spec
 * 8.2), so a group of forty faces from one shot does not show the same face five times. */
function faceStrip(state, cluster) {
  if (cluster === null) {
    return [];
  }
  if (state.expanded && state.expanded.cluster_id === cluster.cluster_id) {
    return state.expanded.faces;
  }
  return cluster.sample;
}

/* The crop's URL. RELATIVE, for the reason in this file's header. */
function cropPath(face) {
  return "api/faces/crop/" + face.face_id;
}

/* UX spec 7: thumbnails get `alt` = the old filename. A face knows which photograph it
 * came from only when that content still has a `files` row, so the fallback says what the
 * picture is rather than leaving a blank the screen reader reads as nothing. */
function cropAlt(face) {
  return face.filename ? "Face detected in " + face.filename : "A detected face";
}

function copyState(state) {
  return {
    loaded: state.loaded,
    stopped: state.stopped,
    clusters: state.clusters,
    persons: state.persons,
    skipped: state.skipped,
    expanded: state.expanded,
    named: state.named,
    remaining: state.remaining,
    hiddenBelowFloor: state.hiddenBelowFloor,
    floor: state.floor,
    durations: state.durations,
    note: state.note,
    requestError: state.requestError,
    undo: state.undo,
    split: state.split,
    ignoredOpen: state.ignoredOpen,
    ignored: state.ignored,
    peopleOpen: state.peopleOpen,
    managed: state.managed,
    selected: state.selected,
    mergePending: state.mergePending,
  };
}

/* --- the controller: sequences the pure functions, owns no DOM -------------------- */

/* A refusal carrying a sentence, as distinct from the transport failing. */
function FacesRefusal(message) {
  this.message = message;
  this.name = "FacesRefusal";
}

/* `request(method, path, body)` -> Promise of the parsed payload, rejecting with a
 * `FacesRefusal` for a 4xx/5xx and with anything else for a transport failure.
 * `onState(state)` is called after every transition. `now()` returns seconds.
 *
 * NOT A DOM IN SIGHT, which is what lets `node` drive the whole workflow. */
function createFacesApp(options) {
  var request = options.request;
  var onState = options.onState || function () {};
  var now =
    options.now ||
    function () {
      return Date.now() / 1000;
    };
  var state = initialState();
  var openedAt = now();

  function settle(next) {
    state = next;
    onState(state);
    return state;
  }

  function failed(error) {
    if (error instanceof FacesRefusal) {
      return settle(afterRefusal(state, error.message));
    }
    return settle(afterServerGone(state));
  }

  /* The queue, re-read from the server after a write that re-shaped it. */
  function reloadQueue() {
    return request("GET", "api/faces/clusters", null).then(function (clusters) {
      openedAt = now();
      return settle(withClusters(state, clusters));
    });
  }

  function splitPath(clusterId) {
    return "api/faces/cluster/" + clusterId + "/split";
  }

  function personPath(personId) {
    return "api/faces/person/" + personId;
  }

  /* Manage people after a write: the list re-read, then the open person, if still open. */
  function reloadPeople() {
    return request("GET", "api/faces/persons", null).then(function (persons) {
      settle(withPersons(state, persons));
      var managed = state.managed;
      if (managed === null) {
        return state;
      }
      var still = persons.persons.some(function (person) {
        return person.id === managed.person.id;
      });
      if (!still) {
        return settle(withManagedPerson(state, null));
      }
      return request("GET", personPath(managed.person.id), null).then(function (payload) {
        return settle(withManagedPerson(state, payload));
      });
    });
  }

  /* One Manage people write: the request, the server's sentence, then the reloads. A
   * refusal lands in `failed`, which moves nothing but the sentence. */
  function manageWrite(path, body, after) {
    return request("POST", path, body)
      .then(function (response) {
        settle(after(state, response));
        return reloadPeople();
      })
      .catch(failed);
  }

  /* The selected faces of the open person, or null after telling the user to pick some. */
  function selectedOrRefuse() {
    if (state.managed === null) {
      return null;
    }
    if (!state.selected.length) {
      settle(afterRefusal(state, COPY.selectFaces));
      return null;
    }
    return state.selected.slice();
  }

  return {
    state: function () {
      return state;
    },
    view: function () {
      return view(state);
    },

    /* The first paint's two reads. BOTH ARE GETS AND THERE IS NO THIRD CALL - the Node
     * test asserts exactly that, which is how "pam never auto-labels" is measured. */
    load: function () {
      return request("GET", "api/faces/clusters", null)
        .then(function (clusters) {
          settle(withClusters(state, clusters));
          return request("GET", "api/faces/persons", null);
        })
        .then(function (persons) {
          openedAt = now();
          return settle(withPersons(state, persons));
        })
        .catch(failed);
    },

    /* Every face in the open group. A read, and the only thing it changes is what is
     * drawn. */
    expand: function () {
      var cluster = currentCluster(state);
      if (cluster === null) {
        return Promise.resolve(state);
      }
      return request("GET", "api/faces/cluster/" + cluster.cluster_id, null)
        .then(function (payload) {
          return settle(withExpanded(state, payload));
        })
        .catch(failed);
    },

    /* The labeling write. A blank name never reaches the socket: the server refuses it
     * too, and would be right to, but a round trip to be told what the page already knows
     * is a round trip that teaches the user nothing. */
    save: function (typed) {
      var name = (typed || "").trim();
      var cluster = currentCluster(state);
      if (cluster === null) {
        return Promise.resolve(state);
      }
      if (!name) {
        return Promise.resolve(settle(afterRefusal(state, COPY.nameNeeded)));
      }
      var startedAt = openedAt;
      return request("POST", "api/faces/cluster/" + cluster.cluster_id + "/label", {
        person: name,
      })
        .then(function (response) {
          var elapsed = Math.max(0, now() - startedAt);
          openedAt = now();
          var next = settle(afterLabel(state, response, elapsed));
          return request("GET", "api/faces/persons", null).then(function (persons) {
            return settle(withPersons(next, persons));
          });
        })
        .catch(failed);
    },

    /* No request, by design. */
    skip: function () {
      openedAt = now();
      return settle(skipCurrent(state));
    },

    restoreSkipped: function () {
      openedAt = now();
      return settle(restoreSkipped(state));
    },

    /* UX spec 6's Ignore: ONE dismiss for the group on screen, then the queue re-read. */
    ignore: function () {
      var cluster = currentCluster(state);
      if (cluster === null) {
        return Promise.resolve(state);
      }
      return request("POST", "api/faces/cluster/" + cluster.cluster_id + "/dismiss", {})
        .then(function (response) {
          settle(afterIgnore(state, response));
          return reloadQueue();
        })
        .catch(failed);
    },

    /* UX spec 6's Not the same person. THE PREVIEW AND NOTHING ELSE: this method cannot
     * confirm, and the confirm below cannot run without a token a preview handed out. */
    splitPreview: function () {
      var cluster = currentCluster(state);
      if (cluster === null) {
        return Promise.resolve(state);
      }
      var clusterId = cluster.cluster_id;
      return request("POST", splitPath(clusterId), { action: "preview" })
        .then(function (response) {
          return settle(withSplitPreview(state, response, clusterId));
        })
        .catch(failed);
    },

    confirmSplit: function () {
      var split = state.split;
      if (split === null || !split.separated || split.token === null) {
        return Promise.resolve(state);
      }
      return request("POST", splitPath(split.cluster_id), {
        action: "confirm",
        token: split.token,
      })
        .then(function (response) {
          settle(afterSplitConfirm(state, response));
          return reloadQueue();
        })
        .catch(failed);
    },

    /* No request: the preview wrote nothing, so there is nothing to take back. */
    cancelSplit: function () {
      return settle(cancelSplit(state));
    },

    /* The one undo offer, whichever write it belongs to. */
    undo: function () {
      var offer = state.undo;
      if (offer === null) {
        return Promise.resolve(state);
      }
      var call =
        offer.kind === "label"
          ? request("POST", "api/faces/faces/assign", {
              face_ids: offer.face_ids.slice(),
              person: null,
            }).then(function (response) {
              return afterLabelUndo(state, response);
            })
          : request("POST", splitPath(offer.cluster_id), {
              action: "undo",
              token: offer.token,
            }).then(function (response) {
              return afterSplitUndo(state, response);
            });
      return call
        .then(function (next) {
          settle(next);
          return reloadQueue();
        })
        .catch(failed);
    },

    /* The Ignored faces view: a read, and the only way to find a face Ignore set aside. */
    openIgnored: function () {
      settle(showIgnored(state));
      return request("GET", "api/faces/ignored", null)
        .then(function (payload) {
          return settle(withIgnored(state, payload));
        })
        .catch(failed);
    },

    /* Restore ONE face: its `review_state` cleared, and the list re-read for the counts. */
    restore: function (faceId) {
      return request("POST", "api/faces/faces/state", {
        face_ids: [faceId],
        review_state: null,
      })
        .then(function (response) {
          settle(afterRestore(state, response, faceId));
          return request("GET", "api/faces/ignored", null);
        })
        .then(function (payload) {
          return settle(withIgnored(state, payload));
        })
        .catch(failed);
    },

    /* Back to naming; the queue is re-read, because a restored face rejoins its group. */
    closeIgnored: function () {
      settle(closeIgnored(state));
      return reloadQueue().catch(failed);
    },

    /* --- Manage people (Task 11 Step 2) --- */

    /* A read: every named person, hidden ones included. */
    openPeople: function () {
      settle(showPeople(state));
      return request("GET", "api/faces/persons", null)
        .then(function (persons) {
          return settle(withPersons(state, persons));
        })
        .catch(failed);
    },

    /* A read: one person's faces, for correcting. */
    expandPerson: function (personId) {
      return request("GET", personPath(personId), null)
        .then(function (payload) {
          return settle(withManagedPerson(state, payload));
        })
        .catch(failed);
    },

    /* No request. */
    toggleFace: function (faceId) {
      return settle(toggleFaceSelection(state, faceId));
    },

    /* Exactly `{label}` - nothing else rides along. */
    renamePerson: function (typed) {
      var name = (typed || "").trim();
      if (state.managed === null) {
        return Promise.resolve(state);
      }
      if (!name) {
        return Promise.resolve(settle(afterRefusal(state, COPY.renameNeeded)));
      }
      return manageWrite(personPath(state.managed.person.id), { label: name }, function (s, r) {
        return afterPersonUpdate(s, r, "label");
      });
    },

    setFilenameLabel: function (typed) {
      var name = (typed || "").trim();
      if (state.managed === null) {
        return Promise.resolve(state);
      }
      if (!name) {
        return Promise.resolve(settle(afterRefusal(state, COPY.filenameNeeded)));
      }
      return manageWrite(
        personPath(state.managed.person.id),
        { filename_label: name },
        function (s, r) {
          return afterPersonUpdate(s, r, "filename");
        }
      );
    },

    /* CLEARING IS `null`, NEVER "": spec 8.2's `filename_label|null`, and `null` is what
     * the server reads as "no override". */
    clearFilenameLabel: function () {
      if (state.managed === null) {
        return Promise.resolve(state);
      }
      return manageWrite(
        personPath(state.managed.person.id),
        { filename_label: null },
        function (s, r) {
          return afterPersonUpdate(s, r, "filename");
        }
      );
    },

    /* Exactly `{hidden}`. Hiding is not clearing the filename name. */
    setHidden: function (hidden) {
      if (state.managed === null) {
        return Promise.resolve(state);
      }
      return manageWrite(
        personPath(state.managed.person.id),
        { hidden: hidden === true },
        function (s, r) {
          return afterPersonUpdate(s, r, "hidden");
        }
      );
    },

    unassignFaces: function () {
      var ids = selectedOrRefuse();
      if (ids === null) {
        return Promise.resolve(state);
      }
      return manageWrite("api/faces/faces/assign", { face_ids: ids, person: null }, function (s, r) {
        return afterFacesCorrected(s, r, "unassign");
      });
    },

    moveFaces: function (typed) {
      var name = (typed || "").trim();
      if (state.managed === null) {
        return Promise.resolve(state);
      }
      if (!name) {
        return Promise.resolve(settle(afterRefusal(state, COPY.nameNeeded)));
      }
      var ids = selectedOrRefuse();
      if (ids === null) {
        return Promise.resolve(state);
      }
      return manageWrite("api/faces/faces/assign", { face_ids: ids, person: name }, function (s, r) {
        return afterFacesCorrected(s, r, "move");
      });
    },

    /* `not_a_face`, the word A18 gives a false detection - NOT `dismissed`, which is Ignore. */
    markNotAFace: function () {
      var ids = selectedOrRefuse();
      if (ids === null) {
        return Promise.resolve(state);
      }
      return manageWrite(
        "api/faces/faces/state",
        { face_ids: ids, review_state: NOT_A_FACE },
        function (s, r) {
          return afterFacesCorrected(s, r, "not_a_face");
        }
      );
    },

    /* Merge step one. NO REQUEST: the confirmation is drawn from the list on screen. */
    proposeMerge: function (intoId) {
      if (state.managed === null) {
        return state;
      }
      return settle(proposeMerge(state, state.managed.person.id, Number(intoId)));
    },

    /* No request. */
    cancelMerge: function () {
      return settle(cancelMerge(state));
    },

    /* Merge step two, and the only request a merge makes: the MERGED person's path, the
     * SURVIVING person's id in the body. */
    confirmMerge: function () {
      var pending = state.mergePending;
      if (pending === null) {
        return Promise.resolve(state);
      }
      return manageWrite(
        personPath(pending.source.id) + "/merge",
        { into: pending.into.id },
        afterMerge
      );
    },

    /* Back to naming; the queue is re-read, because a face whose name was taken off rejoins
     * the groups on offer. */
    closePeople: function () {
      settle(closePeople(state));
      return reloadQueue().catch(failed);
    },
  };
}

/* --- the document layer: the only part of this file that knows what a page is ----- */

function elements() {
  return {
    faceStrip: document.getElementById("face-strip"),
    photoCount: document.getElementById("photo-count"),
    groupPosition: document.getElementById("group-position"),
    nameInput: document.getElementById("name-input"),
    personList: document.getElementById("person-list"),
    nameForm: document.getElementById("name-form"),
    saveName: document.getElementById("save-name"),
    expandCluster: document.getElementById("expand-cluster"),
    notSamePerson: document.getElementById("not-same-person"),
    skipGroup: document.getElementById("skip-group"),
    ignoreGroup: document.getElementById("ignore-group"),
    splitPanel: document.getElementById("split-panel"),
    splitMessage: document.getElementById("split-message"),
    splitGroups: document.getElementById("split-groups"),
    confirmSplit: document.getElementById("confirm-split"),
    cancelSplit: document.getElementById("cancel-split"),
    unskip: document.getElementById("unskip"),
    progressLine: document.getElementById("progress-line"),
    actionNote: document.getElementById("action-note"),
    undoLast: document.getElementById("undo-last"),
    requestError: document.getElementById("request-error"),
    donePanel: document.getElementById("done-panel"),
    doneHidden: document.getElementById("done-hidden"),
    emptyPanel: document.getElementById("empty-panel"),
    skippedPanel: document.getElementById("skipped-panel"),
    groupRegion: document.getElementById("group-region"),
    errorBanner: document.getElementById("error-banner"),
    showIgnored: document.getElementById("show-ignored"),
    closeIgnored: document.getElementById("close-ignored"),
    ignoredRegion: document.getElementById("ignored-region"),
    ignoredEmpty: document.getElementById("ignored-empty"),
    ignoredCount: document.getElementById("ignored-count"),
    ignoredList: document.getElementById("ignored-list"),
    notFaceCount: document.getElementById("not-face-count"),
    notFaceList: document.getElementById("not-face-list"),
    showPeople: document.getElementById("show-people"),
    closePeople: document.getElementById("close-people"),
    peopleRegion: document.getElementById("people-region"),
    peopleEmpty: document.getElementById("people-empty"),
    peopleList: document.getElementById("people-list"),
    personPanel: document.getElementById("person-panel"),
    personTitle: document.getElementById("person-title"),
    personSummary: document.getElementById("person-summary"),
    filenameCurrent: document.getElementById("filename-current"),
    renameForm: document.getElementById("rename-form"),
    renameInput: document.getElementById("rename-input"),
    renamePerson: document.getElementById("rename-person"),
    filenameForm: document.getElementById("filename-form"),
    filenameInput: document.getElementById("filename-input"),
    setFilename: document.getElementById("set-filename"),
    clearFilename: document.getElementById("clear-filename"),
    toggleHidden: document.getElementById("toggle-hidden"),
    personFaces: document.getElementById("person-faces"),
    selectedCount: document.getElementById("selected-count"),
    moveForm: document.getElementById("move-form"),
    moveInput: document.getElementById("move-input"),
    moveFaces: document.getElementById("move-faces"),
    unassignFaces: document.getElementById("unassign-faces"),
    notAFace: document.getElementById("not-a-face"),
    mergeInto: document.getElementById("merge-into"),
    proposeMerge: document.getElementById("propose-merge"),
    mergePanel: document.getElementById("merge-panel"),
    mergeMessage: document.getElementById("merge-message"),
    confirmMerge: document.getElementById("confirm-merge"),
    cancelMerge: document.getElementById("cancel-merge"),
  };
}

function make(tag, className, text) {
  var node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined && text !== null) {
    node.textContent = text;
  }
  return node;
}

function clear(node) {
  while (node.firstChild) {
    node.removeChild(node.firstChild);
  }
}

/* `textContent` everywhere, never `innerHTML`: a filename is archive data, and a page
 * that pastes archive data into markup is a page whose behaviour depends on what a camera
 * happened to write. */
function paint(el, snapshot, app) {
  el.errorBanner.hidden = snapshot.mode !== "stopped";
  el.donePanel.hidden = snapshot.mode !== "done";
  el.doneHidden.textContent = snapshot.doneNote;
  el.doneHidden.hidden = !snapshot.doneNote;
  el.emptyPanel.hidden = snapshot.mode !== "empty";
  el.skippedPanel.hidden = snapshot.mode !== "skipped";
  el.groupRegion.hidden = snapshot.mode !== "cluster";
  el.ignoredRegion.hidden = snapshot.mode !== "ignored";
  el.showIgnored.hidden = snapshot.mode === "ignored";
  el.peopleRegion.hidden = snapshot.mode !== "people";
  el.showPeople.hidden = snapshot.mode === "people";

  el.groupPosition.textContent = snapshot.position;
  el.photoCount.textContent = snapshot.scope;
  el.progressLine.textContent = snapshot.progress;

  el.actionNote.textContent = snapshot.note;
  el.actionNote.hidden = !snapshot.note;
  el.undoLast.hidden = !snapshot.undoOffered;
  el.requestError.textContent = snapshot.requestError;
  el.requestError.hidden = !snapshot.requestError;

  /* The field is set from the view, and the view's value is always empty (vision R6). */
  el.nameInput.value = snapshot.nameValue;

  clear(el.personList);
  for (var i = 0; i < snapshot.suggestions.length; i += 1) {
    var option = make("option", null, null);
    option.value = snapshot.suggestions[i];
    el.personList.appendChild(option);
  }

  clear(el.faceStrip);
  for (var f = 0; f < snapshot.faces.length; f += 1) {
    el.faceStrip.appendChild(faceCard(snapshot.faces[f]));
  }

  paintSplit(el, snapshot.split);
  paintIgnored(el, snapshot.ignored, app);
  paintPeople(el, snapshot.people, app);

  if (snapshot.mode === "stopped") {
    var controls = document.querySelectorAll("button, input");
    for (var c = 0; c < controls.length; c += 1) {
      controls[c].disabled = true;
    }
  }
}

function paintSplit(el, split) {
  el.splitPanel.hidden = split === null;
  clear(el.splitGroups);
  if (split === null) {
    return;
  }
  el.splitMessage.textContent = split.message;
  el.confirmSplit.hidden = !split.canConfirm;
  for (var g = 0; g < split.groups.length; g += 1) {
    var group = make("div", "split-group", null);
    group.appendChild(make("p", "split-caption", split.groups[g].caption));
    for (var f = 0; f < split.groups[g].faces.length; f += 1) {
      group.appendChild(faceCard(split.groups[g].faces[f]));
    }
    el.splitGroups.appendChild(group);
  }
}

function paintIgnored(el, ignored, app) {
  clear(el.ignoredList);
  clear(el.notFaceList);
  if (ignored === null) {
    return;
  }
  el.ignoredEmpty.hidden = !ignored.empty;
  el.ignoredCount.textContent = String(ignored.dismissedCount);
  el.notFaceCount.textContent = String(ignored.notAFaceCount);
  fillIgnored(el.ignoredList, ignored.dismissed, app);
  fillIgnored(el.notFaceList, ignored.notAFace, app);
}

function fillIgnored(list, faces, app) {
  faces.forEach(function (face) {
    var card = make("div", "ignored-card", null);
    card.appendChild(faceCard(face));
    var restore = make("button", "restore-face", "Restore");
    restore.type = "button";
    restore.addEventListener("click", function () {
      app.restore(face.face_id);
    });
    card.appendChild(restore);
    list.appendChild(card);
  });
}

function paintPeople(el, people, app) {
  clear(el.peopleList);
  if (people === null) {
    el.personPanel.hidden = true;
    el.mergePanel.hidden = true;
    el.personPanel.removeAttribute("data-key");
    return;
  }
  el.peopleEmpty.hidden = !people.empty;
  people.persons.forEach(function (person) {
    var row = make("div", "person-row", null);
    var open = make("button", "person-open", person.label);
    open.type = "button";
    open.addEventListener("click", function () {
      app.expandPerson(person.id);
    });
    row.appendChild(open);
    row.appendChild(make("span", "person-counts", person.caption));
    if (person.flag) {
      row.appendChild(make("span", "person-hidden", person.flag));
    }
    el.peopleList.appendChild(row);
  });

  el.mergePanel.hidden = people.merge === null;
  el.mergeMessage.textContent = people.merge === null ? "" : people.merge.message;

  var panel = people.panel;
  el.personPanel.hidden = panel === null;
  if (panel === null) {
    el.personPanel.removeAttribute("data-key");
    return;
  }
  el.personTitle.textContent = panel.title;
  el.personSummary.textContent = panel.summary;
  el.filenameCurrent.textContent = panel.filenameLine || "";
  el.filenameCurrent.hidden = panel.filenameLine === null;
  el.toggleHidden.textContent = panel.hideLabel;
  el.selectedCount.textContent = panel.selectedCount ? panel.selectedCount + " selected" : "";

  /* A fresh load of this person: empty the fields (vision R6) and redraw the faces. */
  if (el.personPanel.getAttribute("data-key") !== panel.key) {
    el.personPanel.setAttribute("data-key", panel.key);
    el.renameInput.value = panel.renameValue;
    el.filenameInput.value = panel.filenameValue;
    el.moveInput.value = panel.moveValue;
    clear(el.mergeInto);
    panel.mergeTargets.forEach(function (target) {
      var option = make("option", null, target.label);
      option.value = String(target.id);
      el.mergeInto.appendChild(option);
    });
    clear(el.personFaces);
    panel.faces.forEach(function (face) {
      el.personFaces.appendChild(personFaceCard(face, app));
    });
  }
  var boxes = el.personFaces.getElementsByTagName("input");
  for (var b = 0; b < boxes.length; b += 1) {
    boxes[b].checked = panel.faces.some(function (face) {
      return String(face.face_id) === boxes[b].value && face.selected;
    });
  }
}

/* One of the open person's faces: a checkbox, the crop, and - for a detection below the
 * configured threshold - a word saying pam is less sure of it. */
function personFaceCard(face, app) {
  var card = make("label", "person-face", null);
  var box = make("input", "face-select", null);
  box.type = "checkbox";
  box.value = String(face.face_id);
  box.setAttribute("aria-label", face.accessibleName);
  box.addEventListener("change", function () {
    app.toggleFace(face.face_id);
  });
  card.appendChild(box);
  card.appendChild(faceCard(face));
  if (face.below_floor) {
    card.appendChild(make("span", "below-floor", "less certain"));
  }
  return card;
}

/* A crop that will not load gets a card that SAYS SO. Every unavailable state behind
 * `/faces/crop` is a 404 with a sentence - a moved photograph, a cloud placeholder pam
 * will not hydrate (vision R2), a geometry document that does not match the raster - and
 * a broken image icon would report all of them as nothing at all. */
function faceCard(face) {
  var wrapper = make("div", "face-card", null);
  var image = make("img", "face-crop", null);
  image.src = cropPath(face);
  image.alt = cropAlt(face);
  image.addEventListener("error", function () {
    wrapper.replaceChild(make("div", "face-missing", "no crop"), image);
  });
  wrapper.appendChild(image);
  return wrapper;
}

/* The browser's transport. A NETWORK failure and a REFUSAL are different things: `fetch`
 * rejecting means the server is gone, which makes the page inert; a 4xx/5xx carries
 * `{error}`, which is a sentence to show beside the control that caused it.
 *
 * THE BINDING (M14 review R1). Under `pam start` the server refuses a write that does not name
 * the binding its page was loaded on (`X-Pam-Binding`, R1-F1), so a page left open on a folder
 * pam has since left cannot write into the new one. The list learns the binding from
 * `api/ping`; this page did not, and every write was refused with "pam is working on another
 * folder now" while pam was on the very same folder. The page now pings ONCE, when the
 * transport is made at load, and names that binding on every request it sends - never a binding
 * it learned later, which would defeat the check. `fetchFn` is the seam the Node driver
 * records through. */
var PING_PATH = "api/ping";
var BINDING_HEADER = "X-Pam-Binding";

function bindingRequest(fetchFn) {
  var binding = fetchFn(PING_PATH, { method: "GET" }).then(
    function (response) {
      return response.ok ? response.json() : null;
    },
    function () {
      return null;
    }
  ).then(function (ping) {
    if (ping === null || ping.binding === null || ping.binding === undefined) {
      return null;
    }
    return String(ping.binding);
  });
  return function (method, path, body) {
    return binding.then(function (sent) {
      var options = { method: method, headers: {} };
      if (sent !== null) {
        options.headers[BINDING_HEADER] = sent;
      }
      if (body !== null && body !== undefined) {
        options.headers["Content-Type"] = "application/json";
        options.body = JSON.stringify(body);
      }
      return fetchFn(path, options).then(readResponse);
    });
  };
}

/* The page's own transport: the browser's `fetch`, through the binding. Named and exported so
 * the Node driver executes the transport the page really uses, not a copy of it (M3500). */
function pageRequest() {
  return bindingRequest(function (path, options) {
    return fetch(path, options);
  });
}

function readResponse(response) {
  return response.text().then(function (text) {
    var payload = null;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch (badJson) {
        payload = null;
      }
    }
    if (!response.ok) {
      throw new FacesRefusal(payload && payload.error ? payload.error : COPY.loadFailed);
    }
    return payload;
  });
}

function initFacesPage() {
  var el = elements();
  var app = createFacesApp({
    request: pageRequest(),
    onState: function () {
      paint(el, app.view(), app);
    },
  });

  el.nameForm.addEventListener("submit", function (event) {
    event.preventDefault();
    app.save(el.nameInput.value);
  });
  el.skipGroup.addEventListener("click", function () {
    app.skip();
  });
  el.unskip.addEventListener("click", function () {
    app.restoreSkipped();
  });
  el.expandCluster.addEventListener("click", function () {
    app.expand();
  });
  el.ignoreGroup.addEventListener("click", function () {
    app.ignore();
  });
  el.notSamePerson.addEventListener("click", function () {
    app.splitPreview();
  });
  el.confirmSplit.addEventListener("click", function () {
    app.confirmSplit();
  });
  el.cancelSplit.addEventListener("click", function () {
    app.cancelSplit();
  });
  el.undoLast.addEventListener("click", function () {
    app.undo();
  });
  el.showIgnored.addEventListener("click", function () {
    app.openIgnored();
  });
  el.closeIgnored.addEventListener("click", function () {
    app.closeIgnored();
  });
  el.showPeople.addEventListener("click", function () {
    app.openPeople();
  });
  el.closePeople.addEventListener("click", function () {
    app.closePeople();
  });
  el.renameForm.addEventListener("submit", function (event) {
    event.preventDefault();
    app.renamePerson(el.renameInput.value);
  });
  el.filenameForm.addEventListener("submit", function (event) {
    event.preventDefault();
    app.setFilenameLabel(el.filenameInput.value);
  });
  el.clearFilename.addEventListener("click", function () {
    app.clearFilenameLabel();
  });
  el.toggleHidden.addEventListener("click", function () {
    var managed = app.state().managed;
    app.setHidden(!(managed && managed.person.hidden));
  });
  el.unassignFaces.addEventListener("click", function () {
    app.unassignFaces();
  });
  el.moveForm.addEventListener("submit", function (event) {
    event.preventDefault();
    app.moveFaces(el.moveInput.value);
  });
  el.notAFace.addEventListener("click", function () {
    app.markNotAFace();
  });
  el.proposeMerge.addEventListener("click", function () {
    app.proposeMerge(el.mergeInto.value);
  });
  el.confirmMerge.addEventListener("click", function () {
    app.confirmMerge();
  });
  el.cancelMerge.addEventListener("click", function () {
    app.cancelMerge();
  });

  paint(el, app.view(), app);
  app.load();
  return app;
}

/* Guarded so this file loads under a plain `node` run with no DOM, which is what Step 8's
 * pure-function tests use. */
if (typeof document !== "undefined") {
  initFacesPage();
}

/* The seam `tests/js/faces_state_test.js` requires. Absent in a browser, where `module`
 * is undefined and this block is skipped. */
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    COPY: COPY,
    bindingRequest: bindingRequest,
    pageRequest: pageRequest,
    SECONDS_PER_CLUSTER: SECONDS_PER_CLUSTER,
    FacesRefusal: FacesRefusal,
    initialState: initialState,
    withClusters: withClusters,
    withPersons: withPersons,
    withExpanded: withExpanded,
    visibleClusters: visibleClusters,
    currentCluster: currentCluster,
    skipCurrent: skipCurrent,
    restoreSkipped: restoreSkipped,
    afterLabel: afterLabel,
    afterIgnore: afterIgnore,
    withSplitPreview: withSplitPreview,
    cancelSplit: cancelSplit,
    afterSplitConfirm: afterSplitConfirm,
    afterSplitUndo: afterSplitUndo,
    afterLabelUndo: afterLabelUndo,
    showIgnored: showIgnored,
    withIgnored: withIgnored,
    afterRestore: afterRestore,
    restoreSentence: restoreSentence,
    hiddenBelowFloorSentence: hiddenBelowFloorSentence,
    faceCheckboxName: faceCheckboxName,
    closeIgnored: closeIgnored,
    showPeople: showPeople,
    closePeople: closePeople,
    withManagedPerson: withManagedPerson,
    toggleFaceSelection: toggleFaceSelection,
    afterPersonUpdate: afterPersonUpdate,
    afterFacesCorrected: afterFacesCorrected,
    proposeMerge: proposeMerge,
    cancelMerge: cancelMerge,
    afterMerge: afterMerge,
    mergeSentence: mergeSentence,
    peopleView: peopleView,
    afterRefusal: afterRefusal,
    afterServerGone: afterServerGone,
    labelSentence: labelSentence,
    ignoreSentence: ignoreSentence,
    splitPreviewSentence: splitPreviewSentence,
    splitSentence: splitSentence,
    scopeSentence: scopeSentence,
    progressLine: progressLine,
    estimateSeconds: estimateSeconds,
    estimateText: estimateText,
    positionLine: positionLine,
    view: view,
    cropPath: cropPath,
    cropAlt: cropAlt,
    createFacesApp: createFacesApp,
  };
}
