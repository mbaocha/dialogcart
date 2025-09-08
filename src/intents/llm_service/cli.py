"""
CLI for LLM Service - Interactive testing (same behavior as original llm.py)
"""
from core.nlu_service import NLUService
from core.conversation_manager import ConversationManager
from core.config import CONFIG

def main():
    print("🛒 Grocery Assistant — Simple NLU + Deterministic Dialog")
    print("Type 'exit' to quit.\n")
    
    manager = ConversationManager(NLUService(), CONFIG)
    
    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 Goodbye!")
            break
        
        if user_input.lower() in {"exit", "quit"}:
            print("👋 Goodbye!")
            break
        
        lines = manager.handle(user_input)
        for line in lines:
            print(line)
        print("\n" + "-" * 40 + "\n")

if __name__ == "__main__":
    main()
