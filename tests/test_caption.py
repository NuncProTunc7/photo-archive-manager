"""Design 8.1's `caption` enricher - M10 Task 8, the first model pam has ever asked.

NOT ONE TEST HERE TALKS TO THE REAL OLLAMA, and none of them skips because it is absent.
The real server is installed on the developer's machine, which is exactly what makes both
halves necessary (M10 plan, clarification 7): a test that reached it would pass here and
mean nothing anywhere else, and a test that SKIPPED without it would disarm itself
silently on the one machine where the check is worth having. Everything below drives
`tests/ollama_stub.py`, a stdlib loopback server in `tests/`, which the network guard does
not walk.

EVERY EXPECTED `{label, sentence}` IS SPELLED OUT BY HAND. An expectation built from
"whatever the stub returned" - `json.loads(reply.body["response"])` - passes for any parser
at all, including one that returns the raw envelope, and it is this project's most repeated
defect (M5's `open_plan`, M6's `ENRICHERS`, M8's partition oracles). So the reply is
scripted from two string literals and the expectation is written a SECOND time, as a
literal dict, a few lines away.

THE NEGATIVE CASES ARE BUILT BY EDITING ONE LAYER OF THE CANONICAL FIXTURE, so a failing
test always says which layer it is about:

    layer 1  the HTTP response body            -> json.loads -> a dict
    layer 2  that dict's "response" field      -> a JSON STRING, because format=json
    layer 3  json.loads of THAT string         -> {"label": ..., "sentence": ...}
    layer 4  design 6.4's validation, then the fact

A failure at layers 1-2 is a PROTOCOL error (something that is not Ollama is on that port);
a failure at layer 3 is a MALFORMED MODEL ANSWER; a failure at layer 4 is a well-formed
answer that is not a caption, and the runner drops it and counts it. They are three
conditions and the tests keep them three.
"""

import base64
import builtins
import io
import json
import threading
from pathlib import Path

import pytest
from archives import FAKE_COMMAND
from ollama_stub import OllamaStub, Reply, generate_reply, scripted
from PIL import Image

from pam.catalog import repo
from pam.catalog.db import connect, migrate
from pam.cli import main
from pam.enrich.base import FileRecord, pil_image_loader, run_enricher
from pam.enrich.caption import (
    COST_ESTIMATE_MS,
    DOWNSCALE_LONG_EDGE_PX,
    JPEG_QUALITY,
    PROMPT,
    CaptionEnricher,
    CaptionMalformedError,
    CaptionProtocolError,
    parse_reply,
)
from pam.errors import PamNetworkError
from pam.paths import workspace_if_exists
from pam.scan.runner import run_scan

NOW = "2026-09-02T10:00:00"

# The canonical fixture's two strings, and - written a SECOND time, by hand, as an
# independent literal - what parsing it must produce. Two spellings that a broken parser
# cannot make agree with each other.
CANONICAL_LABEL = "Birthday Cake"
CANONICAL_SENTENCE = "A child blowing out candles on a birthday cake."
EXPECTED_CAPTION = {
    "label": "Birthday Cake",
    "sentence": "A child blowing out candles on a birthday cake.",
}

# `caption`'s fact identity, spelled by hand, on `test_phash.py::SPEC_PHASH_IDENTITY`'s
# precedent and for its reason: A RECORDED REASON IS NOT A PIN. Every stored caption keys
# on (name, model_name, model_version), so moving `MODEL_VERSION` silently re-runs every
# model call in the archive - twenty thousand at the target scale, at seconds each
# on a CPU - to arrive at identical answers, and the cost lands on the user rather than on
# CI. `model_name` is the CONFIGURED model and is therefore not part of this tuple.
#
# THE PROMPT AND THE DOWNSCALE ARE PINNED WITH IT, because they are what the version is
# ABOUT. Changing either changes the question the model was asked - O1 measured that a
# full-size photograph fills the context window and comes back truncated - so a change to
# one without the other is a cache full of answers to a question nobody asked. This row
# has to be edited in the same commit as the constant.
SPEC_CAPTION_IDENTITY = ("caption", "1.0")
SPEC_DOWNSCALE_LONG_EDGE_PX = 1024
SPEC_JPEG_QUALITY = 85

# THE PROMPT, SPELLED OUT A SECOND TIME BY HAND. FINDING F2 (stage-1 review, M1596): the
# block above claimed the prompt was "pinned with" the version and it was not. `PROMPT`
# could be rewritten wholesale - keeping only the two field names, which is all
# `test_the_prompt_is_fixed_in_code_...` checks - with 2810 tests green, and design 8.1
# says "prompt fixed in code". A prompt that can change silently is not fixed: it is item
# 2 of the four things `MODEL_VERSION` is about, so changing it without moving the version
# leaves every cached caption in the archive an answer to a question nobody asked.
#
# A verbatim copy pin ALONE would be M7's M769/M776 - two spellings can agree perfectly
# about less and less, and deleting a clause from both is green - so the floor below is
# written from the SPEC side and asserted separately: the prompt must still ASK FOR the
# two fields design 8.1 declares and still STATE spec 6.1's three-word rule, whatever
# words carry it.
SPEC_PROMPT = (
    "Describe this photograph. Reply with a JSON object with exactly two keys and no"
    ' others: "label", at most three words naming the subject or scene, and "sentence",'
    " one short sentence describing the photograph."
)


