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
# Multiword Action Map - REMOVED
# Priority is now handled by sorting synonyms by length (longest first)
# -------------------------------

# -------------------------------
# Load Action Synonyms
# -------------------------------
def load_action_synonyms():
    """Load action synonyms from training data (exit if missing)."""
    try:
        from .training_data_loader import training_data_loader
    except ImportError:
        # Fallback for when running as script
        from training_data_loader import training_data_loader
    return training_data_loader.get_action_synonyms()

# Lazy loading to avoid import errors when running as script
ACTION_SYNONYMS = None
CANONICAL_ACTIONS = None

def _ensure_action_synonyms_loaded():
    """Ensure action synonyms are loaded."""
    global ACTION_SYNONYMS, CANONICAL_ACTIONS
    if ACTION_SYNONYMS is None:
        ACTION_SYNONYMS = load_action_synonyms()
        CANONICAL_ACTIONS = set(ACTION_SYNONYMS.keys())

# -------------------------------
# Normalization Data
# -------------------------------
def load_normalization_data():
    """Load normalization data from centralized loader."""
    try:
        from .training_data_loader import training_data_loader
    except ImportError:
        # Fallback for when running as script
        from training_data_loader import training_data_loader
    return training_data_loader.get_normalization_data()

# Lazy loading to avoid import errors when running as script
PRODUCTS = None
PRODUCT_SYNONYMS = None
SYMBOL_MAP = None

def _ensure_normalization_data_loaded():
    """Ensure normalization data is loaded."""
    global PRODUCTS, PRODUCT_SYNONYMS, SYMBOL_MAP
    if PRODUCTS is None:
        PRODUCTS, PRODUCT_SYNONYMS, SYMBOL_MAP = load_normalization_data()

# -------------------------------
# Symbol Normalization
# -------------------------------
def normalize_symbols(text: str) -> str:
    """Normalize symbols in text using the same logic as the normalizer."""
    _ensure_normalization_data_loaded()
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
    _ensure_action_synonyms_loaded()
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

def normalize_action_with_synonyms(word: str, filtered_synonyms: Dict[str, List[str]]) -> str:
    """Normalize action using only filtered synonyms."""
    w = word.lower().strip()

    # Exact synonym match
    for action, synonyms in filtered_synonyms.items():
        for syn in synonyms:
            if w == syn.lower().strip():
                return action

    # Prefix match (inflections like "adding")
    for action, synonyms in filtered_synonyms.items():
        for syn in synonyms:
            if w.startswith(syn.lower().strip()):
                return action

    return "unknown"

# -------------------------------
# Verb Extraction
# -------------------------------
def extract_verbs(user_text: str) -> List[str]:
    _ensure_action_synonyms_loaded()
    # First normalize symbols
    normalized_text = normalize_symbols(user_text)
    text_lower = normalized_text.lower()
    verbs = []
    skip_spans = []

    print(f"\n📝 Extracting verbs from: '{user_text}' (normalized: '{normalized_text}')")

    # spaCy verbs
    doc = nlp(normalized_text)
    for token in doc:
        if token.pos_ == "VERB":
            lemma = token.lemma_.lower().strip()
            norm = normalize_action(lemma)
            if norm != "unknown":
                print(f"   • token='{token.text}' lemma='{lemma}' → '{norm}'")
                verbs.append(norm)

    # Collect all synonyms with their actions, sorted by length (longest first)
    all_synonyms = []
    for action, synonyms in ACTION_SYNONYMS.items():
        # 🚫 EXCLUDE CART ACTION
        if action.lower() == "cart":
            continue
        for syn in synonyms:
            all_synonyms.append((syn, action))
    
    # Sort by length (longest first) for proper priority
    all_synonyms.sort(key=lambda x: len(x[0]), reverse=True)
    
    # Process synonyms in priority order
    for syn, action in all_synonyms:
        # Check if this synonym overlaps with any already matched spans
        match = re.search(rf"\b{re.escape(syn)}\b", text_lower)
        if match:
            start, end = match.span()
            # Check if this match overlaps with any skip span
            overlaps = any(start < skip_end and end > skip_start for skip_start, skip_end in skip_spans)
            
            if not overlaps:
                print(f"🔎 Synonym match: '{syn}' → '{action}'")
                verbs.append(action)
                skip_spans.append((start, end))

    # Deduplicate
    unique = []
    seen = set()
    for v in verbs:
        if v not in seen:
            seen.add(v)
            unique.append(v)

    print(f"✅ Final extracted verbs: {unique}")
    return unique

