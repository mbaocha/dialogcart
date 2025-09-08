"""
CLI for Unified API - Interactive testing of Rasa + LLM fallback
"""
from orchestrator import classify

def main():
    print("🔗 Unified Classifier (Rasa → LLM fallback)")
    print("Type 'exit' to quit.\n")
    
    while True:
        try:
            text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 Goodbye!")
            break
        
        if text.lower() in {"exit", "quit"}:
            print("👋 Goodbye!")
            break
        
        result = classify(text)
        print(f"Source: {result['source']}")
        print(f"Intent: {result['intent_meta']['intent']}")
        print(f"Confidence: {result['intent_meta']['confidence']}")
        print(f"Entities: {result['intent_meta']['entities']}")
        print("\n" + "-" * 40 + "\n")

if __name__ == "__main__":
    main()
