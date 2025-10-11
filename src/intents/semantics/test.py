from transformers import AutoTokenizer
tok = AutoTokenizer.from_pretrained("microsoft/MiniLM-L12-H384-uncased")
tok.add_tokens(["producttoken", "brandtoken", "varianttoken", "unittoken", "quantitytoken"])

s = "do you sell producttoken from brandtoken"
print(tok.tokenize(s))
