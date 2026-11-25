"""The single Gemini call that reads the RFP.

Why Gemini: the free tier needs no payment card, and Flash models take ~1M tokens
of context, so a 100-page RFP (~60-80k tokens) fits in ONE call. No chunking means
no facts lost between chunks and no merge step.

Why one call: the brief's budget is one minute. In testing, a single call with
"thinking" left at its default took ~73 s on the sample RFP; with thinking set to
"low" and everything asked for at once (facts, dates, ratings AND the reasons),
it fits comfortably. Python then checks, scores and decides -- no second call.

Models are tried in order from GEMINI_MODELS. A retired model (404) or a rate
limit (429) falls through to the next, so a model shutdown never takes the app down.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import List, Optional, Tuple, Type, TypeVar

from pydantic import BaseModel, ValidationError

from .profile import profile_as_text
from .schemas import Extraction

log = logging.getLogger("rfp.llm")
T = TypeVar("T", bound=BaseModel)

# Full model first; "lite" models run on separate capacity, so they come early as fallbacks
# when the main model is overloaded (503). The report shows which model answered.
DEFAULT_MODELS = (
    "gemini-3.5-flash,gemini-3.5-flash-lite,gemini-3.8-flash,"
    "gemini-3.1-flash-lite,gemini-3.7-flash,gemini-3.6-flash"
)
TRANSIENT = ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "500", "INTERNAL", "DEADLINE",
             "10054", "getaddrinfo", "timed out", "Connection", "ConnectError", "ReadTimeout")  # busy server or flaky network


class LLMError(RuntimeError):
    pass


EXTRACTION_PROMPT = """You are the RFP triage analyst for idiaz.io. Read the ENTIRE government RFP below,
including schedules, attachments, addenda and forms, and fill in the response schema.

{profile}

GROUND RULES -- follow all of them:
1. Use ONLY what is written in the document. Do not use outside knowledge about the agency.
2. If something is missing, write exactly "Not stated" (and leave page/quote null).
3. For every fact, date, requirement and factor give the PDF page number (from the
   "=== PDF PAGE n ===" markers) and a SHORT quote (under 25 words) copied character-for-character
   from that page. Quote ONE continuous passage (never join pieces with "..."). Never paraphrase
   inside a quote: quotes are machine-checked against the PDF.
4. DATES: list every date (issue, questions due, pre-proposal conference or site visit, proposal
   due, demonstrations, award, contract start/end). Copy "as_written" exactly as printed.
   ADDENDA WIN: if an addendum changed a date, report ONLY the new date and explain the change in
   ambiguity_note. Text shown as [STRUCK OUT: ...] was crossed out in the PDF and is NOT valid:
   never report it as current. as_written must contain only the valid date. Fill date_iso (YYYY-MM-DD) only for an exact calendar day. A
   week ("week of ..."), a relative date ("30 days after award") or a missing year -> date_iso null,
   ambiguous=true. If two sections disagree with no addendum to settle it, list both and flag them.
5. REQUIREMENTS: list every minimum qualification, set-aside, certification, security clearance or
   background check, past-performance/reference ask, staffing or key-personnel rule, licence or
   registration, insurance or bond, and on-site requirement. ALSO include: addendum
   acknowledgements; set-aside rules (e.g. how much work the prime must perform); mandatory
   security, accessibility, data-residency and availability requirements (anything marked [M]);
   mandatory languages; background checks, wage rules and bonds; rules that can disqualify a
   bidder (e.g. no contact with other staff); proposal validity period. mandatory=true only for
   shall/must/minimum-qualification language. Do not invent any and do not skip any. Judge
   idiaz_status ONLY from the profile: if the profile does not say, use "unknown".
6. AMBIGUITIES: flag anything that could be read two ways instead of quietly choosing one.
7. FACTORS: rate all eight against the profile, one line each:
   service_match (is this what we build?), technical_alignment (AI/automation/software depth),
   eligibility_set_aside, certifications_clearances, team_size_staffing (large headcount, 24/7 crews
   or round-the-clock on-call hurt a small senior team), location_onsite, past_performance,
   value_and_timeline. "not_stated" when the RFP gives nothing to judge. "deal_breaker" only for a
   hard blocker that makes bidding pointless.
8. work_category: the single best category. Construction, janitorial/facilities, physical goods
   supply and non-IT professional services are NOT work idiaz.io does.
