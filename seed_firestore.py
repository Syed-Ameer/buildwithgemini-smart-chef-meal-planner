# Copyright 2026 Google LLC
# Seed script for Firestore database in Smart Chef & Meal Planner

from google.cloud import firestore

# IMPORTANT: Project ID hardcoded as a string (project ID, NOT project number)
FIRESTORE_PROJECT = "qwiklabs-gcp-01-fed5137b0b67"

def seed_database():
    print(f"Connecting to Firestore with project ID: {FIRESTORE_PROJECT}...")
    db = firestore.Client(project=FIRESTORE_PROJECT)

    # 1. Seed Pantry Items
    pantry_items = [
        {
            "id": "olive_oil",
            "name": "Extra Virgin Olive Oil",
            "category": "Oils & Condiments",
            "quantity": 750,
            "unit": "ml",
            "expiration_date": "2027-01-15",
        },
        {
            "id": "spaghetti_pasta",
            "name": "Spaghetti Pasta",
            "category": "Grains & Pasta",
            "quantity": 2,
            "unit": "boxes",
            "expiration_date": "2027-06-30",
        },
        {
            "id": "canned_tomatoes",
            "name": "Diced Canned Tomatoes",
            "category": "Canned Goods",
            "quantity": 4,
            "unit": "cans",
            "expiration_date": "2027-03-20",
        },
        {
            "id": "fresh_garlic",
            "name": "Fresh Garlic",
            "category": "Produce",
            "quantity": 3,
            "unit": "heads",
            "expiration_date": "2026-10-30",
        },
        {
            "id": "parmesan_cheese",
            "name": "Parmesan Cheese",
            "category": "Dairy",
            "quantity": 250,
            "unit": "g",
            "expiration_date": "2026-11-15",
        },
    ]

    print("Seeding 'pantry_items' collection...")
    for item in pantry_items:
        doc_id = item.pop("id")
        db.collection("pantry_items").document(doc_id).set(item)
        print(f"  - Added pantry item: {doc_id} ({item['name']})")

    # 2. Seed Recipes
    recipes = [
        {
            "id": "classic_pasta_pomodoro",
            "name": "Classic Pasta Pomodoro",
            "cuisine": "Italian",
            "prep_time_minutes": 20,
            "servings": 4,
            "dietary_tags": ["Vegetarian"],
            "ingredients": ["Spaghetti Pasta", "Diced Canned Tomatoes", "Extra Virgin Olive Oil", "Fresh Garlic", "Parmesan Cheese"],
            "instructions": "1. Boil spaghetti in salted water. 2. Sauté minced garlic in olive oil. 3. Add canned tomatoes and simmer for 15 mins. 4. Toss pasta with sauce and top with fresh grated parmesan.",
        },
        {
            "id": "garlic_parmesan_bread",
            "name": "Garlic Parmesan Breadsticks",
            "cuisine": "Italian",
            "prep_time_minutes": 15,
            "servings": 4,
            "dietary_tags": ["Vegetarian"],
            "ingredients": ["Fresh Garlic", "Extra Virgin Olive Oil", "Parmesan Cheese"],
            "instructions": "1. Mix minced garlic with olive oil. 2. Brush on sliced dough/bread. 3. Sprinkle parmesan cheese. 4. Bake at 400°F until golden brown.",
        }
    ]

    print("Seeding 'recipes' collection...")
    for recipe in recipes:
        doc_id = recipe.pop("id")
        db.collection("recipes").document(doc_id).set(recipe)
        print(f"  - Added recipe: {doc_id} ({recipe['name']})")

    print("Seeding completed successfully! ✅")

if __name__ == "__main__":
    seed_database()
