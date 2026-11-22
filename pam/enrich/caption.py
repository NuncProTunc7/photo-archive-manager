"""Design 8.1's `caption` enricher: a local vision model, over a loopback socket.

THE FIRST SOCKET PAM HAS EVER OPENED, AND IT ADDS NO PYTHON DEPENDENCY. Ollama is an
EXTERNAL SERVICE on the exiftool precedent - user-installed, checked by `pam doctor`,
sanctioned by design 1's stack table since rev 2 - and pam reaches it through
`pam.net.LoopbackHttpClient`, which design 10 names as this module's client and which is
the only LOOPBACK HTTP code in the package (`pam/update/fetch.py` is the release client). The `ollama` pip package would be both a SIXTH runtime
dependency and a SECOND HTTP client, and design 10 permits exactly one; nothing here
imports `http.client`, `urllib` or `socket`, so `tests/guards.py`'s
`ALLOWED_NETWORK_IMPORTS` gained nothing for it (it lists the sanctioned imports of `net.py`,
`webui/server.py` and, from M15 Task 12, `update/fetch.py`).

EVERY REFUSAL IS `net.py`'S. This module never decides what an acceptable address is: it
hands `net.LoopbackHttpClient` a host and a port, and the client resolves the name, refuses
unless EVERY answer is loopback, and then dials the literal it checked. A non-loopback
host therefore never reaches a connection attempt at all - it is refused when the client is
built, which is before `available()` has anything to report, which is why the failure is
carried below as a sentence rather than as an exception (see `__init__`).

WHAT LEAVES THIS MACHINE: NOTHING. The one request goes to `127.0.0.1` by default and to a
host `net.py` has proven is loopback in every other case. The image bytes and the caption
text never touch any other destination, there is no telemetry, no retry against a fallback
host, and no logging of a caption anywhere but this archive's own session log.

NO REGEX SALVAGE, NO "TAKE THE FIRST LINE", NO RETRY LOOP (M10 plan, clarification 5). A
model that will not answer in the shape asked for is a model this archive gets no captions
from, and the log says so per file. The prompt is the lever, and it is fixed in code.
"""

import base64
import io
import json
import logging
from typing import Any

from pam import net
from pam.enrich.base import FileRecord, InferredFact, PILImageLoader
from pam.errors import PamNetworkError

NAME = "caption"

# THE IDENTITY THE RUNNER'S CACHE AND RESUME ARE KEYED ON, and the constant block
# `phash.py` argues out. `model_name` is the CONFIGURED Ollama model (design 11's
# `[enrich.caption] model`), so it is an instance attribute and not a constant here.
#
# `MODEL_VERSION` MOVES WHEN THE OUTPUT WOULD MOVE, and nothing else moves it. Four things
# are the question this enricher asks, and changing any one of them makes every cached
# caption a fact about a different question:
#   1. the model tag - which is `model_name`, not this;
#   2. `PROMPT` below, which is what the model was asked;
#   3. how the answer is parsed (`parse_reply`), which is what pam decided it heard;
#   4. `DOWNSCALE_LONG_EDGE_PX` below, which is WHICH IMAGE the model was shown.
# It is emphatically NOT the Ollama server's version: a patch release of the server that
# changes no caption would invalidate the whole archive's captions and re-run twenty
# thousand model calls for identical answers. `tests/test_caption.py` pins this tuple by
# hand, because a recorded reason is not a pin.
MODEL_VERSION = "1.0"

# THE PROMPT IS FIXED IN CODE (design 8.1: "prompt fixed in code"), it names the two fields
# design 8.1 declares ("output parsed to `{label, sentence}`"), and it states spec 6.1's
# three-word rule so a cooperative model does not have its answer dropped for a reason it
# was never told. The rule is still ENFORCED at write time (design 6.4 / amendment A3) and
# never here: asking politely is not a validation.
PROMPT = (
    "Describe this photograph. Reply with a JSON object with exactly two keys and no"
    ' others: "label", at most three words naming the subject or scene, and "sentence",'
    " one short sentence describing the photograph."
)

