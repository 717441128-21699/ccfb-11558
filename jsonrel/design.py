"""表设计与映射规则。

动作（action）：
  column      标量 → 父表的一列
  flatten     对象 → 展开成父表的列（即"两层合成一层"，默认）
  child_table 数组 → 子表（带父表主键 __parent_id 和数组下标 __idx）
  json_column 任意值 → 以 JSON 文本存入一列（用于类型不稳定或用户指定）

类型漂移处理：加载时实际值与既有规则不兼容 → 自动升级为 json_column，
并记录 pending_upgrades 由 migrate 模块迁移已入库数据。
"""
import json

from .common import SCALAR_TYPES, jtype, sanitize

CTYPE_SQL = {"int": "INTEGER", "float": "REAL", "bool": "INTEGER",
             "text": "TEXT", "json": "TEXT"}

ACTION_LABELS = {
    "column": "拍平为列",
    "flatten": "展开为列(合并层级)",
    "child_table": "拆为子表",
    "json_column": "存为JSON列",
}


def ctype_for_types(types):
    t = set(types) - {"null"}
    if not t:
        return "text"
    if t <= {"bool"}:
        return "bool"
    if t <= {"int"}:
        return "int"
    if t <= {"int", "float"}:
        return "float"
    if t == {"str"}:
        return "text"
    return None  # 混杂标量


def value_fits(v, ctype):
    t = jtype(v)
    if t == "null":
        return True
    if ctype == "bool":
        return t == "bool"
    if ctype == "int":
        return t == "int"
    if ctype == "float":
        return t in ("int", "float")
    if ctype == "text":
        return t == "str"
    return True


def suggest_for(st, total):
    """根据统计给出处理建议。返回 (action, ctype, elem, 说明)。"""
    ratio = st["count"] / total if total else 0
    types = {t for t, c in st["types"].items() if c}
    nonnull = types - {"null"}
    head = "出现率 {:.0%}，类型 {}".format(ratio, "/".join(sorted(types)) or "无")
    if not nonnull:
        return "column", "text", None, head + "；全为 null，按文本列"
    if nonnull <= SCALAR_TYPES:
        ct = ctype_for_types(nonnull)
        if ct:
            return "column", ct, None, head
        return "json_column", "json", None, head + "；标量类型混杂（如字符串/数字交替），建议存 JSON 列"
    if nonnull == {"object"}:
        return "flatten", None, None, head + "；对象展开为列"
    if nonnull == {"array"}:
        ets = {t for t, c in st["elem_types"].items() if c} - {"null"}
        if ets == {"object"}:
            return "child_table", None, "object", head + "；对象数组拆为子表"
        if ets and ets <= SCALAR_TYPES and ctype_for_types(ets):
            return "child_table", ctype_for_types(ets), "scalar", head + "；标量数组拆为子表(value 列)"
        if not ets:
            return "child_table", "text", "scalar", head + "；仅见空数组，暂按标量子表"
        return "json_column", "json", None, head + "；数组元素类型混杂，建议存 JSON 列"
    return "json_column", "json", None, head + "；同一字段对象/数组/字符串不定，建议存 JSON 列"


def natural_key_candidates(stats, total):
    cands = []
    for p, st in stats.items():
        if not p or len(p) > 2:
            continue
        if st["count"] != total or not total:
            continue
        if set(st["types"]) - {"str", "int"}:
            continue
        dc = st.get("distinct_count")
        if dc is None:  # 内存态统计：用去重集合
            dc = len(st["distinct"]) if st["distinct_full"] else -1
        if dc >= 0 and dc == st["count"]:
            cands.append(".".join(p))
    return sorted(cands)


