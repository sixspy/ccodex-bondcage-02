"""拆笼失败测（先红）：拆笼必须真正切分笼面，而不是只登记作业。

两份失败测：
1. test_split_must_cut_incident_cage —— 事故夹具拆后不得仍是单笼假绿。
2. test_two_people_split_same_cage_contention —— 双人同拆必须抢笼锁，后到只留争用痕。

在拆笼服务改造成“整笔切分同事务落库”之前，这两条必定见红。
"""
from __future__ import annotations

from conftest import auth


def test_split_must_cut_incident_cage(incident_db):
    from fastapi.testclient import TestClient

    from app import app
    from db import connect

    c = TestClient(app)
    h = auth(c)
    cage_id = incident_db["id"]
    before = c.get(f"/api/cages/{cage_id}", headers=h).json()["cage"]
    assert before["available"] == 120000
    assert before["status"] == "已冻结"

    with connect() as conn:
        before_count = conn.execute("SELECT COUNT(*) c FROM cages").fetchone()["c"]

    r = c.post(
        "/api/splits",
        headers=h,
        json={
            "cage_id": cage_id,
            "left_amount": 50000,
            "right_amount": 70000,
            "mode": "整笔切分",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    left_id = body["left_cage_id"]
    right_id = body["right_cage_id"]
    assert left_id and right_id and left_id != right_id

    # 原笼可用下降，不再是原封不动
    parent = c.get(f"/api/cages/{cage_id}", headers=h).json()["cage"]
    assert parent["available"] == 0
    assert parent["status"] == "已拆分"
    assert parent["amount"] == 120000

    # 两只子份额可查、各自独立
    left = c.get(f"/api/cages/{left_id}", headers=h).json()["cage"]
    right = c.get(f"/api/cages/{right_id}", headers=h).json()["cage"]
    assert left["amount"] == 50000 and left["available"] == 50000
    assert right["amount"] == 70000 and right["available"] == 70000
    assert left["status"] == "已冻结" and right["status"] == "已冻结"
    assert left["split_parent_id"] == cage_id
    assert right["split_parent_id"] == cage_id

    with connect() as conn:
        after_count = conn.execute("SELECT COUNT(*) c FROM cages").fetchone()["c"]
    assert after_count == before_count + 2

    # 子份额可以独立处置：左子笼核验通过并全额解笼
    assert (
        c.post(
            "/api/attestations",
            headers=h,
            json={"cage_id": left_id, "outcome": "通过", "evidence": "左份验收"},
        ).status_code
        == 200
    )
    rel = c.post("/api/releases", headers=h, json={"cage_id": left_id}).json()
    assert rel["status"] == "已拨付" and rel["amount"] == 50000

    # 对账：原笼按“可用 + 拨付 + 罚没 + 已拆出 = 笼总额”平衡；子笼各自平衡
    parent_recon = c.get(f"/api/reconcile/{incident_db['cage_code']}", headers=h).json()
    assert parent_recon["balanced"] is True
    assert parent_recon["split_out"] == 120000
    left_recon = c.get(f"/api/reconcile/{left['cage_code']}", headers=h).json()
    right_recon = c.get(f"/api/reconcile/{right['cage_code']}", headers=h).json()
    assert left_recon["balanced"] is True and right_recon["balanced"] is True

    # 账户页互证：账户冻结 == 活笼可用合计（父 0 + 左 0 + 右 70000 = 70000）
    proof = c.get("/api/proof/P-INC", headers=h).json()
    assert proof["frozen_matches_live"] is True
    assert proof["live_cage_available"] == 70000

    # 审计页可见切分与子笼审计
    actions = [a["action"] for a in c.get("/api/audit?limit=200", headers=h).json()["items"]]
    assert "cage_split" in actions
    assert actions.count("cage_split_child") >= 2

    # 拆分作业状态不再是空桩登记
    jobs = c.get("/api/split/jobs", headers=h).json()["items"]
    job = [j for j in jobs if j["cage_id"] == cage_id][0]
    assert job["status"] == "已切分"
    assert job["left_cage_id"] == left_id and job["right_cage_id"] == right_id


def test_two_people_split_same_cage_contention(incident_db):
    from fastapi.testclient import TestClient

    from app import app
    from db import connect

    c = TestClient(app)
    admin = auth(c, "admin")
    clerk = auth(c, "clerk")
    cage_id = incident_db["id"]
    lock_key = f"cage:{cage_id}"

    # 甲先抢到笼锁
    assert (
        c.post("/api/locks/acquire", headers=admin, json={"lock_key": lock_key}).status_code
        == 200
    )
    # 乙后到同拆：只留争用痕，不写业务
    r = c.post(
        "/api/splits",
        headers=clerk,
        json={
            "cage_id": cage_id,
            "left_amount": 50000,
            "right_amount": 70000,
            "mode": "整笔切分",
        },
    )
    assert r.status_code == 409, r.text
    cont = c.get("/api/contention", headers=admin).json()["items"]
    assert any(x["lock_key"] == lock_key and x["actor"] == "clerk" for x in cont)
    with connect() as conn:
        assert conn.execute("SELECT available FROM cages WHERE id=?", (cage_id,)).fetchone()[
            "available"
        ] == 120000
        assert conn.execute("SELECT COUNT(*) c FROM split_jobs").fetchone()["c"] == 0

    # 甲放锁后拆成功
    c.post("/api/locks/release", headers=admin, json={"lock_key": lock_key})
    ok = c.post(
        "/api/splits",
        headers=admin,
        json={
            "cage_id": cage_id,
            "left_amount": 50000,
            "right_amount": 70000,
            "mode": "整笔切分",
        },
    )
    assert ok.status_code == 200, ok.text

    # 乙再来拆同一只笼：拒绝且不再新增子笼
    again = c.post(
        "/api/splits",
        headers=clerk,
        json={
            "cage_id": cage_id,
            "left_amount": 60000,
            "right_amount": 60000,
            "mode": "整笔切分",
        },
    )
    assert again.status_code == 409
    with connect() as conn:
        children = conn.execute(
            "SELECT COUNT(*) c FROM cages WHERE split_parent_id=?", (cage_id,)
        ).fetchone()["c"]
        total = conn.execute(
            "SELECT COALESCE(SUM(amount),0) s FROM cages WHERE split_parent_id=?", (cage_id,)
        ).fetchone()["s"]
    assert children == 2
    assert total == 120000


def test_split_midway_failure_rolls_back_everything(incident_db, monkeypatch):
    """甲路要求：任一步失败，原笼与新建子笼一并回滚。"""
    import split_svc
    from fastapi.testclient import TestClient

    from app import app
    from db import connect

    real_write_audit = split_svc.write_audit
    state = {"child_audits": 0}

    def boom(conn, actor, action, target, payload):
        if action == "cage_split_child":
            state["child_audits"] += 1
            if state["child_audits"] >= 2:
                raise RuntimeError("注入失败：第二只子笼审计写入失败")
        return real_write_audit(conn, actor, action, target, payload)

    monkeypatch.setattr(split_svc, "write_audit", boom)
    c = TestClient(app, raise_server_exceptions=False)
    h = auth(c)
    cage_id = incident_db["id"]

    r = c.post(
        "/api/splits",
        headers=h,
        json={
            "cage_id": cage_id,
            "left_amount": 50000,
            "right_amount": 70000,
            "mode": "整笔切分",
        },
    )
    assert r.status_code == 500, r.text

    with connect() as conn:
        parent = conn.execute("SELECT * FROM cages WHERE id=?", (cage_id,)).fetchone()
        assert parent["available"] == 120000
        assert parent["status"] == "已冻结"
        assert (
            conn.execute(
                "SELECT COUNT(*) c FROM cages WHERE split_parent_id=?", (cage_id,)
            ).fetchone()["c"]
            == 0
        )
        assert conn.execute("SELECT COUNT(*) c FROM split_jobs").fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) c FROM split_links").fetchone()["c"] == 0
        assert (
            conn.execute(
                "SELECT COUNT(*) c FROM audit_trail WHERE action='cage_split'"
            ).fetchone()["c"]
            == 0
        )


def test_shadow_mode_is_deviation_and_rejected(incident_db):
    """偏离纪要（影子子笼）记拆笼偏航并拒写。"""
    from fastapi.testclient import TestClient

    from app import app
    from db import connect

    c = TestClient(app)
    h = auth(c)
    cage_id = incident_db["id"]

    r = c.post(
        "/api/splits",
        headers=h,
        json={
            "cage_id": cage_id,
            "left_amount": 50000,
            "right_amount": 70000,
            "mode": "影子子笼",
        },
    )
    assert r.status_code == 400, r.text
    assert "偏航" in r.json()["detail"]

    with connect() as conn:
        parent = conn.execute("SELECT * FROM cages WHERE id=?", (cage_id,)).fetchone()
        assert parent["available"] == 120000 and parent["status"] == "已冻结"
        assert conn.execute("SELECT COUNT(*) c FROM split_jobs").fetchone()["c"] == 0
        deviations = conn.execute(
            "SELECT * FROM audit_trail WHERE action='split_deviation'"
        ).fetchall()
        assert len(deviations) == 1


def test_parallel_same_cage_only_one_wins(incident_db):
    """真并发：两线程同时拆同一只笼，恰好一人成功、另一人留争用痕。"""
    import threading

    from fastapi.testclient import TestClient

    from app import app
    from db import connect

    cage_id = incident_db["id"]
    results = []

    def worker(user):
        cli = TestClient(app)
        h = auth(cli, user)
        r = cli.post(
            "/api/splits",
            headers=h,
            json={
                "cage_id": cage_id,
                "left_amount": 50000,
                "right_amount": 70000,
                "mode": "整笔切分",
            },
        )
        results.append((user, r.status_code))

    threads = [
        threading.Thread(target=worker, args=("admin",)),
        threading.Thread(target=worker, args=("clerk",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    codes = sorted(code for _, code in results)
    assert codes == [200, 409], results

    with connect() as conn:
        parent = conn.execute("SELECT * FROM cages WHERE id=?", (cage_id,)).fetchone()
        assert parent["available"] == 0 and parent["status"] == "已拆分"
        assert (
            conn.execute(
                "SELECT COUNT(*) c FROM cages WHERE split_parent_id=?", (cage_id,)
            ).fetchone()["c"]
            == 2
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) c FROM split_links WHERE parent_cage_id=?", (cage_id,)
            ).fetchone()["c"]
            == 2
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) c FROM contention_log WHERE lock_key=?",
                (f"cage:{cage_id}",),
            ).fetchone()["c"]
            >= 1
        )