# THE DOWNSCALE, AND IT IS A MEASUREMENT RATHER THAN A TASTE (M10 finding O1, measured on
# this machine 2026-09-01 against Ollama 0.33.2 and `qwen2.5vl:latest`).
#
# A full-size photograph FILLS THE CONTEXT WINDOW and the caption comes back truncated.
# A 4032x3024 HEIC re-encoded to JPEG cost 4071 prompt tokens; Ollama's default `num_ctx`
# is 4096, leaving 25 tokens for the answer, so generation stopped at
# `done_reason: "length"` in the middle of the JSON string. TWO OF THREE REAL PHOTOGRAPHS
# CAME BACK THAT WAY - on a default install, on correct hardware, with a correctly
# installed model, most captions would have been dropped as malformed and the user would
# have concluded the model was bad.
#
# Downscaling to a 1024 px long edge before encoding fixed it and cost nothing: 0.88 /
# 0.94 / 1.02 s per caption, with prompt_eval dropping 4071 -> 1092. The alternative
# measured - raising the server's `num_ctx` to 8192 and sending full size - also works and
# costs 4.56 / 4.57 / 11.02 s, so this is ~4.5x faster AND needs no server-side option.
# It costs no caption quality: 1024 px and 768 px labels were as good as the full-size
# ones and sometimes better, and both cost 1092 prompt tokens - so 1024 is free relative
# to 768 and is the larger of the two. qwen2.5vl uses dynamic resolution, so a bigger
# image buys nothing.
#
# IT IS PART OF `MODEL_VERSION`'S IDENTITY EXACTLY AS THE PROMPT IS. Change it and the
# model is being shown a different photograph.
DOWNSCALE_LONG_EDGE_PX = 1024

# The JPEG the model is shown. 85 is the quality above which a VLM's answer stops changing
# and below which compression artefacts start being describable; it is part of the same
# identity as the line above, for the same reason.
JPEG_QUALITY = 85

# Per-image cost for the ETA's prior (design 8.1), IN MILLISECONDS OF A WHOLE MODEL CALL -
# not of a decode, which is what every other enricher's prior measures. Task 3's corrected
# formula makes this number the displayed ETA for the FIRST TWENTY FILES of a run, so over
# 20,000 files it is what a user sees before any measurement replaces it, and picking it
# badly is a wrong number shown at exactly the moment the user is deciding whether to wait.
#
# 5000 ms. Measured on this machine's GPU a caption is ~0.9 s (finding O1), but the prior a
# CPU user meets first is the one worth being right about, and design 11 sizes this
# enricher's timeout at 120 s BECAUSE "a vision model on CPU is seconds to a minute per
# image". Five seconds is the low end of that band: it overstates a GPU run by ~5x for
# twenty files and understates a slow CPU run rather than promising a number the machine
# cannot keep. An ETA that starts high and falls is the honest direction (design 8.1: "the
# ETA is a display, never a contract").
COST_ESTIMATE_MS = 5000

# Ollama's own route and the field names in its reply. Written out because they are the
# wire protocol of another program: a typo in one of them is a protocol error this module
# must report as such, not something to guess around.
GENERATE_PATH = "/api/generate"
RESPONSE_FIELD = "response"
DONE_REASON_FIELD = "done_reason"
TRUNCATED_DONE_REASON = "length"


class CaptionProtocolError(RuntimeError):
    """LAYER 1/2: what answered is not speaking Ollama.

    A non-200 status, a body that is not JSON, or a JSON body with no `response` string in
    it. This is a fault of the SERVER or of the configuration - something else is listening
    on that port, or the Ollama there is too old to have this route - and it is a different
    condition from a model that answered badly. Collapsing the two into one `except` would
    tell a user whose port is wrong that their model is bad.
    """


class CaptionMalformedError(RuntimeError):
    """LAYER 3: the model answered, and what it said is not a caption.

    Its answer did not parse as JSON, or it stopped mid-sentence because it ran out of
    room. Per clarification 5 the answer is DROPPED and logged; there is no salvage and no
    retry. The runner counts the file failed, which is how `pam enrich` reaches exit 1.
    """


