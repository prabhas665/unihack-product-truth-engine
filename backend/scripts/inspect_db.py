import sqlite3, sys, json
sys.stdout.reconfigure(encoding="utf-8")
db = sqlite3.connect(r"D:\unihack\backend\data\unihack.db")
cur = db.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
print("tables:", [t[0] for t in cur.fetchall()])
cur.execute("SELECT name FROM pragma_table_info('product_records')")
cols = [c[1] for c in cur.fetchall()]
print("product_records cols:", cols)
cur.execute("SELECT * FROM product_records ORDER BY rowid DESC LIMIT 3")
for row in cur.fetchall():
    rec = dict(zip(cols, row))
    print("=== MPN:", rec.get("mpn"), "status:", rec.get("status"))
    for k in ("review_reasons", "processing_status"):
        if k in rec and rec[k]:
            print(" ", k, ":", str(rec[k])[:300])