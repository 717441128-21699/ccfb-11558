"""生成 10 种结构差异大的"合作方接口"样本，各 5000 条，写入 samples/*.jsonl。

刻意包含：
- 字段随机缺失、null、空数组、空对象
- 类型漂移：同一字段一会儿对象一会儿字符串（有的前 4000 条才稳定，之后漂移，
  用于考验加载期自动升级）；标量数组元素混杂
- 后期才出现的新字段（i>=4000），考验自动加列、旧数据补空
- 无自然主键的数据源（resumes），考验稳定哈希键
"""
import json
import os
import random

N = 5000
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples")

CITIES = ["北京", "上海", "深圳", "杭州", "成都", "西安", "武汉", "南京"]
WORDS = ["alpha", "beta", "gamma", "delta", "omega", "kappa", "lambda", "sigma"]


def w(rng, n=3):
    return " ".join(rng.choice(WORDS) for _ in range(n))


def maybe(rng, p):
    return rng.random() < p


# 1. 电商订单：对象嵌套 + 对象数组 + 漂移字段 coupon（str/object）
def gen_shop_orders(i, rng):
    doc = {
        "order_id": f"SO-{i:06d}",
        "placed_at": f"2026-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}T{rng.randint(0,23):02d}:00:00",
        "customer": {
            "cid": f"C{rng.randint(1000,9999)}",
            "name": w(rng, 2),
            "vip": maybe(rng, 0.3),
            "address": {"city": rng.choice(CITIES), "zip": f"{rng.randint(100000,999999)}"},
        },
        "items": [
            {"sku": f"SKU{rng.randint(100,999)}", "qty": rng.randint(1, 5),
             "price": round(rng.uniform(1, 500), 2),
             **({"gift": True} if maybe(rng, 0.1) else {})}
            for _ in range(rng.randint(1, 4))
        ],
        "coupon": (f"CPN{rng.randint(1000,9999)}" if maybe(rng, 0.7)
                   else {"code": f"CPN{rng.randint(1000,9999)}",
                         "percent": rng.randint(5, 30)}),
        "total": round(rng.uniform(10, 2000), 2),
        "status": rng.choice(["paid", "shipped", "closed", "refund"]),
    }
    if maybe(rng, 0.5):
        doc["note"] = w(rng, 4)
    if maybe(rng, 0.05):
        doc["items"] = []
    if i >= 4000:
        doc["loyalty_points"] = rng.randint(0, 500)  # 后期新字段
    return doc


# 2. 物联网传感器：读数数组 + int/float 混排 + 标量数组
def gen_iot_sensors(i, rng):
    doc = {
        "device_id": f"DEV-{rng.randint(100, 200)}",
        "ts": 1730000000 + i * 7,
        "site": {"building": rng.choice(["A", "B", "C"])},
        "readings": [
            {"metric": rng.choice(["temp", "hum", "pm25", "co2"]),
             "value": round(rng.uniform(-10, 100), 2),
             "unit": rng.choice(["C", "%", "ug/m3", "ppm"])}
            for _ in range(rng.randint(1, 5))
        ],
        "battery": rng.randint(0, 100) if maybe(rng, 0.5) else round(rng.uniform(0, 100), 1),
        "alerts": ([] if maybe(rng, 0.2)
                   else [rng.choice(["low_battery", "offline", "spike"])
                         for _ in range(rng.randint(1, 2))]),
    }
    if maybe(rng, 0.7):
        doc["site"]["floor"] = rng.randint(1, 30)
    if maybe(rng, 0.7):
        doc["firmware"] = f"{rng.randint(1,5)}.{rng.randint(0,9)}"
    if i >= 4000:
        doc["firmware_channel"] = rng.choice(["stable", "beta"])
    return doc


# 3. 社交帖子：两层嵌套数组（评论→回复）+ 标量数组
def gen_social_posts(i, rng):
    doc = {
        "post_id": f"P{i:07d}",
        "author": {"uid": f"u{rng.randint(1, 5000)}", "handle": w(rng, 1),
                   "verified": maybe(rng, 0.2)},
        "text": w(rng, 8),
        "tags": [rng.choice(WORDS) for _ in range(rng.randint(0, 4))],
        "comments": [
            {"user": f"u{rng.randint(1, 5000)}", "text": w(rng, 5),
             "likes": rng.randint(0, 100),
             "replies": [{"user": f"u{rng.randint(1, 5000)}", "text": w(rng, 3)}
                         for _ in range(rng.randint(0, 2))]}
            for _ in range(rng.randint(0, 4))
        ],
    }
    if maybe(rng, 0.8):
        doc["lang"] = rng.choice(["zh", "en", "ja"])
    if i >= 4000:
        doc["edited"] = maybe(rng, 0.5)
    return doc