def record(
    rel_path: str,
    *,
    media_type: str = "image",
    placeholder: bool = False,
    file_id: int = 1,
) -> FileRecord:
    """A `FileRecord` for `wants()`. Only four of its fields matter to this enricher."""
    return FileRecord(
        id=file_id,
        rel_path=rel_path,
        filename=rel_path.rsplit("/", 1)[-1],
        ext=rel_path.rsplit(".", 1)[-1].lower(),
        media_type=media_type,
        size=1024,
        mtime_ns=1,
        sha256=f"sha-{rel_path}",
        status="present",
        placeholder=placeholder,
        companion_group_id=None,
    )


def picture(path: Path, size: tuple[int, int] = (40, 30), colour: int = 0) -> None:
    """A real PNG on disk, distinct per `colour` so no two files share a sha256."""
    Image.new("RGB", size, (colour % 251, (colour * 7) % 251, (colour * 13) % 251)).save(
        path, format="PNG"
    )


def captioner(stub: OllamaStub, *, model: str = "qwen2.5vl", timeout_s: float = 10.0):
    return CaptionEnricher(model, port=stub.port, timeout_s=timeout_s)


def one_caption(tmp_path, stub: OllamaStub, *, name: str = "photo.png", timeout_s=10.0):
    """Drive `enrich()` over one real picture. Returns whatever it returns, or raises."""
    root = tmp_path / "archive"
    root.mkdir(parents=True, exist_ok=True)
    picture(root / name)
    return captioner(stub, timeout_s=timeout_s).enrich(record(name), pil_image_loader(root))


def small_catalog(tmp_path, names, placeholders=()):
    """An archive of real files and a real catalog, without paying for a scan."""
    root = tmp_path / "archive"
    root.mkdir(parents=True, exist_ok=True)
    conn = connect(tmp_path / "catalog.db")
    migrate(conn)
    for index, name in enumerate(names):
        picture(root / name, colour=index + 1)
        file_id = repo.upsert_file(
            conn,
            rel_path=name,
            filename=name,
            ext="png",
            media_type="image",
            size=(root / name).stat().st_size,
            mtime_ns=1,
            placeholder=name in placeholders,
            now=NOW,
        )
        repo.set_sha256(conn, file_id, f"sha-{name}")
    conn.commit()
    return root, conn


def stored(conn):
    """Every live caption in a catalog, as parsed values. Ordered by insertion."""
    rows = conn.execute(
        "SELECT value_json FROM inferred_facts"
        " WHERE kind = 'caption' AND superseded_by IS NULL ORDER BY id"
    ).fetchall()
    return [json.loads(row["value_json"]) for row in rows]


# --- 1. the identity, the prompt and the constants -------------------------------------


def test_the_caption_fact_identity_does_not_move_without_someone_deciding_it_should():
    from pam.enrich import caption as caption_module

    assert (caption_module.NAME, caption_module.MODEL_VERSION) == SPEC_CAPTION_IDENTITY
    assert DOWNSCALE_LONG_EDGE_PX == SPEC_DOWNSCALE_LONG_EDGE_PX
    assert JPEG_QUALITY == SPEC_JPEG_QUALITY


def test_the_prompt_is_fixed_in_code_and_names_both_fields_design_8_1_declares():
    """Design 8.1: "prompt fixed in code; output parsed to `{label, sentence}`".

    The prompt is not a template, not a config key and not built from anything: a prompt a
    user could edit is a prompt `MODEL_VERSION` cannot describe, and every cached caption
    would become a fact about an unknown question.
    """
    assert isinstance(PROMPT, str)
    assert '"label"' in PROMPT and '"sentence"' in PROMPT
    # FINDING F2: the identity pin, spelled by hand above. Without it the two lines
    # overhead pass for ANY prompt that happens to name the two fields.
    assert PROMPT == SPEC_PROMPT, (
        "the prompt moved without `SPEC_PROMPT` moving with it. It is item 2 of what"
        " `MODEL_VERSION` is about - change it and every cached caption in an archive is"
        " an answer to a different question, so the version must move in the same commit"
    )
    # THE FLOOR, written from the spec side rather than from `PROMPT`, so that deleting a
    # clause from both spellings at once is still a failure (M7's M769/M776).
    assert "three words" in PROMPT, (
        "spec 6.1's <=3-word rule is no longer stated to the model. It stays ENFORCED at"
        " write time either way (design 6.4 / A3), but a cooperative model that was never"
        " told the rule has its answer dropped for a reason nobody gave it"
    )
    assert len(PROMPT.split()) >= 25, (
        f"the prompt is down to {len(PROMPT.split())} words; a copy pin proves two"
        " spellings match and says nothing about extent"
    )


def test_the_cost_prior_is_a_whole_model_call_and_not_a_decode():
    """Design 8.1's ETA prior. Task 3's formula makes it the number shown for the first
    twenty files of a run, so it has to be the right ORDER OF MAGNITUDE for a VLM.

    The bounds are spelled from design 11's own reasoning for `timeout_s = 120` - "a vision
    model on CPU is seconds to a minute per image" - not from the constant. A prior in
    milliseconds-of-a-decode (`phash`'s 25) would promise a 20,000-file run in eight
    minutes when it takes a day.
    """
    assert 1_000 <= COST_ESTIMATE_MS <= 60_000


def test_the_enricher_satisfies_design_8_1s_protocol_and_adds_no_method_to_it():
    """The Protocol is normative and the runner treats every enricher identically.

    The four names are written out from design 8.1's block rather than read off
    `Enricher.__protocol_attrs__`, which would agree with any value of either.
    """
    with scripted(generate_reply(CANONICAL_LABEL, CANONICAL_SENTENCE)) as stub:
        enricher = captioner(stub)

    for attribute in ("name", "model_name", "model_version"):
        assert isinstance(getattr(enricher, attribute), str)
    for method in ("available", "cost_estimate_ms", "wants", "enrich"):
        assert callable(getattr(enricher, method))
    assert enricher.model_name == "qwen2.5vl"


