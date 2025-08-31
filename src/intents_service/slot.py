from collections import defaultdict
from itertools import chain
from sentence_transformers import SentenceTransformer
import numpy as np

class SlotMemory:
    def __init__(self):
        self.data = defaultdict(list)  # (intent, slot_name) -> list of (value_text, embedding)

    def add(self, intent: str, slot_name: str, value: str, embedder):
        vec = embedder.encode(value)
        self.data[(intent, slot_name)].append((value, vec))

    def predict(self, intent: str, text: str, embedder, threshold: float = 0.75) -> Dict[str, str]:
        spans = extract_candidate_spans(text)
        predictions = {}

        for slot_key, examples in self.data.items():
            if slot_key[0] != intent:
                continue
            slot_name = slot_key[1]
            best_match, best_score = None, -1.0

            for span in spans:
                span_vec = embedder.encode(span)
                for _, ex_vec in examples:
                    score = cos(span_vec, ex_vec)
                    if score > best_score:
                        best_match, best_score = span, score

            if best_score >= threshold:
                predictions[slot_name] = best_match

        return predictions

def extract_candidate_spans(text: str, max_n: int = 4) -> List[str]:
    words = text.split()
    spans = set()
    for n in range(1, max_n+1):
        for i in range(len(words)-n+1):
            spans.add(" ".join(words[i:i+n]))
    return list(spans)
