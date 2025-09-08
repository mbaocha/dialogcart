import os
import sys
import json
import argparse
from typing import Any, Dict

import requests


def call_unified_api(text: str, url: str, sender_id: str | None = None, route: str | None = None) -> Dict[str, Any]:
    payload = {"text": text}
    if sender_id:
        payload["sender_id"] = sender_id
    if route and route != "none":
        payload["route"] = route
    headers = {"Content-Type": "application/json"}
    resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=30)
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    parser = argparse.ArgumentParser(description="Test the unified intent API (Rasa → LLM fallback)")
    parser.add_argument("text", nargs="?", default=None, help="Text to classify. Omit to enter interactive mode.")
    parser.add_argument("--url", dest="url", default=os.getenv("UNIFIED_URL", "http://localhost:9000/classify"), help="Unified API classify endpoint URL")
    parser.add_argument("--sender-id", dest="sender_id", default=os.getenv("SENDER_ID", "tester"), help="Sender/session ID to pass through")
    parser.add_argument("--route", dest="route", choices=["rasa", "llm", "none"], default="none", help="Route to specific service: rasa, llm, or none (fallback)")
    parser.add_argument("-i", "--interactive", action="store_true", help="Interactive mode")
    args = parser.parse_args()

    def _print(data: Dict[str, Any]) -> None:
        result = (data or {}).get("result") or {}
        print(json.dumps(data, indent=2))
        print("\nSummary:")
        print(f"  source:            {result.get('source')}")
        sid = result.get('sender_id') or (result.get('slots') and result.get('sender_id'))
        if sid:
            print(f"  sender_id:         {sid}")
        
        # Always handle list of intents
        intents = result.get('intents', [])
        print(f"  intents:           {len(intents)} detected")
        for i, intent in enumerate(intents):
            print(f"    {i+1}. {intent.get('intent')} ({intent.get('confidence')})")
            print(f"       Entities: {intent.get('entities')}")

    def _split_text_and_sender(line: str, default_sender: str) -> tuple[str, str]:
        """Support inline sender id: 'message here@12345' → ("message here", "12345").
        Falls back to default_sender if no suffix present or suffix empty.
        """
        if "@" in line:
            msg, sid = line.rsplit("@", 1)
            msg = (msg or "").strip()
            sid = (sid or "").strip()
            if sid:
                return msg, sid
        return line.strip(), default_sender

    def _parse_inline_route(line: str) -> tuple[str, str | None]:
        """Parse inline route commands: 'message -i llm' → ("message", "llm").
        Returns (text, route) where route is None if no route specified.
        """
        # Look for route patterns: -i rasa, -i llm, -i none, --route rasa, etc.
        import re
        patterns = [
            r'\s+-i\s+(rasa|llm|none)\s*$',
            r'\s+--route\s+(rasa|llm|none)\s*$',
            r'\s+--r\s+(rasa|llm|none)\s*$'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, line)
            if match:
                route = match.group(1)
                text = re.sub(pattern, '', line).strip()
                return text, route
        
        return line, None

    if args.interactive or args.text is None:
        route_info = f" (route: {args.route})" if args.route != "none" else " (fallback)"
        print(f"Unified API interactive test{route_info}. Type 'exit' to quit.")
        print("Inline routing: 'message -i rasa', 'message -i llm', 'message -i none'")
        print()
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
            try:
                # Parse inline route commands
                text, inline_route = _parse_inline_route(line)
                if not text:
                    print("Empty message after parsing route command")
                    continue
                
                # Use inline route if specified, otherwise use default route
                current_route = inline_route if inline_route is not None else args.route
                
                msg, sid = _split_text_and_sender(text, args.sender_id)
                data = call_unified_api(msg, args.url, sender_id=sid, route=current_route)
                _print(data)
            except Exception as e:
                print(f"Request failed: {e}")
            print("\n" + "-" * 40 + "\n")
        return

    # one-shot
    try:
        text = args.text
        if text and "@" in text:
            text, sid = _split_text_and_sender(text, args.sender_id)
        else:
            sid = args.sender_id
        data = call_unified_api(text, args.url, sender_id=sid, route=args.route)
        _print(data)
    except Exception as e:
        print(f"Request failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()


