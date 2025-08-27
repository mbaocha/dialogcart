import boto3
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table("b_users")

class UserModel:
    @staticmethod
    def put(user: dict):
        table.put_item(Item=user)

    @staticmethod
    def get(phone: str) -> dict:
        try:
            response = table.get_item(Key={"phone": phone})
            return response.get("Item")
        except ClientError as e:
            print("DynamoDB get error:", e.response["Error"]["Message"])
            return None
