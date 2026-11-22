"""审计只读面。"""
from __future__ import annotations

from fastapi import APIRouter

from auth import AuthHeader, require_user
from db import connect

router = APIRouter(tags=["audit"])


@router.get("/api/audit")
def list_audit(authorization: AuthHeader = None, limit: int = 100):
    require_user(authorization)
    limit = max(1, min(int(limit), 500))
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_trail ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.get("/api/audit/actions")
def list_actions(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT action, COUNT(*) c FROM audit_trail
               GROUP BY action ORDER BY c DESC"""
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.get("/api/audit/splits")
def list_split_audit(authorization: AuthHeader = None, limit: int = 200):
    """拆笼审计同源面：父笼切分、子笼落地、拆笼偏航三类动作。"""
    require_user(authorization)
    limit = max(1, min(int(limit), 500))
    with connect() as conn:
        rows = conn.execute(
            """SELECT * FROM audit_trail
               WHERE action IN ('cage_split','cage_split_child','split_deviation')
               ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        deviations = conn.execute(
            "SELECT COUNT(*) c FROM audit_trail WHERE action='split_deviation'"
        ).fetchone()["c"]
        splits = conn.execute(
            "SELECT COUNT(*) c FROM split_links"
        ).fetchone()["c"]
        return {
            "items": [dict(r) for r in rows],
            "summary": {"split_links": splits, "deviations": int(deviations)},
        }
