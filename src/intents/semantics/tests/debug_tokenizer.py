import spacy
from spacy.tokenizer import Tokenizer
import re

# Test the current tokenizer
nlp = spacy.load("en_core_web_sm")

def current_tokenizer(nlp):
    """Current implementation from test.py"""
    infix_re = re.compile(r'''[-~]''')
    prefix_re = re.compile(r'''^[\[\("']''')
    suffix_re = re.compile(r'''[\]\)"']$''')
    
    # This DOESN'T actually split 5kg - it just matches patterns
    token_match = re.compile(r'\d+(?=[a-zA-Z]+)|[a-zA-Z]+|\d+').match
    
    return Tokenizer(
        nlp.vocab,
        rules=nlp.Defaults.tokenizer_exceptions,
        prefix_search=prefix_re.search,
        suffix_search=suffix_re.search,
        infix_finditer=infix_re.finditer,
        token_match=token_match
    )

def fixed_tokenizer(nlp):
    """Fixed implementation that properly splits number+unit"""
    # Add infix pattern to split between digit and letter
    infix_re = re.compile(r'''[-~]|(?<=\d)(?=[a-zA-Z])|(?<=[a-zA-Z])(?=\d)''')
    prefix_re = re.compile(r'''^[\[\("']''')
    suffix_re = re.compile(r'''[\]\)"']$''')
    
    return Tokenizer(
        nlp.vocab,
        rules=nlp.Defaults.tokenizer_exceptions,
        prefix_search=prefix_re.search,
        suffix_search=suffix_re.search,
        infix_finditer=infix_re.finditer,
        token_match=None  # Remove problematic token_match
    )

# Test strings
test_strings = [
    "Add 5kg of rice",
    "Get 10kg flour",
    "I want 3 packs",
    "400ml bottle"
]

print("="*80)
print("CURRENT TOKENIZER (BROKEN)")
print("="*80)
nlp.tokenizer = current_tokenizer(nlp)
for text in test_strings:
    doc = nlp(text.lower())
    tokens = [token.text for token in doc]
    print(f"{text:30s} → {tokens}")

print("\n" + "="*80)
print("FIXED TOKENIZER (SPLITS CORRECTLY)")
print("="*80)
nlp.tokenizer = fixed_tokenizer(nlp)
for text in test_strings:
    doc = nlp(text.lower())
    tokens = [token.text for token in doc]
    print(f"{text:30s} → {tokens}")

print("\n" + "="*80)
print("EXPLANATION:")
print("="*80)
print("""
The issue is with the custom tokenizer in test.py:

PROBLEM:
  - token_match parameter is used to match ENTIRE tokens, not split them
  - Pattern r'\d+(?=[a-zA-Z]+)|[a-zA-Z]+|\d+' tells tokenizer what counts as 
    a valid token, but doesn't split "5kg"
  - Result: "5kg" stays as one token, so "kg" is never recognized as a UNIT

SOLUTION:
  - Use INFIX pattern with lookahead/lookbehind to split at digit-letter boundary
  - Pattern: r'(?<=\d)(?=[a-zA-Z])|(?<=[a-zA-Z])(?=\d)'
    * (?<=\d)(?=[a-zA-Z]) - split after digit, before letter (5|kg)
    * (?<=[a-zA-Z])(?=\d) - split after letter, before digit (kg|5)
  - Result: "5kg" splits into ["5", "kg"], then "kg" is recognized as UNIT

This fix will make Test 1, 8, 13 pass by properly extracting units like "kg".
""")
