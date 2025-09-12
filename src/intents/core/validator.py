import re
import spacy
import requests
import json
import yaml
import sys
import logging
from datetime import datetime
from typing import Dict, List, Any, Set
from pathlib import Path

# -------------------------------
# Logging Setup
# -------------------------------
def setup_validator_logging():
    """Setup file logging for validator pass/fail results."""
    # Create logs directory if it doesn't exist
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    
    # Create timestamped log file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"validator_{timestamp}.log"
    
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout)  # Also print to console
        ]
    )
    
    logger = logging.getLogger(__name__)
    logger.info("Validator logging started - Log file: %s", log_file)
    return logger, log_file

# Initialize logging
logger, log_file = setup_validator_logging()

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
            
            # Load symbols
            symbol_map = {}
            for symbol, replacements in data.get("symbols", {}).items():
                if replacements:
                    symbol_map[symbol] = replacements[0]  # Use first replacement as canonical
            
            print(f"✅ Loaded {len(products)} products, {len(product_synonyms)} synonyms, {len(symbol_map)} symbols")
            return products, product_synonyms, symbol_map
    print("⚠️  Normalization file not found, skipping products and symbols")
    return set(), {}, {}

PRODUCTS, PRODUCT_SYNONYMS, SYMBOL_MAP = load_normalization_data()

# -------------------------------
# Symbol Normalization
# -------------------------------
def normalize_symbols(text: str) -> str:
    """Normalize symbols in text using the same logic as the normalizer."""
    if not SYMBOL_MAP:
        return text
    
    normalized = text
    for symbol, replacement in SYMBOL_MAP.items():
        # Pattern 1: Symbol at start of word (no space before, word after)
        pat1 = re.compile(rf"(?<!\w){re.escape(symbol)}(?=\w)")
        normalized = pat1.sub(f"{replacement} ", normalized)
        
        # Pattern 2: Symbol at start with space after (start of string, space after)
        pat2 = re.compile(rf"^{re.escape(symbol)}(?=\s)")
        normalized = pat2.sub(replacement, normalized)
        
        # Pattern 3: Symbol with space before and after
        pat3 = re.compile(rf"(?<=\s){re.escape(symbol)}(?=\s)")
        normalized = pat3.sub(replacement, normalized)
        
        # Pattern 4: Symbol at end of word (word before, no word after)
        pat4 = re.compile(rf"(?<=\w){re.escape(symbol)}(?!\w)")
        normalized = pat4.sub(f" {replacement}", normalized)
    
    # Collapse multiple spaces
    normalized = re.sub(r"\s+", " ", normalized).strip()
    
    if normalized != text:
        print(f"🔄 Symbol normalization: '{text}' → '{normalized}'")
    
    return normalized

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

# -------------------------------
# Verb Extraction
# -------------------------------
def extract_verbs(user_text: str) -> List[str]:
    # First normalize symbols
    normalized_text = normalize_symbols(user_text)
    text_lower = normalized_text.lower()
    verbs = []
    skip_spans = []

    print(f"\n📝 Extracting verbs from: '{user_text}' (normalized: '{normalized_text}')")

    # Regex multiword matches
    for phrase, action in MULTIWORD_ACTIONS.items():
        match = re.search(rf"\b{re.escape(phrase)}\b", text_lower)
        if match:
            print(f"🔎 Regex multiword match: '{phrase}' → '{action}'")
            verbs.append(action)
            skip_spans.append(match.span())  # record start-end indices to skip tokens

    # spaCy verbs
    doc = nlp(normalized_text)  # Use normalized text
    for token in doc:
        # Skip tokens inside multiword action spans
        if any(start <= token.idx < end for (start, end) in skip_spans):
            continue

        if token.pos_ == "VERB":
            lemma = token.lemma_.lower().strip()
            norm = normalize_action(lemma)
            if norm != "unknown":
                print(f"   • token='{token.text}' lemma='{lemma}' → '{norm}'")
                verbs.append(norm)

    # Synonym direct matches (excluding 'cart')
    for action, synonyms in ACTION_SYNONYMS.items():
        if action == "cart":
            continue
        for syn in synonyms:
            if re.search(rf"\b{re.escape(syn)}\b", text_lower):
                # Skip if this synonym is part of a multiword phrase already captured
                if any(re.search(rf"\b{re.escape(phrase)}\b", text_lower) 
                       for phrase in MULTIWORD_ACTIONS.keys()):
                    continue
                print(f"🔎 Synonym match: '{syn}' → '{action}'")
                verbs.append(action)

    # Deduplicate
    unique = []
    seen = set()
    for v in verbs:
        if v not in seen:
            seen.add(v)
            unique.append(v)

    print(f"✅ Final extracted verbs: {unique}")
    return unique

# -------------------------------
# Product Extraction
# -------------------------------
def extract_products(
    user_text: str,
    products: Set[str] = None,
    product_synonyms: Dict[str, str] = None
) -> Set[str]:
    """Match products via regex against canonical list + synonyms."""
    if not products and not product_synonyms:
        return set()

    # Normalize symbols before product extraction
    normalized_text = normalize_symbols(user_text)
    text_lower = normalized_text.lower()
    found = set()

    # Match canonical products
    for p in products:
        if re.search(rf"\b{re.escape(p)}\b", text_lower):
            found.add(p)

    # Match synonyms → map to canonical
    for syn, canonical in product_synonyms.items():
        if re.search(rf"\b{re.escape(syn)}\b", text_lower):
            found.add(canonical)

    return found

