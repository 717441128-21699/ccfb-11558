"""jsonrel 网页台：样本上传 / 结构推断 / 表设计 / 映射规则 / 加载记录 / 还原校验。

运行:  py webapp.py  →  http://127.0.0.1:5000
"""
import json
import os

from flask import Flask, abort, redirect, render_template_string, request, url_for

from jsonrel.design import ACTION_LABELS, natural_key_candidates, suggest_for
from jsonrel.project import Project

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("JSONREL_DB", os.path.join(BASE, "project.db"))

app = Flask(__name__)
proj = Project(DB)

PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>{{ title }} · jsonrel</title>
<style>
body{font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;margin:0;background:#f5f6f8;color:#222}
header{background:#1f2937;color:#fff;padding:10px 24px;display:flex;gap:18px;align-items:baseline;flex-wrap:wrap}
header a{color:#cbd5e1;text-decoration:none}header a:hover{color:#fff}
header .brand{font-weight:700;color:#fff}
main{max-width:1180px;margin:20px auto;padding:0 16px}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:16px 20px;margin-bottom:16px}
table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid #eee;padding:6px 8px;text-align:left;vertical-align:top}
th{background:#f9fafb;font-weight:600}tr:hover td{background:#fafafa}
code,pre{background:#f3f4f6;border-radius:4px;padding:1px 5px;font-family:Consolas,monospace;font-size:13px}
pre{padding:10px;overflow:auto;max-height:480px}
.btn{display:inline-block;background:#2563eb;color:#fff;border:0;border-radius:6px;padding:6px 14px;cursor:pointer;text-decoration:none;font-size:14px}
.btn.gray{background:#6b7280}.btn.green{background:#059669}
.tag{display:inline-block;background:#eef2ff;color:#3730a3;border-radius:4px;padding:0 6px;font-size:12px;margin-right:4px}
.warn{color:#b45309}.ok{color:#047857}.bad{color:#b91c1c}
.muted{color:#6b7280;font-size:13px}
select,input[type=text]{padding:4px 6px;border:1px solid #d1d5db;border-radius:4px}
nav.src{margin:0 0 12px}nav.src a{margin-right:10px}
</style></head><body>
<header><a class="brand" href="/">jsonrel</a>
<a href="/samples">样本上传</a><a href="/infer">结构推断</a><a href="/design">表设计</a>
<a href="/mapping">映射规则</a><a href="/loads">加载记录</a><a href="/verify">还原校验</a></header>
<main>
{% if sources %}<nav class="src card">数据源：
{% for s in sources %}<a href="?source={{ s }}" class="{{ 'ok' if s==src else '' }}">{{ s }}</a>{% endfor %}
</nav>{% endif %}
{{ body|safe }}
</main></body></html>"""


def render(title, body, **ctx):
    sources = proj.sources()
    source = ctx.pop("source", None) or request.args.get("source") \
        or (sources[0] if sources else None)
    return render_template_string(PAGE, title=title, body=body, sources=sources,
                                  src=source, **ctx)


@app.route("/")
def home():
    rows = []
    for src, cnt in proj.store.sample_counts():
        d = proj.get_design(src)
        tc = proj.table_counts(src) if d else {}
        rows.append((src, cnt, len(d.active_tables()) if d else 0,
                     sum(tc.values()), d.natural_key if d else []))
    body = """
    <div class="card"><h2>概览</h2>
    <p class="muted">把合作方接口返回的嵌套 JSON 自动拍平成 SQLite 关联表，并可无损还原。</p>
    <table><tr><th>数据源</th><th>样本数</th><th>表数</th><th>已加载行数</th><th>主键</th><th></th></tr>
    {% for s,cnt,nt,loaded,nk in rows %}
    <tr><td><b>{{ s }}</b></td><td>{{ cnt }}</td><td>{{ nt }}</td><td>{{ loaded }}</td>
        <td>{% if nk %}<span class="tag">{{ nk|join(', ') }}</span>{% else %}<span class="muted">自动哈希键</span>{% endif %}</td>
        <td><a href="/design?source={{ s }}">表设计</a> · <a href="/verify?source={{ s }}">校验</a></td></tr>
    {% endfor %}</table>
    {% if not rows %}<p>还没有数据，先去 <a href="/samples">上传样本</a>。</p>{% endif %}
    </div>"""
    return render("概览", render_template_string(body, rows=rows))


@app.route("/samples", methods=["GET", "POST"])
def samples():
    msg = ""
    if request.method == "POST":
        source = (request.form.get("source") or "").strip()
        text = ""
        f = request.files.get("file")
        if f and f.filename:
            text = f.read().decode("utf-8")
        elif request.form.get("text"):
            text = request.form["text"]
        if not source:
            msg = '<p class="bad">请填写数据源名称</p>'
        elif not text.strip():
            msg = '<p class="bad">请上传 .jsonl/.json 文件或粘贴 JSON</p>'
        else:
            docs, errs = parse_docs(text)
            if docs:
                proj.add_samples(source, docs)
                msg = f'<p class="ok">已接收 {len(docs)} 条样本到数据源 <b>{source}</b>（跳过无法解析的 {errs} 条）。下一步：<a href="/infer?source={source}">结构推断</a></p>'
            else:
                msg = '<p class="bad">没有解析出任何 JSON 文档</p>'
    counts = proj.store.sample_counts()
    body = """
    <div class="card"><h2>上传样本</h2>
    <form method="post" enctype="multipart/form-data">
      <p>数据源名称（合作方/接口名）：<input type="text" name="source" required placeholder="如 shop_orders"></p>
      <p>样本文件（.jsonl 每行一条，或 .json 数组/单条）：<input type="file" name="file"></p>
      <p>或直接粘贴：</p><textarea name="text" rows="6" style="width:100%"></textarea>
      <p><button class="btn">上传</button></p>
    </form>{{ msg|safe }}</div>
    <div class="card"><h2>样本库</h2>
    <table><tr><th>数据源</th><th>样本数</th><th>操作</th></tr>
    {% for s,c in counts %}<tr><td>{{ s }}</td><td>{{ c }}</td>
    <td><a href="/infer?source={{ s }}">结构推断</a> · <a href="/design?source={{ s }}">表设计</a> · <a href="/mapping?source={{ s }}">映射规则</a></td></tr>{% endfor %}
    </table></div>"""
    return render("样本上传", render_template_string(body, msg=msg, counts=counts))


def parse_docs(text):
    text = text.strip()
    docs, errs = [], 0
    try:
        v = json.loads(text)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)], 0
        if isinstance(v, dict):
            return [v], 0
    except json.JSONDecodeError:
        pass
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            v = json.loads(line)
            if isinstance(v, dict):
                docs.append(v)
            else:
                errs += 1
        except json.JSONDecodeError:
            errs += 1
    return docs, errs


@app.route("/infer", methods=["GET", "POST"])
def infer():
    source = request.values.get("source")
    if not source:
        return redirect(url_for("samples"))
    msg = ""
    if request.method == "POST":
        limit = request.form.get("limit", type=int) or None
        r = proj.infer(source, limit=limit)
        proj.build_design(source, overrides=proj.get_overrides(source), natural_key="auto")
        msg = f'<p class="ok">已扫描 {r["docs_scanned"]} 条样本，统计 {r["fields"]} 个字段路径，并刷新表设计。</p>'
    stats = proj.field_stats(source)
    total = proj.infer_total(source)
    rows = []
    for path in sorted(stats, key=lambda p: (len(p), p)):
        if not path:
            continue
        st = stats[path]
        act, _ct, _el, note = suggest_for(st, total)
        rows.append((".".join(path), st["count"], f"{st['count']/total:.1%}" if total else "-",
                     "/".join(sorted(st["types"])),
                     "/".join(sorted(st["elem_types"])) if st["elem_types"] else "",
                     ACTION_LABELS[act], note))
    body = """
    <div class="card"><h2>结构推断 · {{ src }}</h2>
    <form method="post">样本共 {{ total }} 条已统计。
      重新扫描前 <input type="text" name="limit" size="5" placeholder="全部"> 条
      <button class="btn">重新推断</button>
      <input type="hidden" name="source" value="{{ src }}"></form>
    {{ msg|safe }}
    <p class="muted">字段有时缺失、有时类型不同：下表给出每个路径的出现比例、类型分布与处理建议。</p>
    <table><tr><th>字段路径</th><th>出现次数</th><th>出现率</th><th>类型分布</th><th>数组元素</th><th>建议</th><th>说明</th></tr>
    {% for r in rows %}<tr><td><code>{{ r[0] }}</code></td><td>{{ r[1] }}</td><td>{{ r[2] }}</td>
      <td>{{ r[3] }}</td><td>{{ r[4] }}</td><td><span class="tag">{{ r[5] }}</span></td>
      <td class="muted">{{ r[6] }}</td></tr>{% endfor %}
    </table></div>"""
    return render("结构推断", render_template_string(body, src=source, total=total,
                                                     msg=msg, rows=rows), source=source)


@app.route("/design")
def design():
    source = request.args.get("source")
    d = proj.get_design(source) if source else None
    if not d:
        return redirect(url_for("samples"))
    tables = []
    for t in d.active_tables():
        tdef = d.tables[t]
        cols = []
        if t == d.root_table:
            cols.append(("__id", "TEXT", "稳定主键"))
        else:
            cols += [("__id", "TEXT", "稳定主键"), ("__parent_id", "TEXT", "父表主键"),
                     ("__idx", "INTEGER", "数组下标")]
            if tdef["elem"] == "scalar":
                cols.append(("value", tdef["ctype"], "数组元素值"))
        for p, info in d.iter_columns(t):
            cols.append((info["column"], info["ctype"],
                         ("JSON列" if info["action"] == "json_column" else "列")
                         + " · " + ".".join(p)))
        cols.append(("__present", "TEXT", "出现性记录(缺失/null/空结构)"))
        cnt = proj.store.count(t) if proj.store.table_exists(t) else 0
        tables.append((t, ".".join(tdef["path"]) or "(根表)", tdef["parent"], cols, cnt))
    body = """
    <div class="card"><h2>表设计 · {{ src }}</h2>
    <p>自然主键：{% if d.natural_key %}<span class="tag">{{ d.natural_key|join(', ') }}</span>
    {% else %}<span class="muted">无自然键，自动生成稳定哈希键（同一数据重复加载键不变）</span>{% endif %}
    　<a href="/mapping?source={{ src }}">修改映射规则</a></p></div>
    {% for name, path, parent, cols, cnt in tables %}
    <div class="card"><h3><code>{{ name }}</code> <span class="muted">{{ path }}{% if parent %} · 父表 {{ parent }}{% endif %} · {{ cnt }} 行</span></h3>
    <table><tr><th>列</th><th>类型</th><th>来源</th></tr>
    {% for c in cols %}<tr><td><code>{{ c[0] }}</code></td><td>{{ c[1] }}</td><td class="muted">{{ c[2] }}</td></tr>{% endfor %}
    </table></div>{% endfor %}"""
    return render("表设计", render_template_string(body, src=source, d=d, tables=tables),
                  source=source)


@app.route("/mapping", methods=["GET", "POST"])
def mapping():
    source = request.values.get("source")
    d = proj.get_design(source) if source else None
    if not d:
        return redirect(url_for("samples"))
    stats = proj.field_stats(source)
    total = proj.infer_total(source)
    msg = ""
    if request.method == "POST":
        overrides = {}
        for key, val in request.form.items():
            if key.startswith("act:"):
                overrides[key[4:]] = val
        nk = request.form.getlist("nk")
        r = proj.apply_rules(source, overrides, nk)
        res = r.get(source, r)
        msg = (f'<p class="ok">规则已保存并重新加载：{res["docs"]} 条，'
               f'自动加列 {len(res["new_columns"])} 个。</p>')
        d = proj.get_design(source)
    cands = natural_key_candidates(stats, total) if stats else []
    rows = []
    for path in sorted(d.paths, key=lambda p: (len(p), p)):
        info = d.paths[path]
        pstr = json.dumps(list(path), ensure_ascii=False)
        st = stats.get(path)
        allowed = allowed_actions(st, info["action"])
        rows.append((".".join(path), pstr, info, allowed))
    body = """
    <div class="card"><h2>映射规则 · {{ src }}</h2>{{ msg|safe }}
    <p class="muted">可以把子表改回 JSON 列、把嵌套对象合成一层（展开为列）或改存 JSON 列；
    保存后自动重建并重新加载全部样本。</p>
    <form method="post"><input type="hidden" name="source" value="{{ src }}">
    <p>自然主键：{% for c in cands %}<label><input type="checkbox" name="nk" value="{{ c }}"
      {% if c in d.natural_key %}checked{% endif %}> {{ c }}</label>　{% endfor %}
      <span class="muted">不选则用稳定哈希键</span></p>
    <table><tr><th>字段路径</th><th>当前规则</th><th>改为</th><th>说明</th></tr>
    {% for dot, pstr, info, allowed in rows %}
    <tr><td><code>{{ dot }}</code></td><td><span class="tag">{{ labels[info.action] }}</span></td>
    <td><select name="act:{{ pstr }}">
      {% for a in allowed %}<option value="{{ a }}" {% if a==info.action %}selected{% endif %}>{{ labels[a] }}</option>{% endfor %}
    </select></td><td class="muted">{{ info.note }}</td></tr>{% endfor %}
    </table>
    <p><button class="btn green">保存规则并重新加载</button></p></form></div>"""
    return render("映射规则", render_template_string(body, src=source, d=d, rows=rows,
                                                   cands=cands, msg=msg,
                                                   labels=ACTION_LABELS), source=source)


def allowed_actions(st, current):
    if st is None:
        return [current, "json_column"]
    types = set(st["types"]) - {"null"}
    if types == {"array"}:
        return ["child_table", "json_column"]
    if types == {"object"}:
        return ["flatten", "json_column"]
    if types <= {"null", "bool", "int", "float", "str"}:
        return ["column", "json_column"]
    return ["json_column"]


@app.route("/load", methods=["POST"])
def load():
    source = request.form.get("source") or None
    proj.load(source)
    return redirect(url_for("loads"))


@app.route("/loads")
def loads():
    recs = proj.store.list_loads()
    for r in recs:
        r["detail"] = json.loads(r["detail"] or "{}")
    body = """
    <div class="card"><h2>加载记录</h2>
    <form method="post" action="/load" style="display:inline"><button class="btn">加载全部数据源</button></form>
    <p class="muted">每次加载的文档数、各表行数、自动加列与类型漂移升级都会记录在案。</p>
    <table><tr><th>#</th><th>时间</th><th>数据源</th><th>文档数</th><th>各表行数</th><th>自动加列</th><th>漂移升级</th></tr>
    {% for r in recs %}<tr><td>{{ r.id }}</td><td>{{ r.ts }}</td><td>{{ r.source }}</td><td>{{ r.docs }}</td>
      <td class="muted">{% for t,n in r.detail.rows.items() %}{{ t }}:{{ n }} {% endfor %}</td>
      <td class="muted">{{ r.detail.new_columns|join(', ') }}</td>
      <td class="muted">{% for u in r.detail.upgrades %}{{ u.kind }}@{{ u.path|join('.') }} {% endfor %}</td>
      </tr>{% endfor %}
    </table></div>"""
    return render("加载记录", render_template_string(body, recs=recs))


@app.route("/verify")
def verify():
    sources = proj.sources()
    results = []
    for src in sources:
        if proj.get_design(src):
            results.append(proj.verify(src))
    total = sum(r["total"] for r in results)
    ok = sum(r["ok"] for r in results)
    body = """
    <div class="card"><h2>反向还原校验</h2>
    <p>从关系表反向拼回 JSON，与原始文档逐条比对（语义一致：键序无关、缺失与 null 有别）。</p>
    <p>合计：<b class="{{ 'ok' if ok==total else 'bad' }}">{{ ok }}/{{ total }}</b> 一致</p>
    <table><tr><th>数据源</th><th>一致</th><th>总数</th><th>结果</th></tr>
    {% for r in results %}<tr><td>{{ r.source }}</td><td>{{ r.ok }}</td><td>{{ r.total }}</td>
    <td>{% if r.mismatch==0 %}<span class="ok">全部一致</span>{% else %}<span class="bad">{{ r.mismatch }} 条不一致</span>{% endif %}</td></tr>{% endfor %}
    </table></div>
    <div class="card"><h3>抽查单条</h3>
    <form method="get" action="/doc">数据源 <input type="text" name="source" value="{{ sources[0] if sources }}">
    文档键 <input type="text" name="key" size="46" placeholder="留空随机抽一条"> <button class="btn gray">对照</button></form></div>"""
    return render("还原校验", render_template_string(body, results=results, ok=ok,
                                                   total=total, sources=sources))


@app.route("/doc")
def doc():
    source = request.args.get("source")
    key = request.args.get("key") or ""
    d = proj.get_design(source) if source else None
    if not d:
        abort(404)
    if not key:
        import random
        recon = proj.reconstruct_all(source)
        if not recon:
            return render("单条对照", "<div class='card'>尚未加载数据。</div>", source=source)
        key = random.choice(list(recon))
        rec = recon[key]
    else:
        rec = proj.reconstruct_one(source, key)
    orig = proj.find_sample_key(source, key)
    same = (orig is not None and rec == orig)
    body = """
    <div class="card"><h2>单条对照 · {{ src }}</h2>
    <p>文档键：<code>{{ key }}</code>　比对结果：
    {% if same %}<b class="ok">语义一致</b>{% else %}<b class="bad">不一致或未找到原文</b>{% endif %}</p>
    <div style="display:flex;gap:16px;flex-wrap:wrap">
    <div style="flex:1;min-width:320px"><h3>原始 JSON</h3><pre>{{ orig_s }}</pre></div>
    <div style="flex:1;min-width:320px"><h3>关系表还原</h3><pre>{{ rec_s }}</pre></div>
    </div></div>"""
    return render("单条对照", render_template_string(
        body, src=source, key=key, same=same,
        orig_s=json.dumps(orig, ensure_ascii=False, indent=2),
        rec_s=json.dumps(rec, ensure_ascii=False, indent=2)), source=source)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
