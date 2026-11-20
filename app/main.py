"""HTTP API + static UI for the AP Invoice Agent."""
from __future__ import annotations

import json
import threading
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import ROOT, settings
from app.db import Database
from app.pipeline.reader import ocr_available, render_preview
from app.pipeline.runner import STAGES, BusyError, Runner
from app.reference import Reference

VERSION = "1.0.0"
STATIC = ROOT / "static"

db = Database(settings.db_path)
ref = Reference(settings.reference_dir)
runner = Runner(db, ref, settings)
state = {"seeding": False, "seed_error": None}
_seed_lock = threading.Lock()


def _manifest() -> dict:
    return json.loads((settings.samples_dir / "manifest.json").read_text())


def _seed_async(reset: bool = False) -> None:
    state["seeding"] = True  # set synchronously so no run can start against a half-seeded database

    def work():
        with _seed_lock:
            state["seeding"] = True
            state["seed_error"] = None
            try:
                if reset:
                    runner.reset()
                elif not db.list_runs(limit=1):
                    runner.seed_history()
            except Exception as exc:  # pragma: no cover
                state["seed_error"] = str(exc)
            finally:
                state["seeding"] = False
    threading.Thread(target=work, daemon=True).start()


@asynccontextmanager
async def lifespan(_app):
    _seed_async()
    yield


