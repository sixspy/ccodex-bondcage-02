"""五页账实同源：拆笼页 / 入笼页 / 对账页 / 账户页 / 审计页 读同一份数据。

只改角标不算过关——本测直接比对五条接口的金额与关系，不从页面文案取证。
"""
from __future__ import annotations

from conftest import auth


def test_five_surfaces_share_one_truth(incident_db):
    from fastapi.testclient import TestClient

    from app import app

    c = TestClient(app)
    h = auth(c)
    cage_id = incident_db["id"]
    party = incident_db["party_key"]
    parent_code = incident_db["cage_code"]

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
    left_id, right_id = body["left_cage_id"], body["right_cage_id"]

    # 1) 拆笼页：拆分作业 + 拆分关系
    jobs = c.get("/api/split/jobs", headers=h).json()["items"]
    job = [j for j in jobs if j["cage_id"] == cage_id][0]
    assert job["left_cage_id"] == left_id and job["right_cage_id"] == right_id
    links = c.get(f"/api/split/links?parent_cage_id={cage_id}", headers=h).json()["items"]
    assert sorted((l["child_cage_id"], l["amount"]) for l in links) == sorted(
        [(left_id, 50000.0), (right_id, 70000.0)]
    )

    # 2) 入笼页：笼详情父子关系与金额
    detail = c.get(f"/api/cages/{cage_id}", headers=h).json()
    assert detail["cage"]["available"] == 0 and detail["cage"]["status"] == "已拆分"
    assert {ch["id"] for ch in detail["children"]} == {left_id, right_id}
    left_detail = c.get(f"/api/cages/{left_id}", headers=h).json()
    assert left_detail["parent"]["id"] == cage_id
    assert left_detail["cage"]["amount"] == 50000

    # 3) 对账页：原笼已拆出 + 子笼平衡
    recon = c.get(f"/api/reconcile/{parent_code}", headers=h).json()
    assert recon["split_out"] == 120000
    assert recon["available"] == 0
    assert recon["balanced"] is True
    left_recon = c.get(f"/api/reconcile/{left_detail['cage']['cage_code']}", headers=h).json()
    assert left_recon["balanced"] is True and left_recon["amount"] == 50000

    # 4) 账户页：活笼可用合计 == 账户冻结，且列出子份额关系
    bond = c.get(f"/api/bonds/{party}", headers=h).json()
    assert any(x["id"] == left_id and x["split_parent_id"] == cage_id for x in bond["cages"])
    live = sum(
        x["available"]
        for x in bond["cages"]
        if x["status"] in ("已冻结", "核验通过", "核验违约", "部分解笼", "部分罚没")
    )
    assert abs(live - bond["account"]["frozen"]) < 1e-6

    # 5) 审计页：父/子/作业同源
    sa = c.get("/api/audit/splits", headers=h).json()
    assert sa["summary"]["split_links"] == 2
    actions = [x["action"] for x in sa["items"]]
    assert "cage_split" in actions
    assert actions.count("cage_split_child") == 2
