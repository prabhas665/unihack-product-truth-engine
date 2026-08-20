import csv, httpx, json, os, sys, time

BASE = "https://unihack-product-truth-engine.onrender.com"
SRC = r"D:\unihack\Unihack_ Sample Dataset - Input.csv"
OUT_DIR = r"D:\unihack\stage6_full"
STATE = os.path.join(OUT_DIR, "progress.json")
CHUNK = 20

os.makedirs(OUT_DIR, exist_ok=True)
sys.stdout.reconfigure(encoding="utf-8")

with open(SRC, encoding="utf-8-sig") as f:
    src = list(csv.DictReader(f))
total = len(src)
print(f"total rows: {total}", flush=True)

state = {"done": []}
if os.path.exists(STATE):
    with open(STATE) as f:
        state = json.load(f)
done = set(state["done"])

chunks = [src[i:i + CHUNK] for i in range(0, total, CHUNK)]
client = httpx.Client(timeout=120)

def run_chunk(idx, rows):
    payload = [{"Mfg_Part_Num": r["Mfg_Part_Num"], "Part_Manuf": r["Part_Manuf"],
                "E1_Brand": r["E1_Brand"], "Part_Desc": r["Part_Desc"][:80]} for r in rows]
    for attempt in range(5):
        try:
            r = client.post(f"{BASE}/api/batch", json={"rows": payload})
            if r.status_code != 200:
                print(f"  chunk {idx}: HTTP {r.status_code} {r.text[:120]}", flush=True)
                time.sleep(20); continue
            d = r.json()
            fname = d.get("delivery_file")
            if not fname:
                print(f"  chunk {idx}: no file in response", flush=True)
                return None
            csv_text = client.get(f"{BASE}{d.get('download_url','')}").text
            local = os.path.join(OUT_DIR, f"chunk_{idx:03d}.csv")
            with open(local, "w", encoding="utf-8") as f:
                f.write(csv_text)
            print(f"  chunk {idx}: {len(d.get('rows', []))} rows, counts={d.get('status_counts')} file={local}", flush=True)
            return local
        except Exception as e:
            print(f"  chunk {idx} attempt {attempt}: {str(e)[:80]}", flush=True)
            time.sleep(15)
    return None

try:
    for idx, chunk in enumerate(chunks):
        if idx in done:
            continue
        print(f"=== chunk {idx} of {len(chunks)} (rows {idx*CHUNK}-{min((idx+1)*CHUNK, total)})", flush=True)
        local = run_chunk(idx, chunk)
        if local:
            done.add(idx)
            with open(STATE, "w") as f:
                json.dump({"done": sorted(done)}, f)
        time.sleep(5)
except KeyboardInterrupt:
    pass

# merge
header = None
merged = []
for idx in sorted(done):
    p = os.path.join(OUT_DIR, f"chunk_{idx:03d}.csv")
    if not os.path.exists(p):
        continue
    with open(p, encoding="utf-8-sig") as f:
        lines = f.read().splitlines()
    if header is None:
        header = lines[0].lstrip("\ufeff").split(",")
    merged.extend(lines[1:])

if header is None:
    print("NO CHUNKS COMPLETED - rerun to resume", flush=True)
else:
    master = os.path.join(OUT_DIR, "delivery_master.csv")
    with open(master, "w", encoding="utf-8-sig") as f:
        f.write(",".join(header) + "\n" + "\n".join(merged))
    print(f"MASTER: {master} rows={len(merged)} cols={len(header)}", flush=True)
    idx = {n: i for i, n in enumerate(header)}
    if "SKU - MY_PART_NUMBER" in idx and "MOBILE_DESC" in idx:
        sku = sum(1 for l in merged if l.split(",")[idx["SKU - MY_PART_NUMBER"]] == l.split(",")[idx["Mfg_Part_Num"]])
        desc = sum(1 for l in merged if len(l.split(",")) > idx["MOBILE_DESC"] and l.split(",")[idx["MOBILE_DESC"]])
        print(f"SKU filled: {sku}/{len(merged)} | with description: {desc}", flush=True)
print("DONE", flush=True)