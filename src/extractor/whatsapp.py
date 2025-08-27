def extract_message(payload: dict) -> dict:
    try:
        change = payload["entry"][0]["changes"][0]["value"]
        message = change["messages"][0]
        contact = change.get("contacts", [{}])[0]

        msg_type = message.get("type")
        wa_id = message.get("from")
        name = contact.get("profile", {}).get("name", "")
        timestamp = message.get("timestamp")
        msg_id = message.get("id")

        # Initialize base response
        base = {
            "wa_id": wa_id,
            "name": name,
            "timestamp": timestamp,
            "message_id": msg_id,
            "type": msg_type,
            "content": None
        }

        # Type-specific extraction
        if msg_type == "text":
            base["content"] = message.get("text", {}).get("body")

        elif msg_type == "image":
            base["content"] = {
                "media_id": message.get("image", {}).get("id"),
                "caption": message.get("image", {}).get("caption"),
                "mime_type": message.get("image", {}).get("mime_type")
            }

        elif msg_type == "button":
            base["content"] = {
                "button_text": message.get("button", {}).get("text"),
                "button_payload": message.get("button", {}).get("payload")
            }

        elif msg_type == "interactive":
            interactive = message.get("interactive", {})
            itype = interactive.get("type")

            if itype == "button_reply":
                base["content"] = {
                    "interactive_type": "button_reply",
                    "reply_id": interactive["button_reply"].get("id"),
                    "reply_title": interactive["button_reply"].get("title")
                }
            elif itype == "list_reply":
                base["content"] = {
                    "interactive_type": "list_reply",
                    "reply_id": interactive["list_reply"].get("id"),
                    "reply_title": interactive["list_reply"].get("title")
                }
            else:
                base["content"] = {"interactive_type": itype}

        else:
            base["content"] = f"[Unsupported or unhandled type: {msg_type}]"

        return base

    except (KeyError, IndexError, TypeError) as e:
        return {
            "error": "Invalid or malformed WhatsApp payload",
            "exception": str(e)
        }