# --- 2. `wants()` - an allowlist, and vision R2 --------------------------------------


@pytest.mark.parametrize(
    ("media_type", "wanted"),
    [("image", True), ("video", False), ("sidecar", False), ("other", False)],
)
def test_only_images_are_fed_to_a_model(media_type, wanted, tmp_path):
    """Design 8.1 rev 3: no video decoding exists in v1.0.

    The four media types are spelled from design 5's own vocabulary. The allowlist reading
    matters beyond videos: `wants()` returning True for a media type nobody has thought of
    would hand an arbitrary file to a model, and the direction a mistake here falls should
    be "not captioned".
    """
    with scripted(generate_reply(CANONICAL_LABEL, CANONICAL_SENTENCE)) as stub:
        assert captioner(stub).wants(record(f"f.{media_type}", media_type=media_type)) is wanted


def test_a_cloud_placeholder_is_never_OPENED_and_the_watch_proves_it_can_see(
    tmp_path, monkeypatch
):
    """Vision R2, asserted against the CALL rather than against the outcome.

    Reading a cloud placeholder HYDRATES it - OneDrive downloads gigabytes the user did not
    ask for - and here it would then send those bytes to a model. An assertion that "no
    caption was written for the placeholder" cannot tell "never read it" from "read it and
    threw the answer away", and by the time the answer is thrown away the download has
    happened. That is M8's M697 exactly.

    So this watches `builtins.open`, which is the call `PIL.Image.open` makes, and asserts
    three things:

      * the placeholder's path is NOT in the record - the property under test;
      * the ordinary image's path IS - the control, without which a wrapper recording
        nothing at all would pass the first assertion;
      * the stub received exactly ONE request - the second half of the property, because a
        file pam did not open is also a file whose bytes never left the machine.
    """
    root, conn = small_catalog(tmp_path, ["ordinary.png", "in_cloud.png"], ["in_cloud.png"])
    opened: list[str] = []
    real_open = builtins.open

    def watching_open(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    with scripted(generate_reply(CANONICAL_LABEL, CANONICAL_SENTENCE)) as stub:
        monkeypatch.setattr(builtins, "open", watching_open)
        try:
            summary = run_enricher(conn, captioner(stub), pil_image_loader(root), workers=1)
        finally:
            monkeypatch.setattr(builtins, "open", real_open)
        paths = stub.paths()

    assert summary.skipped_wants == 1 and summary.enriched == 1
    assert str(root / "ordinary.png") in opened, (
        "the watch recorded nothing for a file that WAS read - it cannot see, so its"
        " silence about the placeholder means nothing"
    )
    assert str(root / "in_cloud.png") not in opened, (
        "pam opened a cloud placeholder; reading one hydrates it (vision R2)"
    )
    assert paths == ["/api/generate"], (
        "a placeholder's bytes reached the model server; R2 forbids reading it at all"
    )
    conn.close()


# --- 3. what pam puts on the wire ------------------------------------------------------


def test_the_request_is_ollamas_own_shape_and_carries_the_fixed_prompt(tmp_path):
    """`POST /api/generate`, `stream: false`, `format: json`, one base64 image.

    `format: json` is what makes layer 2 a JSON STRING rather than prose, and
    `stream: false` is not a preference: `LoopbackHttpClient` reads a whole response and
    does not stream, deliberately (design 10), so a streamed reply would arrive as a run of
    newline-separated objects that layer 1 cannot parse.
    """
    with scripted(generate_reply(CANONICAL_LABEL, CANONICAL_SENTENCE)) as stub:
        one_caption(tmp_path, stub)
        sent = stub.requests[-1]
        assert (sent.method, sent.path) == ("POST", "/api/generate")
        body = sent.json()

    assert body["model"] == "qwen2.5vl"
    # `SPEC_PROMPT`, not `PROMPT`: comparing the wire to the constant the code sent
    # proves only that the code sent what it sent (F2).
    assert body["prompt"] == SPEC_PROMPT
    assert body["stream"] is False
    assert body["format"] == "json"
    assert len(body["images"]) == 1
    assert Image.open(io.BytesIO(base64.b64decode(body["images"][0]))).format == "JPEG"


def test_a_full_size_photograph_is_DOWNSCALED_before_it_is_encoded(tmp_path):
    """FINDING O1, measured on this machine, and the reason it is not a preference.

    A 4032x3024 photograph re-encoded whole costs 4071 prompt tokens against Ollama's
    default 4096 `num_ctx`, leaving 25 tokens for the answer - so generation stops at
    `done_reason: "length"` mid-string and the caption is dropped as malformed. Two of
    three real photographs came back that way. Downscaling to a 1024 px long edge dropped
    prompt_eval to 1092 and the caption to ~0.9 s, with no loss of quality.

    Asserted on the LONG EDGE and on the aspect ratio, from a source that is neither
    square nor already small, so a downscale that ignored the aspect ratio (or that
    resized the short edge) fails.
    """
    root = tmp_path / "archive"
    root.mkdir()
    picture(root / "big.png", size=(2000, 1500))

    with scripted(generate_reply(CANONICAL_LABEL, CANONICAL_SENTENCE)) as stub:
        captioner(stub).enrich(record("big.png"), pil_image_loader(root))
        body = stub.requests[-1].json()

    sent = Image.open(io.BytesIO(base64.b64decode(body["images"][0])))
    assert max(sent.size) == 1024
    assert sent.size == (1024, 768)


def test_a_photograph_smaller_than_the_cap_is_sent_as_it_is_and_never_upscaled(tmp_path):
    """The other half of the same branch, and the one a naive `resize` would fail.

    Upscaling a small image spends prompt tokens on pixels that carry no information -
    which is precisely what O1 measured going wrong in the other direction - so the cap is
    a ceiling and not a target.
    """
    root = tmp_path / "archive"
    root.mkdir()
    picture(root / "small.png", size=(320, 200))

    with scripted(generate_reply(CANONICAL_LABEL, CANONICAL_SENTENCE)) as stub:
        captioner(stub).enrich(record("small.png"), pil_image_loader(root))
        body = stub.requests[-1].json()

    assert Image.open(io.BytesIO(base64.b64decode(body["images"][0]))).size == (320, 200)


# --- 4. CASE 1: a good answer ----------------------------------------------------------


def test_a_good_answer_becomes_one_caption_fact_with_no_confidence(tmp_path):
    """The happy path, with the expectation written out by hand.

    `confidence is None` because Ollama returns none and design 8.1 says so
    ("confidence proxy: none from Ollama -> store NULL"). Vision Q3 rev 4 (amendment A7)
    settles that this satisfies the criterion by traceability plus the tier, and that A
    FABRICATED CONFIDENCE WOULD BE WORSE THAN NONE - so a proxy invented to fill the
    column is the failure this asserts against.
    """
    with scripted(generate_reply(CANONICAL_LABEL, CANONICAL_SENTENCE)) as stub:
        facts = one_caption(tmp_path, stub)

    assert len(facts) == 1
    assert facts[0].kind == "caption"
    assert facts[0].value == EXPECTED_CAPTION
    assert facts[0].confidence is None


def test_the_stored_fact_is_the_models_words_and_not_the_envelopes(tmp_path):
    """End to end through the runner: what lands in `inferred_facts.value_json`.

    A parser that returned layer 1 (the envelope) or layer 2 (the JSON string) instead of
    layer 3 would still produce "a fact", and the direct test above would catch it - but
    only this one proves the whole chain, validation included, agrees.
    """
    root, conn = small_catalog(tmp_path, ["photo.png"])

    with scripted(generate_reply(CANONICAL_LABEL, CANONICAL_SENTENCE)) as stub:
        summary = run_enricher(conn, captioner(stub), pil_image_loader(root), workers=1)

    assert (summary.facts_written, summary.facts_dropped, summary.failed) == (1, 0, 0)
    assert stored(conn) == [EXPECTED_CAPTION]
    conn.close()


# --- 5. CASES 2-3: layer 1 and layer 2, the protocol ----------------------------------


def test_a_500_is_a_protocol_error_and_not_a_bad_model(tmp_path):
    """LAYER 1, edited: the status. Something is on that port and it is unhappy."""
    with scripted(Reply(status=500, body={"error": "internal"})) as stub:
        with pytest.raises(CaptionProtocolError, match="500"):
            one_caption(tmp_path, stub)


def test_a_body_that_is_not_json_at_all_is_a_protocol_error(tmp_path):
    """LAYER 1, edited: the body. A web server, or an Ollama too old for this route."""
    with scripted(Reply(body="<html>not ollama</html>")) as stub:
        with pytest.raises(CaptionProtocolError, match="not JSON"):
            one_caption(tmp_path, stub)


def test_a_json_body_with_no_response_field_is_a_protocol_error(tmp_path):
    """LAYER 2, edited: the `response` field is gone.

    This is the layering's whole point. A dict with no `response` is not a model that
    answered badly - it is something that is not speaking Ollama - and reporting it as a
    malformed caption would send a user whose port is wrong to look at their model.
    """
    with scripted(Reply(body={"model": "qwen2.5vl", "done": True})) as stub:
        with pytest.raises(CaptionProtocolError, match="response"):
            one_caption(tmp_path, stub)


def test_the_protocol_error_and_the_malformed_answer_are_different_types(tmp_path):
    """Neither exception is a subclass of the other, so no `except` can collapse them.

    Written as a claim about the CLASSES rather than about two calls, because the way this
    would regress is one `except (A, B)` in a later milestone's caller.
    """
    assert not issubclass(CaptionProtocolError, CaptionMalformedError)
    assert not issubclass(CaptionMalformedError, CaptionProtocolError)


# --- 6. CASES 4 and 10: layer 3, a malformed model answer ------------------------------


def test_an_answer_that_is_not_json_is_malformed_and_there_is_no_salvage(tmp_path):
    """LAYER 3, edited: the model wrote prose.

    Clarification 5: no regex salvage, no "take the first line", no retry loop. The string
    below contains a plausible label and a plausible sentence, so a parser tempted to
    scrape one out of it would find something - and would store a caption pam invented.
    """
    with scripted(
        Reply(body={"response": "label: Birthday Cake. sentence: A child.", "done": True})
    ) as stub:
        with pytest.raises(CaptionMalformedError, match="not JSON"):
            one_caption(tmp_path, stub)
        assert len(stub.requests) == 1, "a retry loop; clarification 5 forbids one"


def test_a_truncated_answer_that_PARSES_is_still_malformed(tmp_path):
    """LAYER 3, edited: `done_reason`. FINDING O1, and the case `json.loads` cannot catch.

    The body here is the canonical fixture with `done_reason: "length"` and NOTHING ELSE
    CHANGED - the `response` field is complete, valid JSON that parses into a perfectly
    well-formed caption. That is the point: O1 measured truncation landing mid-string, but
    a truncation that happened to land after a closing brace parses, and pam would store a
    caption the model did not finish writing with nothing downstream able to tell.

    Relying on `json.loads` to notice is therefore relying on where the truncation landed.
    """
    with scripted(
        generate_reply(CANONICAL_LABEL, CANONICAL_SENTENCE, done_reason="length")
    ) as stub:
        with pytest.raises(CaptionMalformedError, match="context"):
            one_caption(tmp_path, stub)


def test_the_same_body_without_the_truncation_flag_is_a_good_caption(tmp_path):
    """The control for the test above: ONE field separates the two.

    Without it, a `parse_reply` that raised `CaptionMalformedError` unconditionally would
    pass every negative test in this section.
    """
    with scripted(generate_reply(CANONICAL_LABEL, CANONICAL_SENTENCE, done_reason="stop")) as s:
        assert one_caption(tmp_path, s)[0].value == EXPECTED_CAPTION


def test_parse_reply_reports_the_layer_for_each_edit_of_the_canonical_fixture():
    """The four layers as a table, against `parse_reply` alone.

    Each row is the canonical body with ONE thing changed, so the mapping from "what is
    wrong" to "which exception" is asserted as a whole rather than one case at a time.
    """
    canonical = {
        "model": "qwen2.5vl",
        "created_at": "2026-09-01T12:00:00Z",
        "response": json.dumps(EXPECTED_CAPTION),
        "done": True,
        "done_reason": "stop",
    }
    assert parse_reply(200, json.dumps(canonical).encode()) == EXPECTED_CAPTION

    with pytest.raises(CaptionProtocolError):
        parse_reply(404, json.dumps(canonical).encode())
    with pytest.raises(CaptionProtocolError):
        parse_reply(200, b"[]")
    with pytest.raises(CaptionProtocolError):
        parse_reply(200, json.dumps({**canonical, "response": 17}).encode())
    with pytest.raises(CaptionMalformedError):
        parse_reply(200, json.dumps({**canonical, "done_reason": "length"}).encode())
    with pytest.raises(CaptionMalformedError):
        parse_reply(200, json.dumps({**canonical, "response": "{oops"}).encode())


# --- 7. CASES 5-8: layer 4, design 6.4's validation ------------------------------------


@pytest.mark.parametrize(
    ("why", "answer"),
    [
        ("a missing field", {"label": "Birthday Cake"}),
        (
            "an extra field",
            {"label": "Birthday Cake", "sentence": "A child.", "mood": "happy"},
        ),
        (
            "a four-word label",
            {"label": "A Very Big Cake", "sentence": "A child."},
        ),
        ("an empty label", {"label": "", "sentence": "A child."}),
        ("a whitespace-only label", {"label": "   ", "sentence": "A child."}),
    ],
)
def test_a_well_formed_answer_that_is_not_a_caption_is_DROPPED_and_counted(
    tmp_path, why, answer
):
    """LAYER 4, edited: the model's answer parses and fails design 6.4.

    Every one of these is a MODEL that answered in JSON and a CATALOG that refuses the
    answer - not a protocol failure and not a parse failure - so the enricher hands it on
    untouched and the runner drops it. `facts_dropped` is what reaches `pam enrich`'s exit
    code (design 13), and invariant I9 is the property: garbage never enters the catalog,
    and IT IS NEVER TRUNCATED TO FIT. A four-word label cut down to three would be pam
    inventing a label the model did not propose, and a filename is not the place to find
    that out (design 8.1 rev 17).

    The extra-field row is design 6.4's schema being EXACT rather than a minimum: an
    undeclared key is as much a sign of a misparsed answer as a missing one, and nothing
    downstream would ever look for it.
    """
    root, conn = small_catalog(tmp_path, ["photo.png"])

    with scripted(Reply(body={"response": json.dumps(answer), "done": True})) as stub:
        summary = run_enricher(conn, captioner(stub), pil_image_loader(root), workers=1)

    assert summary.facts_dropped == 1, why
    assert summary.facts_written == 0
    assert stored(conn) == []
    conn.close()


def test_the_word_cap_is_a_boundary_at_three_and_not_at_two_or_four(tmp_path):
    """Design 6.1's "`label` <=3 words", at the boundary in both directions.

    Three words is ACCEPTED and four is DROPPED. A cap tested only from the failing side
    passes for any cap at all - including one, which would drop nearly every real caption.
    """
    for label, expected_written in (("Cake", 1), ("Birthday Cake", 1), ("A Birthday Cake", 1)):
        root, conn = small_catalog(tmp_path / label.replace(" ", "_"), ["photo.png"])
        with scripted(
            Reply(body={"response": json.dumps({"label": label, "sentence": "A."})})
        ) as stub:
            summary = run_enricher(conn, captioner(stub), pil_image_loader(root), workers=1)
        assert summary.facts_written == expected_written, label
        conn.close()

    root, conn = small_catalog(tmp_path / "four", ["photo.png"])
    with scripted(
        Reply(body={"response": json.dumps({"label": "One Two Three Four", "sentence": "A."})})
    ) as stub:
        summary = run_enricher(conn, captioner(stub), pil_image_loader(root), workers=1)
    assert (summary.facts_written, summary.facts_dropped) == (0, 1)
    conn.close()


def test_the_sentence_is_UNCAPPED(tmp_path):
    """Only `label` carries a word cap. `sentence` is design 6.1's prose half.

    A cap applied to the wrong field, or to both, would drop every real caption - and it
    would do so silently, since a dropped fact is a log line and an exit code, not a crash.
    """
    long_sentence = " ".join(["word"] * 60) + "."
    root, conn = small_catalog(tmp_path, ["photo.png"])

    with scripted(
        Reply(body={"response": json.dumps({"label": "Cake", "sentence": long_sentence})})
    ) as stub:
        summary = run_enricher(conn, captioner(stub), pil_image_loader(root), workers=1)

    assert summary.facts_written == 1
    assert stored(conn) == [{"label": "Cake", "sentence": long_sentence}]
    conn.close()


# --- 8. CASE 9: a server that times out ------------------------------------------------


def test_a_server_that_never_answers_is_a_network_error_and_not_a_hang(tmp_path):
    """The stub is told to wait; the client's own timeout is what ends the call.

    No `time.sleep` anywhere: the handler waits on an EVENT with its own deadline, so the
    test releases it deliberately and a forgotten release fails the stub's deadline rather
    than hanging the suite. The client timeout is short and the deadline is long, so which
    of the two fires is not a race - the assertion is about `LoopbackHttpClient`'s timeout
    and nothing else.
    """
    held = threading.Event()
    stub = OllamaStub(
        {"/api/generate": [Reply(block_until=held, block_deadline_s=20.0)]},
    )
    with stub:
        try:
            with pytest.raises(PamNetworkError, match="could not reach"):
                one_caption(tmp_path, stub, name="slow.png", timeout_s=0.5)
        finally:
            held.set()


# --- 9. CASE 11 (P3): interrupted mid-run, and resumed ---------------------------------


def test_an_interrupted_run_resumes_and_does_not_ASK_THE_MODEL_AGAIN(tmp_path):
    """Design 8.1's resume, and the assertion is about WORK NOT REPEATED.

    Sixty files, so the runner's 50-file commit batch really closes: the interrupt is
    raised on the 51st model call, which lands in the SECOND flush, after the cursor was
    committed at file 50. A resume then takes files with `id > cursor_file_id`.

    ASSERTED ON THE REQUESTS THE STUB RECEIVED, not on the facts in the catalog. A resume
    that re-ran all sixty files would end with the same sixty captions stored - the cache
    skip would refuse the duplicates - and an outcome assertion could not tell the two
    apart. On a real captioner that difference is fifty model calls, which on a CPU is
    minutes; it is the whole reason the cursor exists.

    `KeyboardInterrupt` on purpose: `_enrich_one` catches `Exception` and deliberately not
    `BaseException`, precisely so an interruption stops the run rather than being counted
    as one unreadable file.
    """
    names = [f"photo_{index:03d}.png" for index in range(60)]
    root, conn = small_catalog(tmp_path, names)
    reply = generate_reply(CANONICAL_LABEL, CANONICAL_SENTENCE)

    with OllamaStub({"/api/generate": [reply]}) as stub:
        interrupted = captioner(stub)
        calls = {"n": 0}
        genuine_enrich = interrupted.enrich

        def enrich_until_the_fifty_first(file, image):
            calls["n"] += 1
            if calls["n"] > 50:
                raise KeyboardInterrupt("the user pressed Ctrl+C")
            return genuine_enrich(file, image)

        interrupted.enrich = enrich_until_the_fifty_first  # type: ignore[method-assign]
        with pytest.raises(KeyboardInterrupt):
            run_enricher(conn, interrupted, pil_image_loader(root), workers=1)
        after_interrupt = len(stub.requests)

        resumed = run_enricher(conn, captioner(stub), pil_image_loader(root), workers=1)
        total_requests = len(stub.requests)

    assert after_interrupt == 50, "the first run's committed batch is not fifty files"
    assert resumed.total == 10, (
        f"the resume walked {resumed.total} files; it must take only the ten past the"
        " committed cursor"
    )
    assert total_requests == 60, (
        f"the model was asked {total_requests} times about sixty files - a resume that"
        " re-asks is the cost the cursor exists to avoid"
    )
    assert len(stored(conn)) == 60
    conn.close()


# --- 10. `available()` - amendment A2's THIRD state is not here ------------------------


def test_available_is_true_when_the_server_lists_the_model():
    with scripted(tags="qwen2.5vl") as stub:
        ready, reason = captioner(stub).available()

    assert ready is True
    assert "qwen2.5vl" in reason


def test_available_accepts_the_implicit_latest_tag(tmp_path):
    """FINDING O2, measured on this machine: `/api/tags` lists `qwen2.5vl:latest` while
    design 11's default is the bare `qwen2.5vl`.

    A membership test against the listing alone reports "not installed" ON A CORRECT
    INSTALL and sends the user to pull a model they already have. On this machine the real
    answer is the one that would hide the bug, which is why it is the fake that is asked.
    """
    with scripted(tags="qwen2.5vl:latest") as stub:
        assert captioner(stub, model="qwen2.5vl").available()[0] is True


def test_available_does_not_accept_a_DIFFERENT_tag_of_the_same_model():
    """The other half of O2, and the half a `startswith` would fail.

    `qwen2.5vl:7b` is a different model whose captions would differ, so a user who
    configured `qwen2.5vl` and has only the 7b tag has not got what they asked for.
    """
    with scripted(tags="qwen2.5vl:7b") as stub:
        ready, reason = captioner(stub, model="qwen2.5vl").available()

    assert ready is False
    assert "pam models pull" in reason


def test_available_is_a_sentence_when_nothing_is_listening_and_never_a_traceback():
    """Design 8.1's `available()` contract. The stub is shut down before it is asked.

    `_run_one` treats a False here as a refusal - reported, and design 13's exit 1 - so an
    exception escaping instead would be a traceback where the product promises a sentence.
    """
    with scripted() as stub:
        port = stub.port
    # A short timeout, because nothing is listening and the assertion is about the
    # SENTENCE rather than about how long pam waits for it. It cannot flip to a pass:
    # there is no server to answer sooner.
    enricher = CaptionEnricher("qwen2.5vl", port=port, timeout_s=0.25)

    ready, reason = enricher.available()

    assert ready is False
    assert "could not reach" in reason


def test_available_says_so_when_something_that_is_not_ollama_answers():
    """Three failures, three sentences: unreachable, not-Ollama, and no-such-model.

    Collapsing them would tell a user running some other server on 11434 that their model
    is missing - `pam doctor` reports the same three separately for the same reason.
    """
    with OllamaStub({"/api/tags": [Reply(body={"hello": "world"})]}) as stub:
        ready, reason = captioner(stub).available()

    assert ready is False
    assert "not as Ollama" in reason


# --- 11. amendment A2: disabled is NOT unavailable, and it exits 0 ---------------------


def scanned_archive(tmp_path, toml: str) -> Path:
    """A real, scanned archive whose `pam.toml` is written before `pam enrich` reads it."""
    root = tmp_path / "photos"
    root.mkdir(parents=True)
    picture(root / "photo.png")
    (root / "photo.png.tags.json").write_text(
        json.dumps({"DateTimeOriginal": "2019:07:04 18:23:11"}), encoding="utf-8"
    )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    ws = workspace_if_exists(root)
    assert ws is not None
    ws.config_path.write_text(toml, encoding="utf-8")
    return root


def test_pam_enrich_EXITS_ZERO_on_a_default_install_with_captioning_off(tmp_path, capsys):
    """AMENDMENT A2, AND THE TRAP IT EXISTS FOR.

    `[enrich.caption] enabled` ships `false`, so on every default install the captioner
    does not run. Routing that through `available()` would make it a refusal, and
    `EnrichSummaryReport.had_errors` counts refusals - so `pam enrich` would EXIT 1 ON A
    MACHINE WHERE NOTHING IS WRONG, on the first command a new user runs.

    Driven with NO `pam.toml` at all, which is what a default install really is, and
    asserting the exit code, the report line and the absence of the diagnostic route: a
    configuration is not an error, and `Run: pam doctor` for a setting the user chose is
    UX 1.4's dead end.
    """
    root = scanned_archive(tmp_path, "")

    code = main(["enrich", "caption", "--archive", str(root)])

    text = capsys.readouterr().out
    assert code == 0
    assert "caption: not run - [enrich.caption] enabled is false" in text
    assert "pam doctor" not in text
    assert "!" not in text


def test_a_disabled_captioner_BUILDS_NO_CLIENT_and_so_resolves_no_host(tmp_path, monkeypatch):
    """ "Off" that still opens a socket is not off.

    The enabled check runs BEFORE the builder, so a disabled captioner never constructs a
    `LoopbackHttpClient` - and constructing one is what RESOLVES the host (`net.py`:
    "Construction resolves and checks the host, so a misconfigured destination fails where
    it is configured"). So "no client was built" is the observable, and it is watched at
    the class rather than at the outcome: an assertion that no caption was written cannot
    tell "never asked" from "asked and got nothing".

    The ENABLED case is the control that proves the watch can see. Without it a spy that
    recorded nothing at all - which is exactly what watching `net.system_resolver` does,
    since `LoopbackHttpClient` binds it as a DEFAULT ARGUMENT at definition time and a
    monkeypatch on the module attribute never reaches it - would pass on its own.
    """
    import pam.net as net_module

    built: list[tuple[str, int]] = []
    real_client = net_module.LoopbackHttpClient

    class WatchingClient(real_client):  # type: ignore[misc, valid-type]
        def __init__(self, host="127.0.0.1", port=80, **kwargs):
            built.append((host, port))
            super().__init__(host, port, **kwargs)

    monkeypatch.setattr(net_module, "LoopbackHttpClient", WatchingClient)

    off = scanned_archive(tmp_path / "off", "")
    assert main(["enrich", "caption", "--archive", str(off)]) == 0
    assert built == [], "a disabled captioner built a client, which resolves a host"

    with scripted(generate_reply(CANONICAL_LABEL, CANONICAL_SENTENCE)) as stub:
        on = scanned_archive(
            tmp_path / "on",
            f"[enrich.caption]\nenabled = true\nollama_port = {stub.port}\n",
        )
        assert main(["enrich", "caption", "--archive", str(on)]) == 0

    assert built == [("127.0.0.1", stub.port)], (
        "the watch recorded nothing for an ENABLED captioner - it cannot see, so its"
        " silence about the disabled one means nothing"
    )


def test_an_enabled_captioner_that_cannot_reach_ollama_is_a_refusal_and_exits_one(
    tmp_path, capsys
):
    """The other side of A2's distinction: the user ASKED for work pam could not do.

    Disabled exits 0; unavailable exits 1 and points at `pam doctor`. If the two collapsed
    in either direction one of these two tests fails, which is why they are written as a
    pair.
    """
    with scripted() as stub:
        port = stub.port
    root = scanned_archive(
        tmp_path, f"[enrich.caption]\nenabled = true\nollama_port = {port}\n"
    )

    code = main(["enrich", "caption", "--archive", str(root)])

    text = capsys.readouterr().out
    assert code == 1
    assert "! caption did not run:" in text
    assert "pam doctor" in text


# --- 12. amendments A1 and B2: the two config keys the captioner reads -----------------


def test_the_captioner_is_built_with_its_OWN_worker_count_and_not_the_global_one(
    tmp_path, monkeypatch
):
    """Amendment A1: `[enrich.caption] workers` overrides `[enrich] workers` for this
    enricher only, and it is a builder-supplied value - design 8.1's Protocol gains no
    fifth method, because an enricher the runner treats differently is what that Protocol
    forbids.

    The two numbers are made DIFFERENT on purpose (4 global, 3 for the captioner): a test
    written against the default 1 would pass for an implementation that hard-coded 1, and
    one written with the two equal would pass for an implementation that read the wrong key.
    B2 also requires the code to work at any value in the 1-32 range, so 3 is a value
    nothing special is written about.
    """
    seen: list[int] = []
    from pam.enrich import command as command_module

    real_run = command_module.run_enricher

    def watching(conn, enricher, loader, *, workers, **kwargs):
        seen.append(workers)
        return real_run(conn, enricher, loader, workers=workers, **kwargs)

    monkeypatch.setattr(command_module, "run_enricher", watching)

    with scripted(generate_reply(CANONICAL_LABEL, CANONICAL_SENTENCE)) as stub:
        root = scanned_archive(
            tmp_path,
            "[enrich]\nworkers = 4\n\n[enrich.caption]\nenabled = true\n"
            f"ollama_port = {stub.port}\nworkers = 3\n",
        )
        assert main(["enrich", "caption", "--archive", str(root)]) == 0

    assert seen == [3]


def test_the_other_enrichers_still_take_the_global_worker_count(tmp_path, monkeypatch):
    """The control for the test above. Without it, an override applied to EVERY enricher
    would pass - and `[enrich] workers` would have silently stopped meaning anything."""
    seen: list[int] = []
    from pam.enrich import command as command_module

    real_run = command_module.run_enricher

    def watching(conn, enricher, loader, *, workers, **kwargs):
        seen.append(workers)
        return real_run(conn, enricher, loader, workers=workers, **kwargs)

    monkeypatch.setattr(command_module, "run_enricher", watching)
    root = scanned_archive(tmp_path, "[enrich]\nworkers = 4\n")

    assert main(["enrich", "phash", "--archive", str(root)]) == 0
    assert seen == [4]


def test_the_configured_timeout_reaches_the_loopback_client(tmp_path):
    """Amendment A1's `timeout_s`. `net.DEFAULT_TIMEOUT_S` is 10 s and a vision model on
    CPU is seconds to a minute per image, so a captioner left on the default would fail
    every call on exactly the hardware this product is for.

    Read off the client the enricher actually built, because that is the object whose
    `timeout` the socket is opened with - asserting the constructor argument would only
    prove the test passed what the test passed.
    """
    with scripted() as stub:
        enricher = CaptionEnricher("qwen2.5vl", port=stub.port, timeout_s=97.0)

    assert enricher._client is not None
    assert enricher._client.timeout == 97.0
    assert enricher._client.port == stub.port


def test_the_configured_timeout_reaches_the_client_THE_BUILDER_MAKES(tmp_path, monkeypatch):
    """FINDING F1 (stage-1 review, M1585). The test above proves the CLASS honours its
    `timeout_s` parameter; nothing proved the BUILDER passes the CONFIGURED one.

    `enrich/command.py::_caption` could read `net.DEFAULT_TIMEOUT_S` - or any literal -
    instead of `config.caption_timeout_s`, and the whole suite stayed green: 2809 passed.
    That is not a cosmetic gap. Amendment A1 exists BECAUSE `net.py`'s 10 s default fails
    every call on a CPU, so the mutation that survives is exactly the bug A1 was written to
    prevent, on exactly the hardware this product is for.

    So this drives the real command and reads the timeout off the client the run actually
    built, the way `..._BUILDS_NO_CLIENT_...` reads the fact that one was built at all. The
    value is a number nothing else in the suite uses and is neither the 120 s default nor
    `net.py`'s 10 s: a test written against either would pass for an implementation that
    hard-coded it. Its companion `workers` key was already driven through `main()`; this is
    the same treatment for the key that was not.
    """
    import pam.net as net_module

    built: list[float] = []
    real_client = net_module.LoopbackHttpClient

    class WatchingClient(real_client):  # type: ignore[misc, valid-type]
        def __init__(self, host="127.0.0.1", port=80, **kwargs):
            built.append(kwargs.get("timeout"))
            super().__init__(host, port, **kwargs)

    monkeypatch.setattr(net_module, "LoopbackHttpClient", WatchingClient)

    with scripted(generate_reply(CANONICAL_LABEL, CANONICAL_SENTENCE)) as stub:
        root = scanned_archive(
            tmp_path,
            f"[enrich.caption]\nenabled = true\nollama_port = {stub.port}\ntimeout_s = 47\n",
        )
        assert main(["enrich", "caption", "--archive", str(root)]) == 0

    assert built == [47], (
        f"the captioner the command built has timeout {built}; `[enrich.caption]"
        " timeout_s` was 47 and a captioner left on net.py's 10 s default fails every"
        " call on the CPU hardware amendment A1 was written for"
    )
