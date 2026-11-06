"""Start page key figures and the in-app documentation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import stats
from app.db import Base, Document, ReviewItem

NOW = datetime(2026, 3, 15, 10, 0, tzinfo=UTC)


def _doc(days_ago: float, *, by: str = "worker", total: str = "100.00", buyer: str = "A GmbH", **kw) -> Document:
    return Document(
        created_at=NOW - timedelta(days=days_ago),
        source=kw.get("source", "idoc"),
        profile=kw.get("profile", "zugferd"),
        invoice_number="X",
        buyer_name=buyer,
        grand_total=Decimal(total),
        currency=kw.get("currency", "EUR"),
        invoice={},
        filename="x.pdf",
        media_type="application/pdf",
        content=b"",
        created_by=by,
    )


def test_compute_figures() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add_all(
            [
                _doc(0, total="119.00"),
                _doc(0, by="anna", source="pdf", profile="xrechnung", buyer="B AG", total="500.00"),
                _doc(3, by="anna", source="manual", buyer="B AG", total="50.00"),
                _doc(20, total="10.00"),  # previous month (Feb), still in the 30-day window
                _doc(40, total="1000.00", currency="USD"),  # outside window; USD not in the EUR volume
                _doc(400),  # older than 12 months
                ReviewItem(status="offen", source="pdf", profile="zugferd", invoice={}),
                ReviewItem(
                    status="erledigt",
                    source="pdf",
                    profile="zugferd",
                    invoice={},
                    created_at=NOW - timedelta(hours=5),
                    updated_at=NOW - timedelta(hours=1),
                ),
            ]
        )
        s.commit()
        st = stats.compute(s, days=30, now=NOW)

    assert len(st.daily) == 30 and st.daily[-1].date.isoformat() == "2026-03-15"
    assert (st.daily[-1].automatic, st.daily[-1].manual) == (1, 1)
    assert st.automation_rate == 0.5  # 2 of 4 documents in the window
    assert st.by_profile == {"zugferd": 3, "xrechnung": 1}
    assert st.by_source == {"idoc": 2, "pdf": 1, "manual": 1}
    assert st.this_month.count == 3 and st.this_month.volume == Decimal("669.00")
    assert st.last_month.count == 2 and st.last_month.volume == Decimal("10.00")  # USD left out
    assert st.last_month_to_date.count == 1  # Feb 1..15 only: the doc from 23 Feb is not compared
    assert [m.month for m in st.monthly][-1] == "2026-03" and len(st.monthly) == 12
    assert st.top_buyers[0].name == "B AG" and st.top_buyers[0].volume == Decimal("550.00")
    assert (st.review.open, st.review.resolved, st.review.avg_hours_to_resolve) == (1, 1, 4.0)


def test_stats_endpoint(client: TestClient) -> None:
    r = client.get("/api/dashboard/stats", params={"days": 7})
    assert r.status_code == 200
    assert len(r.json()["daily"]) == 7
    assert client.get("/api/dashboard/stats", params={"days": 3}).status_code == 422


def test_user_manual_for_everyone_system_doc_for_admins(client: TestClient, admin_client: TestClient) -> None:
    manual = client.get("/api/docs/anwender").json()["markdown"]
    assert manual.startswith("# ")
    assert client.get("/api/docs/system").status_code == 403

    doc = admin_client.get("/api/docs/system").json()
    assert "# " in doc["markdown"]
    by_path = {(e["method"], e["path"]): e["access"] for e in doc["endpoints"]}
    assert by_path[("POST", "/api/auth/login")] == "öffentlich"
    assert by_path[("GET", "/api/dashboard/stats")] == "angemeldet"
    assert by_path[("GET", "/api/docs/system")] == "Admin"


def test_every_endpoint_has_a_german_description() -> None:
    from app.docs import DESCRIPTIONS, _api_routes
    from app.main import app

    missing = {r.name for r in _api_routes(app.routes) if r.path.startswith("/api")} - set(DESCRIPTIONS)
    assert not missing, f"Beschreibung in app/docs.py DESCRIPTIONS ergänzen: {missing}"


def test_manual_images_are_served_and_referenced() -> None:
    import re

    from app.docs import DOCS_DIR

    names = set(re.findall(r"\]\(img/([a-z0-9-]+\.png)\)", (DOCS_DIR / "anwender.md").read_text(encoding="utf-8")))
    assert names, "the manual should contain screenshots"
    assert all((DOCS_DIR / "img" / n).is_file() for n in names)


def test_image_endpoint(client: TestClient, admin_client: TestClient) -> None:
    assert client.get("/api/docs/img/startseite.png").headers["content-type"] == "image/png"
    assert client.get("/api/docs/img/admin-benutzer.png").status_code == 403
    assert admin_client.get("/api/docs/img/admin-benutzer.png").status_code == 200
    assert client.get("/api/docs/img/../docs.py").status_code == 404
    assert client.get("/api/docs/img/gibtsnicht.png").status_code == 404
