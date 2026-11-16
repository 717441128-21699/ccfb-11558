import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["JSONREL_DB"] = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "webtest.db")
if os.path.exists(os.environ["JSONREL_DB"]):
    os.remove(os.environ["JSONREL_DB"])

from webapp import app

c = app.test_client()
print("GET  /           ->", c.get("/").status_code)

docs = [
    {"order_id": "A1", "customer": {"name": "张三", "vip": True},
     "items": [{"sku": "S1", "qty": 2}], "coupon": "X9"},
    {"order_id": "A2", "customer": {"name": "李四"}, "items": [],
     "coupon": {"code": "Y1", "percent": 10}},
    {"order_id": "A3", "customer": {"name": "王五", "vip": False},
     "items": [{"sku": "S2", "qty": 1, "gift": True}], "total": 99.5},
]
r = c.post("/samples", data={"source": "demo",
                             "text": "\n".join(json.dumps(d, ensure_ascii=False) for d in docs)})
print("POST /samples    ->", r.status_code)
print("POST /infer      ->", c.post("/infer", data={"source": "demo"}).status_code)
print("GET  /infer      ->", c.get("/infer?source=demo").status_code)
print("GET  /design     ->", c.get("/design?source=demo").status_code)
print("GET  /mapping    ->", c.get("/mapping?source=demo").status_code)
print("POST /load       ->", c.post("/load", data={"source": "demo"}).status_code)
print("GET  /loads      ->", c.get("/loads").status_code)
r = c.get("/verify")
print("GET  /verify     ->", r.status_code, "3/3 一致" if "3/3" in r.data.decode() else "FAIL")
r = c.get("/doc?source=demo")
print("GET  /doc        ->", r.status_code, "语义一致" if "语义一致" in r.data.decode() else "FAIL")

# 改映射规则：items 子表改回 JSON 列，保存后自动重建重载
r = c.post("/mapping", data={"source": "demo",
                             'act:["items"]': "json_column",
                             'act:["coupon"]': "json_column",
                             'act:["customer"]': "flatten",
                             "nk": "order_id"})
print("POST /mapping    ->", r.status_code)
r = c.get("/verify")
print("GET  /verify2    ->", r.status_code, "改规则后仍 3/3 一致" if "3/3" in r.data.decode() else "FAIL")