class Design:
    """一个数据源的完整映射设计。"""

    def __init__(self, root_table="root"):
        self.root_table = root_table
        self.natural_key = []          # 点分路径列表
        self.paths = {}                # tuple -> {action, table, column, ctype, note}
        self.tables = {root_table: {"path": (), "parent": None, "elem": None,
                                    "ctype": None, "archived": False}}
        self.pending_upgrades = []     # 加载期类型漂移产生的升级（待迁移）

    # ---------- 命名 ----------

    def table_base(self, table):
        return tuple(self.tables[table]["path"])

    def parent_table(self, path):
        for i in range(len(path) - 1, -1, -1):
            anc = path[:i]
            if not anc:
                return self.root_table
            info = self.paths.get(anc)
            if info and info["action"] == "child_table":
                return info["table"]
        return self.root_table

    def _cols_of_table(self, table):
        cols = {"__id", "__parent_id", "__idx", "__present", "value"}
        for info in self.paths.values():
            if info.get("table") == table and info.get("column"):
                cols.add(info["column"])
        return cols

    def _unique_col(self, table, path):
        existing = self._cols_of_table(table)
        base = sanitize(path[-1])
        if base not in existing:
            return base
        for i in range(len(path) - 2, -1, -1):
            cand = sanitize(path[i]) + "_" + base
            if cand not in existing:
                return cand
        n = 2
        while f"{base}_{n}" in existing:
            n += 1
        return f"{base}_{n}"

    def _unique_table(self, path):
        base = sanitize(self.root_table + "__" + "_".join(str(k) for k in path))
        name, n = base, 2
        while name in self.tables:
            name = f"{base}_{n}"
            n += 1
        return name

    # ---------- 规则增改 ----------

    def add_path(self, path, action, ctype=None, elem=None, note=""):
        info = {"action": action, "table": None, "column": None,
                "ctype": ctype, "note": note}
        if action == "child_table":
            tname = self._unique_table(path)
            info["table"] = tname
            self.tables[tname] = {"path": tuple(path), "parent": self.parent_table(path),
                                  "elem": elem or "object", "ctype": ctype,
                                  "archived": False}
        elif action in ("column", "json_column"):
            info["table"] = self.parent_table(path)
            info["column"] = self._unique_col(info["table"], path)
            if action == "json_column":
                info["ctype"] = "json"
        else:  # flatten
            info["table"] = self.parent_table(path)
        self.paths[tuple(path)] = info
        return info

    def set_action(self, path, action, ctype=None, elem=None, note="用户指定"):
        """用户改规则：移除旧规则（含子树），按新动作重建。"""
        path = tuple(path)
        old = self.paths.get(path)
        if old and old["action"] == "child_table":
            self._drop_table_tree(old["table"])
        if action == "json_column":
            for p in [p for p in self.paths if p[: len(path)] == path and p != path]:
                self._remove_path_entry(p)
        self.paths.pop(path, None)
        return self.add_path(path, action, ctype=ctype, elem=elem, note=note)

    def _remove_path_entry(self, path):
        info = self.paths.pop(path, None)
        if info and info["action"] == "child_table":
            self._drop_table_tree(info["table"])

    def _drop_table_tree(self, tname):
        for n in [n for n, t in self.tables.items() if t["parent"] == tname]:
            self._drop_table_tree(n)
        self.tables.pop(tname, None)

    # ---------- 加载期自动推断/升级 ----------

    def ensure_path(self, path, value):
        path = tuple(path)
        info = self.paths.get(path)
        if info is None:
            return self._auto_add(path, value)
        return self._compat(path, info, value)

    def _auto_add(self, path, value):
        t = jtype(value)
        if t == "object":
            return self.add_path(path, "flatten", note="加载时自动新增")
        if t == "array":
            ets = {jtype(e) for e in value} - {"null"}
            if ets == {"object"}:
                return self.add_path(path, "child_table", note="加载时自动新增")
            if ets and ets <= SCALAR_TYPES and ctype_for_types(ets):
                return self.add_path(path, "child_table", ctype=ctype_for_types(ets),
                                     note="加载时自动新增")
            if not ets:
                return self.add_path(path, "child_table", ctype="text",
                                     note="加载时自动新增(空数组)")
            return self.add_path(path, "json_column", note="加载时自动新增(元素混杂)")
        return self.add_path(path, "column", ctype=ctype_for_types({t}),
                             note="加载时自动新增")

    def _compat(self, path, info, value):
        act = info["action"]
        t = jtype(value)
        if act == "json_column":
            return info
        if act == "column":
            if t in ("object", "array") or not value_fits(value, info["ctype"]):
                return self._upgrade_to_json(path, info, "column")
            return info
        if act == "flatten":
            if t in ("object", "null"):
                return info
            return self._upgrade_to_json(path, info, "flatten")
        if act == "child_table":
            if t == "null":
                return info
            if t != "array":
                return self._upgrade_to_json(path, info, "child_table")
            tbl = self.tables[info["table"]]
            ets = {jtype(e) for e in value} - {"null"}
            if tbl["elem"] == "object":
                if ets <= {"object"}:
                    return info
                return self._upgrade_to_json(path, info, "child_table")
            if ets <= SCALAR_TYPES and all(value_fits(e, tbl["ctype"]) for e in value):
                return info
            return self._upgrade_to_json(path, info, "child_table")
        return info

    def _upgrade_to_json(self, path, info, old_kind):
        pt = self.parent_table(path)
        if old_kind == "column":
            col = info["column"]  # 复用原列，值重写为 JSON 文本
        else:
            col = self._unique_col(pt, path)
        old_table = info.get("table") if old_kind == "child_table" else None
        old_ctype = info.get("ctype")
        info.update({"action": "json_column", "table": pt, "column": col,
                     "ctype": "json",
                     "note": (info.get("note") or "") + " → 类型漂移，自动升级为 JSON 列"})
        if old_table:
            self.tables[old_table]["archived"] = True
            self.pending_upgrades.append({"kind": "child_to_json", "path": list(path),
                                          "old_table": old_table, "parent_table": pt,
                                          "column": col})
        else:
            self.pending_upgrades.append({"kind": "column_to_json", "path": list(path),
                                          "table": pt, "column": col,
                                          "old_ctype": old_ctype})
        return info

    # ---------- 迭代 ----------

    def iter_columns(self, table):
        for p, info in self.paths.items():
            if info["action"] in ("column", "json_column") and info["table"] == table:
                yield p, info

    def iter_children(self, table, include_archived=False):
        for name, t in self.tables.items():
            if name == self.root_table or t["parent"] != table:
                continue
            if t.get("archived") and not include_archived:
                continue
            yield tuple(t["path"]), name, t

    def active_tables(self):
        return [n for n, t in self.tables.items() if not t.get("archived")]

    # ---------- 序列化 ----------

    def to_dict(self):
        return {
            "root_table": self.root_table,
            "natural_key": self.natural_key,
            "paths": [[list(p), info] for p, info in self.paths.items()],
            "tables": {n: {**t, "path": list(t["path"])} for n, t in self.tables.items()},
        }

    @classmethod
    def from_dict(cls, d):
        obj = cls(d["root_table"])
        obj.natural_key = list(d.get("natural_key") or [])
        obj.paths = {tuple(p): dict(info) for p, info in d.get("paths", [])}
        obj.tables = {n: {**t, "path": tuple(t["path"])} for n, t in d["tables"].items()}
        return obj


