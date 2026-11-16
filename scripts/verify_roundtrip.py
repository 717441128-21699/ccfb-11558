"""端到端验收：
1. 生成 10 种结构 × 5000 条样本（若尚未生成）
2. 每个数据源只用前 500 条做结构推断（后期新字段考验自动加列）
3. 全量加载进 SQLite
4. 反向还原，与原始文档逐条比对 —— 目标 50000/50000 一致
5. 重复加载一遍，验证键稳定、行数不变（幂等）
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jsonrel.project import Project
from scripts.gen_samples import GENERATORS, N, main as gen_main

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(BASE, "samples")
DB = os.path.join(BASE, "verify.db")
INFER_LIMIT = 500  # 只用前 500 条推断，剩余字段靠加载期自动演进


def ensure_samples():
    missing = [s for s in GENERATORS if not os.path.exists(os.path.join(SAMPLES, f"{s}.jsonl"))]
    if missing:
        gen_main()


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def main():
    ensure_samples()
    if os.path.exists(DB):
        os.remove(DB)
    proj = Project(DB)

    t0 = time.time()
    print("== 导入样本 ==")
    for src in GENERATORS:
        proj.add_samples(src, read_jsonl(os.path.join(SAMPLES, f"{src}.jsonl")))
    print(f"  10 个数据源 × {N} 条，{time.time()-t0:.1f}s")

    t0 = time.time()
    print(f"\n== 结构推断（每个源仅扫前 {INFER_LIMIT} 条） ==")
    for src in GENERATORS:
        r = proj.infer(src, limit=INFER_LIMIT)
        d = proj.build_design(src)
        tables = len(d.tables)
        print(f"  {src:15s} 扫描 {r['docs_scanned']:4d} 条，字段 {r['fields']:3d} 个，"
              f"生成 {tables} 张表，自然键 {d.natural_key or '(自动哈希)'}")

    t0 = time.time()
    print("\n== 全量加载 ==")
    results = proj.load()
    total_new_cols = 0
    total_upgrades = 0
    for src, r in results.items():
        nc = len(r["new_columns"])
        nu = len(r["upgrades"])
        total_new_cols += nc
        total_upgrades += nu
        print(f"  {src:15s} {r['docs']} 条，新增列 {nc} 个，类型漂移升级 {nu} 次")
    print(f"  合计自动加列 {total_new_cols}，漂移升级 {total_upgrades}，{time.time()-t0:.1f}s")

    print("\n== 加载记录（最近 3 条） ==")
    for rec in proj.store.list_loads()[:3]:
        print(f"  #{rec['id']} {rec['ts']} {rec['source']}: {rec['docs']} 条")

    t0 = time.time()
    print("\n== 反向还原 + 逐条比对 ==")
    all_total = all_ok = 0
    failed = []
    for src in GENERATORS:
        v = proj.verify(src)
        all_total += v["total"]
        all_ok += v["ok"]
        mark = "OK " if v["mismatch"] == 0 else "FAIL"
        print(f"  [{mark}] {src:15s} {v['ok']}/{v['total']}")
        if v["mismatch"]:
            failed.append((src, v["bad"]))
    print(f"  合计 {all_ok}/{all_total}，{time.time()-t0:.1f}s")

    print("\n== 幂等性：重复加载一遍，行数应不变 ==")
    before = {src: proj.table_counts(src) for src in GENERATORS}
    proj.load()
    stable = True
    for src in GENERATORS:
        after = proj.table_counts(src)
        if after != before[src]:
            stable = False
            print(f"  [FAIL] {src}: {before[src]} -> {after}")
    print("  键稳定，重复加载无重复行" if stable else "  存在变化！")

    print("\n== 重复加载后再次全量比对 ==")
    all_ok2 = 0
    for src in GENERATORS:
        v = proj.verify(src)
        all_ok2 += v["ok"]
    print(f"  合计 {all_ok2}/{all_total}")

    if failed:
        print("\n失败明细（前 20 条/源）:")
        for src, bad in failed:
            print(f"  {src}: {bad}")
    ok = all_ok == all_total and all_ok2 == all_total and stable
    print("\n" + ("[PASS] 全部通过：50000 条加载后反向还原与原文逐条一致"
                  if ok else "[FAIL] 存在不一致，见上"))
    proj.close()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
