# RFP Triage Agent

Upload a government RFP as a PDF and get a bid / no-bid call in under a minute. Every fact, date and requirement in the report links to the PDF page it came from, and the app checks each quote against the document before showing it.

**Live demo:** `<YOUR-VERCEL-URL>` &nbsp;·&nbsp; **API docs:** `<YOUR-VERCEL-URL>/docs`

Built by Muhammad Umar Farooq for the idiaz.io AI & Product Internship Challenge 2026.

---

## Results on the sample RFPs

| RFP | Decision | Fit | Time | Quotes verified | Notes |
|---|---|---|---|---|---|
| Sample 1: Port Alder permit automation | **BID** | **7.2** | **47 s** | **38/39** | Proposal due 15 Jan 2027, 3:00 PM Pacific: the struck-out 8 Jan date was detected and ignored. Main risks flagged: 24/7 Severity 1 on-call, $420k cap covering build plus 2 years of hosting and AI fees, UAT termination clause. The one unverified quote joined two separate sentences, and the checker caught it. |
| Sample 2: Larkspur County janitorial | `<NO-BID>` | `<x.x>` | `<s>` | `<n/m>` | Fast no-bid: janitorial work. |

---

## What it returns

| The brief asks for | Field |
|---|---|
| Plain-English summary | `summary`, `scope_of_work` |
| Key dates as real calendar dates | `deadlines` (brief's shape) and `key_dates` (ISO date, text exactly as printed, time, time zone, days left, page, issues) |
| Requirements: skills, certifications, set-asides, clearances, past performance, staffing | `requirements`: each marked mandatory or preferred, and *meets / gap / unknown* for idiaz.io |
| Fit score 0–10 with a one-line reason per factor | `fit_score`, `fit_breakdown.factors` |
| Bid / no-bid, three strongest reasons, what would change our mind | `decision`, `reasons`, `deal_breakers`, `what_would_change_our_mind` |
| Agency, scope, contract type and value, location, how to submit | `facts.*` |
| Questions for the agency | `open_questions` |
| Ambiguities flagged, not silently resolved | `ambiguities`, plus `issues` on each date |
| "Not stated" when missing | every `facts.*` field |
| Fast no-bid for work that isn't ours | `fast_no_bid: true`, score capped at 2.0 |

The top level of the JSON matches the example in the brief (`decision`, `fit_score`, `deadlines`, `requirements`, `reasons`). Everything else sits alongside it.

---

## How it works

```
PDF ─► PyMuPDF: text of every page, tagged "=== PDF PAGE n ===",
       crossed-out words marked [STRUCK OUT: ...]
    ─► ONE Gemini call (structured output): facts, dates, requirements, 8 factor ratings,
       reasons on both sides, each with a page number and a short exact quote
    ─► Verifier (plain Python): is each quote really on that page? Does each date's printed
       text match its ISO date? Does "Tuesday" fall on a Tuesday? Was this date crossed out?
    ─► Scorer (plain Python): weighted 0-10 score, gates, BID / NO_BID,
       then picks the 3 reasons that support that decision
    ─► JSON report ─► web UI
```

**The model reads; the code checks and decides.**

### Why the score is computed in code

If you ask a model for "a score from 0 to 10", you get a number you can't audit, and it can change from one run to the next. Here the model only rates eight factors on a fixed scale (strong positive → deal-breaker). `app/scoring.py` turns those ratings into the score:

| Factor | Weight |
|---|---|
| Matches what we build | 3.0 |
| Technical / AI alignment | 1.5 |
| Eligibility and set-asides | 1.5 |
| Certifications and clearances | 1.0 |
| Team size and staffing load | 1.0 |
| Location and on-site needs | 0.75 |
| Past-performance asks | 0.75 |
| Contract value and timeline | 0.5 |

Each rating earns a share of its weight: strong positive 1.0, positive 0.75, neutral or not stated 0.5, negative 0.15, deal-breaker 0. Then three gates apply:

* construction, janitorial, goods supply and non-IT services → **fast no-bid**, capped at 2.0;
* any deal-breaker → capped at 4.0 and NO_BID;
* adjacent work (IT staffing, hardware, advisory) → capped at 7.0.

BID needs 6.0 or more and no deal-breaker. Scores within 1.0 of the threshold are labelled *borderline*. "Not stated" counts as neutral: missing information shouldn't push the score either way, so it becomes an open question for the agency instead.

The model lists the strongest reasons **on both sides**. Code then shows the side that matches its decision, so the reasons can never argue against the call. The other side is still shown under "The case for/against bidding".

### Why one call, and why under a minute

A 100-page RFP is about 60–80k tokens, and Gemini Flash takes about a million, so the whole document goes in one request. There's no chunking and no merge step where facts can get lost.

The first prototype took 73 s on Sample 1 because the model's "thinking" was left at its default. Copying facts out of a document doesn't need deep reasoning, so thinking is set to `low`. Everything the report needs, including the reasons, comes from that one call. Verification and scoring take milliseconds.

### Why Gemini

* Free tier through Google AI Studio, no payment card.
* Long context, so one call is enough.
* Structured output: the Pydantic schema in `app/schemas.py` is sent as the response schema, so the reply is always valid JSON in the expected shape.
* `temperature=0` for repeatable reads.

Model names come from `GEMINI_MODELS`, an ordered list. When Gemini is busy (503/429) or the network drops, the call is retried once after 2 s, then the next model is tried (lighter "lite" models come early because they run on separate capacity). A hung call is abandoned after 60 s and the whole attempt after 80 s, so the user gets a clear "try again" message instead of a long wait. A retired model (404) is skipped at once. During testing Google returned 503 "high demand" for every model, which is why this exists.

### Why Vercel

The whole app (FastAPI serving both the API and a static UI) is deployed on Vercel's free Hobby plan.

* **Free with no card**, and listed in the brief. I tried Hugging Face Spaces first, but Docker Spaces now need a paid plan. Render asked for a card to verify identity, which the brief rules out.
* **Zero configuration for FastAPI**: Vercel finds the `app` object in `app/main.py` and runs it as a single serverless function. `vercel.json` raises the function's time limit to 300 s, so a 30–50 s model call is never cut off.
* **No sleeping server**: unlike free container hosts that sleep after 15 minutes and take about a minute to wake, a serverless function starts in seconds.
* **Trade-offs:** Vercel caps uploads at 4.5 MB. A 100-page text PDF is usually well under that (the samples are 175 KB and 138 KB), and the UI says so if a file is too big. PyMuPDF is pinned to 1.x because 2.x pulls in a large ML runtime we don't use, which would bloat the function.
* The same code also runs anywhere Docker runs (`Dockerfile`, `render.yaml`) or locally with `uvicorn`.

---

## Bonus feature: a report you can check

**What it is.** Every fact, date, requirement and fit factor carries a page chip such as `p.12 ✓`. Click it and a side panel shows that PDF page's full text with the quote highlighted. Before anything is shown, plain Python checks each quote against the PDF:

| Chip | Meaning |
|---|---|
| `p.12 ✓` verified | Quote found word for word on the cited page |
| `p.14 ↻` page corrected | Quote found on a different page; the page number is fixed automatically |
| `p.12 ≈` near-exact | ≥ 90 % match, usually a line break inside the quote |
| `p.? ⚠` unverified | Not found anywhere in the PDF; flagged for manual review |

Dates get extra checks:
* the printed text is parsed on its own and must give the same calendar date the model returned;
* a printed weekday must agree with the date;
* **crossed-out text is detected.** Agencies amend RFPs by striking out the old value and printing the new one next to it. Plain text extraction loses the line, so a PDF can read "Proposals due January 8, 2027 January 15, 2027". The reader finds thin lines drawn through the *middle* of words (underlines sit at the bottom and table borders sit between rows) and marks those words `[STRUCK OUT: January 8, 2027]` before the model sees them. If the model still reports a crossed-out date, the verifier flags it.

The header shows a trust meter, e.g. *Evidence checked: 38/39 (97 %)*.

**Safety net: no must-have is missed.** The model's requirement list is curated, and a model can still skip an item. So `app/coverage.py` scans every page in plain code for sentences with mandatory language (*must, shall, required, mandatory, minimum qualification* or an `[M]` marker). It lists, under the requirements table, every one the curated list doesn't already cover, each with a clickable page. On Sample 1 it surfaces items such as *"Proposers must acknowledge this addendum on Attachment A"* and *"the prime Proposer must perform at least 50% of the work by cost"*. Missing either of those can get a proposal rejected.

**Why I chose it.** The brief asks for a decision the team can *trust*: every date exactly as in the document, and no invented requirements. A language model can't guarantee either on its own. A bids lead is about to commit a week of proposal writing on this report, so they need to check any line in two seconds, and the system should tell them which lines need checking. Sample 1's struck-out due date is exactly the kind of mistake that loses a bid.

**How it works.** `app/pdf_reader.py` finds strike lines from the PDF's vector drawings. `app/verify.py` normalises text (curly quotes, dashes, hyphenated line breaks, whitespace), looks for an exact match on the cited page, then on every page, then a fuzzy match with RapidFuzz. No model is involved, so the checks are fast, deterministic and explainable. The tests include a made-up "Secret clearance" requirement, which gets caught and marked red.

### Small extras
* **Triage board:** every RFP analysed in your browser, ranked BID-first by fit score, with days until the deadline. Built for the "three new RFPs on Monday" scenario.
* **Calendar export:** an `.ics` file of all dated milestones, with two-day reminders before the question and proposal deadlines.
* **Compliance checklist:** requirements as CSV, a starting point for the proposal team's compliance matrix.
* **Questions email:** the open questions, ready to send to the contracting officer.
* **Cache:** re-uploading the same PDF (matched by SHA-256) returns the stored report instantly, which also saves free-tier quota during demos.

---

## Run it locally (Windows PowerShell)

```powershell
git clone <this-repo>; cd rfp-triage-agent
python -m venv .venv; .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
copy .env.example .env        # then put your key from https://aistudio.google.com/apikey in .env
uvicorn app.main:app --port 7860
# open http://localhost:7860
```
On macOS/Linux use `source .venv/bin/activate` and `cp`.

The tests run offline with a fake model client, so no key is needed:
```powershell
pytest -q
```
They cover PDF reading, strike-through detection, every verification status (including quotes stitched with "..."), the weekday and date checks, scoring and gates, reasons matching the decision, the must-have safety net, the full pipeline, caching and the HTTP API.

### API
```bash
curl -F "file=@Sample-RFP-1-Port-Alder-Permit-Automation.pdf" https://<YOUR-VERCEL-URL>/api/triage
```

## Deploy (free)

1. Push this repo to GitHub.
2. vercel.com → sign up with GitHub (Hobby plan, no card) → **Add New → Project** → import the repo.
3. Under **Environment Variables**, add `GEMINI_API_KEY` → **Deploy**.
4. Open `https://<project>.vercel.app`.

Locally: `uvicorn app.main:app --port 7860`. With Docker: `docker build -t rfp . && docker run -p 7860:7860 -e GEMINI_API_KEY=... rfp`.

---

## Project layout

```
app/
  main.py        FastAPI routes: /, /api/triage, /api/health, /api/profile, /docs
  agent.py       the pipeline and report assembly
  pdf_reader.py  page text with markers, strike-through detection, scanned-PDF check
  llm.py         the prompt, the Gemini call, model fallback
  schemas.py     Pydantic schema (also sent to Gemini as the response schema)
  verify.py      quote and date checks
  scoring.py     weights, gates, decision, reason picking
  coverage.py    safety net: every "must" sentence not already in the requirements list
  profile.py     the idiaz.io profile the agent scores against
static/          index.html, app.js, styles.css (no build step)
tests/           offline tests with generated PDFs and a fake model client
```

## Limits and next steps

* **Text PDFs only.** Scanned PDFs are rejected with a clear message. Next: an OCR fallback.
* **Company profile.** The brief doesn't say how long idiaz.io has existed, its size, set-aside status, SOC 2 or clearances, so these are reported as *unknown* rather than guessed. Filling in `app/profile.py` turns many unknowns into firm meets or gaps. For example, Sample 1 requires 3 years in business.
* **Amendments in separate files.** In-document addenda and struck-out text are handled; separate addendum PDFs would need a multi-file upload.
* **Scale.** The cache is in memory and resets on restart. A team version would add a small database and a shared board.
