examples = [
    # --- ADD ---
    {
        "sentence": "Add 3 bags of ricce please.",
        "response": {
            "intent": "add", "action": "add", "brand": None, "product": "rice",
            "tokens": [], "quantity": 3, "unit": "bag",
            "confidence": 1.0, "product_validated": True, "product_validation_method": "fuzzy",
            "brand_validated": False, "brand_validation_method": None
        }
    },
    {
        "sentence": "Add 2 large imported bags of Ricce from Golden Penny.",
        "response": {
            "intent": "add", "action": "add", "brand": "golden penny", "product": "rice",
            "tokens": ["large", "imported"], "quantity": 2, "unit": "bag",
            "confidence": 1.0, "product_validated": True, "product_validation_method": "fuzzy",
            "brand_validated": True, "brand_validation_method": "exact"
        }
    },
    {
        "sentence": "Add one small packet of Milo chocolate drink.",
        "response": {
            "intent": "add", "action": "add", "brand": "milo", "product": "chocolate drink",
            "tokens": ["small"], "quantity": 1, "unit": "packet",
            "confidence": 1.0, "product_validated": True, "product_validation_method": "exact",
            "brand_validated": True, "brand_validation_method": "exact"
        }
    },
    {
        "sentence": "Please add 6 bottles of cold Coka Cola soda.",
        "response": {
            "intent": "add", "action": "add", "brand": "coca cola", "product": "soda",
            "tokens": ["cold"], "quantity": 6, "unit": "bottle",
            "confidence": 1.0, "product_validated": True, "product_validation_method": "exact",
            "brand_validated": True, "brand_validation_method": "fuzzy"
        }
    },
    {
        "sentence": "Add 5 imported Peak milk tins.",
        "response": {
            "intent": "add", "action": "add", "brand": "peak", "product": "milk",
            "tokens": ["imported"], "quantity": 5, "unit": "tin",
            "confidence": 1.0, "product_validated": True, "product_validation_method": "exact",
            "brand_validated": True, "brand_validation_method": "exact"
        }
    },

    # --- REMOVE ---
    {
        "sentence": "Please remove two cartons of palm oil.",
        "response": {
            "intent": "remove", "action": "remove", "brand": None, "product": "palm oil",
            "tokens": [], "quantity": 2, "unit": "carton",
            "confidence": 1.0, "product_validated": True, "product_validation_method": "exact",
            "brand_validated": False, "brand_validation_method": None
        }
    },
    {
        "sentence": "Remove 1 packet of crayfsh.",
        "response": {
            "intent": "remove", "action": "remove", "brand": None, "product": "crayfish",
            "tokens": [], "quantity": 1, "unit": "packet",
            "confidence": 1.0, "product_validated": True, "product_validation_method": "fuzzy",
            "brand_validated": False, "brand_validation_method": None
        }
    },
    {
        "sentence": "Remove 3 large imported yam tubers.",
        "response": {
            "intent": "remove", "action": "remove", "brand": None, "product": "yam tuber",
            "tokens": ["large", "imported"], "quantity": 3, "unit": "tuber",
            "confidence": 1.0, "product_validated": True, "product_validation_method": "exact",
            "brand_validated": False, "brand_validation_method": None
        }
    },
    {
        "sentence": "Remove 5 packs of imported swettened Peak milk powder.",
        "response": {
            "intent": "remove", "action": "remove", "brand": "peak", "product": "milk powder",
            "tokens": ["imported", "sweetened"], "quantity": 5, "unit": "pack",
            "confidence": 1.0, "product_validated": True, "product_validation_method": "fuzzy",
            "brand_validated": True, "brand_validation_method": "exact"
        }
    },
    {
        "sentence": "Remove 2 cold bottles of Fantaa.",
        "response": {
            "intent": "remove", "action": "remove", "brand": "fanta", "product": "bottle drink",
            "tokens": ["cold"], "quantity": 2, "unit": "bottle",
            "confidence": 1.0, "product_validated": True, "product_validation_method": "exact",
            "brand_validated": True, "brand_validation_method": "fuzzy"
        }
    },

    # --- SET ---
    {
        "sentence": "Set my rice to 7kg.",
        "response": {
            "intent": "set", "action": "set", "brand": None, "product": "rice",
            "tokens": [], "quantity": 7, "unit": "kg",
            "confidence": 1.0, "product_validated": True, "product_validation_method": "exact",
            "brand_validated": False, "brand_validation_method": None
        }
    },
    {
        "sentence": "Set 2 small sachets of Dangote sugar.",
        "response": {
            "intent": "set", "action": "set", "brand": "dangote", "product": "sugar",
            "tokens": ["small"], "quantity": 2, "unit": "sachet",
            "confidence": 1.0, "product_validated": True, "product_validation_method": "exact",
            "brand_validated": True, "brand_validation_method": "exact"
        }
    },
    {
        "sentence": "Set my small red Nikie running shoe to 1 pair.",
        "response": {
            "intent": "set", "action": "set", "brand": "nike", "product": "running shoe",
            "tokens": ["small", "red"], "quantity": 1, "unit": "pair",
            "confidence": 1.0, "product_validated": True, "product_validation_method": "exact",
            "brand_validated": True, "brand_validation_method": "fuzzy"
        }
    },
    {
        "sentence": "Set garri flour to 10kg.",
        "response": {
            "intent": "set", "action": "set", "brand": None, "product": "garri flour",
            "tokens": [], "quantity": 10, "unit": "kg",
            "confidence": 1.0, "product_validated": True, "product_validation_method": "exact",
            "brand_validated": False, "brand_validation_method": None
        }
    },
    {
        "sentence": "Set Indomie noodles to 5 cartons.",
        "response": {
            "intent": "set", "action": "set", "brand": "indomie", "product": "noodles",
            "tokens": [], "quantity": 5, "unit": "carton",
            "confidence": 1.0, "product_validated": True, "product_validation_method": "exact",
            "brand_validated": True, "brand_validation_method": "exact"
        }
    },

    # --- CHECK ---
    {
        "sentence": "Do you have garri in stock?",
        "response": {
            "intent": "check", "action": "check", "brand": None, "product": "garri",
            "tokens": [], "quantity": None, "unit": None,
            "confidence": 1.0, "product_validated": True, "product_validation_method": "exact",
            "brand_validated": False, "brand_validation_method": None
        }
    },
    {
        "sentence": "Do you sell red Nike shoes?",
        "response": {
            "intent": "check", "action": "check", "brand": "nike", "product": "shoe",
            "tokens": ["red"], "quantity": None, "unit": None,
            "confidence": 1.0, "product_validated": True, "product_validation_method": "exact",
            "brand_validated": True, "brand_validation_method": "exact"
        }
    },
    {
        "sentence": "Do you stock fresh organic yam tubers from Dangote?",
        "response": {
            "intent": "check", "action": "check", "brand": "dangote", "product": "yam tuber",
            "tokens": ["fresh", "organic"], "quantity": None, "unit": None,
            "confidence": 1.0, "product_validated": True, "product_validation_method": "exact",
            "brand_validated": True, "brand_validation_method": "exact"
        }
    },
    {
        "sentence": "Do you have cold Fantaa drinks?",
        "response": {
            "intent": "check", "action": "check", "brand": "fanta", "product": "drink",
            "tokens": ["cold"], "quantity": None, "unit": None,
            "confidence": 1.0, "product_validated": True, "product_validation_method": "exact",
            "brand_validated": True, "brand_validation_method": "fuzzy"
        }
    },
    {
        "sentence": "Can I get large imported Peak milk powder?",
        "response": {
            "intent": "check", "action": "check", "brand": "peak", "product": "milk powder",
            "tokens": ["large", "imported"], "quantity": None, "unit": None,
            "confidence": 1.0, "product_validated": True, "product_validation_method": "exact",
            "brand_validated": True, "brand_validation_method": "exact"
        }
    },

    # --- MULTI-ACTION ---
    {
        "sentence": "Remove 2 bags of rice and add 5kg beans.",
        "response": {
            "intent": "modify_cart", "action": None,  # handled as multi-action
            "brand": None, "product": None,
            "tokens": [], "quantity": None, "unit": None,
            "confidence": 1.0, "product_validated": True, "product_validation_method": "exact",
            "brand_validated": False, "brand_validation_method": None
        }
    },
    {
        "sentence": "Remove 3 cartons of Indomie and add 2 packs of Milo.",
        "response": {
            "intent": "modify_cart", "action": None,
            "brand": None, "product": None,
            "tokens": [], "quantity": None, "unit": None,
            "confidence": 1.0, "product_validated": True, "product_validation_method": "exact",
            "brand_validated": True, "brand_validation_method": "exact"
        }
    },
    {
        "sentence": "Take out 1 bag of flour and put in 4 tins of Peak milk.",
        "response": {
            "intent": "modify_cart", "action": None,
            "brand": None, "product": None,
            "tokens": [], "quantity": None, "unit": None,
            "confidence": 1.0, "product_validated": True, "product_validation_method": "exact",
            "brand_validated": True, "brand_validation_method": "exact"
        }
    },
    {
        "sentence": "Drop 2 bottles of Coka Cola and include 1 carton of noodles.",
        "response": {
            "intent": "modify_cart", "action": None,
            "brand": None, "product": None,
            "tokens": [], "quantity": None, "unit": None,
            "confidence": 1.0, "product_validated": True, "product_validation_method": "fuzzy",
            "brand_validated": True, "brand_validation_method": "fuzzy"
        }
    },
    {
        "sentence": "Cancel 3 kg of beans and add 2 imported rice bags.",
        "response": {
            "intent": "modify_cart", "action": None,
            "brand": None, "product": None,
            "tokens": ["imported"], "quantity": None, "unit": None,
            "confidence": 1.0, "product_validated": True, "product_validation_method": "fuzzy",
            "brand_validated": False, "brand_validation_method": None
        }
    }
]