# 4. 金融交易：amount 前 4000 条为数字、之后变成字符串（加载期漂移升级）
def gen_bank_txns(i, rng):
    amount = round(rng.uniform(1, 99999), 2)
    doc = {
        "txn_id": f"TX{i:08d}",
        "ts": f"2026-10-{rng.randint(1,31):02d} {rng.randint(0,23):02d}:00:00",
        "amount": amount if i < 4000 else f"{amount:.2f}",
        "currency": rng.choice(["CNY", "USD", "EUR"]),
        "splits": [
            {"account": f"ACC{rng.randint(100, 999)}",
             "share": round(rng.uniform(0, 1), 4),
             **({"memo": w(rng, 2)} if maybe(rng, 0.3) else {})}
            for _ in range(rng.randint(1, 3))
        ],
        "counterparty": (None if maybe(rng, 0.2) else
                         {"name": w(rng, 2), "bank": rng.choice(["ICBC", "BOC", "CMB"])}),
    }
    if maybe(rng, 0.6):
        doc["channel"] = rng.choice(["app", "web", "counter"])
    if i >= 4000:
        doc["risk_score"] = round(rng.uniform(0, 1), 3)
    return doc


# 5. 医疗病历：稀疏嵌套对象 + 标量数组 + 高缺失率
def gen_clinic_visits(i, rng):
    patient = {"pid": f"PT{rng.randint(10000, 99999)}", "name": w(rng, 2)}
    if maybe(rng, 0.7):
        patient["age"] = rng.randint(1, 95)
    if maybe(rng, 0.4):
        patient["allergies"] = [rng.choice(["青霉素", "花粉", "海鲜"])
                                for _ in range(rng.randint(1, 2))]
    visits = []
    for _ in range(rng.randint(1, 3)):
        vitals = {}
        if maybe(rng, 0.8):
            vitals["temp"] = round(rng.uniform(35.5, 40.0), 1)
        if maybe(rng, 0.6):
            vitals["hr"] = rng.randint(50, 130)
        if maybe(rng, 0.4):
            vitals["bp"] = f"{rng.randint(90,160)}/{rng.randint(60,100)}"
        visits.append({
            "date": f"2026-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}",
            "dept": rng.choice(["内科", "外科", "儿科", "眼科"]),
            "diags": [rng.choice(["感冒", "高血压", "糖尿病", "骨折"])
                      for _ in range(rng.randint(1, 2))],
            "vitals": vitals,
        })
    doc = {"patient": patient, "visits": visits}
    if maybe(rng, 0.6):
        doc["insurance"] = {"provider": rng.choice(["医保", "商保"]),
                            "rate": round(rng.uniform(0.5, 0.95), 2)}
    if i >= 4000:
        doc["follow_up"] = maybe(rng, 0.5)
    return doc


# 6. 物流运单：service_level 前 4000 条为 int 之后为 str；aliases 元素混杂
def gen_logistics(i, rng):
    doc = {
        "tracking_no": f"TRK{i:09d}",
        "origin": {"city": rng.choice(CITIES)},
        "dest": {"city": rng.choice(CITIES),
                 "geo": {"lat": round(rng.uniform(18, 53), 5),
                         "lng": round(rng.uniform(73, 135), 5)}},
        "events": [
            {"ts": 1730000000 + i * 3 + j * 3600,
             "loc": rng.choice(CITIES),
             "status": rng.choice(["picked", "transit", "delivered", "exception"])}
            for j in range(rng.randint(1, 5))
        ],
        "packages": [
            {"weight": round(rng.uniform(0.1, 50), 2),
             **({"dims": {"l": rng.randint(5, 100), "w": rng.randint(5, 100),
                          "h": rng.randint(5, 100)}} if maybe(rng, 0.7) else {})}
            for _ in range(rng.randint(1, 2))
        ],
        "service_level": rng.randint(1, 3) if i < 4000 else rng.choice(["std", "exp", "eco"]),
        "aliases": [rng.choice([f"A{rng.randint(1,99)}", rng.randint(100, 999)])
                    for _ in range(rng.randint(0, 3))],
    }
    if maybe(rng, 0.1):
        doc["events"].append(None)  # 数组里的 null 元素
    if i >= 4000:
        doc["co2_kg"] = round(rng.uniform(0, 20), 2)
    return doc


# 7. 新闻文章：author 一会儿字符串一会儿对象；段落为标量数组
def gen_news_articles(i, rng):
    doc = {
        "article_id": f"NA{i:06d}",
        "title": w(rng, 6),
        "author": (w(rng, 2) if maybe(rng, 0.6) else
                   {"name": w(rng, 2), "email": f"a{rng.randint(1,999)}@news.cn"}),
        "sections": [
            {"heading": w(rng, 3),
             "paras": [w(rng, 10) for _ in range(rng.randint(1, 3))]}
            for _ in range(rng.randint(1, 3))
        ],
        "related": [{"id": f"NA{rng.randint(0, N):06d}",
                     "score": round(rng.uniform(0, 1), 3)}
                    for _ in range(rng.randint(0, 2))],
        "tags": [rng.choice(WORDS) for _ in range(rng.randint(1, 4))],
    }
    if maybe(rng, 0.7):
        doc["paywalled"] = maybe(rng, 0.3)
    if i >= 4000:
        doc["reading_time"] = rng.randint(1, 30)
    return doc


