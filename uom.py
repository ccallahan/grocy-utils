import os
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
GROCY_URL = os.getenv("GROCY_URL", "http://localhost:9283")  # Replace with your URL
GROCY_API_KEY = os.getenv("GROCY_API_KEY", "YOUR_API_KEY_HERE")  # Replace with your API key

HEADERS = {
    "GROCY-API-KEY": GROCY_API_KEY,
    "Content-Type": "application/json",
    "Accept": "application/json",
}

# ---------------------------------------------------------------------------
# Quantity Units Data
# ---------------------------------------------------------------------------
QUANTITY_UNITS = [
    {"name": "Piece", "name_plural": "Pieces", "description": "Single discrete item or unit"},
    {"name": "Pack", "name_plural": "Packs", "description": "Sealed multi-item retail package"},
    {"name": "Box", "name_plural": "Boxes", "description": "Boxed items or storage container"},
    {"name": "Can", "name_plural": "Cans", "description": "Canned food or beverage container"},
    {"name": "Bottle", "name_plural": "Bottles", "description": "Bottled liquid or condiment"},
    {"name": "Jar", "name_plural": "Jars", "description": "Glass jar container"},
    {"name": "Bag", "name_plural": "Bags", "description": "Bagged produce, grains, or snacks"},
    {"name": "Bunch", "name_plural": "Bunches", "description": "Tied group of fresh produce (e.g., herbs, bananas)"},
    {"name": "Head", "name_plural": "Heads", "description": "Whole head of produce (e.g., lettuce, cabbage, garlic)"},
    {"name": "Gram", "name_plural": "Grams", "description": "Metric unit for small dry weights"},
    {"name": "Kilogram", "name_plural": "Kilograms", "description": "Metric unit for bulk dry weights"},
    {"name": "Ounce", "name_plural": "Ounces", "description": "Imperial unit for dry weights"},
    {"name": "Pound", "name_plural": "Pounds", "description": "Imperial unit for bulk dry weights"},
    {"name": "Milliliter", "name_plural": "Milliliters", "description": "Metric unit for small liquid volumes"},
    {"name": "Liter", "name_plural": "Liters", "description": "Metric unit for large liquid volumes"},
    {"name": "Fluid Ounce", "name_plural": "Fluid Ounces", "description": "Imperial unit for liquid volumes"},
    {"name": "Cup", "name_plural": "Cups", "description": "Standard cooking measure (approx. 240 ml)"},
    {"name": "Tablespoon", "name_plural": "Tablespoons", "description": "Standard cooking measure (approx. 15 ml)"},
    {"name": "Teaspoon", "name_plural": "Teaspoons", "description": "Standard cooking measure (approx. 5 ml)"},
    {"name": "Pinch", "name_plural": "Pinches", "description": "Very small untracked quantity for spices/salts"},
    {"name": "Clove", "name_plural": "Cloves", "description": "Individual garlic clove"},
    {"name": "Slice", "name_plural": "Slices", "description": "Single slice of bread, cheese, or meat"},
    {"name": "Roll", "name_plural": "Rolls", "description": "Paper towel, toilet paper, or foil roll"},
]


def add_quantity_units():
    endpoint = f"{GROCY_URL.rstrip('/')}/api/objects/quantity_units"

    # Fetch existing units to prevent duplicates
    try:
        response = requests.get(endpoint, headers=HEADERS)
        response.raise_for_status()
        existing_units = {u["name"].lower(): u for u in response.json()}
    except requests.exceptions.RequestException as e:
        print(f"Error connecting to Grocy API: {e}")
        return

    created_count = 0
    skipped_count = 0

    for unit in QUANTITY_UNITS:
        if unit["name"].lower() in existing_units:
            print(f"Skipping existing unit: '{unit['name']}'")
            skipped_count += 1
            continue

        payload = {
            "name": unit["name"],
            "name_plural": unit["name_plural"],
            "description": unit["description"],
        }

        try:
            res = requests.post(endpoint, json=payload, headers=HEADERS)
            if res.status_code in (200, 201):
                print(f"Successfully added: '{unit['name']}'")
                created_count += 1
            else:
                print(f"Failed to add '{unit['name']}': {res.status_code} - {res.text}")
        except requests.exceptions.RequestException as e:
            print(f"Network error adding '{unit['name']}': {e}")

    print(f"\nSummary: Added {created_count} units, skipped {skipped_count} existing units.")


if __name__ == "__main__":
    add_quantity_units()