def _encoded_image(decoded: Any) -> str:
    """The photograph as Ollama wants it: base64 of a downscaled JPEG.

    NO PILLOW IMPORT. Everything here is a method on the image the loader already opened
    (`convert`, `thumbnail`, `save`), so this module adds nothing to the eager import
    closure and `pam --version` pays for no decoder.

    `Image.thumbnail` is in place, preserves the aspect ratio and NEVER UPSCALES, so a
    photograph smaller than the cap is sent as it is rather than blown up into tokens that
    carry no more information. `convert("RGB")` first because it returns a new image - the
    loader's is not modified - and because JPEG cannot hold an alpha channel or a palette,
    which a PNG or a HEIC with transparency would arrive as.
    """
    prepared = decoded.convert("RGB")
    prepared.thumbnail((DOWNSCALE_LONG_EDGE_PX, DOWNSCALE_LONG_EDGE_PX))
    buffer = io.BytesIO()
    prepared.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def parse_reply(status: int, body: bytes) -> Any:
    """The four layers of an Ollama caption, and WHICH ONE FAILED IS THE WHOLE POINT.

        layer 1  the HTTP response body        -> json.loads -> a dict
        layer 2  that dict's "response" field  -> a JSON STRING, because format=json
        layer 3  json.loads of THAT string     -> {"label": ..., "sentence": ...}
        layer 4  spec 6.4's validation, then the fact

    Layers 1 and 2 raise `CaptionProtocolError`; layer 3 raises `CaptionMalformedError`;
    LAYER 4 IS NOT HERE AT ALL - whatever layer 3 produced is handed to the runner exactly
    as parsed, and `validate.fact_error` drops it if it is not a caption. That is
    clarification 5's "it needs no new machinery": a reply missing `sentence`, or carrying
    a third key, or whose `label` is four words, is a well-formed model answer that fails
    spec 6.4, and it must be counted as a DROPPED FACT rather than as a broken server.

    `done_reason == "length"` IS CHECKED EXPLICITLY AND BEFORE THE PARSE, rather than being
    left to `json.loads` to notice. Finding O1 measured this case on real photographs, and
    the reason it needs its own check is that a truncation which happened to land after a
    closing brace WOULD PARSE - and pam would store a caption the model did not finish
    writing, with no way for anyone downstream to tell.
    """
    if status != 200:
        raise CaptionProtocolError(f"Ollama answered HTTP {status} to POST {GENERATE_PATH}")
    try:
        envelope = json.loads(body)
    except ValueError as exc:
        raise CaptionProtocolError(f"Ollama's reply is not JSON: {exc}") from exc
    if not isinstance(envelope, dict):
        raise CaptionProtocolError(
            f"Ollama's reply is a {type(envelope).__name__}, not an object"
        )
    answer = envelope.get(RESPONSE_FIELD)
    if not isinstance(answer, str):
        raise CaptionProtocolError(
            f"Ollama's reply carries no {RESPONSE_FIELD!r} string (keys: {sorted(envelope)})"
        )
    if envelope.get(DONE_REASON_FIELD) == TRUNCATED_DONE_REASON:
        raise CaptionMalformedError(
            "the model ran out of context and stopped mid-answer"
            f" ({DONE_REASON_FIELD}={TRUNCATED_DONE_REASON!r}); the caption is unfinished"
        )
    try:
        return json.loads(answer)
    except ValueError as exc:
        raise CaptionMalformedError(
            f"the model's answer is not JSON: {exc}: {answer[:120]!r}"
        ) from exc


