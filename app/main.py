"""FastAPI entry point: serves the UI and the JSON API."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from pathlib import Path

from dotenv import load_dotenv

# Local runs: read GEMINI_API_KEY from the project's .env (on Hugging Face it comes from Secrets).
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .agent import TriageAgent
from .llm import LLMError
from .pdf_reader import PDFError, read_pdf
from .profile import IDIAZ_PROFILE

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# Vercel caps request bodies at 4.5 MB; elsewhere allow up to 25 MB.
MAX_MB = float(os.getenv("MAX_UPLOAD_MB", "4.5" if os.getenv("VERCEL") else "25"))
STATIC = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(
    title="RFP Triage Agent",
    description="Upload a government RFP PDF; get a source-checked bid/no-bid triage report.",
    version="1.0.0",
)
agent = TriageAgent()


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "llm_configured": agent.client.configured,
        "models": agent.client.models,
        "last_model_used": agent.client.last_model,
        "max_upload_mb": MAX_MB,
    }


@app.get("/api/profile")
def profile():
    return IDIAZ_PROFILE


@app.post("/api/triage")
async def triage(
    file: UploadFile = File(..., description="RFP as a text-based PDF"),
    include_pages: bool = Query(False, description="Also return the text of every page (used by the UI's evidence viewer)."),
):
    data = await file.read()
    if len(data) > MAX_MB * 1024 * 1024:
        raise HTTPException(413, f"File is larger than {MAX_MB:.0f} MB.")
    try:
        report = await run_in_threadpool(agent.triage, data, file.filename or "rfp.pdf")
    except PDFError as exc:
        raise HTTPException(422, str(exc))
    except LLMError as exc:
        msg = str(exc)
        if "503" in msg or "UNAVAILABLE" in msg or "429" in msg:
            raise HTTPException(503, "Gemini is overloaded right now (Google-side). Please try again in a minute.")
        raise HTTPException(502, f"The language model could not be reached: {msg[:400]}")

    if include_pages:
        report = dict(report)
        report["pages"] = read_pdf(data).pages
    return JSONResponse(report)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
