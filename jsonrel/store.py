"""SQLite 存储：元数据、样本库、加载记录、数据表与结构演进（自动加列）。"""
import json
import sqlite3

from .design import CTYPE_SQL

META_SQL = """
CREATE TABLE IF NOT EXISTS __jr_meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS __jr_samples(
  id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, doc TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_samples_source ON __jr_samples(source);
CREATE TABLE IF NOT EXISTS __jr_loads(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
  source TEXT, docs INTEGER, detail TEXT);
CREATE TABLE IF NOT EXISTS __jr_fields(
  source TEXT NOT NULL, path TEXT NOT NULL, count INTEGER, types TEXT,
  elem_types TEXT, max_len INTEGER, distinct_count INTEGER,
  PRIMARY KEY(source, path));
"""


def q(name):
    return '"' + str(name).replace('"', '""') + '"'


class Store:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(META_SQL)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ---------- 元数据 ----------

    def meta_get(self, key, default=None):
        r = self.conn.execute("SELECT value FROM __jr_meta WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    def meta_set(self, key, value):
        self.conn.execute("INSERT OR REPLACE INTO __jr_meta(key,value) VALUES(?,?)",
                          (key, json.dumps(value, ensure_ascii=False)))
        self.conn.commit()

    # ---------- 样本库 ----------

    def add_samples(self, source, docs):
        self.conn.executemany(
            "INSERT INTO __jr_samples(source, doc) VALUES(?,?)",
            [(source, json.dumps(d, ensure_ascii=False)) for d in docs])
        self.conn.commit()

    def iter_samples(self, source=None):
        sql = "SELECT source, doc FROM __jr_samples"
        args = ()
        if source:
            sql += " WHERE source=?"
            args = (source,)
        sql += " ORDER BY id"
        cur = self.conn.execute(sql, args)
        for row in cur:
            yield row["source"], row["doc"]

    def sample_counts(self):
        cur = self.conn.execute(
            "SELECT source, COUNT(*) c FROM __jr_samples GROUP BY source ORDER BY source")
        return [(r["source"], r["c"]) for r in cur]

    def delete_samples(self, source):
        self.conn.execute("DELETE FROM __jr_samples WHERE source=?", (source,))
        self.conn.commit()

    # ---------- 字段统计（推断结果） ----------

    def save_field_stats(self, source, stats, total):
        self.conn.execute("DELETE FROM __jr_fields WHERE source=?", (source,))
        self.conn.executemany(
            "INSERT INTO __jr_fields(source,path,count,types,elem_types,max_len,distinct_count)"
            " VALUES(?,?,?,?,?,?,?)",
            [(source, json.dumps(list(p), ensure_ascii=False), st["count"],
              json.dumps(dict(st["types"])), json.dumps(dict(st["elem_types"])),
              st["max_len"],
              len(st["distinct"]) if st["distinct_full"] else -1)
             for p, st in stats.items()])
        self.meta_set(f"infer_total:{source}", total)

    def load_field_stats(self, source):
        from collections import Counter
        stats = {}
        cur = self.conn.execute(
            "SELECT * FROM __jr_fields WHERE source=?", (source,))
        for r in cur:
            dc = r["distinct_count"]
            stats[tuple(json.loads(r["path"]))] = {
                "count": r["count"],
                "types": Counter(json.loads(r["types"])),
                "elem_types": Counter(json.loads(r["elem_types"])),
                "max_len": r["max_len"],
                "distinct": set(),          # 去重值不持久化，仅保留数量
                "distinct_full": dc >= 0,
                "distinct_count": dc,
            }
        return stats

    def infer_total(self, source):
        return self.meta_get(f"infer_total:{source}", 0)

    # ---------- 加载记录 ----------

    def record_load(self, source, docs, detail):
        from datetime import datetime
        self.conn.execute(
            "INSERT INTO __jr_loads(ts,source,docs,detail) VALUES(?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), source, docs,
             json.dumps(detail, ensure_ascii=False)))
        self.conn.commit()

    def list_loads(self):
        cur = self.conn.execute(
            "SELECT * FROM __jr_loads ORDER BY id DESC LIMIT 200")
        return [dict(r) for r in cur]

    # ---------- 数据表 ----------

    def table_exists(self, name):
        r = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
        return r is not None

    def columns_of(self, table):
        return [r["name"] for r in self.conn.execute(f"PRAGMA table_info({q(table)})")]

    def drop_table(self, name):
        self.conn.execute(f"DROP TABLE IF EXISTS {q(name)}")

    def ensure_data_table(self, design, tname):
        """建表（若缺）并补齐设计要求的列（自动加列，旧行自动为 NULL）。返回新增列名。"""
        tdef = design.tables[tname]
        added = []
        if not self.table_exists(tname):
            if tname == design.root_table:
                ddl = '"__id" TEXT PRIMARY KEY, "__present" TEXT'
            else:
                ddl = ('"__id" TEXT PRIMARY KEY, "__parent_id" TEXT, '
                       '"__idx" INTEGER, "__present" TEXT')
                if tdef["elem"] == "scalar":
                    ddl += f', "value" {CTYPE_SQL[tdef["ctype"] or "text"]}'
            self.conn.execute(f"CREATE TABLE {q(tname)} ({ddl})")
            if tname != design.root_table:
                self.conn.execute(
                    f"CREATE INDEX {q('ix_' + tname)} ON {q(tname)}(__parent_id, __idx)")
        existing = set(self.columns_of(tname))
        want = {}
        if tdef["elem"] == "scalar":
            want["value"] = CTYPE_SQL[tdef["ctype"] or "text"]
        for _p, info in design.iter_columns(tname):
            want[info["column"]] = CTYPE_SQL[info["ctype"] or "text"]
        for c, t in want.items():
            if c not in existing:
                self.conn.execute(f"ALTER TABLE {q(tname)} ADD COLUMN {q(c)} {t}")
                added.append(c)
        return added

    def insert_rows(self, table, rows):
        cols, seen = [], set()
        for r in rows:
            for k in r:
                if k not in seen:
                    seen.add(k)
                    cols.append(k)
        sql = (f"INSERT OR REPLACE INTO {q(table)} "
               f"({','.join(q(c) for c in cols)}) "
               f"VALUES ({','.join('?' for _ in cols)})")
        self.conn.executemany(sql, [[r.get(c) for c in cols] for r in rows])

    def fetch_all(self, table):
        return self.conn.execute(f"SELECT * FROM {q(table)}").fetchall()

    def get_row(self, table, rid):
        return self.conn.execute(
            f"SELECT * FROM {q(table)} WHERE __id=?", (rid,)).fetchone()

    def update_row(self, table, rid, sets):
        sql = f"UPDATE {q(table)} SET " + ",".join(f"{q(k)}=?" for k in sets) + " WHERE __id=?"
        self.conn.execute(sql, [*sets.values(), rid])

    def count(self, table):
        return self.conn.execute(f"SELECT COUNT(*) c FROM {q(table)}").fetchone()["c"]
