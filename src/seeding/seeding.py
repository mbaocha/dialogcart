#!/usr/bin/env python3
"""
DialogCart DynamoDB Setup and Seeding Script

This script provides comprehensive DynamoDB table creation and data seeding functionality
for the DialogCart application. It can be used both as a standalone script and as a
reusable module.

FEATURES:
- Creates all required DynamoDB tables with proper indexes
- Seeds catalog with sample product data
- Supports environment-based configuration
- Robust error handling and validation
- Can be used programmatically or via command line

USAGE:
    # Command line - create tables and seed data
    python3 src/seeding/seeding.py
    
    # Command line - with custom settings
    AWS_REGION=us-east-1 TENANT_ID=my-tenant python3 src/seeding/seeding.py
    
    # Programmatic usage
    from seeding import ensure_tables_exist, seed_catalog_data
    ensure_tables_exist(["catalog", "carts"])
    seed_catalog_data()

ENVIRONMENT VARIABLES:
    AWS_REGION or AWS_DEFAULT_REGION: AWS region (default: eu-west-2)
    TENANT_ID: Tenant ID for seeding (default: demo-tenant-001)

TABLES CREATED:
    - catalog: Product/variant data with 4 GSIs (category, tag, collection, title search)
    - carts: Shopping cart data
    - cart_backups: Cart backup/restore functionality
    - orders: Order management with 2 GSIs (user, status)
    - payments: Payment tracking with 2 GSIs (order, status)
    - customer_addresses: Address management with 1 GSI (customer)
    - customers: Customer data
    - tenants: Multi-tenant configuration

SAMPLE DATA:
    The script includes sample products:
    - African bean (Grains) - £5.50/kg
    - Yam (Tubers) - £8.00/kg
    - Plantain (Fruits) - £3.00/bunch
    - Frozen Chicken (Frozen Food) - £120.00/box (10kg)

REQUIREMENTS:
    - AWS CLI configured with appropriate permissions
    - Python environment with boto3 installed
    - AWS credentials configured (via AWS CLI, environment variables, or IAM roles)

PERMISSIONS REQUIRED:
    - dynamodb:CreateTable
    - dynamodb:DescribeTable
    - dynamodb:PutItem
    - dynamodb:BatchWriteItem
"""

import os
import sys
try:
    import boto3  # type: ignore
except ImportError:  # pragma: no cover - dev env without boto3
    boto3 = None  # type: ignore
from decimal import Decimal
from datetime import datetime, timezone
from typing import List

# Configure region and table name (env overrides)
REGION = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "eu-west-2"
TENANT_ID = os.getenv("TENANT_ID") or "demo-tenant-001"

# Ensure boto3 is available
if boto3 is None:
    print("boto3 is required. Install with: pip install boto3")
    sys.exit(1)

# Initialize AWS clients
dynamodb_client = boto3.client("dynamodb", region_name=REGION)
dynamodb_resource = boto3.resource("dynamodb", region_name=REGION)

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def pk_tenant(tenant_id: str) -> str:
    return f"TENANT#{tenant_id}"

def sk_catalog(catalog_id: str) -> str:
    return f"CATALOG#{catalog_id}"

def sk_variant(variant_id: str) -> str:
    return f"VARIANT#{variant_id}"

def zero_pad_price(price: float, width: int = 11, decimals: int = 2) -> str:
    return f"{float(price):0{width}.{decimals}f}"

