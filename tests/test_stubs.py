import pytest
from fastapi.testclient import TestClient

from app import app
from db import connect, init_db


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", tmp_path / "t.db")
    init_db()
    yield


def _auth(c: TestClient) -> dict:
    tok = c.post(
        "/api/login", json={"username": "admin", "password": "123456"}
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}


def test_topup_stub_no_balance_change():
    c = TestClient(app)
    h = _auth(c)
    cage = c.get("/api/cages", headers=h).json()["items"][0]
    before_amount = cage["amount"]
    before_avail = cage["available"]
    r = c.post(
        "/api/topup-stub",
        headers=h,
        json={"cage_id": cage["id"], "amount": 5000, "mode": "同笼扩代"},
    )
    assert r.status_code == 200
    assert r.json()["cage_unchanged"] is True
    after = c.get(f"/api/cages/{cage['id']}", headers=h).json()["cage"]
    assert after["amount"] == before_amount
    assert after["available"] == before_avail
    jobs = c.get("/api/topup/jobs", headers=h).json()["items"]
    assert any(j["status"] == "空桩登记" for j in jobs)


def test_split_is_no_longer_stub():
    """拆笼空桩已落地：拆笼必须新增两只子笼，且原笼可用下降。

    原 test_split_stub_no_new_cage 断言“不新增子笼”，现已反转为失败语义：
    只登记作业不切分笼面即为事故，必须见红。
    """
    c = TestClient(app)
    h = _auth(c)
    with connect() as conn:
        before = conn.execute("SELECT COUNT(*) c FROM cages").fetchone()["c"]
    cage = [
        x
        for x in c.get("/api/cages", headers=h).json()["items"]
        if x["available"] > 0 and x["status"] == "已冻结" and x["split_parent_id"] is None
    ][0]
    half = round(cage["available"] / 2, 2)
    other = round(cage["available"] - half, 2)
    r = c.post(
        "/api/splits",
        headers=h,
        json={
            "cage_id": cage["id"],
            "left_amount": half,
            "right_amount": other,
            "mode": "整笔切分",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["left_cage_id"] and body["right_cage_id"]
    assert body["parent_available_after"] == 0
    with connect() as conn:
        after = conn.execute("SELECT COUNT(*) c FROM cages").fetchone()["c"]
    assert after == before + 2
