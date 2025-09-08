import os
import sys
import json
import argparse
from typing import Any, Dict
import requests

RASA_URL = os.getenv("RASA_URL", "http://localhost:8000/")

def call_rasa_predict(text: str, sender_id: str) -> Dict[str, Any]:
    payload = {"action": "predict", "text": text, "sender_id": sender_id}
    resp = requests.post(RASA_URL, headers={"Content-Type": "application/json"}, data=json.dumps(payload), timeout=30)
    resp.raise_for_status()
    return resp.json()

def call_slots_get(sender_id: str) -> Dict[str, Any]:
    resp = requests.post(RASA_URL.replace("/", "") + "/slots/get", headers={"Content-Type": "application/json"},
                         data=json.dumps({"sender_id": sender_id}), timeout=10)
    resp.raise_for_status()
    return resp.json()

def call_slots_clear(sender_id: str) -> Dict[str, Any]:
    resp = requests.post(RASA_URL.replace("/", "") + "/slots/clear", headers={"Content-Type": "application/json"},
                         data=json.dumps({"sender_id": sender_id}), timeout=10)
    resp.raise_for_status()
    return resp.json()

def split_text_and_sender(line: str, default_sender: str) -> tuple[str, str]:
    if "@" in line:
        msg, sid = line.rsplit("@", 1)
        msg = (msg or "").strip()
        sid = (sid or "").strip()
        if sid:
            return msg, sid
    return line.strip(), default_sender

def pretty_print(resp: Dict[str, Any]) -> None:
    # Remove text_tokens from response before printing
    filtered_resp = resp.copy()
    if "nlu" in filtered_resp and isinstance(filtered_resp["nlu"], dict):
        nlu_copy = filtered_resp["nlu"].copy()
        nlu_copy.pop("text_tokens", None)
        filtered_resp["nlu"] = nlu_copy
    
    print(json.dumps(filtered_resp, indent=2))
    nlu = resp.get("nlu") or {}
    print("\nSummary:")
    intent = (nlu.get("intent") or {}).get("name")
    conf = (nlu.get("intent") or {}).get("confidence")
    print(f"  intent:     {intent} (confidence={conf})")
    print(f"  entities:   {nlu.get('entities')}")
    slots = (resp.get("slots") or {})
    if slots:
        print(f"  slots:      {slots}")
    if resp.get("sender_id"):
        print(f"  sender_id:  {resp.get('sender_id')}")

def call_rasa_train(intent: str, examples: list[str]) -> Dict[str, Any]:
    """Trigger Rasa retraining with provided intent and examples."""
    try:
        payload = {"action": "train", "intent": intent, "examples": examples}
        resp = requests.post(RASA_URL, headers={"Content-Type": "application/json"},
                           data=json.dumps(payload), timeout=900)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e)}

def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive tester for Rasa NLU service (/ action=predict)")
    parser.add_argument("--url", default=os.getenv("RASA_URL", "http://localhost:8000/"), help="Rasa service URL (root, not /classify)")
    parser.add_argument("--sender-id", default=os.getenv("SENDER_ID", "tester"), help="Default sender ID")
    parser.add_argument("-i", "--interactive", action="store_true", help="Interactive mode")
    args = parser.parse_args()

    global RASA_URL
    RASA_URL = args.url

    # Always run in interactive mode for multiple sentences
    print("Rasa interactive test. Type 'exit' to quit.")
    print("Inline sender: 'message @sender'. Commands: ':slots', ':clear', ':train [example]', ':help'")
    print("=" * 60)
    
    while True:
        try:
            line = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye")
            break
        if not line:
            continue
        if line.lower() in {"exit", "quit"}:
            print("Bye")
            break
        if line.startswith(":"):
            cmd = line[1:].strip()
            if cmd == "help":
                print("Commands:")
                print("  :slots             - Show current slots")
                print("  :clear             - Clear current slots")
                print("  :train [example]   - Retrain Rasa model with optional example")
                print("  :help              - Show this help")
                continue
            if cmd == "slots":
                print(json.dumps(call_slots_get(args.sender_id), indent=2))
                continue
            if cmd == "clear":
                print(json.dumps(call_slots_clear(args.sender_id), indent=2))
                continue
            if cmd.startswith("train"):
                # Optional inline example after ':train'
                example_text = cmd[len("train"):].strip()
                if example_text:
                    examples = [example_text]
                else:
                    # Default multi-entity examples to reinforce training
                    examples = [
                        "add 3kg rice and 5kg beans to cart",
                        "add 2 kg yam, 1 kg sugar and 2 bottles of oil",
                        "put 4 kg rice and 2 kg beans in basket",
                    ]
                print("Training Rasa model... (this may take a few minutes)")
                result = call_rasa_train("ADD_TO_CART", examples)
                print(json.dumps(result, indent=2))
                continue
            print("Unknown command. Try :help")
            continue
        try:
            text, sid = split_text_and_sender(line, args.sender_id)
            resp = call_rasa_predict(text, sid)
            pretty_print(resp)
        except Exception as e:
            print(f"Request failed: {e}")
        print("\n" + "-" * 40 + "\n")

if __name__ == "__main__":
    main()