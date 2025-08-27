"""
Utility functions for the Bulkpot agent.
"""

from functools import wraps
from typing import Callable

NUMBER_EMOJI = {
    1: "1️⃣",
    2: "2️⃣",
    3: "3️⃣",
    4: "4️⃣",
    5: "5️⃣",
    6: "6️⃣",
    7: "7️⃣",
    8: "8️⃣",
    9: "9️⃣",
    10: "🔟",
    11: "⓫",
    12: "⓬",
    13: "⓭",
    14: "⓮",
    15: "⓯",
    16: "⓰",
    17: "⓱",
    18: "⓲",
    19: "⓳",
    20: "⓴"
}

def get_number_emoji(n: int) -> str:
    """
    Return the emoji representation of a number if available,
    otherwise return the plain number as a string.
    """
    return NUMBER_EMOJI.get(n, str(n))


def split_name(full_name: str):
    """
    Given a full name, returns first_name and last_name.
    - If only one name: first_name is set, last_name is empty.
    - If two or more: first word = first_name, the rest = last_name.
    """
    parts = full_name.strip().split()
    if not parts:
        raise ValueError("No name provided.")
    first_name = parts[0]
    last_name = ' '.join(parts[1:]) if len(parts) > 1 else ""
    return first_name, last_name


def convert_floats_for_dynamodb(obj):
    """
    Recursively convert float values to integers for DynamoDB compatibility.
    DynamoDB doesn't support float types, so we convert them to integers.
    
    Args:
        obj: Any object (dict, list, float, or other types)
        
    Returns:
        The same object with all float values converted to integers (if whole numbers) or strings (if decimals)
        
    Examples:
        >>> convert_floats_for_dynamodb(815.0)
        815
        >>> convert_floats_for_dynamodb(2.5)
        "2.5"
        >>> convert_floats_for_dynamodb({"tokens": 777.0, "score": 3.14})
        {"tokens": 777, "score": "3.14"}
    """
    if isinstance(obj, dict):
        return {k: convert_floats_for_dynamodb(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_floats_for_dynamodb(item) for item in obj]
    elif isinstance(obj, float):
        return int(obj) if obj.is_integer() else str(obj)
    else:
        return obj


def search_in_list(
    name: str,
    items_dict: dict,
    fallback_to_fuzzy: bool = True,
    threshold: float = 0.6
) -> list:
    """
    Search for items by name in a given dictionary and return ALL matching IDs.
    If no matches found and fallback_to_fuzzy is True, automatically tries fuzzy matching.
    
    Args:
        name: The name of the item to search for
        items_dict: Dictionary with item IDs as keys and names as values
        fallback_to_fuzzy: Whether to fall back to fuzzy matching if no exact/partial matches found
        threshold: Minimum similarity score for fuzzy matching (0.0 to 1.0), defaults to 0.6
        
    Returns:
        List of matching item IDs (empty list if none found)
    """
    if not name or not items_dict:
        return []
        
    # Convert name to lowercase for case-insensitive search
    search_name = name.lower().strip()
    matching_ids = []
    
    for item_id, item_name in items_dict.items():
        item_name_lower = item_name.lower()
        
        # Exact match
        if item_name_lower == search_name:
            matching_ids.append(item_id)
        # Contains match (partial name matching)
        elif search_name in item_name_lower:
            matching_ids.append(item_id)
    
    print(f"[DEBUG] search_in_list matching_ids={matching_ids}")
    
    # If no matches found and fallback is enabled, try fuzzy matching
    if not matching_ids and fallback_to_fuzzy:
        print(f"[DEBUG] No exact/partial matches found, trying fuzzy matching for '{name}'")
        fuzzy_results = search_similar(name, items_dict, threshold=threshold, max_results=3)
        if fuzzy_results:
            # Extract just the IDs from fuzzy results
            fuzzy_ids = [item_id for item_id, score in fuzzy_results]
            print(f"[DEBUG] Fuzzy matching found IDs: {fuzzy_ids}")
            return fuzzy_ids
    
    return matching_ids


def search_similar(
    name: str,
    items_dict: dict,
    threshold: float = 0.7,
    max_results: int = 5
) -> list:
    """
    Search for items by name similarity using fuzzy matching and return matching IDs with scores.
    
    Args:
        name: The name to search for
        items_dict: Dictionary with item IDs as keys and names as values
        threshold: Minimum similarity score (0.0 to 1.0), defaults to 0.7
        max_results: Maximum number of results to return, defaults to 5
        
    Returns:
        List of tuples: [(item_id, similarity_score), ...] sorted by score (highest first)
    """
    try:
        from rapidfuzz import fuzz, process
    except ImportError:
        print("[WARNING] rapidfuzz not installed, falling back to basic search")
        # Fallback to basic search if rapidfuzz is not available
        basic_matches = search_in_list(name, items_dict)
        return [(item_id, 1.0) for item_id in basic_matches]
    
    if not name or not items_dict:
        return []
    
    # Convert threshold from 0.0-1.0 to 0-100 for rapidfuzz
    score_cutoff = int(threshold * 100)
    
    # Get all item names for processing
    item_names = list(items_dict.values())
    item_ids = list(items_dict.keys())
    
    # Use rapidfuzz to find similar items
    matches = process.extract(
        name,
        item_names,
        scorer=fuzz.token_set_ratio,  # ✅ Better for product names
        limit=max_results,
        score_cutoff=score_cutoff
    )
    
    # Convert results to (item_id, score) format and normalize scores to 0.0-1.0
    results = []
    for item_name, score, _ in matches:
        # Find the item_id for this item_name
        for item_id, dict_name in items_dict.items():
            if dict_name == item_name:
                normalized_score = score / 100.0  # Convert from 0-100 to 0.0-1.0
                results.append((item_id, normalized_score))
                break
    
    # Sort by score (highest first)
    results.sort(key=lambda x: x[1], reverse=True)
    
    print(f"[DEBUG] search_similar for '{name}' found {len(results)} matches: {results}")
    return results
