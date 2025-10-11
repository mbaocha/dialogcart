from symspellpy import SymSpell, Verbosity
import re
import os
from load_data import load_global_entities


def build_symspell0(entities, max_edit_distance=2, prefix_length=7):
    """
    Build and return a SymSpell instance seeded with:
    1. A base English frequency dictionary
    2. Domain-specific canonicals (from global_entities)
    """
    sym_spell = SymSpell(max_edit_distance, prefix_length)

    # --- 1. Load base English dictionary ---
    # You may need to download this file once from:
    # https://github.com/wolfgarbe/SymSpell/blob/master/SymSpell/frequency_dictionary_en_82_765.txt
    dictionary_path = os.path.join(
        os.path.dirname(__file__), "frequency_dictionary_en_82_765.txt"
    )
    if not os.path.exists(dictionary_path):
        raise FileNotFoundError(
            f"Missing {dictionary_path}. Download from SymSpell repo."
        )

    sym_spell.load_dictionary(dictionary_path, term_index=0, count_index=1)

    # --- 2. Add domain-specific canonicals ---
    for e in entities:
        canonical = e.get("canonical", "").lower()
        if canonical:
            # give very high frequency so entities are preferred
            sym_spell.create_dictionary_entry(canonical, 10**9)
        for s in e.get("synonyms", []):
            sym_spell.create_dictionary_entry(s.lower(), 10**9)

    return sym_spell

def build_symspell(entities, max_edit_distance=2, prefix_length=7):
    sym_spell = SymSpell(max_edit_distance, prefix_length)

    # seed catalog words + synonyms
    for e in entities:
        canonical = e.get("canonical", "").lower()
        if canonical:
            sym_spell.create_dictionary_entry(canonical, 10**9)
            for token in canonical.split():
                sym_spell.create_dictionary_entry(token, 10**9)
        for s in e.get("synonyms", []):
            sym_spell.create_dictionary_entry(s.lower(), 10**9)
            for token in s.lower().split():
                sym_spell.create_dictionary_entry(token, 10**9)

    return sym_spell


def correct_sentence(sentence, sym_spell):
    sentence = sentence.lower()

    # 1. Tokenize with protection for number+unit
    tokens = re.findall(r"\d+[a-zA-Z]+|[a-zA-Z]+(?:-[a-zA-Z]+)*|\d+|[^\w\s]", sentence)

    protected = []
    passthrough_mask = []  # True = skip correction

    for token in tokens:
        if token.isdigit():
            protected.append(token)
            passthrough_mask.append(True)
        elif re.match(r"^\d+[a-zA-Z]+$", token):
            # number+unit combo -> split but protect
            num, unit = re.match(r"(\d+)([a-zA-Z]+)", token).groups()
            protected.extend([num, unit])
            passthrough_mask.extend([True, True])
        else:
            protected.append(token)
            passthrough_mask.append(False)

    pre_sentence = " ".join(protected)

    # 2. Run compound correction
    suggestions = sym_spell.lookup_compound(pre_sentence, max_edit_distance=2)
    if not suggestions:
        return pre_sentence

    corrected = suggestions[0].term.split()
    final_tokens = []

    # 3. Only accept corrections if in dictionary, and skip protected tokens
    for orig, corr, skip in zip(pre_sentence.split(), corrected, passthrough_mask):
        if skip:  # number/unit protected
            final_tokens.append(orig)
        elif corr in sym_spell.words:
            final_tokens.append(corr)
        else:
            final_tokens.append(orig)

    return " ".join(final_tokens)


def main():
    entities = load_global_entities()
    sym_spell = build_symspell(entities)

    sentences = [
        "i want to buy ricce",
        "do you have cat fsh",
        "i want to buy plaintain",
        "can i get golden penny rice",
        "add goat met to cart",
        "bring 2kg of coca-cola soda",
    ]

    for s in sentences:
        print(s, "->", correct_sentence(s, sym_spell))

    # Interactive test
    print("\n=== Interactive Test ===")
    print("Enter sentences to test (type 'quit' to exit):")
    while True:
        try:
            user_input = input("> ").strip()
            if user_input.lower() in ['quit', 'exit', 'q']:
                break
            if user_input:
                corrected = correct_sentence(user_input, sym_spell)
                print(f"Corrected: {corrected}")
        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except EOFError:
            print("\nExiting...")
            break


if __name__ == "__main__":
    main()