9. reasons_to_bid and reasons_not_to_bid: the 3-5 strongest of each, strongest first, specific to
   THIS document, each ending with a page reference like "(p. 7)". Do not decide; list both sides.
10. what_would_change_our_mind: 1-3 specific facts that, if learned, would flip the call.
11. open_questions: specific questions idiaz.io should submit before the question deadline.

THE RFP:
{document}
"""


class GeminiClient:
    def __init__(self, api_key: Optional[str] = None, models: Optional[str] = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        raw = models or os.getenv("GEMINI_MODELS") or DEFAULT_MODELS
        chain = [m.strip() for m in raw.split(",") if m.strip()]
        preferred = os.getenv("GEMINI_MODEL")          # a single preferred model goes first,
        if preferred and not models:                   # the rest stay as fallbacks
            chain = [preferred.strip()] + [m for m in chain if m != preferred.strip()]
        self.models: List[str] = chain
        self.timeout_s = int(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
        self._client = None
        self.last_model: Optional[str] = None

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _get(self):
        if not self.api_key:
            raise LLMError("GEMINI_API_KEY is not set on the server.")
        if self._client is None:
            from google import genai
            from google.genai import types
            self._client = genai.Client(
                api_key=self.api_key,
                http_options=types.HttpOptions(timeout=self.timeout_s * 1000),
            )
        return self._client

    def _config(self, model: str, schema: Type[BaseModel], thinking: bool, json_schema: bool = False):
        from google.genai import types
        kwargs = dict(temperature=0.0, response_mime_type="application/json")
        if json_schema:   # plain JSON-Schema route, for models that reject the typed schema
            kwargs["response_json_schema"] = schema.model_json_schema()
        else:
            kwargs["response_schema"] = schema
        if thinking:
            # Reading and copying facts does not need deep reasoning; "low" keeps it fast.
            if model.startswith("gemini-2.5"):
                budget = int(os.getenv("GEMINI_THINKING_BUDGET", "1024"))
                kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=budget)
            else:
                level = os.getenv("GEMINI_THINKING_LEVEL", "low")
                kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=level)
        return types.GenerateContentConfig(**kwargs)

    def generate(self, prompt: str, schema: Type[T]) -> Tuple[T, str]:
        client = self._get()
        errors: List[str] = []
        budget = float(os.getenv("LLM_TOTAL_BUDGET_SECONDS", "80"))
        start = time.time()
        # Per model: try with low thinking, then without a thinking setting, then the plain
        # JSON-schema route. "Busy" errors (503/429) are retried with a short back-off before
        # moving on; a 400 (bad config) moves to the next mode; 404 (retired) to the next model.
        modes = [(True, False), (False, False), (False, True)]
        for model in self.models:
            next_model = False
            for thinking, json_schema in modes:
                if next_model:
                    break
                for attempt in range(2):
                    if time.time() - start > budget:
                        raise LLMError("Gave up after %.0fs. " % budget + " | ".join(errors))
                    try:
                        t0 = time.time()
                        resp = client.models.generate_content(
                            model=model, contents=prompt,
                            config=self._config(model, schema, thinking, json_schema),
                        )
                        log.info("model=%s thinking=%s took %.1fs", model, thinking, time.time() - t0)
                        parsed = getattr(resp, "parsed", None)
                        obj = parsed if isinstance(parsed, schema) else schema.model_validate(json.loads(resp.text))
                        self.last_model = model
                        return obj, model
                    except (ValidationError, json.JSONDecodeError, TypeError) as exc:
                        errors.append(f"{model}: malformed JSON ({exc.__class__.__name__})")
                        break          # try the next mode
                    except Exception as exc:  # API errors
                        msg = str(exc)
                        errors.append(f"{model}: {msg[:120]}")
                        if any(code in msg for code in TRANSIENT):
                            if attempt < 1:
                                time.sleep(2)                   # busy: one quick retry
                                continue
                            next_model = True                   # still busy: try another model
                            break
                        if "400" in msg or "INVALID_ARGUMENT" in msg:
                            break      # config rejected: try the next mode
                        next_model = True                       # 404 or anything else
                        break
        raise LLMError("All configured models failed. " + " | ".join(errors))


def build_extraction_prompt(tagged_document: str) -> str:
    return EXTRACTION_PROMPT.format(profile=profile_as_text(), document=tagged_document)


def extract(client: GeminiClient, tagged_document: str) -> Tuple[Extraction, str]:
    return client.generate(build_extraction_prompt(tagged_document), Extraction)
