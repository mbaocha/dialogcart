import re
import spacy
import requests
import json
import yaml
import subprocess
import sys
from typing import Dict, List, Any, Set
from pathlib import Path

# -------------------------------
# Ensure spaCy model
# -------------------------------
def ensure_spacy_model():
    try:
        return spacy.load("en_core_web_sm")
    except OSError:
        print("❌ spaCy model not found. Please install with:")
        print("   pip install spacy")
        print("   python -m spacy download en_core_web_sm")
        sys.exit(1)

nlp = ensure_spacy_model()

# -------------------------------
# Multiword Action Map
# -------------------------------
MULTIWORD_ACTIONS = {
    "get rid of": "remove",
    "throw in": "add",
    "switch out": "replace",
    "make it": "set",
    "take out": "remove",
    "knock off": "remove",
}

# -------------------------------
# Load Action Synonyms
# -------------------------------
def load_action_synonyms():
    """Load action synonyms from training data (exit if missing)."""
    possible_paths = [
        "trainings/initial_training_data.yml",
        "../trainings/initial_training_data.yml",
        "src/intents/trainings/initial_training_data.yml",
    ]
    training_data_path = next((p for p in possible_paths if Path(p).exists()), None)

    if not training_data_path:
        print("❌ Training data file not found in any of:")
        for p in possible_paths:
            print("   -", p)
        sys.exit(1)

    with open(training_data_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    action_synonyms = {}
    for item in data.get("nlu", []):
        if item.get("synonym"):
            synonym_name = item["synonym"]
            examples = item.get("examples", "")
            if isinstance(examples, str):
                synonym_list = [
                    line.lstrip("-").strip()
                    for line in examples.split("\n")
                    if line.strip()
                ]
            else:
                synonym_list = [str(x).strip() for x in examples]
            action_synonyms[synonym_name] = synonym_list

    print(f"✅ Loaded {len(action_synonyms)} action synonyms from training data")
    return action_synonyms

ACTION_SYNONYMS = load_action_synonyms()
CANONICAL_ACTIONS = set(ACTION_SYNONYMS.keys())

# -------------------------------
# Normalization Data
# -------------------------------
def load_normalization_data():
    possible_paths = [
        "trainings/normalization/normalization.yml",
        "../trainings/normalization/normalization.yml",
        "src/intents/trainings/normalization/normalization.yml",
    ]
    for path in possible_paths:
        if Path(path).exists():
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            products = set()
            product_synonyms = {}
            for canonical, variants in data.get("products", {}).items():
                products.add(canonical.lower())
                for v in variants:
                    product_synonyms[v.lower()] = canonical.lower()
            print(f"✅ Loaded {len(products)} products and {len(product_synonyms)} synonyms")
            return products, product_synonyms
    print("⚠️  Normalization file not found, skipping products")
    return set(), {}

PRODUCTS, PRODUCT_SYNONYMS = load_normalization_data()

# -------------------------------
# Extraction Helpers
# -------------------------------
def normalize_action(word: str) -> str:
    w = word.lower().strip()

    # Exact synonym match
    for action, synonyms in ACTION_SYNONYMS.items():
        for syn in synonyms:
            if w == syn.lower().strip():
                return action

    # Prefix match (inflections like "adding")
    for action, synonyms in ACTION_SYNONYMS.items():
        for syn in synonyms:
            if w.startswith(syn.lower().strip()):
                return action

    return "unknown"

def extract_verbs(user_text: str) -> List[str]:
    text_lower = user_text.lower()
    verbs = []

    #print(f"\n📝 Extracting verbs from: '{user_text}'")

    # Regex multiword phrase match first
    for phrase, action in MULTIWORD_ACTIONS.items():
        if re.search(rf"\b{re.escape(phrase)}\b", text_lower):
            #print(f"🔎 Regex multiword match: '{phrase}' → '{action}'")
            verbs.append(action)

    # spaCy verbs
    doc = nlp(user_text)
    for token in doc:
        if token.pos_ == "VERB":
            lemma = token.lemma_.lower()
            norm = normalize_action(lemma)
            #print(f"   • token='{token.text}' lemma='{lemma}' → '{norm}'")
            verbs.append(norm)

    # Fallback: single word synonym search
    if not verbs:
        for action, synonyms in ACTION_SYNONYMS.items():
            for syn in synonyms:
                if syn.lower() in text_lower:
                    #print(f"🔎 Fallback match: '{syn}' → '{action}'")
                    verbs.append(action)

    # Deduplicate
    unique = []
    seen = set()
    for v in verbs:
        if v not in seen:
            seen.add(v)
            unique.append(v)

    # If unknown exists alongside a valid action, drop unknown
    if "unknown" in unique and any(v in CANONICAL_ACTIONS for v in unique):
        unique = [v for v in unique if v != "unknown"]

    #print(f"✅ Final extracted verbs: {unique}")
    return unique

def extract_quantities(user_text: str) -> List[float]:
    return [float(num) for num in re.findall(r"\d+\.?\d*", user_text)]

def extract_products(user_text: str,
                     products: Set[str] = None,
                     product_synonyms: Dict[str, str] = None) -> Set[str]:
    if not products and not product_synonyms:
        return set()
    words = set(user_text.lower().split())
    found = set()
    if products:
        found |= {p for p in products if p in words}
    if product_synonyms:
        for syn, canonical in product_synonyms.items():
            if syn in words:
                found.add(canonical)
    return found

# -------------------------------
# Validation Rules
# -------------------------------
def check_unknown_verbs(user_text: str) -> bool:
    return any(v == "unknown" for v in extract_verbs(user_text))

def check_quantity_mismatch(user_text: str, rasa_actions: List[Dict]) -> bool:
    """True if user mentions a number but Rasa omits or mismatches it."""
    text_q = extract_quantities(user_text)

    # Collect all quantities Rasa provided (ignore None)
    rasa_q = [a.get("quantity") for a in rasa_actions]

    # If any number from text is missing entirely or Rasa marked it None, mismatch
    for q in text_q:
        if q not in rasa_q:
            return True
    return False


def check_missing_verbs_in_rasa(user_text: str, rasa_actions: List[Dict]) -> bool:
    text_verbs = extract_verbs(user_text)
    rasa_verbs = {a.get("action") for a in rasa_actions}
    return any(v in CANONICAL_ACTIONS and v not in rasa_verbs for v in text_verbs)

def check_unexpected_rasa_actions(rasa_actions: List[Dict]) -> bool:
    return any(a.get("action") not in CANONICAL_ACTIONS for a in rasa_actions)

def check_product_mismatch(user_text: str, rasa_actions: List[Dict]) -> bool:
    if not PRODUCTS and not PRODUCT_SYNONYMS:
        return False
    text_products = extract_products(user_text, PRODUCTS, PRODUCT_SYNONYMS)
    rasa_products = {a.get("product") for a in rasa_actions if a.get("product")}
    return not text_products.issubset(rasa_products)

# -------------------------------
# Validator
# -------------------------------
def validate_rasa_response(user_text: str, rasa_response: Dict[str, Any]) -> Dict[str, Any]:
    rasa_actions = rasa_response.get("result", {}).get("actions", [])
    if check_unknown_verbs(user_text):
        route = "llm"
    elif check_quantity_mismatch(user_text, rasa_actions):
        route = "llm"
    elif check_missing_verbs_in_rasa(user_text, rasa_actions):
        route = "llm"
    elif check_unexpected_rasa_actions(rasa_actions):
        route = "llm"
    elif check_product_mismatch(user_text, rasa_actions):
        route = "llm"
    else:
        route = "trust_rasa"
    return {"route": route, "rasa_actions": rasa_actions}

# -------------------------------
# Call Rasa API
# -------------------------------
def call_rasa_api(text: str, api_url: str = "http://localhost:9000") -> Dict[str, Any]:
    try:
        url = f"{api_url}/classify"
        payload = {"text": text, "sender_id": "validator_test", "validate": False}
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"❌ API call failed: {e}")
        return None

