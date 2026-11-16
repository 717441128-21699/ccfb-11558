"""拍平：按映射设计把一条嵌套 JSON 拆成多张表的行。

行结构：
  __id        稳定主键（根行为文档键，子行为 父键+路径+下标 的哈希）
  __parent_id 父表主键（子表）
  __idx       数组下标（子表）
  __present   本行在原文中出现的列与结构标记（区分"缺失"与 null，
              并记录空对象/空数组的存在，保证可无损还原）
"""
import json
from collections import defaultdict

from .common import NULL_EL, child_key, marker


def adapt(v, ctype):
    if v is None:
        return None
    if ctype == "bool":
        return 1 if v else 0
    return v


def flatten_doc(doc, design, doc_id):
    out = defaultdict(list)

    def emit(obj, table, base_path, row, present):
        tbase = design.table_base(table)
        for k, v in obj.items():
            path = base_path + (k,)
            info = design.ensure_path(path, v)
            act = info["action"]
            rel = list(path[len(tbase):])
            if act == "column":
                row[info["column"]] = adapt(v, info["ctype"])
                present.append(info["column"])
            elif act == "json_column":
                row[info["column"]] = None if v is None else json.dumps(v, ensure_ascii=False)
                present.append(info["column"])
            elif act == "flatten":
                if v is None:
                    present.append(marker("null", rel))
                else:
                    present.append(marker("obj", rel))
                    emit(v, table, path, row, present)
            elif act == "child_table":
                if v is None:
                    present.append(marker("null", rel))
                else:
                    present.append(marker("arr", rel))
                    tname = info["table"]
                    tdef = design.tables[tname]
                    for i, el in enumerate(v):
                        crow = {"__id": child_key(row["__id"], path, i),
                                "__parent_id": row["__id"], "__idx": i}
                        if el is None:
                            crow["__present"] = json.dumps([NULL_EL])
                        elif isinstance(el, dict):
                            cpresent = []
                            emit(el, tname, path, crow, cpresent)
                            crow["__present"] = json.dumps(cpresent, ensure_ascii=False)
                        else:
                            crow["value"] = adapt(el, tdef["ctype"])
                            crow["__present"] = "[]"
                        out[tname].append(crow)

    root = {"__id": doc_id}
    present = []
    emit(doc, design.root_table, (), root, present)
    root["__present"] = json.dumps(present, ensure_ascii=False)
    out[design.root_table].append(root)
    return out
