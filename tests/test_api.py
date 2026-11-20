"""HTTP API smoke tests."""
import time

from fastapi.testclient import TestClient

from app.main import app, state


def _client():
    c = TestClient(app)
    c.__enter__()  # run startup (seeding)
    for _ in range(100):
        if not state["seeding"]:
            break
        time.sleep(0.1)
    return c


client = _client()


def test_config_and_samples():
    cfg = client.get("/api/config").json()
    assert len(cfg["stages"]) == 9 and cfg["company"]["name"].startswith("Nimbus")
    samples = client.get("/api/samples").json()
    assert {s["id"] for s in samples} >= {"happy-path", "ec1-scanned-no-po", "ec4-bank-change"}
    assert client.get("/api/samples/happy-path/preview.png").headers["content-type"] == "image/png"


def test_run_lifecycle_and_events():
    rid = client.post("/api/runs", data={"sample_id": "x3-blocked-vendor"}).json()["run_id"]
    for _ in range(200):
        ev = client.get(f"/api/runs/{rid}/events").json()
        if ev["status"] != "running":
            break
        time.sleep(0.05)
    types = [e["type"] for e in ev["events"]]
    assert types[0] == "run_start" and types[-1] == "run_end" and types.count("stage_end") == 9
    run = client.get(f"/api/runs/{rid}").json()
    assert run["final_decision"] == "REJECTED" and "file_path" not in run
    assert client.post(f"/api/runs/{rid}/review", json={"action": "approve", "reviewer": "x"}).status_code == 409


def test_rejects_non_pdf_upload():
    r = client.post("/api/runs", files={"file": ("evil.pdf", b"<script>alert(1)</script>", "application/pdf")})
    assert r.status_code == 400


def test_dashboard_and_reference():
    stats = client.get("/api/stats").json()
    assert stats["total_runs"] >= 7 and 0 <= stats["straight_through_rate"] <= 100
    ref = client.get("/api/reference").json()
    po = next(p for p in ref["purchase_orders"] if p["po_number"] == "4500001187")
    assert po["utilisation_pct"] == 100.0
    assert all(v["bank"]["account"].startswith("…") for v in ref["vendors"])
