import json, os, boto3, numpy as np
from typing import Dict, List
from slot import SlotMemory  # <-- import from slot.py

# ---------- Config ----------
S3_BUCKET = os.environ.get("S3_BUCKET")
S3_KEY = os.environ.get("S3_KEY", "intents/prod/intents.json")
SIM_THRESHOLD = float(os.environ.get("SIM_THRESHOLD", "0.60"))
MARGIN_THRESHOLD = float(os.environ.get("MARGIN_THRESHOLD", "0.08"))
EMBED_PREFIX = os.environ.get("EMBED_PREFIX", "ecommerce intent: ")

s3 = boto3.client("s3")

# ---------- Math helpers ----------
def l2(x: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(x)
    return x / (n + 1e-12)

def cos(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a)*np.linalg.norm(b) + 1e-12))

# ---------- Embedder ----------
class Embedder:
    _model = None
    def __init__(self):
        from sentence_transformers import SentenceTransformer
        if Embedder._model is None:
            print("Loading model from /app/models/all-MiniLM-L6-v2")
            Embedder._model = SentenceTransformer("/app/models/all-MiniLM-L6-v2")
        self.model = Embedder._model
    def encode(self, text: str) -> np.ndarray:
        v = self.model.encode(text, normalize_embeddings=True)
        return v.astype(np.float32)

# ---------- In-memory state ----------
intents: Dict[str, Dict] = {}
_embedder = None
slot_memory = SlotMemory()  # <--- instantiate the slot memory

def _ensure_ready():
    global _embedder
    print("Ensuring ready")
    if _embedder is None:
        _embedder = Embedder()
    if not intents and S3_BUCKET:
        _load_state()
    print("Ensured ready")

def _update_centroid(name: str, vectors: List[np.ndarray]):
    vectors = [l2(v) for v in vectors]
    if name not in intents:
        intents[name] = {"centroid": l2(np.mean(vectors, axis=0)), "n": len(vectors)}
    else:
        c = intents[name]["centroid"]
        n = intents[name]["n"]
        new_sum = c * n + np.sum(vectors, axis=0)
        new_n = n + len(vectors)
        intents[name]["centroid"] = l2(new_sum / new_n)
        intents[name]["n"] = new_n

def _predict(text: str):
    if not intents:
        return None, 0.0, {}
    q = l2(_embedder.encode(EMBED_PREFIX + text))
    sims = {k: cos(q, v["centroid"]) for k, v in intents.items()}
    ranked = sorted(sims.items(), key=lambda kv: kv[1], reverse=True)
    best = ranked[0]
    second = ranked[1] if len(ranked) > 1 else ("<none>", -1.0)
    margin = best[1] - second[1]
    conf = max(0.0, min(1.0, 0.6 * best[1] + 0.4 * max(0.0, margin) / 0.5))
    passed = (best[1] >= SIM_THRESHOLD) and (margin >= MARGIN_THRESHOLD)
    return (best[0] if passed else None), conf, sims

# ---------- S3 persistence ----------
def _load_state():
    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key=S3_KEY)
        meta = json.loads(obj["Body"].read())
        intents.clear()
        for k, v in meta.get("intents", {}).items():
            intents[k] = {"centroid": l2(np.array(v["centroid"], dtype=np.float32)), "n": int(v["n"])}
        print(f"Loaded {len(intents)} intents from s3://{S3_BUCKET}/{S3_KEY}")
    except s3.exceptions.NoSuchKey:
        pass
    except Exception as e:
        print("LOAD_STATE_ERROR:", str(e))

def _save_state():
    try:
        blob = {"intents": {k: {"centroid": v["centroid"].tolist(), "n": v["n"]} for k, v in intents.items()}}
        s3.put_object(Bucket=S3_BUCKET, Key=S3_KEY, Body=json.dumps(blob).encode("utf-8"), ContentType="application/json")
    except Exception as e:
        print("SAVE_STATE_ERROR:", str(e))

# ---------- HTTP helpers ----------
def _resp(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body)}

def _normalize_path(event):
    rc = event.get("requestContext", {}); http = rc.get("http", {})
    raw_path = event.get("rawPath") or http.get("path") or ""
    stage = rc.get("stage") or ""
    if stage and raw_path.startswith(f"/{stage}/"):
        return raw_path[len(stage)+1:]
    return raw_path

# ---------- Lambda entry ----------
def handler(event, context):
    if event.get("source") == "dialogcart.warm":
        _ensure_ready()
        return _resp(200, {"ok": True, "warmed": True})

    path = _normalize_path(event)
    method = (event.get("requestContext", {}).get("http", {}).get("method") or "").upper()

    if path in ("/train", "/predict", "/train_slots", "/warm"):
        _ensure_ready()

    if path == "/warm" and method == "GET":
        return _resp(200, {"ok": True, "warmed": True})

    if path == "/train" and method == "POST":
        try:
            body = json.loads(event.get("body") or "{}")
            intent = (body.get("intent") or "").strip()
            examples = body.get("examples") or []
            if not intent or not examples:
                return _resp(400, {"error": "intent and examples are required"})
            vecs = [_embedder.encode(EMBED_PREFIX + t) for t in examples]
            _update_centroid(intent, vecs)
            _save_state()
            return _resp(200, {"ok": True, "intent": intent, "n_total": intents[intent]["n"]})
        except Exception as e:
            return _resp(500, {"error": str(e)})

    if path == "/train_slots" and method == "POST":
        try:
            body = json.loads(event.get("body") or "{}")
            intent = (body.get("intent") or "").strip()
            text = (body.get("text") or "").strip()
            slots = body.get("slots", {})

            if not intent or not text or not slots:
                return _resp(400, {"error": "intent, text, and slots are required"})

            for slot_name, value in slots.items():
                slot_memory.add(intent, slot_name, value, _embedder)

            return _resp(200, {"ok": True, "trained_slots": list(slots.keys())})
        except Exception as e:
            return _resp(500, {"error": str(e)})

    if path == "/predict" and method == "POST":
        try:
            body = json.loads(event.get("body") or "{}")
            text = (body.get("text") or "").strip()
            if not text:
                return _resp(400, {"error": "text is required"})

            intent, conf, sims = _predict(text)
            slots = slot_memory.predict(intent, text, _embedder) if intent else {}
            sims = {k: round(v, 4) for k, v in sims.items()}
            return _resp(200, {
                "intent": intent,
                "confidence": round(conf, 4),
                "slots": slots,
                "scores": sims
            })
        except Exception as e:
            return _resp(500, {"error": str(e)})

    return _resp(404, {"error": "not found"})
