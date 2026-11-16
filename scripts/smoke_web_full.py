import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["JSONREL_DB"] = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "verify.db")

from webapp import app

c = app.test_client()
r = c.get("/")
print("GET /        ->", r.status_code)
r = c.get("/verify")
ok = "50000/50000" in r.data.decode()
print("GET /verify  ->", r.status_code, "50000/50000" if ok else "FAIL")
r = c.get("/design?source=flights")
print("GET /design  ->", r.status_code)
r = c.get("/doc?source=logistics")
print("GET /doc     ->", r.status_code, "语义一致" if "语义一致" in r.data.decode() else "FAIL")
r = c.get("/loads")
print("GET /loads   ->", r.status_code)
