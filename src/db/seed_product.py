import boto3
from decimal import Decimal

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table('b_products')


items = [
    {
        'id': 'african-bean-001',
        'name': 'African bean',
        'category': 'Grains',
        'category_emoji': '🌾',
        'product_emoji': '🫘',
        'status': 'disabled',
        'unit': 'kg',
        'price': Decimal('5.50'),
        'allowed_quantities': {'min': 1},
        'available_quantity': Decimal('10')
    },
    {
        'id': 'yam-001',
        'name': 'Yam',
        'category': 'Tubers',
        'category_emoji': '🥔',
        'product_emoji': '🍠',
        'status': 'enabled',
        'unit': 'kg',
        'price': Decimal('8.00'),
        'allowed_quantities': {'min': 1},
        'available_quantity': Decimal('10')
    },
    {
        'id': 'plantain-001',
        'name': 'Plantain',
        'category': 'Fruits',
        'category_emoji': '🍎',
        'product_emoji': '🍌',
        'status': 'enabled',
        'unit': 'bunch',
        'price': Decimal('3.00'),
        'allowed_quantities': {'min': 1},
        'available_quantity': Decimal('10')
    },
    {
        'id': 'egusi-001',
        'name': 'Egusi',
        'category': 'Seeds',
        'category_emoji': '🌱',
        'product_emoji': '🌰',
        'status': 'enabled',
        'unit': 'kg',
        'price': Decimal('12.00'),
        'allowed_quantities': {'min': 2},
        'available_quantity': Decimal('10')
    },
    {
        'id': 'okra-001',
        'name': 'Okro',
        'category': 'Vegetables',
        'category_emoji': '🥬',
        'product_emoji': '🥒',
        'status': 'enabled',
        'unit': 'kg',
        'price': Decimal('7.00'),
        'allowed_quantities': {'min': 1},
        'available_quantity': Decimal('10')
    },
    {
        'id': 'palm-oil-001',
        'name': 'Red oil',
        'category': 'Oils',
        'category_emoji': '🫗',
        'product_emoji': '🛢️',
        'status': 'enabled',
        'unit': 'litre',
        'price': Decimal('10.00'),
        'allowed_quantities': {'min': 1},
        'available_quantity': Decimal('10')
    },
    {
        'id': 'dried-catfish-001',
        'name': 'Dried cat fish',
        'category': 'Seafood',
        'category_emoji': '🌊',
        'product_emoji': '🐟',
        'status': 'enabled',
        'unit': 'kg',
        'price': Decimal('15.00'),
        'allowed_quantities': [5, 8, 15],
        'available_quantity': Decimal('10')
    },
    {
        'id': 'ogbono-001',
        'name': 'Ogbono',
        'category': 'Seeds',
        'category_emoji': '🌱',
        'product_emoji': '🫘',
        'status': 'enabled',
        'unit': 'kg',
        'price': Decimal('9.50'),
        'allowed_quantities': {'min': 1},
        'available_quantity': Decimal('10')
    },
    {
        'id': 'crayfish-001',
        'name': 'Crayfish',
        'category': 'Seafood',
        'category_emoji': '🌊',
        'product_emoji': '🦐',
        'status': 'enabled',
        'unit': 'kg',
        'price': Decimal('18.00'),
        'allowed_quantities': {'min': 1},
        'available_quantity': Decimal('10')
    },
    {
        'id': 'frozen-chicken-001',
        'name': 'Frozen Chicken',
        'category': 'Frozen Food',
        'category_emoji': '🧊',     # pick any you like (❄️, 🧊)
        'product_emoji': '🍗',
        'status': 'enabled',
        'unit': 'box',              # priced per box
        'price': Decimal('120.00'), # price per box
        'allowed_quantities': {'min': 1},  # user can buy 1+ boxes
        'available_quantity': Decimal('12'),  # 12 boxes in stock
        # tells the formatter to show "10 kg per box"
        'package_size': {
            'value': Decimal('10'),
            'unit': 'kg'
        }
    },
    {
        'id': 'stockfish-001',
        'name': 'Stockfish',
        'category': 'Seafood',
        'category_emoji': '🐟',
        'product_emoji': '🐟',
        'status': 'enabled',
        'unit': 'box',
        'price': Decimal('25.00'),
        'allowed_quantities': {'min': 1},
        'available_quantity': Decimal('15'),
        'package_size': {
            'value': Decimal('10'),
            'unit': 'kg'
        }
    }
]



with table.batch_writer() as batch:
    for item in items:
        batch.put_item(Item=item)
        print(f"Inserted {item['name']}")
