from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.main import app
from app.services import auth as auth_service

LEGACY_FIXTURE = (
    "2026-07-16 15:00:00 INFO price=19.99 P=-12.59 V=-0.13 A=-0.93 "
    "state=恐慌 api_key=legacy-secret\n"
    "2026-07-16 15:00:01 WARNING [ALERT] 恐慌极端\n"
).encode()
VALID_ID = "a" * 64


class FakeStore:
    def __init__(self) -> None:
        self.limits: list[tuple[str, int]] = []

    def list_snapshots(self, limit: int):
        self.limits.append(("snapshots", limit))
        return [{"kind": "snapshot"}]

    def list_candidates(self, limit: int):
        self.limits.append(("candidates", limit))
        return [{"kind": "candidate"}]

    def list_imports(self, limit: int):
        self.limits.append(("imports", limit))
        return [{"kind": "import"}]


class FakeShadowService:
    def status(self):
        return {
            "enabled": True,
            "symbol": "600489.SH",
            "latest": None,
            "latest_market_date": None,
            "health": {
                "latest_success_at": None,
                "latest_market_date": None,
                "consecutive_failures": 0,
                "last_error": None,
            },
            "external_send_count": 0,
        }


class FakeImporter:
    def __init__(self) -> None:
        self.upload: tuple[str, bytes] | None = None

    def import_bytes(self, filename: str, payload: bytes):
        self.upload = (filename, payload)
        return SimpleNamespace(
            import_id=VALID_ID,
            sha256=VALID_ID,
            filename=filename,
            imported_at="2026-07-20T00:00:00+00:00",
            sample_count=1,
            raw="must-not-be-returned",
        )


class FakeComparisonRunner:
    def __init__(self) -> None:
        self.request: tuple[str, str] | None = None
        self.limits: list[int] = []

    def run(self, import_id, market_date):
        self.request = (import_id, market_date.isoformat())
        return {"run_id": VALID_ID, "market_date": market_date.isoformat()}

    def list_runs(self, limit: int):
        self.limits.append(limit)
        return [{"run_id": VALID_ID}]

    def get_run(self, run_id: str):
        return {"run_id": run_id, "rows": [{"kind": "summary"}]}


class FakeObservationService:
    def __init__(self) -> None:
        self.reviews: list[tuple[object, ...]] = []

    def review_day(self, market_date, run_id, verified, note):
        self.reviews.append(("day", market_date.isoformat(), run_id, verified, note))

    def review_restart(self, verified, note):
        self.reviews.append(("restart", verified, note))

    def status(self):
        return {
            "status": "collecting",
            "required_complete_trading_days": 10,
            "complete_trading_days": len(self.reviews),
            "external_send_count": 0,
            "reasons": [],
        }


@pytest.fixture(autouse=True)
def reset_gold_state():
    names = (
        "gold_shadow_service",
        "gold_store",
        "gold_legacy_importer",
        "gold_comparison_runner",
        "gold_observation_service",
        "gold_sampler_registered",
        "scheduler",
    )
    previous = {name: getattr(app.state, name, None) for name in names}
    for name in names:
        setattr(app.state, name, None)
    yield
    for name, value in previous.items():
        setattr(app.state, name, value)


@pytest.fixture
def authenticated_client(monkeypatch):
    monkeypatch.setattr(auth_service, "is_configured", lambda: True)
    monkeypatch.setattr(auth_service, "is_valid_session", lambda token: token == "valid-session")
    client = TestClient(app)
    client.cookies.set(auth_api.COOKIE_NAME, "valid-session")
    yield client
    client.close()


@pytest.fixture
def unauthenticated_client(monkeypatch):
    monkeypatch.setattr(auth_service, "is_configured", lambda: True)
    monkeypatch.setattr(auth_service, "is_valid_session", lambda _token: False)
    client = TestClient(app)
    yield client
    client.close()


@pytest.fixture
def enabled_client(authenticated_client):
    store = FakeStore()
    importer = FakeImporter()
    runner = FakeComparisonRunner()
    observation = FakeObservationService()
    app.state.gold_shadow_service = FakeShadowService()
    app.state.gold_store = store
    app.state.gold_legacy_importer = importer
    app.state.gold_comparison_runner = runner
    app.state.gold_observation_service = observation
    authenticated_client.gold_fakes = SimpleNamespace(
        store=store,
        importer=importer,
        runner=runner,
        observation=observation,
    )
    return authenticated_client


def test_disabled_status_is_available_without_starting_sampler(authenticated_client):
    response = authenticated_client.get("/api/gold/status")

    assert response.status_code == 200
    assert response.json() == {
        "enabled": False,
        "symbol": None,
        "latest": None,
        "latest_market_date": None,
        "health": {
            "latest_success_at": None,
            "latest_market_date": None,
            "consecutive_failures": 0,
            "last_error": None,
        },
        "external_send_count": 0,
    }


@pytest.mark.parametrize(
    "path",
    [
        "/api/gold/snapshots",
        "/api/gold/candidates",
        "/api/gold/imports",
        "/api/gold/comparisons",
    ],
)
def test_disabled_list_endpoints_are_empty_without_creating_storage(
    authenticated_client, path
):
    response = authenticated_client.get(path)

    assert response.status_code == 200
    assert response.json() == {"rows": []}
    assert app.state.gold_store is None


def test_disabled_health_and_observation_gate_remain_readable(authenticated_client):
    health = authenticated_client.get("/api/gold/health")
    gate = authenticated_client.get("/api/gold/observation-gate")

    assert health.status_code == 200
    assert health.json()["last_error"] is None
    assert gate.status_code == 200
    assert gate.json() == {
        "status": "collecting",
        "required_complete_trading_days": 10,
        "complete_trading_days": 0,
        "external_send_count": 0,
        "reasons": ["gold_workspace_disabled"],
    }