# 8. 简历：无自然主键（考验稳定哈希键）+ 高缺失 + 多个对象数组
def gen_resumes(i, rng):
    cand = {"name": w(rng, 2)}
    if maybe(rng, 0.8):
        cand["email"] = f"u{rng.randint(1,99999)}@mail.cn"
    if maybe(rng, 0.5):
        cand["phones"] = [f"13{rng.randint(100000000, 999999999)}"]
    doc = {
        "candidate": cand,
        "experience": [
            {"company": w(rng, 2), "title": rng.choice(["工程师", "经理", "总监"]),
             "from": f"{rng.randint(2010, 2024)}",
             **({"to": f"{rng.randint(2015, 2026)}"} if maybe(rng, 0.7) else {}),
             "skills": [rng.choice(WORDS) for _ in range(rng.randint(1, 3))]}
            for _ in range(rng.randint(0, 4))
        ],
        "education": [
            {"school": w(rng, 2),
             **({"degree": rng.choice(["本科", "硕士", "博士"])} if maybe(rng, 0.8) else {})}
            for _ in range(rng.randint(0, 2))
        ],
    }
    if i >= 4000:
        doc["portfolio_url"] = f"https://folio.cn/{rng.randint(1,99999)}"
    return doc


# 9. 航班动态：aircraft 前 4000 条为对象、之后退化为字符串（flatten→json 升级）
def gen_flights(i, rng):
    doc = {
        "flight_no": f"CA{rng.randint(1000, 9999)}",
        "date": f"2026-11-{rng.randint(1, 30):02d}",
        "aircraft": ({"model": rng.choice(["A320", "B737", "C919"]),
                      "seats": rng.randint(120, 220)} if i < 4000
                     else rng.choice(["A320", "B737", "C919"])),
        "segments": [
            {"from": rng.choice(CITIES), "to": rng.choice(CITIES),
             "dep": f"{rng.randint(0,23):02d}:00", "arr": f"{rng.randint(0,23):02d}:30",
             **({"gate": f"G{rng.randint(1, 60)}"} if maybe(rng, 0.6) else {})}
            for _ in range(rng.randint(1, 3))
        ],
    }
    if maybe(rng, 0.4):
        doc["delays"] = [{"minutes": rng.randint(5, 180),
                          **({"reason": w(rng, 2)} if maybe(rng, 0.5) else {})}
                         for _ in range(rng.randint(1, 2))]
    if i >= 4000:
        doc["codeshare"] = [f"MU{rng.randint(1000,9999)}"]
    return doc


# 10. 音乐歌单：三层嵌套（歌单→曲目→专辑→艺人）+ 可选对象数组
def gen_playlists(i, rng):
    doc = {
        "playlist_id": f"PL{i:06d}",
        "title": w(rng, 3),
        "owner": {"id": f"u{rng.randint(1, 9999)}", "name": w(rng, 2)},
        "tracks": [
            {"title": w(rng, 2), "duration": rng.randint(90, 400),
             "album": {"name": w(rng, 2),
                       **({"year": rng.randint(1980, 2026)} if maybe(rng, 0.8) else {}),
                       "artists": [{"name": w(rng, 2),
                                    **({"role": rng.choice(["主唱", "feat"])}
                                       if maybe(rng, 0.3) else {})}
                                   for _ in range(rng.randint(1, 2))]}}
            for _ in range(rng.randint(1, 5))
        ],
    }
    if maybe(rng, 0.5):
        doc["collaborators"] = [{"id": f"u{rng.randint(1,9999)}",
                                 "perm": rng.choice(["r", "rw"])}
                                for _ in range(rng.randint(1, 2))]
    if i >= 4000:
        doc["mood"] = rng.choice(["安静", "运动", "通勤"])
    return doc


GENERATORS = {
    "shop_orders": gen_shop_orders,
    "iot_sensors": gen_iot_sensors,
    "social_posts": gen_social_posts,
    "bank_txns": gen_bank_txns,
    "clinic_visits": gen_clinic_visits,
    "logistics": gen_logistics,
    "news_articles": gen_news_articles,
    "resumes": gen_resumes,
    "flights": gen_flights,
    "playlists": gen_playlists,
}


def main():
    os.makedirs(OUT, exist_ok=True)
    for idx, (name, gen) in enumerate(GENERATORS.items()):
        rng = random.Random(20261116 + idx)
        path = os.path.join(OUT, f"{name}.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for i in range(N):
                f.write(json.dumps(gen(i, rng), ensure_ascii=False) + "\n")
        print(f"{name}: {N} 条 -> {path}")


if __name__ == "__main__":
    main()
