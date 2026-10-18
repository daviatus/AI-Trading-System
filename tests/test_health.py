from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from trading.api import app

client = TestClient(app)


def test_liveness_does_not_depend_on_database() -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_succeeds_when_database_is_available(monkeypatch) -> None:
    monkeypatch.setattr("trading.api.check_database_connection", lambda: None)

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readiness_reports_database_unavailability(monkeypatch) -> None:
    def fail_connection() -> None:
        raise OperationalError("SELECT 1", {}, RuntimeError("offline"))

    monkeypatch.setattr("trading.api.check_database_connection", fail_connection)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "Database is unavailable"}