def test_health_contains_late_scheduler_lookup_failure(enabled_client):
    class BrokenScheduler:
        def get_job(self, _job_id):
            raise RuntimeError("late-scheduler-secret")

    app.state.scheduler = BrokenScheduler()
    app.state.gold_sampler_registered = True

    status = enabled_client.get("/api/gold/status")
    health = enabled_client.get("/api/gold/health")

    assert status.status_code == 200
    assert status.json()["health"]["last_error"] is None
    assert health.status_code == 200
    assert health.json()["next_scheduled_run"] is None
    assert health.json()["last_error"] == {
        "code": "scheduler_unavailable",
        "message": "Gold sampler scheduler is unavailable",
    }
    assert "late-scheduler-secret" not in health.text


@pytest.mark.parametrize(
    ("path", "kwargs"),
    [
        (
            "/api/gold/imports/legacy",
            {"files": {"file": ("monitor.log", LEGACY_FIXTURE, "text/plain")}},
        ),
        (
            "/api/gold/comparisons",
            {"json": {"import_id": "x", "market_date": "2026-07-16"}},
        ),
        (
            "/api/gold/observation-reviews",
            {"json": {"kind": "restart", "verified": True, "note": "checked"}},
        ),
    ],
)
def test_disabled_write_endpoints_fail_with_stable_code(authenticated_client, path, kwargs):
    response = authenticated_client.post(path, **kwargs)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "gold_workspace_disabled"


@pytest.mark.parametrize(
    ("path", "kwargs"),
    [
        (
            "/api/gold/imports/legacy",
            {"files": {"file": ("monitor.log", LEGACY_FIXTURE, "text/plain")}},
        ),
        (
            "/api/gold/comparisons",
            {"json": {"import_id": VALID_ID, "market_date": "2026-07-16"}},
        ),
        (
            "/api/gold/observation-reviews",
            {"json": {"kind": "restart", "verified": True, "note": "checked"}},
        ),
    ],
)
def test_gold_write_endpoints_require_existing_project_authentication(
    unauthenticated_client, path, kwargs
):
    response = unauthenticated_client.post(path, **kwargs)

    assert response.status_code == 401


def test_upload_never_returns_raw_content(enabled_client):
    response = enabled_client.post(
        "/api/gold/imports/legacy",
        files={"file": ("monitor.log", LEGACY_FIXTURE, "text/plain")},
    )

    assert response.status_code == 201
    assert response.json() == {
        "import_id": VALID_ID,
        "sha256": VALID_ID,
        "filename": "monitor.log",
        "imported_at": "2026-07-20T00:00:00+00:00",
        "sample_count": 1,
    }
    assert "raw" not in response.json()
    assert enabled_client.gold_fakes.importer.upload == ("monitor.log", LEGACY_FIXTURE)


def test_enabled_reads_and_comparison_delegate_to_services(enabled_client):
    assert enabled_client.get("/api/gold/status").json()["enabled"] is True
    assert enabled_client.get("/api/gold/health").json()["consecutive_failures"] == 0
    assert enabled_client.get("/api/gold/snapshots?limit=3").json() == {
        "rows": [{"kind": "snapshot"}]
    }
    assert enabled_client.get("/api/gold/candidates?limit=4").json() == {
        "rows": [{"kind": "candidate"}]
    }
    assert enabled_client.get("/api/gold/imports?limit=5").json() == {
        "rows": [{"kind": "import"}]
    }
    assert enabled_client.get("/api/gold/comparisons?limit=6").json() == {
        "rows": [{"run_id": VALID_ID}]
    }
    created = enabled_client.post(
        "/api/gold/comparisons",
        json={"import_id": VALID_ID, "market_date": "2026-07-16"},
    )
    detail = enabled_client.get(f"/api/gold/comparisons/{VALID_ID}")

    assert created.status_code == 200
    assert created.json()["run_id"] == VALID_ID
    assert detail.status_code == 200
    assert detail.json()["rows"] == [{"kind": "summary"}]
    assert enabled_client.gold_fakes.store.limits == [
        ("snapshots", 3),
        ("candidates", 4),
        ("imports", 5),
    ]
    assert enabled_client.gold_fakes.runner.limits == [6]
    assert enabled_client.gold_fakes.runner.request == (VALID_ID, "2026-07-16")


def test_observation_reviews_use_discriminated_request_models(enabled_client):
    day = enabled_client.post(
        "/api/gold/observation-reviews",
        json={
            "kind": "day",
            "market_date": "2026-07-16",
            "run_id": VALID_ID,
            "complete_window_verified": True,
            "note": "complete window checked",
        },
    )
    restart = enabled_client.post(
        "/api/gold/observation-reviews",
        json={"kind": "restart", "verified": True, "note": "restart checked"},
    )

    assert day.status_code == 200
    assert restart.status_code == 200
    assert restart.json()["complete_trading_days"] == 2
    assert enabled_client.gold_fakes.observation.reviews == [
        ("day", "2026-07-16", VALID_ID, True, "complete window checked"),
        ("restart", True, "restart checked"),
    ]


@pytest.mark.parametrize(
    "path",
    [
        "/api/gold/snapshots",
        "/api/gold/candidates",
        "/api/gold/imports",
        "/api/gold/comparisons",
    ],
)
@pytest.mark.parametrize("limit", [0, 1001])
def test_list_limit_is_validated_from_one_through_one_thousand(
    enabled_client, path, limit
):
    response = enabled_client.get(path, params={"limit": limit})

    assert response.status_code == 422
