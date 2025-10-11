import spacy
from pathlib import Path

MODEL_DIR = Path("spacy_model")

def main():
    # Load the saved model
    nlp = spacy.load(MODEL_DIR)
    print(f"✅ Loaded model from {MODEL_DIR}")
    print("\nType a sentence to test (or 'exit' to quit):\n")

    while True:
        text = input("You: ").strip()
        if text.lower() in {"exit", "quit"}:
            print("👋 Bye!")
            break

        doc = nlp(text)
        if doc.ents:
            print("Entities:")
            for ent in doc.ents:
                print(f"  {ent.text:<15} {ent.label_}")
        else:
            print("⚠️ No entities found.")

if __name__ == "__main__":
    main()