# Table definitions
TABLE_DEFINITIONS = {
    "catalog": {
        "AttributeDefinitions": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "GSI1PK", "AttributeType": "S"},
            {"AttributeName": "GSI1SK", "AttributeType": "S"},
            {"AttributeName": "GSI2PK", "AttributeType": "S"},
            {"AttributeName": "GSI2SK", "AttributeType": "S"},
            {"AttributeName": "GSI3PK", "AttributeType": "S"},
            {"AttributeName": "GSI3SK", "AttributeType": "S"},
            {"AttributeName": "GSI4PK", "AttributeType": "S"},
            {"AttributeName": "GSI4SK", "AttributeType": "S"}
        ],
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"}
        ],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": "GSI1_CategoryPrice",
                "KeySchema": [
                    {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI1SK", "KeyType": "RANGE"}
                ],
                "Projection": {"ProjectionType": "ALL"}
            },
            {
                "IndexName": "GSI2_TagPrice",
                "KeySchema": [
                    {"AttributeName": "GSI2PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI2SK", "KeyType": "RANGE"}
                ],
                "Projection": {"ProjectionType": "ALL"}
            },
            {
                "IndexName": "GSI3_CollectionPrice",
                "KeySchema": [
                    {"AttributeName": "GSI3PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI3SK", "KeyType": "RANGE"}
                ],
                "Projection": {"ProjectionType": "ALL"}
            },
            {
                "IndexName": "GSI4_TitlePrefix",
                "KeySchema": [
                    {"AttributeName": "GSI4PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI4SK", "KeyType": "RANGE"}
                ],
                "Projection": {"ProjectionType": "ALL"}
            }
        ]
    },
    "carts": {
        "AttributeDefinitions": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"}
        ],
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"}
        ]
    },
    "cart_backups": {
        "AttributeDefinitions": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"}
        ],
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"}
        ]
    },
    "orders": {
        "AttributeDefinitions": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "GSI1PK", "AttributeType": "S"},
            {"AttributeName": "GSI1SK", "AttributeType": "S"},
            {"AttributeName": "GSI2PK", "AttributeType": "S"},
            {"AttributeName": "GSI2SK", "AttributeType": "S"}
        ],
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"}
        ],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": "GSI1_UserOrders",
                "KeySchema": [
                    {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI1SK", "KeyType": "RANGE"}
                ],
                "Projection": {"ProjectionType": "ALL"}
            },
            {
                "IndexName": "GSI2_StatusOrders",
                "KeySchema": [
                    {"AttributeName": "GSI2PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI2SK", "KeyType": "RANGE"}
                ],
                "Projection": {"ProjectionType": "ALL"}
            }
        ]
    },
    "payments": {
        "AttributeDefinitions": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "GSI1PK", "AttributeType": "S"},
            {"AttributeName": "GSI1SK", "AttributeType": "S"},
            {"AttributeName": "GSI2PK", "AttributeType": "S"},
            {"AttributeName": "GSI2SK", "AttributeType": "S"}
        ],
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"}
        ],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": "GSI1_OrderPayments",
                "KeySchema": [
                    {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI1SK", "KeyType": "RANGE"}
                ],
                "Projection": {"ProjectionType": "ALL"}
            },
            {
                "IndexName": "GSI2_StatusPayments",
                "KeySchema": [
                    {"AttributeName": "GSI2PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI2SK", "KeyType": "RANGE"}
                ],
                "Projection": {"ProjectionType": "ALL"}
            }
        ]
    },
    "customer_addresses": {
        "AttributeDefinitions": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "GSI1PK", "AttributeType": "S"},
            {"AttributeName": "GSI1SK", "AttributeType": "S"}
        ],
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"}
        ],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": "GSI1_CustomerAddresses",
                "KeySchema": [
                    {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI1SK", "KeyType": "RANGE"}
                ],
                "Projection": {"ProjectionType": "ALL"}
            }
        ]
    },
    "customers": {
        "AttributeDefinitions": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "GSI1PK", "AttributeType": "S"},
            {"AttributeName": "GSI1SK", "AttributeType": "S"},
            {"AttributeName": "GSI2PK", "AttributeType": "S"},
            {"AttributeName": "GSI2SK", "AttributeType": "S"}
        ],
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"}
        ],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": "GSI1_EmailLookup",
                "KeySchema": [
                    {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI1SK", "KeyType": "RANGE"}
                ],
                "Projection": {"ProjectionType": "ALL"}
            },
            {
                "IndexName": "GSI2_PhoneLookup",
                "KeySchema": [
                    {"AttributeName": "GSI2PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI2SK", "KeyType": "RANGE"}
                ],
                "Projection": {"ProjectionType": "ALL"}
            }
        ]
    },
    "tenants": {
        "AttributeDefinitions": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"}
        ],
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"}
        ]
    }
}

