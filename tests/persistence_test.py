import os, sys, json, httpx

BASE = os.environ.get("CB_BASE", "http://127.0.0.1:3000")
KEY = os.environ["BROWSER_API_KEY"]
H = {"X-API-Key": KEY, "Content-Type": "application/json"}


def post(path, **body):
    r = httpx.post(f"{BASE}{path}", headers=H, json=body, timeout=120)
    r.raise_for_status()
    return r.json()


def ev(sid, script):
    return post("/eval", session_id=sid, script=script)["result"]


profile = sys.argv[1] if len(sys.argv) > 1 else "testpersist"
sid = post("/open", url="https://example.com", profile=profile)["session_id"]
post("/cookies", session_id=sid, cookies=[{"name": "tu", "value": "dep", "domain": "example.com", "path": "/"}])
ev(sid, "localStorage.setItem('k','giu-duoc')")
assert "tu=dep" in ev(sid, "document.cookie"), "cookie not set"
post("/close", session_id=sid)

sid2 = post("/open", url="https://example.com", profile=profile)["session_id"]
res = json.loads(ev(sid2, "JSON.stringify({c: document.cookie, l: localStorage.getItem('k')})"))
post("/close", session_id=sid2)
print("RESULT", json.dumps(res))
if "tu=dep" in res["c"] and res["l"] == "giu-duoc":
    print("PERSISTENCE_OK")
    sys.exit(0)
print("PERSISTENCE_FAIL")
sys.exit(1)
