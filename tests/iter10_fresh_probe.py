import json, re, sys, uuid, requests
from dotenv import dotenv_values
BASE = dotenv_values("/app/frontend/.env")["REACT_APP_BACKEND_URL"].rstrip("/")
Q = [
    "Could you tell me the current course fee for PSCRB at HIMT please?",
    "how much does pscrb cost at the visakhapatnam centre",
    "price of the AFF + PSCRB + MFA combo package at HIMT booking portal",
    "GMDSS course cost details",
    "give me HIMT phone and mail id",
]
for q in Q:
    r = requests.post(f"{BASE}/api/chat", json={"message": q, "session_id": f"TEST_{uuid.uuid4()}"}, timeout=120)
    d = r.json()
    print("="*80)
    print("Q:", q)
    print("status:", d["status"], "| cached:", d.get("cached"), "| intent:", d.get("intent"), "| latency:", d.get("latency_ms"))
    print("ANSWER:", d["answer"][:900])
    print("SOURCES:", [s["url"] for s in d.get("sources", [])])