def describe_table(table_name: str) -> dict:
    """Return table description or {} if not found."""
    try:
        return dynamodb_client.describe_table(TableName=table_name)
    except dynamodb_client.exceptions.ResourceNotFoundException:
        return {}

def get_existing_gsi_names(table_desc: dict) -> set:
    """Extract existing GSI names from a table description."""
    try:
        gsis = table_desc.get("Table", {}).get("GlobalSecondaryIndexes", [])
        return {g["IndexName"] for g in gsis}
    except Exception:
        return set()

def wait_for_gsis_active(table_name: str, index_names: List[str], timeout_seconds: int = 600) -> bool:
    """Poll until specified GSIs on a table are ACTIVE or timeout."""
    import time
    deadline = time.time() + timeout_seconds
    pending = set(index_names)
    while time.time() < deadline and pending:
        desc = describe_table(table_name)
        gsis = desc.get("Table", {}).get("GlobalSecondaryIndexes", [])
        status_by_name = {g["IndexName"]: g.get("IndexStatus") for g in gsis}
        pending = {n for n in pending if status_by_name.get(n) != "ACTIVE"}
        if not pending:
            return True
        time.sleep(5)
    return not pending

def wait_for_no_index_updates(table_name: str, timeout_seconds: int = 900) -> bool:
    """Wait until the table has no GSIs in CREATING/UPDATING/DELETING status."""
    import time
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        desc = describe_table(table_name)
        gsis = desc.get("Table", {}).get("GlobalSecondaryIndexes", [])
        busy = [g for g in gsis if g.get("IndexStatus") in {"CREATING", "UPDATING", "DELETING"}]
        if not busy:
            return True
        time.sleep(10)
    return False

