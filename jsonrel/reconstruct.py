"""反向还原：从关系表拼回原始 JSON（语义一致）。

依赖每行的 __present：区分"字段缺失"与 null，并恢复空对象 {}、空数组 []。
"""
import json
from collections import defaultdict

from .common import NULL_EL, ensure_container, marker, set_path


def unadapt(v, ctype):
    if v is None:
        return None
    if ctype == "bool":
        return bool(v)
    return v


def load_tables(store, design, include_archived=False):
    """把设计涉及的表读入内存：根表按 __id，子表按 __parent_id 分组并按下标排序。"""
    data = {}
    for name, tdef in design.tables.items():
        if tdef.get("archived") and not include_archived:
            continue
        if not store.table_exists(name):
            continue
        rows = store.fetch_all(name)
        if name == design.root_table:
            data[name] = {r["__id"]: r for r in rows}
        else:
            idx = defaultdict(list)
            for r in rows:
                idx[r["__parent_id"]].append(r)
            for lst in idx.values():
                lst.sort(key=lambda r: r["__idx"])
            data[name] = dict(idx)
    return data


def build_fragment(design, data, table, row):
    base = design.table_base(table)
    frag = {}
    pset = set(json.loads(row["__present"] or "[]"))
    # 1) 结构标记：空对象/空数组/null 占位
    for m in pset:
        if m.startswith("#obj:"):
            ensure_container(frag, json.loads(m[5:]), {})
        elif m.startswith("#arr:"):
            ensure_container(frag, json.loads(m[5:]), [])
        elif m.startswith("#null:"):
            set_path(frag, json.loads(m[6:]), None)
    # 2) 标量列 / JSON 列
    for p, info in design.iter_columns(table):
        c = info["column"]
        if c in pset:
            v = row[c]
            if info["action"] == "json_column":
                v = None if v is None else json.loads(v)
            elif info["ctype"] == "bool":
                v = unadapt(v, "bool")
            set_path(frag, list(p[len(base):]), v)
    # 3) 子表 → 数组
    for cpath, tname, tdef in design.iter_children(table):
        rel = list(cpath[len(base):])
        kids = data.get(tname, {}).get(row["__id"], [])
        if tdef["elem"] == "scalar":
            lst = [unadapt(k["value"], tdef["ctype"]) for k in kids]
        else:
            lst = []
            for k in kids:
                kpres = set(json.loads(k["__present"] or "[]"))
                lst.append(None if NULL_EL in kpres
                           else build_fragment(design, data, tname, k))
        if lst or marker("arr", rel) in pset:
            set_path(frag, rel, lst)
    return frag


def reconstruct_all(store, design):
    """返回 {文档键: 还原后的 JSON}。"""
    data = load_tables(store, design)
    roots = data.get(design.root_table, {})
    return {rid: build_fragment(design, data, design.root_table, row)
            for rid, row in roots.items()}


def reconstruct_one(store, design, doc_id):
    data = load_tables(store, design)
    row = data.get(design.root_table, {}).get(doc_id)
    if row is None:
        return None
    return build_fragment(design, data, design.root_table, row)