def extract_verbs_with_synonyms(user_text: str, filtered_synonyms: Dict[str, List[str]]) -> List[str]:
    """Extract verbs using only the provided filtered synonyms."""
    # First normalize symbols
    normalized_text = normalize_symbols(user_text)
    text_lower = normalized_text.lower()
    verbs = []
    skip_spans = []

    print(f"\n📝 Extracting verbs from: '{user_text}' (normalized: '{normalized_text}') [FILTERED]")

    # spaCy verbs - only check against filtered synonyms
    doc = nlp(normalized_text)
    for token in doc:
        if token.pos_ == "VERB":
            lemma = token.lemma_.lower().strip()
            norm = normalize_action_with_synonyms(lemma, filtered_synonyms)
            if norm != "unknown":
                print(f"   • token='{token.text}' lemma='{lemma}' → '{norm}' [FILTERED]")
                verbs.append(norm)

    # Collect filtered synonyms with their actions, sorted by length (longest first)
    all_synonyms = []
    for action, synonyms in filtered_synonyms.items():
        # 🚫 EXCLUDE CART ACTION
        if action.lower() == "cart":
            continue
        for syn in synonyms:
            all_synonyms.append((syn, action))
    
    # Sort by length (longest first) for proper priority
    all_synonyms.sort(key=lambda x: len(x[0]), reverse=True)
    
    # Process synonyms in priority order
    for syn, action in all_synonyms:
        # Check if this synonym overlaps with any already matched spans
        match = re.search(rf"\b{re.escape(syn)}\b", text_lower)
        if match:
            start, end = match.span()
            # Check if this match overlaps with any skip span
            overlaps = any(start < skip_end and end > skip_start for skip_start, skip_end in skip_spans)
            
            if not overlaps:
                print(f"🔎 Synonym match: '{syn}' → '{action}' [FILTERED]")
                verbs.append(action)
                skip_spans.append((start, end))

    # Deduplicate
    unique = []
    seen = set()
    for v in verbs:
        if v not in seen:
            seen.add(v)
            unique.append(v)

    print(f"✅ Final extracted verbs [FILTERED]: {unique}")
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
    
    # Written number to digit mapping
    written_numbers = {
        'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
        'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
        'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14, 'fifteen': 15,
        'sixteen': 16, 'seventeen': 17, 'eighteen': 18, 'nineteen': 19, 'twenty': 20
    }
    
    quantities = []
    text_lower = normalized_text.lower()
    
    # Extract numeric quantities
    for num in re.findall(r"\d+\.?\d*", normalized_text):
        quantities.append(float(num))
    
    # Extract written quantities
    for word, value in written_numbers.items():
        if re.search(rf"\b{word}\b", text_lower):
            quantities.append(float(value))
    
    return quantities


# -------------------------------
# Validation Rules
# -------------------------------
def check_unknown_verbs(user_text: str) -> bool:
    return any(v == "unknown" for v in extract_verbs(user_text))

def check_unknown_verbs_with_synonyms(user_text: str, filtered_synonyms: Dict[str, List[str]]) -> bool:
    """Check for unknown verbs using only the provided filtered synonyms."""
    return any(v == "unknown" for v in extract_verbs_with_synonyms(user_text, filtered_synonyms))

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
    _ensure_action_synonyms_loaded()
    text_verbs = extract_verbs(user_text)
    rasa_verbs = {normalize_rasa_action(a.get("action", "")) for a in rasa_actions}
    return any(v in CANONICAL_ACTIONS and v not in rasa_verbs for v in text_verbs)

def check_missing_verbs_in_rasa_with_synonyms(user_text: str, rasa_actions: List[Dict], filtered_synonyms: Dict[str, List[str]]) -> bool:
    """Check for missing verbs using only the provided filtered synonyms."""
    text_verbs = extract_verbs_with_synonyms(user_text, filtered_synonyms)
    rasa_verbs = {normalize_rasa_action_with_synonyms(a.get("action", ""), filtered_synonyms) for a in rasa_actions}
    canonical_actions = set(filtered_synonyms.keys())
    return any(v in canonical_actions and v not in rasa_verbs for v in text_verbs)

def check_unexpected_rasa_actions(rasa_actions: List[Dict]) -> bool:
    """Check if Rasa actions contain non-canonical actions after normalization."""
    _ensure_action_synonyms_loaded()
    for action_dict in rasa_actions:
        action = action_dict.get("action", "")
        if not action:
            continue
            
        # Normalize the action by checking if it contains any canonical action
        normalized_action = normalize_rasa_action(action)
        if normalized_action not in CANONICAL_ACTIONS:
            return True
    return False

def check_unexpected_rasa_actions_with_synonyms(rasa_actions: List[Dict], filtered_synonyms: Dict[str, List[str]]) -> bool:
    """Check if Rasa actions contain non-canonical actions using only filtered synonyms."""
    canonical_actions = set(filtered_synonyms.keys())
    for action_dict in rasa_actions:
        action = action_dict.get("action", "")
        if not action:
            continue
            
        # Normalize the action by checking if it contains any canonical action
        normalized_action = normalize_rasa_action_with_synonyms(action, filtered_synonyms)
        if normalized_action not in canonical_actions:
            return True
    return False