def build_from_inference(stats, total, root_table, overrides=None, natural_key=None):
    """由推断统计生成设计。overrides: {"[路径json]": action} 用户规则覆盖。"""
    overrides = overrides or {}
    d = Design(root_table)
    json_ancestors = []  # 已设为 json_column 的路径，其后代不再单独建规则

    def under_json_ancestor(path):
        return any(path[: len(a)] == a for a in json_ancestors)

    for path in sorted(stats, key=lambda p: (len(p), p)):
        if not path:
            continue
        if under_json_ancestor(path):
            continue
        st = stats[path]
        s_act, s_ctype, s_elem, note = suggest_for(st, total)
        key = json.dumps(list(path), ensure_ascii=False)
        act = overrides.get(key, s_act)
        ctype, elem = s_ctype, s_elem
        if act == "child_table" and s_act != "child_table":
            elem, ctype = "object", None
        if act == "column" and s_act != "column":
            ctype = "text"
        d.add_path(path, act, ctype=ctype, elem=elem,
                   note=note if act == s_act else note + "（用户改为%s）" % ACTION_LABELS[act])
        if act == "json_column":
            json_ancestors.append(path)
    if natural_key is None:
        natural_key = natural_key_candidates(stats, total)[:1]
    d.natural_key = list(natural_key)
    return d
