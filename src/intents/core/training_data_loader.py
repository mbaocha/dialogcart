"""
Centralized training data loader for the intents system.

This module provides a single source of truth for loading and parsing
the initial_training_data.yml file across all components.
"""
import yaml
import pathlib
import sys
from typing import Dict, Any, Optional, List


class TrainingDataLoader:
    """Centralized loader for training data with caching."""
    
    def __init__(self):
        self._data: Optional[Dict[str, Any]] = None
        self._training_path: Optional[str] = None
    
    def _find_training_data_path(self) -> str:
        """Find the training data file across multiple possible locations."""
        possible_paths = [
            # Local development paths
            pathlib.Path("trainings/initial_training_data.yml"),
            pathlib.Path("../trainings/initial_training_data.yml"),
            pathlib.Path("src/intents/trainings/initial_training_data.yml"),
            # Docker container paths
            pathlib.Path("/app/src/intents/trainings/initial_training_data.yml"),
            pathlib.Path("/app/src/initial_training_data.yml"),
            # Fallback
            pathlib.Path("initial_training_data.yml"),
        ]
        
        for path in possible_paths:
            if path.exists():
                return str(path.resolve())
        
        # If no path found, raise error with helpful message
        print("ERROR: Training data file not found. Tried paths:")
        for path in possible_paths:
            print(f"  - {path}")
        sys.exit(1)
    
    def get_training_data(self) -> Dict[str, Any]:
        """Get the training data, loading it if not already cached."""
        if self._data is None:
            if self._training_path is None:
                self._training_path = self._find_training_data_path()
            
            print(f"✅ Found training data at: {self._training_path}")
            
            try:
                with open(self._training_path, 'r', encoding='utf-8') as f:
                    self._data = yaml.safe_load(f) or {}
            except Exception as e:
                print(f"ERROR: Could not load training data from {self._training_path}: {e}")
                sys.exit(1)
        
        return self._data
    
    def get_verb_synonyms(self) -> Dict[str, str]:
        """Extract verb synonyms from training data."""
        data = self.get_training_data()
        mapping: Dict[str, str] = {}
        
        # Process synonym groups for add, remove, set
        for item in data.get("nlu", []):
            if item.get("synonym") in ("add", "remove", "set"):
                canonical = item.get("synonym")
                examples = item.get("examples") or ""
                for line in examples.splitlines():
                    ex = line.strip()
                    if ex.startswith("- "):
                        ex = ex[2:].strip()
                    if ex:
                        mapping[ex.lower()] = canonical
        
        # Ensure canonical verbs map to themselves
        for v in ("add", "remove", "set"):
            mapping[v] = v
        
        if not mapping:
            print("ERROR: No verb synonyms found in training data")
            sys.exit(1)
            
        print(f"✅ Loaded {len(mapping)} verb mappings from training data")
        return mapping
    
    def get_action_synonyms(self) -> Dict[str, List[str]]:
        """Extract action synonyms from training data."""
        data = self.get_training_data()
        action_synonyms = {}
        
        for item in data.get("nlu", []):
            if item.get("synonym"):
                synonym_name = item["synonym"]
                
                # 🚫 EXCLUDE CART AND ALL ITS SYNONYMS
                if synonym_name.lower() == "cart":
                    continue
                
                examples = item.get("examples", "")
                synonyms = []
                for line in examples.splitlines():
                    ex = line.strip()
                    if ex.startswith("- "):
                        ex = ex[2:].strip()
                    if ex:
                        synonyms.append(ex.lower())
                
                action_synonyms[synonym_name.lower()] = synonyms
        
        print(f"✅ Loaded {len(action_synonyms)} action synonym groups from training data")
        return action_synonyms
    
    def get_training_data_path(self) -> str:
        """Get the resolved training data file path."""
        if self._training_path is None:
            self._training_path = self._find_training_data_path()
        return self._training_path
    
    def get_normalization_data(self) -> tuple[set, dict, dict]:
        """
        Load normalization data from normalization.yml file.
        
        Returns:
            tuple: (products, product_synonyms, symbol_map)
        """
        possible_paths = [
            pathlib.Path("trainings/normalization/normalization.yml"),
            pathlib.Path("../trainings/normalization/normalization.yml"),
            pathlib.Path("src/intents/trainings/normalization/normalization.yml"),
        ]
        
        for path in possible_paths:
            if path.exists():
                try:
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
                except Exception as e:
                    print(f"ERROR: Could not load normalization data from {path}: {e}")
                    continue
        
        print("⚠️  Normalization file not found, skipping products and symbols")
        return set(), {}, {}


# Global instance for use across the application
training_data_loader = TrainingDataLoader()