app = FastAPI(title="AP Invoice Agent", version=VERSION, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def revalidate_static(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/health")
def health():
    return {"ok": True, "version": VERSION, "seeding": state["seeding"]}


@app.get("/api/config")
def config():
    return {
        "version": VERSION,
        "company": ref.company,
        "policy": ref.policy,
        "stages": STAGES,
        "ai": {"enabled": settings.ai_enabled, "model": settings.gemini_model if settings.ai_enabled else None},
        "ocr": ocr_available(),
        "stage_delay_ms": settings.stage_delay_ms,
        "max_upload_mb": settings.max_upload_mb,
        "seeding": state["seeding"],
        "reset_protected": bool(settings.admin_token),
    }


# ---------------------------------------------------------------- samples
@app.get("/api/samples")
def samples():
    return _manifest()["demo"]


def _sample(sample_id: str) -> dict:
    item = next((s for s in _manifest()["demo"] if s["id"] == sample_id), None)
    if not item:
        raise HTTPException(404, "Unknown sample")
    return item


@app.get("/api/samples/{sample_id}/file")
def sample_file(sample_id: str):
    item = _sample(sample_id)
    return FileResponse(settings.samples_dir / item["file"], media_type="application/pdf",
                        filename=Path(item["file"]).name, content_disposition_type="inline")


@app.get("/api/samples/{sample_id}/preview.png")
def sample_preview(sample_id: str):
    item = _sample(sample_id)
    cache = settings.data_dir / "previews" / f"sample-{sample_id}.png"
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(render_preview((settings.samples_dir / item["file"]).read_bytes()))
    return FileResponse(cache, media_type="image/png", headers={"Cache-Control": "max-age=3600"})


# ---------------------------------------------------------------- runs
@app.post("/api/runs")
async def create_run(file: UploadFile | None = File(None), sample_id: str | None = Form(None)):
    if state["seeding"]:
        raise HTTPException(503, "Demo history is being prepared - try again in a few seconds")
    if sample_id:
        item = _sample(sample_id)
        data = (settings.samples_dir / item["file"]).read_bytes()
        name, source = Path(item["file"]).name, "sample"
    elif file is not None:
        data = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
        if len(data) > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f"File is larger than {settings.max_upload_mb} MB")
        if not data or b"%PDF" not in data[:1024]:
            raise HTTPException(400, "Please upload a PDF invoice (the file is not a PDF)")
        name = Path(file.filename or "invoice.pdf").name[:120]
        source = "upload"
    else:
        raise HTTPException(400, "Provide a PDF file or a sample_id")
    try:
        run_id = runner.submit(data, name, source, sample_id=sample_id)
    except BusyError as exc:
        raise HTTPException(429, str(exc))
    return {"run_id": run_id}


@app.get("/api/runs")
def list_runs(decision: str | None = None, q: str | None = None, limit: int = Query(200, le=500)):
    return db.list_runs(decision=decision, q=q, limit=limit)


def _run_or_404(run_id: int) -> dict:
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return run


@app.get("/api/runs/{run_id}")
def get_run(run_id: int):
    run = _run_or_404(run_id)
    run.pop("file_path", None)
    run["reviews"] = db.reviews_for_run(run_id)
    return run


@app.get("/api/runs/{run_id}/events")
def run_events(run_id: int, after: int = 0):
    run = _run_or_404(run_id)
    return {"status": run["status"], "decision": run["final_decision"] or run["decision"],
            "events": db.get_events(run_id, after)}


@app.get("/api/runs/{run_id}/file")
def run_file(run_id: int):
    run = _run_or_404(run_id)
    path = Path(run["file_path"])
    if not path.exists():
        raise HTTPException(404, "File no longer available")
    return FileResponse(path, media_type="application/pdf", filename=run["filename"], content_disposition_type="inline")


@app.get("/api/runs/{run_id}/preview.png")
def run_preview(run_id: int):
    run = _run_or_404(run_id)
    path = Path(run["file_path"])
    cache = path.with_suffix(".png")
    if not cache.exists():
        if not path.exists():
            raise HTTPException(404, "File no longer available")
        try:
            cache.write_bytes(render_preview(path.read_bytes()))
        except Exception:
            raise HTTPException(422, "Preview not available for this file")
    return FileResponse(cache, media_type="image/png")


class ReviewIn(BaseModel):
    action: str
    reviewer: str = ""
    note: str = ""


@app.post("/api/runs/{run_id}/review")
def review(run_id: int, body: ReviewIn):
    _run_or_404(run_id)
    try:
        run = runner.apply_review(run_id, body.action, body.reviewer, body.note)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    run.pop("file_path", None)
    return run


# ---------------------------------------------------------------- dashboard
CATEGORY = [("dup.", "Duplicate invoice"), ("vendor.bank", "Bank details changed"), ("vendor.status", "Blocked vendor"),
            ("vendor.known", "Unknown vendor"), ("vendor.gstin", "Vendor GSTIN mismatch"), ("recon.price", "Price variance"),
            ("recon.abs", "Price variance"), ("recon.qty", "Over-billing vs PO"), ("recon.value", "Over-billing vs PO"),
            ("recon.grn", "Goods not received"), ("recon.lines", "Items not on PO"), ("po.", "PO missing / unclear"),
            ("extract.", "Missing information"), ("read.", "Unreadable document"), ("math.", "Arithmetic error"),
            ("tax.", "GST error"), ("gst.", "GST error"), ("date.", "Date issue"), ("gate.", "Above approval limit")]


def _category(check_id: str) -> str:
    return next((label for prefix, label in CATEGORY if check_id.startswith(prefix)), "Other")


@app.get("/api/stats")
def stats():
    rows = [r for r in db.stats()["rows"] if r["status"] == "completed"]
    decisions = Counter(r["d"] for r in rows)
    auto = sum(1 for r in rows if r["agent"] == "APPROVED")
    touched = sum(1 for r in rows if r["decided_by"] not in (None, "agent"))
    reasons: Counter = Counter()
    compute = []
    for r in rows:
        result = json.loads(r["result_json"]) if r.get("result_json") else {}
        dec = result.get("decision") or {}
        if dec.get("reasons"):
            reasons[_category(dec["reasons"][0]["check"])] += 1
        st = result.get("stages") or {}
        compute.append(sum((s.get("compute_ms") or 0) for s in st.values()))
    today = datetime.now(timezone.utc).date()
    days = [(today - timedelta(days=i)) for i in range(20, -1, -1)]
    per_day = defaultdict(Counter)
    for r in rows:
        d = datetime.fromisoformat(r["created_at"]).date()
        per_day[d][r["d"]] += 1
    approved_value = sum(r["total"] or 0 for r in rows if r["d"] == "APPROVED")
    return {
        "total_runs": len(rows),
        "in_progress": sum(1 for r in db.stats()["rows"] if r["status"] == "running"),
        "decisions": dict(decisions),
        "straight_through_rate": round(auto / len(rows) * 100, 1) if rows else 0.0,
        "human_reviewed": touched,
        "open_reviews": decisions.get("NEEDS_REVIEW", 0),
        "value_processed": round(sum(r["total"] or 0 for r in rows), 2),
        "value_approved": round(approved_value, 2),
        "avg_compute_ms": round(sum(compute) / len(compute)) if compute else 0,
        "exceptions_by_reason": reasons.most_common(),
        "daily": [{"date": d.isoformat(), **{k: per_day[d].get(k, 0) for k in ("APPROVED", "NEEDS_REVIEW", "RETURNED", "REJECTED")}}
                  for d in days],
        "seeding": state["seeding"],
    }


@app.get("/api/reference")
def reference():
    ledger = db.ledger_all()
    by_po = defaultdict(list)
    for e in ledger:
        by_po[e["po_number"]].append(e)
    pos = []
    for po in ref.purchase_orders:
        entries = by_po.get(po["po_number"], [])
        billed_value = sum(e["amount_ex_tax"] or 0 for e in entries)
        billed_qty = defaultdict(float)
        for e in entries:
            for a in e["allocations"]:
                if a.get("po_line"):
                    billed_qty[a["po_line"]] += a.get("qty") or 0
        received = ref.received_qty(po["po_number"])
        vendor = ref.vendor(po["vendor_id"])
        pos.append(po | {
            "vendor_name": vendor["name"] if vendor else po["vendor_id"],
            "billed_value": round(billed_value, 2),
            "utilisation_pct": round(billed_value / po["value_ex_tax"] * 100, 1) if po["value_ex_tax"] else 0,
            "invoices": [{"run_id": e["run_id"], "invoice_number": e["invoice_number"], "amount_ex_tax": e["amount_ex_tax"],
                          "posted_by": e["posted_by"]} for e in entries],
            "lines": [ln | {"billed_qty": billed_qty.get(ln["line_no"], 0),
                            "received_qty": received.get(ln["line_no"]) if po["type"] == "goods" else None}
                      for ln in po["lines"]],
        })
    vendors = []
    for v in ref.vendors:
        b = v["bank"]
        vendors.append({k: v[k] for k in v if k != "bank"} | {
            "bank": {"bank_name": b["bank_name"], "branch": b["branch"], "ifsc": b["ifsc"],
                     "account": "…" + b["account_number"][-4:]}})
    return {"company": ref.company, "policy": ref.policy, "vendors": vendors, "purchase_orders": pos,
            "goods_receipts": ref.goods_receipts}


@app.post("/api/admin/reset")
def reset(x_admin_token: str | None = Header(None)):
    if settings.admin_token and x_admin_token != settings.admin_token:
        raise HTTPException(403, "Reset requires the admin token")
    if state["seeding"]:
        raise HTTPException(409, "Demo data is already being prepared")
    if db.count_active():
        raise HTTPException(409, "Invoices are still being processed - wait for them to finish, then reset")
    state["seeding"] = True  # block new runs immediately, before the reset thread starts
    _seed_async(reset=True)
    return JSONResponse({"ok": True}, status_code=202)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)
