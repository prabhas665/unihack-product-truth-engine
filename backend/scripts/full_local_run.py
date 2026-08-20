import csv, httpx, json, os, sys, time

BASE = "http://127.0.0.1:8000"
SRC = r"D:\unihack\Unihack_ Sample Dataset - Input.csv"
OUT_DIR = r"D:\unihack\stage6_full"
STATE = os.path.join(OUT_DIR, "progress_local.json")
CHUNK = 20
API_TIMEOUT = 3600

os.makedirs(OUT_DIR, exist_ok=True)
sys.stdout.reconfigure(encoding="utf-8")

with open(SRC, encoding="utf-8-sig") as f:
    src = list(csv.DictReader(f))
total = len(src)
print(f"total rows: {total}", flush=True)

state = {"done": [], "failed": []}
if os.path.exists(STATE):
    with open(STATE) as f:
        state = json.load(f)
done = set(state.get("done", []))

chunks = [src[i:i + CHUNK] for i in range(0, total, CHUNK)]
client = httpx.Client(timeout=API_TIMEOUT)

def run_chunk(idx, rows):
    payload = [{"Mfg_Part_Num": r["Mfg_Part_Num"], "Part_Manuf": r["Part_Manuf"],
                "E1_Brand": r["E1_Brand"], "Part_Desc": r["Part_Desc"][:80]} for r in rows]
    for attempt in range(3):
        try:
            r = client.post(f"{BASE}/api/batch", json={"rows": payload})
            if r.status_code != 200:
                print(f"  chunk {idx}: HTTP {r.status_code}", flush=True)
                time.sleep(20); continue
            d = r.json()
            fname = d.get("delivery_file")
            if not fname:
                print(f"  chunk {idx}: no file in response", flush=True)
                return None
            csv_text = client.get(f"{BASE}/api/downloads/{fname}").text
            local = os.path.join(OUT_DIR, f"local_{idx:03d}.csv")
            with open(local, "w", encoding="utf-8") as f:
                f.write(csv_text)
            counts = d.get("status_counts", {})
            completed = counts.get("completed", 0)
            print(f"  chunk {idx}: {len(d.get('rows', []))} rows, completed={completed}, needs_review={counts.get('needs_review',0)}", flush=True)
            return local
        except Exception as e:
            print(f"  chunk {idx} attempt {attempt}: {str(e)[:80]}", flush=True)
            time.sleep(10)
    return None

t0 = time.time()
try:
    for idx, chunk in enumerate(chunks):
        if idx in done:
            continue
        print(f"=== chunk {idx+1}/{len(chunks)} (rows {idx*CHUNK+1}-{min((idx+1)*CHUNK, total)})", flush=True)
        local = run_chunk(idx, chunk)
        if local:
            done.add(idx)
            state["done"] = sorted(done)
            with open(STATE, "w") as f:
                json.dump(state, f)
            elapsed = time.time() - t0
            remaining = len(chunks) - len(done)
            avg = elapsed / max(len(done), 1)
            eta_min = round(avg * remaining / 60, 1)
            print(f"  progress: {len(done)}/{len(chunks)} chunks done, ETA ~{eta_min} min", flush=True)
        time.sleep(3)
except KeyboardInterrupt:
    print(f"\ninterrupted at chunk {idx}, {len(done)} chunks saved", flush=True)

# merge
header = None
merged = []
for idx in sorted(done):
    p = os.path.join(OUT_DIR, f"local_{idx:03d}.csv")
    if not os.path.exists(p):
        continue
    with open(p, encoding="utf-8-sig") as f:
        lines = f.read().splitlines()
    if header is None:
        header = lines[0].lstrip("\ufeff").split(",")
    merged.extend([l for l in lines[1:] if l.strip()])

if header:
    master = os.path.join(OUT_DIR, "delivery_master_local.csv")
    with open(master, "w", encoding="utf-8-sig") as f:
        f.write(",".join(header) + "\n" + "\n".join(merged))
    idx = {n: i for i, n in enumerate(header)}
    mfr_col = idx.get("MANUFACTURER_NAME", -1)
    sku_col = idx.get("SKU - MY_PART_NUMBER", -1)
    desc_col = idx.get("MOBILE_DESC", -1)
    total_rows = len(merged)
    sku_ok = sum(1 for l in merged if l.split(",")[sku_col] == l.split(",")[idx.get("Mfg_Part_Num", -1)]) if sku_col >= 0 else 0
    with_desc = sum(1 for l in merged if len(l.split(",")) > desc_col and l.split(",")[desc_col]) if desc_col >= 0 else 0
    print(f"\n{'='*60}", flush=True)
    print(f"MASTER: {master}", flush=True)
    print(f"total: {total_rows} rows x {len(header)} cols", flush=True)
    print(f"SKU filled: {sku_ok}/{total_rows}", flush=True)
    print(f"with description: {with_desc}/{total_rows}", flush=True)
    elapsed = round(time.time() - t0, 1)
    print(f"elapsed: {elapsed}s ({round(elapsed/60,1)} min)", flush=True)
    print("DONE", flush=True)
