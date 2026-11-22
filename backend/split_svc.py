"""拆笼分拨：整笔切分同事务落库（纪要 2026-11-22 甲路，已锁死）。

一只笼的可用额按左右金额切成两笔可独立处置的子份额：
  同一事务内  扣减原笼可用 → 新建左/右子笼 → 写拆分关系/作业 → 写审计。
  任一步失败  原笼可用、原笼状态、两只子笼、拆分关系、拆分作业、审计一并回滚。

禁止影子子笼草稿/晋升（乙路），任何非“整笔切分”模式一律记拆笼偏航并拒写。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit
from locks import record_contention, release, require_lock
from models import SplitIn

router = APIRouter(tags=["split"])

LOCKED_MODE = "整笔切分"
MINUTE_REF = "拆笼分拨纪要_2026-11-22"
SPLITTABLE = ("已冻结",)


@router.get("/api/split/jobs")
def list_jobs(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT j.*, c.cage_code,
                      cl.cage_code AS left_cage_code,
                      cr.cage_code AS right_cage_code
               FROM split_jobs j
               JOIN cages c ON c.id=j.cage_id
               LEFT JOIN cages cl ON cl.id=j.left_cage_id
               LEFT JOIN cages cr ON cr.id=j.right_cage_id
               ORDER BY j.id DESC"""
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.get("/api/split/links")
def list_links(authorization: AuthHeader = None, parent_cage_id: int | None = None):
    """拆分关系只读面：供拆笼页、入笼页、审计页同源查询。"""
    require_user(authorization)
    with connect() as conn:
        sql = """SELECT l.*, p.cage_code AS parent_cage_code, c.cage_code AS child_cage_code,
                        c.available AS child_available, c.status AS child_status
                 FROM split_links l
                 JOIN cages p ON p.id=l.parent_cage_id
                 JOIN cages c ON c.id=l.child_cage_id"""
        params: tuple = ()
        if parent_cage_id is not None:
            sql += " WHERE l.parent_cage_id=?"
            params = (parent_cage_id,)
        sql += " ORDER BY l.id"
        return {"items": [dict(r) for r in conn.execute(sql, params).fetchall()]}


def _reject_deviation(conn, user: str, cage_code: str, mode: str, cage_id: int) -> None:
    """偏离纪要不写任何业务，只落偏航审计。"""
    write_audit(
        conn,
        user,
        "split_deviation",
        cage_code,
        {
            "cage_id": cage_id,
            "requested_mode": mode,
            "locked_mode": LOCKED_MODE,
            "minute": MINUTE_REF,
            "verdict": "拒写：偏离锁定方案",
        },
    )
    conn.commit()
    raise HTTPException(
        400,
        f"拆笼偏航：纪要锁定 {LOCKED_MODE}，拒绝 {mode} 写入",
    )


def _child_code(conn, parent_code: str, side: str) -> str:
    base = f"{parent_code}-{side}"
    code = base
    n = 1
    while conn.execute("SELECT id FROM cages WHERE cage_code=?", (code,)).fetchone():
        n += 1
        code = f"{base}{n}"
    return code


