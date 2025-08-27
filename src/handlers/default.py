import json

def handle_default(data):
    return {
        'statusCode': 200,
        'body': json.dumps({'message': 'Unhandled event type'})
    }