def normalize_rasa_action(rasa_action: str) -> str:
    """Normalize Rasa action to canonical form and flag pollution."""
    _ensure_action_synonyms_loaded()
    action_lower = rasa_action.lower().strip()

    # Direct exact match to canonical
    if action_lower in CANONICAL_ACTIONS:
        return action_lower

    # Exact match to synonym
    for canonical, synonyms in ACTION_SYNONYMS.items():
        if action_lower in [s.lower().strip() for s in synonyms]:
            return canonical

    # ✅ New: check if the action *contains* a canonical verb but has extra tokens
    for canonical in CANONICAL_ACTIONS:
        if canonical in action_lower.split():
            # Flag pollution explicitly
            return f"polluted::{canonical}"

    # No match at all
    return "unknown"

def normalize_rasa_action_with_synonyms(rasa_action: str, filtered_synonyms: Dict[str, List[str]]) -> str:
    """Normalize Rasa action using only filtered synonyms."""
    action_lower = rasa_action.lower().strip()
    canonical_actions = set(filtered_synonyms.keys())

    # Direct exact match to canonical
    if action_lower in canonical_actions:
        return action_lower

    # Exact match to synonym
    for canonical, synonyms in filtered_synonyms.items():
        if action_lower in [s.lower().strip() for s in synonyms]:
            return canonical

    # ✅ New: check if the action *contains* a canonical verb but has extra tokens
    for canonical in canonical_actions:
        if canonical in action_lower.split():
            # Flag pollution explicitly
            return f"polluted::{canonical}"

    # No match at all
    return "unknown"

def check_missing_products(user_text: str, rasa_actions: List[Dict]) -> bool:
    return any(a.get("product") in [None, ""] for a in rasa_actions)



def check_product_mismatch(user_text: str, rasa_actions: List[Dict]) -> bool:
    _ensure_normalization_data_loaded()
    if not PRODUCTS and not PRODUCT_SYNONYMS:
        return False
    text_products = extract_products(user_text, PRODUCTS, PRODUCT_SYNONYMS)
    rasa_products = {a.get("product") for a in rasa_actions if a.get("product")}
    return not text_products.issubset(rasa_products)

def has_invalid_actions_with_synonyms(rasa_actions: List[Dict], filtered_synonyms: Dict[str, List[str]]) -> bool:
    """Fail if any action is not an exact canonical verb or its synonym."""
    for a in rasa_actions:
        raw = (a.get("action") or "").strip()
        if not raw:
            return True
        norm = normalize_rasa_action_with_synonyms(raw, filtered_synonyms)
        # Reject any action that’s not a clean canonical verb
        if not norm or norm == "unknown" or norm.startswith("polluted::"):
            return True
    return False

# -------------------------------
# Intent-Specific Validator
# -------------------------------
def validate_by_intent(
    user_text: str, 
    rasa_response: Dict[str, Any], 
    intent: str,
    allowed_synonyms: List[str]
) -> Dict[str, Any]:
    """
    Validate Rasa response for a specific intent using only the allowed synonyms.
    
    Args:
        user_text: User input text
        rasa_response: Rasa API response
        intent: Intent name (e.g., "modify_cart")
        allowed_synonyms: List of allowed action synonyms (e.g., ["set", "remove", "add"])
    
    Returns:
        Dict with validation results
    """
    rasa_actions = rasa_response.get("result", {}).get("actions", [])
    
    # Filter action synonyms to only include allowed ones
    _ensure_action_synonyms_loaded()
    filtered_synonyms = {action: synonyms for action, synonyms in ACTION_SYNONYMS.items() 
                        if action in allowed_synonyms}
    
    print(f"🎯 Validating intent '{intent}' with synonyms: {allowed_synonyms}")
    print(f"📋 Filtered synonyms loaded: {len(filtered_synonyms)} actions")
    
    # Check each validation condition with filtered synonyms
    validation_checks = [
        ("unknown_verbs", check_unknown_verbs_with_synonyms(user_text, filtered_synonyms)),
        ("quantity_mismatch", check_quantity_mismatch(user_text, rasa_actions)),
        ("missing_verbs_in_rasa", check_missing_verbs_in_rasa_with_synonyms(user_text, rasa_actions, filtered_synonyms)),
        ("unexpected_rasa_actions", check_unexpected_rasa_actions_with_synonyms(rasa_actions, filtered_synonyms)),
        ("product_mismatch", check_product_mismatch(user_text, rasa_actions)),
        ("invalid_actions", has_invalid_actions_with_synonyms(rasa_actions, filtered_synonyms)),
    ]
    
    failed_checks = [check_name for check_name, failed in validation_checks if failed]
    validation_passed = len(failed_checks) == 0
    
    if failed_checks:
        route = "llm"
        logger.warning("VALIDATION FAILED - Intent: '%s' | Text: '%s' | Failed checks: %s | Rasa actions: %s", 
                      intent, user_text, failed_checks, rasa_actions)
    else:
        route = "trust_rasa"
        logger.info("VALIDATION PASSED - Intent: '%s' | Text: '%s' | Rasa actions: %s", 
                   intent, user_text, rasa_actions)
    
    return {
        "route": route, 
        "rasa_actions": rasa_actions, 
        "failed_checks": failed_checks,
        "validation_passed": validation_passed,
        "intent": intent,
        "allowed_synonyms": allowed_synonyms
    }