class CaptionEnricher:
    """Design 8.1's `caption`. Satisfies `enrich.Enricher`; the runner owns everything else.

    `enabled` IS NOT HERE, and that is amendment A2. A disabled enricher is NOT RUN, is
    reported, and EXITS 0, while `available()` returning False means the user asked for
    work pam could not do and exits 1. Routing "captioning is off" through `available()`
    would make `pam enrich` exit 1 on a default install, on a machine where nothing is
    wrong - so the enabled check lives in `enrich/command.py`, before this class is even
    asked whether it is ready.
    """

    name = NAME
    model_version = MODEL_VERSION

    def __init__(
        self,
        model: str,
        *,
        port: int,
        timeout_s: float,
        host: str = "127.0.0.1",
        resolver: net.Resolver = net.system_resolver,
        logger: logging.Logger | None = None,
    ) -> None:
        """Build the loopback client, or remember why it could not be built.

        `net.LoopbackHttpClient`'s constructor resolves the host and refuses unless every
        address it answers is loopback. That refusal is a `PamNetworkError`, and it must
        NOT escape a builder: `_run_one` calls `available()` to find out whether an
        enricher can work, and an exception thrown while constructing one would be a
        traceback where design 8.1 promises a sentence. So it is caught HERE and returned
        THERE, which also means a non-loopback host never reaches a connection attempt -
        there is no client to make one with.
        """
        self.model_name = model
        self._log = logger or logging.getLogger("pam")
        self._client: net.LoopbackHttpClient | None = None
        self._refusal = ""
        try:
            self._client = net.LoopbackHttpClient(
                host, port, timeout=timeout_s, resolver=resolver
            )
        except PamNetworkError as error:
            self._refusal = str(error)

    def available(self) -> tuple[bool, str]:
        """Is there an Ollama on that port, and does it hold the configured model?

        Three failures, each its own sentence, because they are three different mistakes:
        the client could not be built at all (an unacceptable host); nothing answered
        (`PamNetworkError` out of the request); or something answered and does not have
        this model. `pam doctor` reports the same three separately and this refusal ends
        at its door (`enrich/command.py` prints `Run: pam doctor`).

        THE `:latest` SPELLING IS FINDING O2 AND IT IS CHECKED HERE TOO. `GET /api/tags`
        lists the installed model as `qwen2.5vl:latest` while design 11's default is the
        bare `qwen2.5vl`, so a membership test against the listing alone reports "not
        installed" on a correct install. `/api/generate` resolves the bare name fine; it is
        only the listing that differs.
        """
        if self._client is None:
            return False, self._refusal
        try:
            response = self._client.request("GET", "/api/tags")
        except PamNetworkError as error:
            return False, str(error)
        if response.status != 200:
            return False, (
                f"the Ollama on {self._client.host}:{self._client.port} answered HTTP"
                f" {response.status} to GET /api/tags"
            )
        try:
            listed = _model_names(response.body)
        except ValueError as error:
            return False, (
                f"{self._client.host}:{self._client.port} answered, but not as Ollama: {error}"
            )
        if not _is_installed(self.model_name, listed):
            return False, (
                f"the Ollama on {self._client.host}:{self._client.port} does not have"
                f" {self.model_name}. Run: pam models pull {self.model_name}"
            )
        return True, f"{self.model_name} on {self._client.host}:{self._client.port}"

    def cost_estimate_ms(self) -> int:
        return COST_ESTIMATE_MS

    def wants(self, file: FileRecord) -> bool:
        """Images that are really on this disk. Two refusals, each for its own reason.

        A CLOUD PLACEHOLDER IS REFUSED BECAUSE READING ONE HYDRATES IT, which vision R2
        forbids absolutely - and here it would also send the hydrated bytes to a model.
        This is a decision about the ACT of reading, not about the answer, so
        `tests/test_caption.py` watches the OPEN rather than the fact: a refusal that read
        the bytes and then discarded them would look identical from the outside (M8's M697
        is the precedent).

        VIDEOS ARE REFUSED because design 8.1 rev 3 says every pixel-reading enricher does:
        no video decoding exists in v1.0. It is written as an ALLOWLIST of `image` rather
        than a denylist of `video`, so a media type nobody has thought of yet - and
        `sidecar` and `other`, which pam does not caption at all - defaults to NOT BEING
        FED TO A MODEL, which is the direction a mistake here should fall.
        """
        if file.placeholder:
            return False
        return file.media_type == "image"

    def enrich(self, file: FileRecord, image: PILImageLoader) -> list[InferredFact]:
        """One caption. `image` is the runner's loader; this never opens a path itself.

        `confidence=None` because Ollama returns none and design 8.1 says so in as many
        words ("confidence proxy: none from Ollama -> store NULL"). Vision Q3 rev 4
        (amendment A7) settles what that means for the criterion: the token is traceable to
        a named model and carries the TIER its confidence-lessness produced, and A FABRICATED
        CONFIDENCE WOULD BE WORSE THAN NONE - principle 2 forbids provenance that does not
        match the artifact.

        The parsed answer is handed on UNTOUCHED. This method decides nothing about whether
        it is a good caption; spec 6.4's write-time validation does, in the runner.
        """
        if self._client is None:  # pragma: no cover - `available()` refused first
            raise CaptionProtocolError(self._refusal)
        with image(file) as decoded:
            encoded = _encoded_image(decoded)
        body = json.dumps(
            {
                "model": self.model_name,
                "prompt": PROMPT,
                "images": [encoded],
                # `stream: false` because `LoopbackHttpClient` reads a whole response and
                # does not stream, deliberately (design 10). `format: json` is what makes
                # layer 2 a JSON STRING rather than prose.
                "stream": False,
                "format": "json",
            }
        ).encode("utf-8")
        response = self._client.request(
            "POST", GENERATE_PATH, body=body, headers={"Content-Type": "application/json"}
        )
        parsed = parse_reply(response.status, response.body)
        return [InferredFact(kind="caption", value=parsed, confidence=None)]


def _model_names(body: bytes) -> list[str]:
    """The model names in a `GET /api/tags` body, or `ValueError` if it is not one.

    Written independently of `pam/doctor.py`'s reader of the same route rather than shared
    with it. Two implementations of one wire format catch each other's drift; a single
    helper imported by both would make `pam doctor` say "the model is there" and
    `pam enrich` say "it is not" impossible to have measured (CLAUDE.md: keep an
    independent second implementation). The layering also matters - `pam/enrich/` importing
    `pam/doctor.py` would put a diagnostic module inside an enrichment run.
    """
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise ValueError(f"the reply is not JSON: {exc}") from exc
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        raise ValueError("the reply carries no 'models' list")
    return [entry["name"] for entry in models if isinstance(entry, dict) and "name" in entry]


def _is_installed(model: str, listed: list[str]) -> bool:
    """Finding O2: `qwen2.5vl` and `qwen2.5vl:latest` are the same installed model.

    A bare name matches its own `:latest` spelling and nothing else - `qwen2.5vl` does not
    match `qwen2.5vl:7b`, which is a DIFFERENT model whose captions would differ, and a
    name that already carries a tag is compared literally.
    """
    if model in listed:
        return True
    return ":" not in model and f"{model}:latest" in listed