# -------------------------------
# Interactive Main
# -------------------------------
def main():
    print("=== Validator Interactive Test ===")
    print("Available actions:", list(ACTION_SYNONYMS.keys()))
    print("Type 'quit' to exit\n")

    for action, synonyms in ACTION_SYNONYMS.items():
        print(f"  {action}: {synonyms[:5]}...")

    print("\n🔍 Testing API connection...")
    test = call_rasa_api("test connection")
    if not test or not test.get("success"):
        print("❌ API unavailable. Exiting.")
        return
    print("✅ API connection successful!\n")

    sample_cases = [
        "add 2 rice and 1 beans",
        "remove 3 yam and set oil to 5",
        "add some rice",
        "put 4 plantains in cart",
        "delete 2 fish",
        "buy 3 kg of garri",
        "throw in 2 kg rice",
        "get rid of beans",
        "make it 5 kg rice",
    ]

    failures = []
    passed = 0
    failed = 0

    for i, text in enumerate(sample_cases, 1):
        rasa_resp = call_rasa_api(text)
        if rasa_resp and rasa_resp.get("success"):
            result = validate_rasa_response(text, rasa_resp)
            if result["route"] == "llm":
                failed += 1
                verbs = extract_verbs(text)
                quantities = extract_quantities(text)
                products = extract_products(text, PRODUCTS, PRODUCT_SYNONYMS)
                failures.append({
                    "case": i,
                    "text": text,
                    "result": result,
                    "verbs": verbs,
                    "quantities": quantities,
                    "products": products
                })
            else:
                passed += 1

    # Print failures
    print("\n=== FAILURES ===")
    if not failures:
        print("🎉 No failures — validator fully agrees with Rasa!")
    else:
        for f in failures:
            print(f"\n--- Test Case {f['case']} ---")
            print(f"User text: '{f['text']}'")
            print("⚠️  Route decision: llm")
            print(f"Rasa actions: {f['result']['rasa_actions']}")
            print("Extracted:")
            print(f"  Verbs: {f['verbs']}")
            print(f"  Quantities: {f['quantities']}")
            print(f"  Products: {f['products']}")

    # Summary
    total = passed + failed
    print("\n=== SUMMARY ===")
    print(f"Total cases: {total}")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")


if __name__ == "__main__":
    main()
