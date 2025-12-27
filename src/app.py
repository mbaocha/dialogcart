import json
import os

from router import route_event


VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN")


def lambda_handler(event, context):
    print("Lambda invoked! Event:", json.dumps(event))
    try:
        method = event.get("httpMethod", "")

        # Handle webhook verification
        if method == "GET":
            params = event.get("queryStringParameters") or {}
            mode = params.get("hub.mode")
            token = params.get("hub.verify_token")
            challenge = params.get("hub.challenge")

            if mode == "subscribe" and token == VERIFY_TOKEN:
                return {"statusCode": 200, "body": challenge}
            else:
                return {"statusCode": 403, "body": "Forbidden"}

        # Handle incoming messages
        elif method == "POST":
            # body = json.loads(event.get("body", "{}"))
            return route_event(event)

        else:
            return {"statusCode": 405, "body": "Method Not Allowed"}

    except Exception as e:
        print("Error:", str(e))
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }
