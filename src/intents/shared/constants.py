INTENTS = [
    "SHOW_PRODUCT_LIST",
    "VIEW_CART",
    "ADD_TO_CART",
    "REMOVE_FROM_CART",
    "CLEAR_CART",
    "CHECK_PRODUCT_EXISTENCE",
    "RESTORE_CART",
    "UPDATE_CART_QUANTITY",
    "NONE",
]

REQUIRED_SLOTS = {
    "ADD_TO_CART": ["product", "quantity", "unit"],
    "REMOVE_FROM_CART": ["product"],
    "CHECK_PRODUCT_EXISTENCE": ["product"],
    "UPDATE_CART_QUANTITY": ["product", "quantity"],
}

CONF_RANK = {"low": 0, "medium": 1, "high": 2}


