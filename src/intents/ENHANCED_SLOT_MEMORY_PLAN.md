# Enhanced Slot Memory Plan

## Current Issue
Slot memory is currently **only configured for `modify_cart` intent**, but other intents also use entities that should be remembered.

## Intents That Need Slot Memory

### 1. `inquire_product` Intent
**Current entities**: `product`, `action`
**Missing slot mappings**:
```yaml
last_inquired_product:
  type: text
  mappings:
  - type: from_entity
    entity: product
    intent: inquire_product

last_inquiry_type:
  type: text
  mappings:
  - type: from_entity
    entity: action
    intent: inquire_product
```

**Use cases**:
- "How much is rice?" → "What about beans?" (remember rice context)
- "Do you sell milk?" → "Is it available?" (remember milk context)

### 2. `cart_action` Intent  
**Current entities**: `container`, `action`
**Missing slot mappings**:
```yaml
last_cart_action:
  type: text
  mappings:
  - type: from_entity
    entity: action
    intent: cart_action

cart_state:
  type: categorical
  values:
  - empty
  - has_items
  - processing
  mappings:
  - type: custom
```

**Use cases**:
- "Show my cart" → "Clear it" (remember cart was viewed)
- "What's in my cart?" → "Remove everything" (remember cart inquiry)

### 3. `checkout` Intent
**Likely entities**: payment method, address, etc.
**Missing slot mappings**:
```yaml
payment_method:
  type: categorical
  values:
  - credit_card
  - debit_card
  - cash
  - mobile_money
  mappings:
  - type: from_text
    intent: checkout

delivery_address:
  type: text
  mappings:
  - type: from_text
    intent: checkout
```

### 4. `track_order` Intent
**Likely entities**: order_id, status, etc.
**Missing slot mappings**:
```yaml
last_order_id:
  type: text
  mappings:
  - type: from_text
    intent: track_order

tracking_status:
  type: categorical
  values:
  - pending
  - processing
  - shipped
  - delivered
  mappings:
  - type: custom
```

## Universal Slot Updates Needed

### 1. Expand Product Memory Across Intents
```yaml
last_product_added:
  type: text
  mappings:
  - type: from_entity
    entity: product
    intent: modify_cart
  - type: from_entity
    entity: product
    intent: inquire_product  # ← ADD THIS

shopping_list:
  type: list
  mappings:
  - type: from_entity
    entity: product
    intent: modify_cart
  - type: from_entity
    entity: product
    intent: inquire_product  # ← ADD THIS
```

### 2. Universal User Preference Tracking
```yaml
user_preference:
  type: categorical
  values:
  - vegetarian
  - vegan
  - omnivore
  - gluten_free
  - dairy_free
  mappings:
  - type: from_text
    intent: modify_cart
  - type: from_text
    intent: inquire_product  # ← ADD THIS
  - type: from_text
    intent: checkout  # ← ADD THIS
```

### 3. Cross-Intent Context Memory
```yaml
conversation_context:
  type: text
  mappings:
  - type: custom

last_mentioned_product:
  type: text
  mappings:
  - type: from_entity
    entity: product
    # No intent restriction - works across all intents
```

## Benefits of Enhanced Slot Memory

1. **Cross-Intent Context**: "How much is rice?" → "Add 2kg to cart" (remembers rice)
2. **Seamless Transitions**: "Show cart" → "Add more rice" (remembers cart context)  
3. **User Preference Persistence**: Dietary preferences remembered across all intents
4. **Order Continuity**: Checkout → Track Order (remembers order details)
5. **Natural Conversations**: Users can switch between intents naturally

## Implementation Priority

1. **High Priority**: Expand product memory to `inquire_product`
2. **Medium Priority**: Add cart action memory to `cart_action`
3. **Low Priority**: Add checkout and tracking memory
4. **Universal**: Remove intent restrictions from key slots

This would make the slot memory system truly conversational across all intents, not just `modify_cart`.