def ensure_customers_gsis() -> bool:
    """Ensure required GSIs exist on the customers table, creating them if missing."""
    table_name = "customers"
    # Create phone GSI first to unblock agent init, then email GSI
    required_indexes_ordered = [
        (
            "GSI2_PhoneLookup",
            {
                "IndexName": "GSI2_PhoneLookup",
                "KeySchema": [
                    {"AttributeName": "GSI2PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI2SK", "KeyType": "RANGE"}
                ],
                "Projection": {"ProjectionType": "ALL"}
            },
            [
                {"AttributeName": "GSI2PK", "AttributeType": "S"},
                {"AttributeName": "GSI2SK", "AttributeType": "S"},
            ],
        ),
        (
            "GSI1_EmailLookup",
            {
                "IndexName": "GSI1_EmailLookup",
                "KeySchema": [
                    {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI1SK", "KeyType": "RANGE"}
                ],
                "Projection": {"ProjectionType": "ALL"}
            },
            [
                {"AttributeName": "GSI1PK", "AttributeType": "S"},
                {"AttributeName": "GSI1SK", "AttributeType": "S"},
            ],
        ),
    ]

    desc = describe_table(table_name)
    if not desc:
        return False
    existing = get_existing_gsi_names(desc)
    overall_success = True
    for idx_name, gsi_def, attr_defs in required_indexes_ordered:
        if idx_name in existing:
            continue
        print(f"Adding missing GSI to '{table_name}': {idx_name}")
        # Ensure table not busy with another index build
        if not wait_for_no_index_updates(table_name):
            print(f"Timeout waiting for table '{table_name}' to be free of GSI updates before creating {idx_name}")
            return False
        try:
            dynamodb_client.update_table(
                TableName=table_name,
                AttributeDefinitions=attr_defs,
                GlobalSecondaryIndexUpdates=[{"Create": gsi_def}],
            )
            print(f"UpdateTable initiated for GSI: {idx_name}")
        except dynamodb_client.exceptions.LimitExceededException:
            print(f"Limit exceeded when creating {idx_name}. Waiting for ongoing index operations to finish...")
            if not wait_for_no_index_updates(table_name):
                print(f"Timeout while waiting for index operations before retrying {idx_name}")
                return False
            # Retry once
            dynamodb_client.update_table(
                TableName=table_name,
                AttributeDefinitions=attr_defs,
                GlobalSecondaryIndexUpdates=[{"Create": gsi_def}],
            )
            print(f"Retry initiated for GSI: {idx_name}")
        except Exception as e:
            print(f"Error updating table '{table_name}' to add GSI {idx_name}: {e}")
            return False

        # Wait for this index to become ACTIVE before proceeding
        if wait_for_gsis_active(table_name, [idx_name]):
            print(f"GSI now ACTIVE on '{table_name}': {idx_name}")
        else:
            print(f"Timeout waiting for GSI to become ACTIVE on '{table_name}': {idx_name}")
            overall_success = False
            break

    return overall_success

def table_exists(table_name: str) -> bool:
    """Check if a DynamoDB table exists."""
    try:
        dynamodb_client.describe_table(TableName=table_name)
        return True
    except dynamodb_client.exceptions.ResourceNotFoundException:
        return False

def create_table(table_name: str) -> bool:
    """Create a DynamoDB table if it doesn't exist."""
    if table_exists(table_name):
        print(f"Table '{table_name}' already exists.")
        return True
    
    if table_name not in TABLE_DEFINITIONS:
        print(f"No definition found for table '{table_name}'.")
        return False
    
    print(f"Creating table '{table_name}'...")
    
    try:
        table_def = TABLE_DEFINITIONS[table_name].copy()
        table_def["TableName"] = table_name
        table_def["BillingMode"] = "PAY_PER_REQUEST"
        
        # Remove empty GlobalSecondaryIndexes if present
        if "GlobalSecondaryIndexes" in table_def and not table_def["GlobalSecondaryIndexes"]:
            del table_def["GlobalSecondaryIndexes"]
        
        dynamodb_client.create_table(**table_def)
        print(f"Table '{table_name}' creation initiated.")
        
        # Wait for table to be active
        waiter = dynamodb_client.get_waiter('table_exists')
        waiter.wait(TableName=table_name)
        print(f"Table '{table_name}' is now active.")
        return True
        
    except dynamodb_client.exceptions.ResourceInUseException:
        print(f"Table '{table_name}' already exists.")
        return True
    except Exception as e:
        print(f"Error creating table '{table_name}': {e}")
        return False

def ensure_tables_exist(table_names: List[str]) -> bool:
    """Ensure all specified tables exist, creating them if necessary."""
    success = True
    for table_name in table_names:
        if not create_table(table_name):
            success = False
            continue
        # If customers table exists or was created, ensure GSIs are present
        if table_name == "customers":
            gsi_ok = ensure_customers_gsis()
            success = success and gsi_ok
    return success

items = [
    {
        'catalog_id': 'african-bean-001',
        'title': 'African bean',
        'category_name': 'Grains',
        'category_emoji': '🌾',
        'product_emoji': '🫘',
        'status': 'disabled',
        'variants': [
            {
                'variant_id': 'african-bean-001-v1',
                'variant_title': 'Brown Beans / 5kg',
                'unit': 'kg',
                'price_num': Decimal('5.50'),
                'available_qty': Decimal('10'),
                'rules': {'min_order_qty': 1},
                'options': {
                    'Type': 'Brown Beans',
                    'Package': '5kg'
                }
            }
        ]
    },
    {
        'catalog_id': 'yam-001',
        'title': 'Yam',
        'category_name': 'Tubers',
        'category_emoji': '🥔',
        'product_emoji': '🍠',
        'status': 'enabled',
        'variants': [
            {
                'variant_id': 'yam-001-v1',
                'variant_title': 'White Yam / Whole',
                'unit': 'kg',
                'price_num': Decimal('8.00'),
                'available_qty': Decimal('10'),
                'rules': {'min_order_qty': 1},
                'options': {
                    'Type': 'White Yam',
                    'Cut': 'Whole'
                }
            },
            {
                'variant_id': 'yam-001-v2',
                'variant_title': 'White Yam / Sliced',
                'unit': 'kg',
                'price_num': Decimal('8.50'),
                'available_qty': Decimal('8'),
                'rules': {'min_order_qty': 1},
                'options': {
                    'Type': 'White Yam',
                    'Cut': 'Sliced'
                }
            }
        ]
    },
    {
        'catalog_id': 'plantain-001',
        'title': 'Plantain',
        'category_name': 'Fruits',
        'category_emoji': '🍎',
        'product_emoji': '🍌',
        'status': 'enabled',
        'variants': [
            {
                'variant_id': 'plantain-001-v1',
                'variant_title': 'Ripe Plantain / Bunch',
                'unit': 'bunch',
                'price_num': Decimal('3.00'),
                'available_qty': Decimal('10'),
                'rules': {'min_order_qty': 1},
                'options': {
                    'Ripeness': 'Ripe',
                    'Package': 'Bunch'
                }
            },
            {
                'variant_id': 'plantain-001-v2',
                'variant_title': 'Green Plantain / Bunch',
                'unit': 'bunch',
                'price_num': Decimal('2.50'),
                'available_qty': Decimal('12'),
                'rules': {'min_order_qty': 1},
                'options': {
                    'Ripeness': 'Green',
                    'Package': 'Bunch'
                }
            }
        ]
    },
    {
        'catalog_id': 'frozen-chicken-001',
        'title': 'Frozen Chicken',
        'category_name': 'Frozen Food',
        'category_emoji': '🧊',
        'product_emoji': '🍗',
        'status': 'enabled',
        'variants': [
            {
                'variant_id': 'frozen-chicken-001-v1',
                'variant_title': 'Whole Chicken / 10kg Box',
                'unit': 'box',
                'price_num': Decimal('120.00'),
                'available_qty': Decimal('12'),
                'rules': {'min_order_qty': 1},
                'package_size': {'value': Decimal('10'), 'unit': 'kg'},
                'options': {
                    'Cut': 'Whole',
                    'Package': '10kg Box'
                }
            },
            {
                'variant_id': 'frozen-chicken-001-v2',
                'variant_title': 'Chicken Pieces / 5kg Box',
                'unit': 'box',
                'price_num': Decimal('65.00'),
                'available_qty': Decimal('15'),
                'rules': {'min_order_qty': 1},
                'package_size': {'value': Decimal('5'), 'unit': 'kg'},
                'options': {
                    'Cut': 'Pieces',
                    'Package': '5kg Box'
                }
            }
        ]
    },
    {
        'catalog_id': 'egusi-001',
        'title': 'Egusi',
        'category_name': 'Seeds',
        'category_emoji': '🌱',
        'product_emoji': '🌰',
        'status': 'enabled',
        'variants': [
            {
                'variant_id': 'egusi-001-v1',
                'variant_title': 'Ground Egusi / 1kg',
                'unit': 'kg',
                'price_num': Decimal('12.00'),
                'available_qty': Decimal('10'),
                'rules': {'min_order_qty': 2},
                'options': {
                    'Preparation': 'Ground',
                    'Package': '1kg'
                }
            },
            {
                'variant_id': 'egusi-001-v2',
                'variant_title': 'Whole Egusi / 1kg',
                'unit': 'kg',
                'price_num': Decimal('10.00'),
                'available_qty': Decimal('8'),
                'rules': {'min_order_qty': 2},
                'options': {
                    'Preparation': 'Whole',
                    'Package': '1kg'
                }
            }
        ]
    },
    {
        'catalog_id': 'okra-001',
        'title': 'Okro',
        'category_name': 'Vegetables',
        'category_emoji': '🥬',
        'product_emoji': '🥒',
        'status': 'enabled',
        'variants': [
            {
                'variant_id': 'okra-001-v1',
                'variant_title': 'Fresh Okra / 1kg',
                'unit': 'kg',
                'price_num': Decimal('7.00'),
                'available_qty': Decimal('10'),
                'rules': {'min_order_qty': 1},
                'options': {
                    'Freshness': 'Fresh',
                    'Package': '1kg'
                }
            },
            {
                'variant_id': 'okra-001-v2',
                'variant_title': 'Dried Okra / 500g',
                'unit': 'kg',
                'price_num': Decimal('15.00'),
                'available_qty': Decimal('5'),
                'rules': {'min_order_qty': 1},
                'options': {
                    'Freshness': 'Dried',
                    'Package': '500g'
                }
            }
        ]
    },
    {
        'catalog_id': 'palm-oil-001',
        'title': 'Red oil',
        'category_name': 'Oils',
        'category_emoji': '🫗',
        'product_emoji': '🛢️',
        'status': 'enabled',
        'variants': [
            {
                'variant_id': 'palm-oil-001-v1',
                'variant_title': 'Red Palm Oil / 1L',
                'unit': 'litre',
                'price_num': Decimal('10.00'),
                'available_qty': Decimal('10'),
                'rules': {'min_order_qty': 1},
                'options': {
                    'Type': 'Red Palm Oil',
                    'Package': '1L'
                }
            },
            {
                'variant_id': 'palm-oil-001-v2',
                'variant_title': 'Red Palm Oil / 5L',
                'unit': 'litre',
                'price_num': Decimal('45.00'),
                'available_qty': Decimal('6'),
                'rules': {'min_order_qty': 1},
                'options': {
                    'Type': 'Red Palm Oil',
                    'Package': '5L'
                }
            }
        ]
    },
    {
        'catalog_id': 'dried-catfish-001',
        'title': 'Dried cat fish',
        'category_name': 'Seafood',
        'category_emoji': '🌊',
        'product_emoji': '🐟',
        'status': 'enabled',
        'variants': [
            {
                'variant_id': 'dried-catfish-001-v1',
                'variant_title': 'Small Pieces / 5kg',
                'unit': 'kg',
                'price_num': Decimal('15.00'),
                'available_qty': Decimal('10'),
                'rules': {'allowed_quantities': [5, 8, 15]},
                'options': {
                    'Cut': 'Small Pieces',
                    'Package': '5kg'
                }
            },
            {
                'variant_id': 'dried-catfish-001-v2',
                'variant_title': 'Whole Fish / 8kg',
                'unit': 'kg',
                'price_num': Decimal('14.00'),
                'available_qty': Decimal('8'),
                'rules': {'allowed_quantities': [8, 12, 16]},
                'options': {
                    'Cut': 'Whole',
                    'Package': '8kg'
                }
            }
        ]
    },
    {
        'catalog_id': 'ogbono-001',
        'title': 'Ogbono',
        'category_name': 'Seeds',
        'category_emoji': '🌱',
        'product_emoji': '🫘',
        'status': 'enabled',
        'variants': [
            {
                'variant_id': 'ogbono-001-v1',
                'variant_title': 'Ground Ogbono / 1kg',
                'unit': 'kg',
                'price_num': Decimal('9.50'),
                'available_qty': Decimal('10'),
                'rules': {'min_order_qty': 1},
                'options': {
                    'Preparation': 'Ground',
                    'Package': '1kg'
                }
            },
            {
                'variant_id': 'ogbono-001-v2',
                'variant_title': 'Whole Ogbono / 1kg',
                'unit': 'kg',
                'price_num': Decimal('8.00'),
                'available_qty': Decimal('12'),
                'rules': {'min_order_qty': 1},
                'options': {
                    'Preparation': 'Whole',
                    'Package': '1kg'
                }
            }
        ]
    },
    {
        'catalog_id': 'crayfish-001',
        'title': 'Crayfish',
        'category_name': 'Seafood',
        'category_emoji': '🌊',
        'product_emoji': '🦐',
        'status': 'enabled',
        'variants': [
            {
                'variant_id': 'crayfish-001-v1',
                'variant_title': 'Ground Crayfish / 1kg',
                'unit': 'kg',
                'price_num': Decimal('18.00'),
                'available_qty': Decimal('10'),
                'rules': {'min_order_qty': 1},
                'options': {
                    'Preparation': 'Ground',
                    'Package': '1kg'
                }
            },
            {
                'variant_id': 'crayfish-001-v2',
                'variant_title': 'Whole Crayfish / 1kg',
                'unit': 'kg',
                'price_num': Decimal('16.00'),
                'available_qty': Decimal('8'),
                'rules': {'min_order_qty': 1},
                'options': {
                    'Preparation': 'Whole',
                    'Package': '1kg'
                }
            }
        ]
    },
    {
        'catalog_id': 'stockfish-001',
        'title': 'Stockfish',
        'category_name': 'Seafood',
        'category_emoji': '🐟',
        'product_emoji': '🐟',
        'status': 'enabled',
        'variants': [
            {
                'variant_id': 'stockfish-001-v1',
                'variant_title': 'Whole Stockfish / 10kg Box',
                'unit': 'box',
                'price_num': Decimal('25.00'),
                'available_qty': Decimal('15'),
                'rules': {'min_order_qty': 1},
                'package_size': {
                    'value': Decimal('10'),
                    'unit': 'kg'
                },
                'options': {
                    'Cut': 'Whole',
                    'Package': '10kg Box'
                }
            },
            {
                'variant_id': 'stockfish-001-v2',
                'variant_title': 'Stockfish Pieces / 5kg Box',
                'unit': 'box',
                'price_num': Decimal('13.00'),
                'available_qty': Decimal('20'),
                'rules': {'min_order_qty': 1},
                'package_size': {
                    'value': Decimal('5'),
                    'unit': 'kg'
                },
                'options': {
                    'Cut': 'Pieces',
                    'Package': '5kg Box'
                }
            }
        ]
    },
    {
        'catalog_id': 'rice-001',
        'title': 'Rice',
        'category_name': 'Grains',
        'category_emoji': '🌾',
        'product_emoji': '🍚',
        'status': 'enabled',
        'variants': [
            {
                'variant_id': 'rice-001-v1',
                'variant_title': 'Long Grain Rice / 5kg',
                'unit': 'kg',
                'price_num': Decimal('6.50'),
                'available_qty': Decimal('20'),
                'rules': {'min_order_qty': 1},
                'options': {
                    'Type': 'Long Grain',
                    'Package': '5kg'
                }
            },
            {
                'variant_id': 'rice-001-v2',
                'variant_title': 'Basmati Rice / 5kg',
                'unit': 'kg',
                'price_num': Decimal('8.50'),
                'available_qty': Decimal('15'),
                'rules': {'min_order_qty': 1},
                'options': {
                    'Type': 'Basmati',
                    'Package': '5kg'
                }
            },
            {
                'variant_id': 'rice-001-v3',
                'variant_title': 'Jasmine Rice / 5kg',
                'unit': 'kg',
                'price_num': Decimal('7.50'),
                'available_qty': Decimal('18'),
                'rules': {'min_order_qty': 1},
                'options': {
                    'Type': 'Jasmine',
                    'Package': '5kg'
                }
            }
        ]
    }
]

def seed_catalog_data():
    """Seed the catalog table with product data."""
    catalog_table = dynamodb_resource.Table("catalog")
    
    with catalog_table.batch_writer() as batch:
        for p in items:
            # Insert PRODUCT
            product_item = {
                "PK": pk_tenant(TENANT_ID),
                "SK": sk_catalog(p["catalog_id"]),
                "entity": "CATALOG",
                "tenant_id": TENANT_ID,
                "catalog_id": p["catalog_id"],
                "title": p["title"],
                "category_name": p.get("category_name"),
                "status": p.get("status", "active"),
                "updated_at": now_iso(),
            }
            batch.put_item(Item=product_item)
            print(f"Inserted PRODUCT {p['title']}")

            # Insert VARIANTS
            for v in p["variants"]:
                variant_item = {
                    "PK": pk_tenant(TENANT_ID),
                    "SK": sk_variant(v["variant_id"]),
                    "entity": "VARIANT",
                    "tenant_id": TENANT_ID,
                    "catalog_id": p["catalog_id"],
                    "variant_id": v["variant_id"],
                    "variant_title": v["variant_title"],
                    "title": p["title"],  # shared
                    "unit": v["unit"],
                    "price_num": v["price_num"],
                    "price_sort": zero_pad_price(v["price_num"]),
                    "available_qty": v["available_qty"],
                    "in_stock": v["available_qty"] > 0,
                    "rules": v.get("rules"),
                    "package_size": v.get("package_size"),
                    "options": v.get("options", {}),  # Add options field
                    "updated_at": now_iso(),
                    "GSI4PK": f"TENANT#{TENANT_ID}#TITLE",
                    "GSI4SK": p["title"].lower()
                }
                # Denormalize category fields into variant for browse GSIs
                if p.get("category_name"):
                    variant_item["category_name"] = p["category_name"]
                if p.get("category_emoji"):
                    variant_item["category_emoji"] = p["category_emoji"]
                batch.put_item(Item=variant_item)
                print(f"Inserted VARIANT {v['variant_id']} of {p['title']}")

def verify_data():
    """Verify that data was inserted correctly."""
    try:
        catalog_table = dynamodb_resource.Table("catalog")
        response = catalog_table.scan(Select="COUNT")
        item_count = response.get("Count", 0)
        print(f"✓ Catalog table contains {item_count} items")
        return True
    except Exception as e:
        print(f"✗ Failed to verify data: {e}")
        return False

def example_usage():
    """Example of programmatic usage - create specific tables and seed data."""
    print("\n=== Example: Programmatic Usage ===")
    
    # Create only specific tables
    print("1. Creating core tables...")
    core_tables = ["catalog", "carts", "orders"]
    if ensure_tables_exist(core_tables):
        print("✓ Core tables created successfully")
    else:
        print("✗ Failed to create some core tables")
        return False
    
    # Seed catalog data
    print("\n2. Seeding catalog data...")
    try:
        seed_catalog_data()
        print("✓ Catalog data seeded successfully")
    except Exception as e:
        print(f"✗ Failed to seed catalog data: {e}")
        return False
    
    # Verify data was inserted
    print("\n3. Verifying data...")
    if verify_data():
        print("✓ Data verification successful")
        return True
    else:
        return False

def main():
    """Main function to create tables and seed data."""
    print(f"Using AWS Region: {REGION}")
    print(f"Using Tenant ID: {TENANT_ID}")
    
    # Define which tables to create
    required_tables = ["catalog", "carts", "cart_backups", "orders", "payments", 
                      "customer_addresses", "customers", "tenants"]
    
    # Create tables if they don't exist
    print("\n=== Creating DynamoDB Tables ===")
    if not ensure_tables_exist(required_tables):
        print("Failed to create some tables. Exiting.")
        sys.exit(1)
    
    print("\n=== Seeding Catalog Data ===")
    seed_catalog_data()
    
    print("\n=== Verifying Data ===")
    verify_data()
    
    print("\n=== Seeding Complete ===")
    print("All tables created and catalog data seeded successfully!")
    
    # Show example usage
    print("\n" + "="*60)
    print("PROGRAMMATIC USAGE EXAMPLES:")
    print("="*60)
    print("""
# Import the functions
from seeding import ensure_tables_exist, seed_catalog_data, verify_data

# Create specific tables only
ensure_tables_exist(["catalog", "carts"])

# Seed catalog data
seed_catalog_data()

# Verify data was inserted
verify_data()

# Create all tables (default behavior)
ensure_tables_exist(["catalog", "carts", "cart_backups", "orders", 
                    "payments", "customer_addresses", "customers", "tenants"])
""")

if __name__ == "__main__":
    main()
