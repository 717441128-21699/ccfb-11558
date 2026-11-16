"""高层 API：每个数据源（合作方）一套独立的推断/设计/加载/还原。"""
import json
from collections import Counter, defaultdict

from .common import doc_key, sanitize
from .design import Design, build_from_inference
from .flatten import flatten_doc
from .infer import Inferencer
from .migrate import apply_pending_upgrades
from .reconstruct import reconstruct_all, reconstruct_one
from .store import Store


class Project:
    def __init__(self, db_path):
        self.store = Store(db_path)
        self._designs = {}

    def close(self):
        self.store.close()

    # ---------- 样本 ----------

    def add_samples(self, source, docs):
        self.store.add_samples(source, docs)

    def sources(self):
        return [s for s, _ in self.store.sample_counts()]

    # ---------- 结构推断 ----------

    def infer(self, source, limit=None):
        """扫描样本推出整体结构（字段缺失比例、类型分布）。"""
        inf = Inferencer()
        for i, (_src, text) in enumerate(self.store.iter_samples(source)):
            if limit and i >= limit:
                break
            inf.add(json.loads(text))
        self.store.save_field_stats(source, inf.stats, inf.total)
        return {"docs_scanned": inf.total, "fields": len(inf.stats)}

    def field_stats(self, source):
        return self.store.load_field_stats(source)

    def infer_total(self, source):
        return self.store.infer_total(source)

    # ---------- 表设计 / 映射规则 ----------

    def root_table_for(self, source):
        return "t_" + sanitize(source)

    def get_design(self, source):
        if source not in self._designs:
            d = self.store.meta_get(f"design:{source}")
            self._designs[source] = Design.from_dict(d) if d else None
        return self._designs[source]

    def save_design(self, source, design):
        self._designs[source] = design
        self.store.meta_set(f"design:{source}", design.to_dict())

    def build_design(self, source, overrides=None, natural_key=None):
        stats = self.store.load_field_stats(source)
        total = self.store.infer_total(source)
        if natural_key == "auto":
            natural_key = None  # 让 build_from_inference 自动推荐
        d = build_from_inference(stats, total, self.root_table_for(source),
                                 overrides=overrides, natural_key=natural_key)
        self.save_design(source, d)
        return d

    def apply_rules(self, source, overrides, natural_key):
        """保存用户规则并重建：删数据表 → 重新生成设计 → 重新加载全部样本。"""
        self.store.meta_set(f"overrides:{source}", overrides)
        old = self.get_design(source)
        if old:
            for t in list(old.tables):
                self.store.drop_table(t)
        self.store.conn.commit()
        self.build_design(source, overrides=overrides, natural_key=natural_key)
        return self.load(source)

    def get_overrides(self, source):
        return self.store.meta_get(f"overrides:{source}", {})

    # ---------- 加载 ----------

    def load(self, source=None, batch_size=2000):
        sources = [source] if source else self.sources()
        result = {}
        for src in sources:
            if self.get_design(src) is None:
                # 尚未设计：先自动推断（前 1000 条）并生成默认设计
                self.infer(src, limit=1000)
                self.build_design(src)
            result[src] = self._load_one(src, batch_size)
        return result

    def _load_one(self, source, batch_size):
        design = self.get_design(source)
        rows_per_table = Counter()
        new_columns, upgrades = [], []
        n_docs = 0
        batch = []

        def flush(docs):
            # 类型漂移升级时重拍本批（升级会改动设计，已拍行作废）
            while True:
                design.pending_upgrades = []
                all_rows = defaultdict(list)
                for doc in docs:
                    key = doc_key(doc, design.natural_key)
                    for t, rows in flatten_doc(doc, design, key).items():
                        all_rows[t].extend(rows)
                if not design.pending_upgrades:
                    break
                upgrades.extend(design.pending_upgrades)
                apply_pending_upgrades(self.store, design)
            for t, rows in all_rows.items():
                added = self.store.ensure_data_table(design, t)
                new_columns.extend(f"{t}.{c}" for c in added)
                self.store.insert_rows(t, rows)
                rows_per_table[t] += len(rows)
            self.store.conn.commit()
            self.save_design(source, design)

        for _src, text in self.store.iter_samples(source):
            batch.append(json.loads(text))
            if len(batch) >= batch_size:
                flush(batch)
                n_docs += len(batch)
                batch = []
        if batch:
            flush(batch)
            n_docs += len(batch)
        detail = {"rows": dict(rows_per_table),
                  "new_columns": new_columns,
                  "upgrades": upgrades}
        self.store.record_load(source, n_docs, detail)
        return {"docs": n_docs, **detail}

    # ---------- 反向还原 / 校验 ----------

    def reconstruct_all(self, source):
        design = self.get_design(source)
        return reconstruct_all(self.store, design)

    def reconstruct_one(self, source, doc_id):
        design = self.get_design(source)
        return reconstruct_one(self.store, design, doc_id)

    def verify(self, source):
        """逐条比对：加载后的关系表反向还原 vs 原始文档。"""
        design = self.get_design(source)
        recon = reconstruct_all(self.store, design)
        total, ok, bad = 0, 0, []
        for _src, text in self.store.iter_samples(source):
            doc = json.loads(text)
            key = doc_key(doc, design.natural_key)
            r = recon.get(key)
            total += 1
            if r == doc:
                ok += 1
            else:
                bad.append({"key": key, "missing": r is None})
        return {"source": source, "total": total, "ok": ok,
                "mismatch": total - ok, "bad": bad[:20]}

    def table_counts(self, source):
        design = self.get_design(source)
        if not design:
            return {}
        return {t: self.store.count(t) for t in design.active_tables()
                if self.store.table_exists(t)}

    def find_sample_key(self, source, doc_id):
        """按文档键找回原始样本（用于对照展示）。"""
        design = self.get_design(source)
        for _src, text in self.store.iter_samples(source):
            doc = json.loads(text)
            if doc_key(doc, design.natural_key) == doc_id:
                return doc
        return None
