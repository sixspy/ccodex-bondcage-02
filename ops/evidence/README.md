# 拆笼落地 · 验收证据（2026-11-22）

## 顺序

1. 纪要先落：`ops/拆笼分拨纪要_2026-11-22.md`（甲路：整笔切分同事务落库；当日锁死）。
2. 事故夹具先造：`fixtures/split_incident.json` + `tests/conftest.py::incident_db`
   （种子冻结笼 CG-INC-SPLIT-001，可用 120000，账户冻结 120000）。
3. 失败测见红：`ops/evidence/01_split_red.log`
   - `test_split_must_cut_incident_cage` —— 拆后仍单笼假绿
   - `test_two_people_split_same_cage_contention` —— 双人同拆未争锁
   - 另有回滚、偏航两条同批见红（4 failed）。
4. 改拆笼服务 / 入笼服务 / 对账服务 / 审计服务 + 拆笼页 / 入笼页 / 对账页 / 账户页 / 审计页。
5. 全绿：`ops/evidence/02_green_full.log`（13 passed）。
6. 本机文件库跑通夹具：`ops/evidence/03_file_db_fixture.log`（库路径仍为 `data/app.db`）。

## 关键断言

- 原笼 `available` 120000 → 0，`status` → 已拆分，`<<amount>>` 仍 120000。
- 两只子笼 `CG-INC-SPLIT-001-L` / `-R` 可查、可独立核验与解笼。
- 对账：原笼 `available + released + forfeited + split_out == amount`；子笼各自平衡。
- 账户互证：账户冻结 == 活笼可用合计（父子合计）。
- 审计：`cage_split` ×1、`cage_split_child` ×2；`split_deviation` 仅偏航时出现。
- 争用：后到者只留 `contention_log`，业务零变化；真并发（双线程）恰好一人成功。
- 回滚：注入第二只子笼审计失败 → 原笼可用、两只子笼、split_links、split_jobs、审计全部回滚。

## 库文件

- 路径不变：`data/app.db`；旧库通过 `db.migrate()` 就地补列 `split_parent_id / split_side / split_out`
  与 `split_links`、`split_jobs.left_cage_id / right_cage_id`。
- 未引入 `split_drafts`（乙路词汇，已从 schema 删除）。
