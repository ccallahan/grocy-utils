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
# Product Groups Data
# ---------------------------------------------------------------------------
PRODUCT_GROUPS = [
    {"name": "Produce (Fresh)", "description": "Fresh fruits, vegetables, and leafy greens"},
    {"name": "Dairy & Eggs", "description": "Milk, cheese, butter, yogurt, and eggs"},
    {"name": "Meat & Seafood", "description": "Fresh or frozen beef, poultry, pork, and fish"},
    {"name": "Pantry & Dry Goods", "description": "Grains, pasta, flour, rice, canned foods, and dried beans"},
    {"name": "Bakery", "description": "Fresh bread, rolls, tortillas, and pastries"},
    {"name": "Frozen Foods", "description": "Frozen meals, vegetables, fruits, and ice cream"},
    {"name": "Snacks & Sweets", "description": "Chips, crackers, nuts, chocolate, and candy"},
    {"name": "Beverages", "description": "Juices, sodas, tea, coffee, and water"},
    {"name": "Alcohol & Beer", "description": "Wine, beer, spirits, and ciders"},
    {"name": "Spices & Condiments", "description": "Oils, vinegars, sauces, seasonings, and spices"},
    {"name": "Baking Supplies", "description": "Baking powder, sugar, cocoa powder, and yeast"},
    {"name": "Household & Cleaning", "description": "Detergent, dish soap, paper towels, and trash bags"},
    {"name": "Personal Care & Hygiene", "description": "Soap, shampoo, toothpaste, and skincare products"},
    {"name": "Health & Pharmacy", "description": "Medications, vitamins, and first-aid supplies"},
    {"name": "Pet Supplies", "description": "Pet food, treats, and litter"},
]


def add_product_groups():
    endpoint = f"{GROCY_URL.rstrip('/')}/api/objects/product_groups"

    # Fetch existing product groups to prevent duplicate entries
    try:
        response = requests.get(endpoint, headers=HEADERS)
        response.raise_for_status()
        existing_groups = {g["name"].lower(): g for g in response.json()}
    except requests.exceptions.RequestException as e:
        print(f"Error connecting to Grocy API: {e}")
        return

    created_count = 0
    skipped_count = 0

    for group in PRODUCT_GROUPS:
        if group["name"].lower() in existing_groups:
            print(f"Skipping existing group: '{group['name']}'")
            skipped_count += 1
            continue

        payload = {
            "name": group["name"],
            "description": group["description"],
        }

        try:
            res = requests.post(endpoint, json=payload, headers=HEADERS)
            if res.status_code in (200, 201):
                print(f"Successfully added: '{group['name']}'")
                created_count += 1
            else:
                print(f"Failed to add '{group['name']}': {res.status_code} - {res.text}")
        except requests.exceptions.RequestException as e:
            print(f"Network error adding '{group['name']}': {e}")

    print(f"\nSummary: Added {created_count} product groups, skipped {skipped_count} existing groups.")


if __name__ == "__main__":
    add_product_groups()