@router.post("/api/splits")
def split_cage(body: SplitIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    lock_key = f"cage:{body.cage_id}"

    with connect() as conn:
        cage = conn.execute("SELECT * FROM cages WHERE id=?", (body.cage_id,)).fetchone()
        if not cage:
            raise HTTPException(404, "笼不存在")
        if body.mode != LOCKED_MODE:
            _reject_deviation(conn, user, cage["cage_code"], body.mode, body.cage_id)

        try:
            require_lock(conn, lock_key, user)
        except HTTPException:
            # 后到者的争用痕必须落库，然后才拒绝
            conn.commit()
            raise

        try:
            cage = conn.execute(
                "SELECT * FROM cages WHERE id=?", (body.cage_id,)
            ).fetchone()
            if not cage:
                raise HTTPException(404, "笼不存在")
            if cage["status"] not in SPLITTABLE:
                # 已拆分/已处置的笼：后到者只留争用痕，不写业务
                record_contention(
                    conn,
                    lock_key,
                    user,
                    f"笼状态={cage['status']}，拆笼拒绝，只留争用",
                )
                conn.commit()
                raise HTTPException(
                    409,
                    f"笼状态 {cage['status']} 不可拆笼（仅 {'/'.join(SPLITTABLE)} 可拆）",
                )
            if cage["split_parent_id"] is not None:
                record_contention(
                    conn, lock_key, user, "子份额不可再次拆笼，只留争用"
                )
                conn.commit()
                raise HTTPException(409, "子份额不可再次拆笼")
            left = round(float(body.left_amount), 6)
            right = round(float(body.right_amount), 6)
            avail = round(float(cage["available"]), 6)
            if left <= 0 or right <= 0:
                raise HTTPException(400, "左右金额均须大于 0")
            if abs(left + right - avail) > 1e-6:
                raise HTTPException(409, "左右金额之和须等于笼当前可用额")

            now = now_iso()
            parent_code = cage["cage_code"]
            party = cage["party_key"]

            # 1) 原笼扣减（比较并交换，防并发重复切分）
            cur = conn.execute(
                """UPDATE cages
                   SET available=0, status='已拆分', split_out=split_out + ?
                   WHERE id=? AND status='已冻结' AND available=?""",
                (avail, body.cage_id, avail),
            )
            if cur.rowcount != 1:
                raise HTTPException(409, "笼可用额或状态已变化，拆笼中止")

            # 2) 两只子笼，同一事务
            left_id = _insert_child(
                conn, parent_code, party, cage, left, "L", now
            )
            right_id = _insert_child(
                conn, parent_code, party, cage, right, "R", now
            )

            # 3) 拆分关系与作业
            job = conn.execute(
                """INSERT INTO split_jobs(cage_id,left_amount,right_amount,mode,status,
                                          left_cage_id,right_cage_id,detail,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    body.cage_id,
                    left,
                    right,
                    LOCKED_MODE,
                    "已切分",
                    left_id,
                    right_id,
                    f"{MINUTE_REF} 甲路整笔切分",
                    now,
                ),
            )
            job_id = job.lastrowid
            for child_id, side, amount in (
                (left_id, "L", left),
                (right_id, "R", right),
            ):
                conn.execute(
                    """INSERT INTO split_links(parent_cage_id,child_cage_id,side,
                                               amount,job_id,created_at)
                       VALUES (?,?,?,?,?,?)""",
                    (body.cage_id, child_id, side, amount, job_id, now),
                )

            # 4) 审计：父笼一条 + 子笼各一条
            write_audit(
                conn,
                user,
                "cage_split",
                parent_code,
                {
                    "job_id": job_id,
                    "cage_id": body.cage_id,
                    "left_amount": left,
                    "right_amount": right,
                    "left_cage_id": left_id,
                    "right_cage_id": right_id,
                    "mode": LOCKED_MODE,
                    "minute": MINUTE_REF,
                },
            )
            for child_id, child_code, side, amount in (
                (left_id, _code_of(conn, left_id), "L", left),
                (right_id, _code_of(conn, right_id), "R", right),
            ):
                write_audit(
                    conn,
                    user,
                    "cage_split_child",
                    child_code,
                    {
                        "parent_cage_id": body.cage_id,
                        "parent_code": parent_code,
                        "child_cage_id": child_id,
                        "side": side,
                        "amount": amount,
                        "job_id": job_id,
                    },
                )

            conn.commit()
            left_row = conn.execute("SELECT * FROM cages WHERE id=?", (left_id,)).fetchone()
            right_row = conn.execute("SELECT * FROM cages WHERE id=?", (right_id,)).fetchone()
            return {
                "job_id": job_id,
                "status": "已切分",
                "mode": LOCKED_MODE,
                "parent_cage_id": body.cage_id,
                "parent_available_before": avail,
                "parent_available_after": 0.0,
                "left_cage_id": left_id,
                "right_cage_id": right_id,
                "left_cage": dict(left_row),
                "right_cage": dict(right_row),
            }
        except HTTPException:
            conn.rollback()
            raise
        except Exception:
            conn.rollback()
            raise
        finally:
            release(conn, lock_key, user)
            conn.commit()


def _insert_child(conn, parent_code: str, party: str, parent, amount: float, side: str, now: str) -> int:
    code = _child_code(conn, parent_code, side)
    cur = conn.execute(
        """INSERT INTO cages(cage_code,party_key,contract_no,amount,available,
           generation,status,beneficiary,split_parent_id,split_side,created_at,frozen_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            code,
            party,
            parent["contract_no"],
            amount,
            amount,
            1,
            "已冻结",
            parent["beneficiary"],
            int(parent["id"]),
            side,
            now,
            now,
        ),
    )
    return int(cur.lastrowid)


def _code_of(conn, cage_id: int) -> str:
    row = conn.execute("SELECT cage_code FROM cages WHERE id=?", (cage_id,)).fetchone()
    return row["cage_code"]
