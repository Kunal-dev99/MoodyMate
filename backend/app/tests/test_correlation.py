from fastapi.testclient import TestClient


def test_inbound_id_is_echoed(client: TestClient) -> None:
    r = client.get("/api/v1/nope", headers={"X-Correlation-ID": "abc-123"})
    assert r.headers["X-Correlation-ID"] == "abc-123"
    assert r.json()["correlation_id"] == "abc-123"


def test_id_generated_when_absent(client: TestClient) -> None:
    r = client.get("/api/v1/health")
    assert len(r.headers["X-Correlation-ID"]) == 36  # uuid4


def test_unsafe_inbound_id_is_replaced(client: TestClient) -> None:
    r = client.get("/api/v1/health", headers={"X-Correlation-ID": "bad id\nINJECTED"})
    assert "INJECTED" not in r.headers["X-Correlation-ID"]
    assert len(r.headers["X-Correlation-ID"]) == 36


def test_each_request_gets_its_own_id(client: TestClient) -> None:
    a = client.get("/api/v1/health").headers["X-Correlation-ID"]
    b = client.get("/api/v1/health").headers["X-Correlation-ID"]
    assert a != b