def extract_quantities(user_text: str) -> List[float]:
    # Normalize symbols before quantity extraction
    normalized_text = normalize_symbols(user_text)
    return [float(num) for num in re.findall(r"\d+\.?\d*", normalized_text)]


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
    
    # Check each validation condition and log the reason
    validation_checks = [
        ("unknown_verbs", check_unknown_verbs(user_text)),
        ("quantity_mismatch", check_quantity_mismatch(user_text, rasa_actions)),
        ("missing_verbs_in_rasa", check_missing_verbs_in_rasa(user_text, rasa_actions)),
        ("unexpected_rasa_actions", check_unexpected_rasa_actions(rasa_actions)),
        ("product_mismatch", check_product_mismatch(user_text, rasa_actions))
    ]
    
    failed_checks = [check_name for check_name, failed in validation_checks if failed]
    
    if failed_checks:
        route = "llm"
        logger.warning("VALIDATION FAILED - Text: '%s' | Failed checks: %s | Rasa actions: %s", user_text, failed_checks, rasa_actions)
    else:
        route = "trust_rasa"
        logger.info("VALIDATION PASSED - Text: '%s' | Rasa actions: %s", user_text, rasa_actions)
    
    return {"route": route, "rasa_actions": rasa_actions, "failed_checks": failed_checks}

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
    except (requests.RequestException, json.JSONDecodeError, ValueError) as e:
        print(f"❌ API call failed: {e}")
        return None

# -------------------------------
# Interactive Main
# -------------------------------
def load_test_scenarios():
    """Load test scenarios from modify_cart_200_scenarios.jsonl"""
    possible_paths = [
        "tests/modify_cart_200_scenarios.jsonl",
        "../tests/modify_cart_200_scenarios.jsonl", 
        "src/intents/tests/modify_cart_200_scenarios.jsonl",
    ]
    
    scenarios_path = next((p for p in possible_paths if Path(p).exists()), None)
    if not scenarios_path:
        print("❌ Test scenarios file not found in any of:")
        for p in possible_paths:
            print("   -", p)
        return []
    
    scenarios = []
    with open(scenarios_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                scenario = json.loads(line)
                scenarios.append(scenario)
            except json.JSONDecodeError as e:
                print(f"⚠️  Invalid JSON on line {line_num}: {e}")
                continue
    
    print(f"✅ Loaded {len(scenarios)} test scenarios from {scenarios_path}")
    return scenarios

def main():
    print("=== Validator Test on modify_cart_100_scenarios.jsonl ===")
    logger.info("=== VALIDATOR TEST SESSION STARTED ===")
    logger.info("Available actions: %s", list(ACTION_SYNONYMS.keys()))
    print("Available actions:", list(ACTION_SYNONYMS.keys()))
    print()

    for action, synonyms in ACTION_SYNONYMS.items():
        print(f"  {action}: {synonyms[:5]}...")

    print("\n🔍 Testing API connection...")
    test = call_rasa_api("test connection")
    if not test or not test.get("success"):
        print("❌ API unavailable. Exiting.")
        logger.error("API unavailable - exiting")
        return
    print("✅ API connection successful!\n")
    logger.info("API connection successful")

    # Load test scenarios
    scenarios = load_test_scenarios()
    if not scenarios:
        print("❌ No test scenarios loaded. Exiting.")
        logger.error("No test scenarios loaded - exiting")
        return

    failures = []
    passed = 0
    failed = 0
    failed_check_counts = {}

    print(f"🧪 Running validation on {len(scenarios)} scenarios...\n")
    logger.info("Starting validation on %d scenarios", len(scenarios))

    for i, scenario in enumerate(scenarios, 1):
        text = scenario.get("text", "")
        expected_actions = scenario.get("expected_actions", [])
        
        if not text:
            print(f"⚠️  Skipping scenario {i}: empty text")
            logger.warning("Skipping scenario %d: empty text", i)
            continue
            
        rasa_resp = call_rasa_api(text)
        if not rasa_resp or not rasa_resp.get("success"):
            print(f"❌ API call failed for scenario {i}: {text}")
            logger.error("API call failed for scenario %d: %s", i, text)
            failed += 1
            continue
            
        result = validate_rasa_response(text, rasa_resp)
        if result["route"] == "llm":
            failed += 1
            # Track failed check counts
            for check in result.get("failed_checks", []):
                failed_check_counts[check] = failed_check_counts.get(check, 0) + 1
            
            verbs = extract_verbs(text)
            quantities = extract_quantities(text)
            products = extract_products(text, PRODUCTS, PRODUCT_SYNONYMS)
            failures.append({
                "case": i,
                "text": text,
                "expected_actions": expected_actions,
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
            print(f"Expected actions: {f['expected_actions']}")
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
    if total > 0:
        print(f"Success rate: {passed/total*100:.1f}%")
    
    # Log comprehensive summary
    logger.info("=== VALIDATION SUMMARY ===")
    logger.info("Total cases: %d", total)
    logger.info("Passed: %d", passed)
    logger.info("Failed: %d", failed)
    if total > 0:
        logger.info("Success rate: %.1f%%", passed/total*100)
    
    # Log failed check breakdown
    if failed_check_counts:
        logger.info("=== FAILED CHECK BREAKDOWN ===")
        for check, count in sorted(failed_check_counts.items(), key=lambda x: x[1], reverse=True):
            logger.info("%s: %d failures", check, count)
    
    # Log detailed failure analysis
    if failures:
        logger.info("=== DETAILED FAILURE ANALYSIS ===")
        for f in failures:
            logger.warning("FAILURE Case %d: '%s' | Failed checks: %s | Rasa actions: %s", 
                          f['case'], f['text'], f['result'].get('failed_checks', []), f['result']['rasa_actions'])
    
    logger.info("=== VALIDATION SESSION COMPLETED - Log file: %s ===", log_file)


if __name__ == "__main__":
    main()