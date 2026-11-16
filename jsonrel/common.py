"""公共工具：JSON 类型判定、路径操作、稳定键、存在性标记。"""
import hashlib
import json
import re

SCALAR_TYPES = {"null", "bool", "int", "float", "str"}
MISSING = object()
NULL_EL = "#nullel"  # 对象数组中的 null 元素标记


def jtype(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    raise TypeError(f"不支持的 JSON 值类型: {type(v)!r}")


def canonical(doc):
    """文档的规范化序列化（键排序），用于稳定哈希。"""
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def get_path(obj, keys, default=None):
    cur = obj
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def set_path(obj, keys, value):
    cur = obj
    for k in keys[:-1]:
        nxt = cur.get(k)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[k] = nxt
        cur = nxt
    if keys:
        cur[keys[-1]] = value


def ensure_container(obj, keys, default):
    """确保路径存在；不存在则填入 default（{} 或 []）。"""
    cur = obj
    for k in keys[:-1]:
        nxt = cur.get(k)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[k] = nxt
        cur = nxt
    if keys and keys[-1] not in cur:
        cur[keys[-1]] = default


def marker(kind, rel):
    """行内存在性标记：#obj:/#arr:/#null: + 相对路径(JSON)。"""
    return "#" + kind + ":" + json.dumps(list(rel), ensure_ascii=False)


def doc_key(doc, natural_key):
    """稳定主键：有自然键用自然键，否则用整文档哈希。同一数据重复加载键不变。"""
    if natural_key:
        vals, ok = [], True
        for p in natural_key:
            v = get_path(doc, p.split("."), MISSING)
            if v is MISSING or isinstance(v, (dict, list)):
                ok = False
                break
            vals.append(v)
        if ok:
            return "nk-" + hashlib.sha1(canonical(vals).encode("utf-8")).hexdigest()
    return "h-" + hashlib.sha1(canonical(doc).encode("utf-8")).hexdigest()


def child_key(parent_id, path, idx):
    return "c-" + hashlib.sha1(f"{parent_id}|{list(path)}|{idx}".encode("utf-8")).hexdigest()


_ident_re = re.compile(r"\W")


def sanitize(name):
    """转成合法 SQL 标识符。"""
    s = _ident_re.sub("_", str(name)).strip("_") or "x"
    if s[0].isdigit():
        s = "c_" + s
    if s.startswith("__"):
        s = "c" + s
    return s


def path_to_str(path):
    return json.dumps(list(path), ensure_ascii=False)


def str_to_path(s):
    return tuple(json.loads(s))
