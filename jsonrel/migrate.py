"""类型漂移时的已入库数据迁移（加载期自动升级的善后）。

两种升级：
  column → json_column      把列中既有的原生值改写为 JSON 文本
  child_table → json_column 把子表既有的行重新拼成数组，写回父表 JSON 列，
                            然后归档并删除旧子表
"""
import json

from .reconstruct import build_fragment, load_tables, unadapt


def _descendants(design, tname):
    out = []
    for n, t in design.tables.items():
        if n != tname and t["parent"] == tname:
            out.append(n)
            out.extend(_descendants(design, n))
    return out


def apply_pending_upgrades(store, design):
    for up in design.pending_upgrades:
        if up["kind"] == "column_to_json":
            _column_to_json(store, up)
        elif up["kind"] == "child_to_json":
            _child_to_json(store, design, up)
    design.pending_upgrades = []


def _column_to_json(store, up):
    """列升级为 JSON 列：原生值改写为 JSON 文本。

    注意 SQLite 列亲和性：原列若是 INTEGER/REAL，JSON 文本 "123" 会被转回数字，
    因此通过 RENAME + 重建把列类型换成 TEXT。
    """
    from .store import q
    table, col, old_ctype = up["table"], up["column"], up["old_ctype"]
    if not store.table_exists(table) or col not in store.columns_of(table):
        return
    tmp = f"__jr_old_{col}"
    store.conn.execute(f"ALTER TABLE {q(table)} RENAME COLUMN {q(col)} TO {q(tmp)}")
    store.conn.execute(f"ALTER TABLE {q(table)} ADD COLUMN {q(col)} TEXT")
    for r in store.fetch_all(table):
        v = r[tmp]
        if v is None:
            continue
        if old_ctype == "bool":
            v = bool(v)
        store.update_row(table, r["__id"], {col: json.dumps(v, ensure_ascii=False)})
    store.conn.execute(f"ALTER TABLE {q(table)} DROP COLUMN {q(tmp)}")
    store.conn.commit()


def _child_to_json(store, design, up):
    old_table, pt, col = up["old_table"], up["parent_table"], up["column"]
    if not store.table_exists(old_table):
        return
    data = load_tables(store, design, include_archived=True)
    if col not in (store.columns_of(pt) if store.table_exists(pt) else []):
        store.ensure_data_table  # noqa - 列由加载流程统一补齐；此处直接 ALTER
        from .store import q
        store.conn.execute(f"ALTER TABLE {q(pt)} ADD COLUMN {q(col)} TEXT")
    tdef = design.tables[old_table]
    for pid, rows in (data.get(old_table) or {}).items():
        if tdef["elem"] == "scalar":
            lst = [unadapt(r["value"], tdef["ctype"]) for r in rows]
        else:
            lst = [build_fragment(design, data, old_table, r) for r in rows]
        prow = store.get_row(pt, pid)
        if prow is None:
            continue
        pres = json.loads(prow["__present"] or "[]")
        if col not in pres:
            pres.append(col)
        store.update_row(pt, pid, {
            col: json.dumps(lst, ensure_ascii=False),
            "__present": json.dumps(pres, ensure_ascii=False),
        })
    for t in [old_table] + _descendants(design, old_table):
        design.tables[t]["archived"] = True
        store.drop_table(t)
    store.conn.commit()
