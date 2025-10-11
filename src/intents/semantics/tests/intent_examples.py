examples = [
    {
        "sentence": "Actually scratch that, instead of adding the Nike hoodie, set it to Adidas in size M and remove the one I just added.",
        "expected": [
            {"intent": "set", "text": "set it to adidas in size m"},
            {"intent": "remove", "text": "remove the one i just added"},
        ],
    },
    {
        "sentence": "Can you add those black sneakers we talked about earlier and remove the old ones from my cart?",
        "expected": [
            {"intent": "add", "text": "add those black sneakers we talked about earlier"},
            {"intent": "remove", "text": "remove the old ones from my cart"},
        ],
    },
    {
        "sentence": "Add a pair of jeans — oh wait, make it Levi's 501 slim-fit dark wash in size 32 instead.",
        "expected": [
            {"intent": "add", "text": "add levi's 501 slim fit dark wash in size 32"},
        ],
    },
    {
        "sentence": "Remove whatever shirt is in my cart, add a polo from Ralph Lauren in medium, and set the color to navy.",
        "expected": [
            {"intent": "remove", "text": "remove whatever shirt is in my cart"},
            {"intent": "add", "text": "add a polo from ralph lauren in medium"},
            {"intent": "set", "text": "set the color to navy"},
        ],
    },
    {
        "sentence": "Add three t-shirts from Uniqlo — medium size, assorted colors — and also remove the white one from yesterday.",
        "expected": [
            {"intent": "add", "text": "add three t-shirts from uniqlo medium size assorted colors"},
            {"intent": "remove", "text": "remove the white one from yesterday"},
        ],
    },
    {
        "sentence": "Set my sneakers to size 43, whichever brand is already in cart, and add some matching Nike socks.",
        "expected": [
            {"intent": "set", "text": "set my sneakers to size 43 whichever brand is already in cart"},
            {"intent": "add", "text": "add some matching nike socks"},
        ],
    },
    {
        "sentence": "Add a hoodie and remove the blue cap.",
        "expected": [
            {"intent": "add", "text": "add a hoodie"},
            {"intent": "remove", "text": "remove the blue cap"},
        ],
    },
    {
        "sentence": "Replace the jeans in my cart with Levi's skinny fit size 30 in blue.",
        "expected": [
            {"intent": "set", "text": "replace the jeans in my cart with levi's skinny fit size 30 in blue"},
        ],
    },
    {
        "sentence": "Add Nike shorts, remove Adidas shorts, and then set the Nike to size L.",
        "expected": [
            {"intent": "add", "text": "add nike shorts"},
            {"intent": "remove", "text": "remove adidas shorts"},
            {"intent": "set", "text": "set the nike to size l"},
        ],
    },
    {
        "sentence": "Just add socks, any brand is fine, size 10, and get rid of the slippers I had before.",
        "expected": [
            {"intent": "add", "text": "add socks any brand is fine size 10"},
            {"intent": "remove", "text": "remove the slippers i had before"},
        ],
    },
    {
        "sentence": "Remove both jackets, add one hoodie instead, and set the color to grey.",
        "expected": [
            {"intent": "remove", "text": "remove both jackets"},
            {"intent": "add", "text": "add one hoodie instead"},
            {"intent": "set", "text": "set the color to grey"},
        ],
    },
    {
        "sentence": "I changed my mind, don't add the Puma shoes, remove them and set the order to Adidas UltraBoost in size 44.",
        "expected": [
            {"intent": "remove", "text": "remove the puma shoes"},
            {"intent": "set", "text": "set the order to adidas ultraboost in size 44"},
        ],
    },
    {
        "sentence": "Add a shirt — no, make it two shirts, one black and one white, both size L.",
        "expected": [
            {"intent": "add", "text": "add two shirts one black and one white both size l"},
        ],
    },
    {
        "sentence": "Take out the sunglasses, add a cap, and switch the belt in my cart to brown leather.",
        "expected": [
            {"intent": "remove", "text": "remove the sunglasses"},
            {"intent": "add", "text": "add a cap"},
            {"intent": "set", "text": "set the belt in my cart to brown leather"},
        ],
    },
    {
        "sentence": "Add a Nike hoodie size L, and if possible, remove the black jacket I added earlier.",
        "expected": [
            {"intent": "add", "text": "add a nike hoodie size l"},
            {"intent": "remove", "text": "remove the black jacket i added earlier"},
        ],
    },
    {
        "sentence": "Add Adidas joggers in grey and set them to medium, also drop the shorts.",
        "expected": [
            {"intent": "add", "text": "add adidas joggers in grey"},
            {"intent": "set", "text": "set them to medium"},
            {"intent": "remove", "text": "remove the shorts"},
        ],
    },
    {
        "sentence": "Instead of the Zara t-shirt, add an H&M one in size M and remove the Zara completely.",
        "expected": [
            {"intent": "add", "text": "add an h&m one in size m"},
            {"intent": "remove", "text": "remove the zara completely"},
        ],
    },
    {
        "sentence": "Can you swap my blue sneakers for red ones, same brand, and also add white laces?",
        "expected": [
            {"intent": "set", "text": "swap my blue sneakers for red ones same brand"},
            {"intent": "add", "text": "add white laces"},
        ],
    },
    {
        "sentence": "Add socks, remove the slippers, and oh, make sure the socks are black.",
        "expected": [
            {"intent": "add", "text": "add socks"},
            {"intent": "remove", "text": "remove the slippers"},
            {"intent": "set", "text": "set the socks to black"},
        ],
    },
    {
        "sentence": "Add rice and beans to my cart.",
        "expected": [
            {"intent": "add", "text": "add rice and beans to my cart"},
        ],
    },
    {
        "sentence": "Check if you have plantains.",
        "expected": [
            {"intent": "check", "text": "check if you have plantains"},
        ],
    },
    {
        "sentence": "Set the size of the hoodie and the sneakers to large.",
        "expected": [
            {"intent": "set", "text": "set the size of the hoodie and the sneakers to large"},
        ],
    },
    {
        "sentence": "Add milk, bread, and butter.",
        "expected": [
            {"intent": "add", "text": "add milk bread and butter"},
        ],
    },
    {
        "sentence": "Remove apples, bananas, and oranges from my cart.",
        "expected": [
            {"intent": "remove", "text": "remove apples bananas and oranges from my cart"},
        ],
    },
]
