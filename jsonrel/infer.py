"""结构推断：扫描一批样本，统计每个路径的出现比例与类型分布。

- 字段有时缺失：按文档级统计出现次数 → 出现率
- 类型有时不同：记录每种 JSON 类型出现次数；数组另记元素类型分布
- 对形如 id/uid/no/code 的标量字段跟踪去重值，用于推荐自然主键
"""
import re
from collections import Counter

from .common import jtype

IDISH = re.compile(r"(^|[_\W])(id|uid|uuid|no|key|code|number)$", re.I)
DISTINCT_CAP = 20000


def new_stat():
    return {
        "count": 0,                 # 文档级出现次数（一个文档内只计一次）
        "types": Counter(),         # 该路径值的类型分布
        "elem_types": Counter(),    # 数组元素类型分布
        "max_len": 0,               # 数组最大长度
        "distinct": set(),          # id 样字段的去重值（超限后放弃）
        "distinct_full": True,
    }


class Inferencer:
    def __init__(self):
        self.total = 0
        self.stats = {}  # path tuple -> stat

    def add(self, doc):
        self.total += 1
        self._walk(doc, (), set())

    def _walk(self, node, path, seen):
        st = self.stats.get(path)
        if st is None:
            st = self.stats[path] = new_stat()
        if path not in seen:
            seen.add(path)
            st["count"] += 1
            t = jtype(node)
            if path and t in ("str", "int") and IDISH.search(str(path[-1])):
                if st["distinct_full"]:
                    st["distinct"].add(node)
                    if len(st["distinct"]) > DISTINCT_CAP:
                        st["distinct"] = set()
                        st["distinct_full"] = False
        st["types"][jtype(node)] += 1
        if isinstance(node, dict):
            for k, v in node.items():
                self._walk(v, path + (str(k),), seen)
        elif isinstance(node, list):
            if len(node) > st["max_len"]:
                st["max_len"] = len(node)
            for el in node:
                st["elem_types"][jtype(el)] += 1
                if isinstance(el, dict):
                    for k, v in el.items():
                        self._walk(v, path + (str(k),), seen)
                elif isinstance(el, list):
                    # 数组套数组：按同一路径累计元素类型（设计时会建议 JSON 列）
                    self._walk(el, path, seen)