def validate_modify_cart(user_text: str, rasa_response: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convenience function to validate modify_cart intent with its specific synonyms.
    
    Args:
        user_text: User input text
        rasa_response: Rasa API response
    
    Returns:
        Dict with validation results
    """
    modify_cart_synonyms = ["add", "remove", "set"]
    return validate_by_intent(user_text, rasa_response, "modify_cart", modify_cart_synonyms)

def check_partial_product(user_text: str, rasa_actions: List[Dict]) -> bool:
    """
    Detect if Rasa extracted only a subset of the full noun phrase product.
    Works for multi-word products like 'red beans' or 'brown dried cat fish'.
    """
    if not rasa_actions:
        return False

    user_text_lower = user_text.lower()
    rasa_products = [a.get("product", "").lower() for a in rasa_actions if a.get("product")]

    # Use spaCy to extract noun chunks (likely product candidates)
    doc = nlp(user_text_lower)
    noun_phrases = [chunk.text.strip().lower() for chunk in doc.noun_chunks]

    for prod in rasa_products:
        if not prod:
            continue
        for np in noun_phrases:
            if prod in np and prod != np:
                # Example: prod="meat", np="bush meat" → partial
                # Example: prod="fish", np="brown dried cat fish" → partial
                return True
    return False



# -------------------------------
# Validator (Legacy - for backward compatibility)
# -------------------------------
def validate_rasa_response(user_text: str, rasa_response: Dict[str, Any]) -> Dict[str, Any]:
    """Legacy validator that uses all synonyms. Use validate_by_intent for better performance."""
    rasa_actions = rasa_response.get("result", {}).get("actions", [])
    
    # Check each validation condition and log the reason
    validation_checks = [
        ("unknown_verbs", check_unknown_verbs(user_text)),
        ("quantity_mismatch", check_quantity_mismatch(user_text, rasa_actions)),
        ("missing_verbs_in_rasa", check_missing_verbs_in_rasa(user_text, rasa_actions)),
        ("unexpected_rasa_actions", check_unexpected_rasa_actions(rasa_actions)),
        ("product_mismatch", check_product_mismatch(user_text, rasa_actions)),
        ("missing_products", check_missing_products(user_text, rasa_actions)),
        ("partial_product", check_partial_product(user_text, rasa_actions))
    ]
    
    failed_checks = [check_name for check_name, failed in validation_checks if failed]
    validation_passed = len(failed_checks) == 0
    
    if failed_checks:
        route = "llm"
        logger.warning("VALIDATION FAILED - Text: '%s' | Failed checks: %s | Rasa actions: %s", user_text, failed_checks, rasa_actions)
    else:
        route = "trust_rasa"
        logger.info("VALIDATION PASSED - Text: '%s' | Rasa actions: %s", user_text, rasa_actions)
    
    return {
        "route": route, 
        "rasa_actions": rasa_actions, 
        "failed_checks": failed_checks,
        "validation_passed": validation_passed
    }

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

def write_detailed_results_file(all_test_results: List[Dict], log_file: Path) -> None:
    """Write detailed test results to a JSON file for analysis."""
    import json
    from datetime import datetime
    
    # Create results file path based on log file
    results_file = log_file.parent / f"validation_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    
    # Convert sets to lists for JSON serialization
    serializable_results = []
    for test in all_test_results:
        serializable_test = test.copy()
        if 'products' in serializable_test and isinstance(serializable_test['products'], set):
            serializable_test['products'] = list(serializable_test['products'])
        serializable_results.append(serializable_test)
    
    try:
        with open(results_file, 'w', encoding='utf-8') as f:
            json.dump(serializable_results, f, indent=2, ensure_ascii=False)
        print(f"📄 Detailed results written to: {results_file}")
        logger.info("Detailed results written to: %s", results_file)
    except Exception as e:
        print(f"❌ Failed to write results file: {e}")
        logger.error("Failed to write results file: %s", e)