"""拆笼事故夹具与公共测试装配。

夹具来源：fixtures/split_incident.json
场景：一只种子冻结笼 CG-INC-SPLIT-001，可用 120000，账户冻结 120000。
旧空桩拆完后仍是单笼、原笼可用原封不动、对账显示整笼平衡——即“拆后单笼假绿”。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from db import connect, init_db, now_iso

ROOT = Path(__file__).resolve().parents[1]
INCIDENT_PATH = ROOT / "fixtures" / "split_incident.json"


def load_incident() -> dict:
    return json.loads(INCIDENT_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def incident_db(tmp_path, monkeypatch):
    """把事故夹具装进本机文件库（tmp_path 下的 SQLite 文件），返回原笼行。"""
    monkeypatch.setattr("db.DB_PATH", tmp_path / "incident.db")
    init_db()
    data = load_incident()
    bond = data["bond"]
    with connect() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO bond_accounts
               (party_key,party_name,balance,frozen,status,created_at)
               VALUES (?,?,?,?,?,?)""",
            (
                bond["party_key"],
                bond["party_name"],
                float(bond["balance"]),
                float(bond["frozen"]),
                bond["status"],
                bond["created_at"],
            ),
        )
        conn.execute(
            """INSERT INTO deposit_ledger(party_key,amount,memo,created_at,actor)
               VALUES (?,?,?,?,?)""",
            (bond["party_key"], float(bond["balance"]), "事故夹具入金", now_iso(), "seed"),
        )
        for row in data["cages"]:
            conn.execute(
                """INSERT OR IGNORE INTO cages
                   (cage_code,party_key,contract_no,amount,available,generation,
                    status,beneficiary,created_at,frozen_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    row["cage_code"],
                    row["party_key"],
                    row["contract_no"],
                    float(row["amount"]),
                    float(row["available"]),
                    int(row["generation"]),
                    row["status"],
                    row["beneficiary"],
                    row["created_at"],
                    row["frozen_at"],
                ),
            )
        conn.commit()
        cage = conn.execute(
            "SELECT * FROM cages WHERE cage_code=?", (data["cages"][0]["cage_code"],)
        ).fetchone()
    return dict(cage)


def auth(c, user: str = "admin") -> dict:
    tok = c.post(
        "/api/login", json={"username": user, "password": "123456"}
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}
