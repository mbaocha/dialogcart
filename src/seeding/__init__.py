"""
Seeding package for DialogCart database setup and data seeding.

This package contains utilities for:
- Creating DynamoDB tables
- Seeding catalog data
- Loading global entities
"""

from .seeding import (
    ensure_tables_exist,
    seed_catalog_data,
    verify_data,
    create_table,
    table_exists
)

from .seed_global_entities import main as seed_global_entities

__all__ = [
    'ensure_tables_exist',
    'seed_catalog_data', 
    'verify_data',
    'create_table',
    'table_exists',
    'seed_global_entities'
]
