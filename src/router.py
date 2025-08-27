import json
from extractor.whatsapp import extract_message
from intents.classifer import get_intent
from db import get_user, user_exists


def route_event(event):
    """Route WhatsApp events based on intent and user registration status."""
    try:
        body = json.loads(event.get("body", "{}"))
        
        # Extract WhatsApp message using extractor
        extracted_data = extract_message(body)
        
        if "error" in extracted_data:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': extracted_data['error']})
            }
        
        # Get message content and user phone
        message_content = extracted_data.get("content")
        user_phone = extracted_data.get("wa_id")
        
        if not message_content or not user_phone:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Missing message content or user phone'})
            }
        
        # Determine intent using classifier
        intents = get_intent(message_content)
        primary_intent = intents[0] if intents else "other"
        
        # Check if user is already registered
        user = get_user(user_phone)
        is_registered = user is not None
        
        # Log the message for debugging
        print(f"User: {user_phone}")
        print(f"Message: {message_content}")
        print(f"Intents: {intents}")
        print(f"Registered: {is_registered}")
        
        # Create and print dictionary with extracted message, intent, and user registration status
        result = {
            "extracted_message": extracted_data,
            "intent": intents,
            "user_registered": is_registered,
            "user_details": user if is_registered else None
        }
        
     
        
        return {
            'statusCode': 200,
            'body': json.dumps(result)
        }
                
    except Exception as e:
        print(f"Error in route_event: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }


