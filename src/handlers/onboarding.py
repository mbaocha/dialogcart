import json
import os
import requests

def handle_onboarding(extracted_data):
    """Handle onboarding for new users using extracted message data only."""
    sender_id = extracted_data.get("wa_id")
    message_content = extracted_data.get("content")
    user_name = extracted_data.get("name", "")

    if not sender_id or not message_content:
        return {"statusCode": 400, "body": json.dumps({"error": "Missing user or message data"})}

    text = message_content.lower().strip()
    greetings = {"hi", "hello", "hey", "good morning", "good afternoon", "good evening"}
    if text in greetings:
        reply = (
            "Hi there! 👋 Welcome to Bulkpot — your trusted partner for wholesale and food trading. "
            "I'm Ella, your virtual assistant.\n\nLet's get you set up in a few quick steps. What's your name?"
        )
    else:
        reply = (
            "Sorry, I didn't quite catch that. Type 'hi' to get started with Ella, your Bulkpot assistant. 😊"
        )

    send_whatsapp_message(to=sender_id, message=reply)
    return {"statusCode": 200, "body": json.dumps({"message": "Processed onboarding message"})}


WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")

def send_whatsapp_message(to, message):
    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message}
    }
    response = requests.post(url, headers=headers, json=payload)
    print("WhatsApp API response:", response.status_code, response.text)
    return response.status_code == 200
