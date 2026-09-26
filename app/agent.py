# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import base64
import json
import os
import re
import urllib.parse
import urllib.request
from dotenv import load_dotenv

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager

from google import genai
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors.agent_engine_sandbox_code_executor import (
    AgentEngineSandboxCodeExecutor,
)
from google.adk.memory import VertexAiMemoryBankService
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.cloud import firestore, storage
from google.genai import types

from .a2ui_utils import a2ui_callback

load_dotenv()

# IMPORTANT: Hardcode project ID, Cloud Storage bucket, and Memory Bank ID
PROJECT_ID = "qwiklabs-gcp-01-fed5137b0b67"
STORAGE_BUCKET = "smart-chef-dishes-qwiklabs-gcp-01-fed5137b0b67"
MEMORY_BANK_ID = "4066569593852788736"

# Determine Agent Engine resource name from deployment_metadata.json if present
metadata_file = os.path.join(
    os.path.dirname(__file__), "..", "deployment_metadata.json"
)
agent_engine_resource_name = None
if os.path.exists(metadata_file):
    try:
        with open(metadata_file, "r") as f:
            meta = json.load(f)
            runtime_id = meta.get("remote_agent_runtime_id")
            if runtime_id and runtime_id != "None":
                agent_engine_resource_name = runtime_id
    except Exception:
        pass

code_executor = (
    AgentEngineSandboxCodeExecutor(
        agent_engine_resource_name=agent_engine_resource_name
    )
    if agent_engine_resource_name
    else AgentEngineSandboxCodeExecutor()
)


# WRITE: after each turn, extract and send durable facts (including allergies) to Memory Bank.
async def generate_memories_callback(callback_context: CallbackContext):
    try:
        await callback_context.add_session_to_memory()
    except Exception:
        pass
    return None


def memory_bank_service_builder() -> VertexAiMemoryBankService:
    """Builds the Vertex AI Memory Bank service for deployed environments."""
    return VertexAiMemoryBankService(
        project=PROJECT_ID,
        location="us-central1",
        agent_engine_id=MEMORY_BANK_ID,
    )


def get_firestore_client() -> firestore.Client:
    """Returns a Firestore client initialized with the explicit project ID string."""
    return firestore.Client(project=PROJECT_ID)


def check_pantry_inventory(category: str = "") -> str:
    """Checks the user's pantry inventory in Firestore.

    Args:
        category: Optional category filter (e.g. 'Produce', 'Dairy', 'Grains & Pasta', 'Canned Goods').

    Returns:
        A JSON string containing the list of pantry items with quantity and expiration dates.
    """
    db = get_firestore_client()
    query_ref = db.collection("pantry_items")
    if category:
        query_ref = query_ref.where("category", "==", category)

    docs = query_ref.stream()
    items = []
    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id
        items.append(data)

    if not items:
        return f"No pantry items found{' in category ' + category if category else ''}."

    return json.dumps(items, indent=2)


def update_pantry_item(
    item_id: str,
    name: str,
    category: str,
    quantity: float,
    unit: str,
    expiration_date: str = "",
) -> str:
    """Adds a new item or updates an existing item in the user's Firestore pantry inventory.

    Args:
        item_id: Unique identifier string for the item (e.g., 'olive_oil', 'milk').
        name: Name of the food/pantry item.
        category: Category (e.g., 'Produce', 'Dairy', 'Oils & Condiments', 'Grains & Pasta').
        quantity: Amount of the item.
        unit: Unit of measurement (e.g., 'ml', 'g', 'cans', 'boxes', 'head').
        expiration_date: Optional expiration date string (YYYY-MM-DD).

    Returns:
        A success message indicating the item was updated.
    """
    db = get_firestore_client()
    doc_ref = db.collection("pantry_items").document(item_id.lower().replace(" ", "_"))
    item_data = {
        "name": name,
        "category": category,
        "quantity": quantity,
        "unit": unit,
        "expiration_date": expiration_date,
    }
    doc_ref.set(item_data)
    return f"Successfully updated pantry item '{name}' ({item_id}) with quantity {quantity} {unit}."


def search_recipes(ingredient: str = "") -> str:
    """Searches recipes stored in the Firestore recipe collection.

    Args:
        ingredient: Optional ingredient filter to search for recipes containing a specific ingredient.

    Returns:
        A JSON string containing matching recipes.
    """
    db = get_firestore_client()
    docs = db.collection("recipes").stream()
    matching_recipes = []
    for doc in docs:
        recipe = doc.to_dict()
        recipe["id"] = doc.id
        if ingredient:
            ing_list = [i.lower() for i in recipe.get("ingredients", [])]
            if any(ingredient.lower() in ing for ing in ing_list):
                matching_recipes.append(recipe)
        else:
            matching_recipes.append(recipe)

    if not matching_recipes:
        return f"No recipes found{' containing ' + ingredient if ingredient else ''}."

    return json.dumps(matching_recipes, indent=2)


def generate_dish_image(recipe_name: str, description: str = "") -> str:
    """Generates an image/card asset for a dish or recipe and uploads it to Cloud Storage.

    Args:
        recipe_name: Name of the dish or recipe (e.g., 'Classic Pasta Pomodoro').
        description: Optional brief description or garnish details for the dish image.

    Returns:
        A public HTTPS URL pointing to the uploaded dish image asset on Cloud Storage.
    """
    clean_slug = re.sub(r"[^a-z0-9]", "_", recipe_name.lower().strip()).strip("_")
    blob_name = f"dishes/{clean_slug}.svg"

    svg_content = f"""<svg xmlns="http://www.w3.org/2000/svg" width="600" height="400" viewBox="0 0 600 400">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#1A365D" />
      <stop offset="50%" stop-color="#2B6CB0" />
      <stop offset="100%" stop-color="#2C5282" />
    </linearGradient>
    <linearGradient id="accent" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#ED8936" />
      <stop offset="100%" stop-color="#ECC94B" />
    </linearGradient>
  </defs>
  <rect width="600" height="400" rx="16" fill="url(#bg)" />
  <circle cx="300" cy="160" r="80" fill="#FFFFFF" opacity="0.1" />
  <text x="300" y="175" font-family="system-ui, sans-serif" font-size="64" text-anchor="middle" fill="#ECC94B">🍽️</text>
  <text x="300" y="270" font-family="system-ui, sans-serif" font-size="28" font-weight="bold" text-anchor="middle" fill="#FFFFFF">{recipe_name}</text>
  <text x="300" y="310" font-family="system-ui, sans-serif" font-size="16" text-anchor="middle" fill="#CBD5E0">{description or 'Smart Chef Culinary Creation'}</text>
  <rect x="200" y="340" width="200" height="4" rx="2" fill="url(#accent)" />
</svg>"""

    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(STORAGE_BUCKET)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(svg_content, content_type="image/svg+xml")

    public_url = f"https://storage.googleapis.com/{STORAGE_BUCKET}/{blob_name}"
    return f"Generated dish image for '{recipe_name}'. Available publicly at: {public_url}"


def generate_recipe_photo(
    recipe_name: str,
    prompt_description: str = "",
    tool_context: ToolContext = None,
) -> str:
    """Generates a high-resolution AI photograph for a dish or recipe using gemini-3.1-flash-lite-image in the global region.

    Saves the image as a Playground Artifact via tool_context.save_artifact and uploads the same image bytes
    to the public Cloud Storage bucket, returning its public HTTPS URL.

    Args:
        recipe_name: Name of the dish or recipe (e.g. 'Classic Pasta Pomodoro').
        prompt_description: Optional extra visual styling or garnish description for the food photo.
        tool_context: ADK ToolContext injected automatically by the framework.

    Returns:
        A public HTTPS URL pointing to the uploaded image on Cloud Storage.
    """
    client = genai.Client(vertexai=True, project=PROJECT_ID, location="global")
    prompt = f"A delicious high-resolution culinary food photograph of {recipe_name}. {prompt_description}".strip()

    response = client.models.generate_content(
        model="gemini-3.1-flash-lite-image",
        contents=prompt,
        config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
    )

    image_bytes = None
    if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
        for part in response.candidates[0].content.parts:
            if part.inline_data is not None:
                image_bytes = part.inline_data.data
                break

    if not image_bytes:
        return f"Error: Failed to generate photo for '{recipe_name}'."

    clean_slug = re.sub(r"[^a-z0-9]", "_", recipe_name.lower().strip()).strip("_")
    filename = f"{clean_slug}.jpg"
    blob_name = f"photos/{filename}"

    # 1. Save artifact if tool_context is provided so it shows in Playground Artifacts panel
    if tool_context is not None:
        artifact_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
        tool_context.save_artifact(filename=filename, artifact=artifact_part)

    # 2. Upload same image bytes directly to GCS bucket (without writing local file)
    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(STORAGE_BUCKET)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(image_bytes, content_type="image/jpeg")

    public_url = f"https://storage.googleapis.com/{STORAGE_BUCKET}/{blob_name}"
    return public_url


def generate_recipe_video(
    recipe_name: str,
    prompt_description: str = "",
    tool_context: ToolContext = None,
) -> str:
    """Generates a short culinary video for a dish or recipe using Google's Omni model (gemini-omni-flash-preview) in the global region.

    Saves the video as a Playground Artifact via tool_context.save_artifact and uploads the same video bytes
    to the public Cloud Storage bucket, returning its public HTTPS URL.

    Args:
        recipe_name: Name of the dish or recipe (e.g., 'Sizzling Beef Fajitas').
        prompt_description: Optional extra description or visual style for the video (e.g. 'steam rising, close-up shot').
        tool_context: ADK ToolContext injected automatically by the framework.

    Returns:
        A public HTTPS URL pointing to the uploaded MP4 video on Cloud Storage.
    """
    client = genai.Client(vertexai=True, project=PROJECT_ID, location="global")
    prompt = f"A short video of a delicious dish: {recipe_name}. {prompt_description}".strip()

    interaction = client.interactions.create(
        model="gemini-omni-flash-preview",
        input=prompt,
    )

    video_bytes = None
    if hasattr(interaction, "output_video") and interaction.output_video:
        data = getattr(interaction.output_video, "data", None)
        if isinstance(data, str):
            video_bytes = base64.b64decode(data)
        elif isinstance(data, bytes):
            video_bytes = data

    if not video_bytes and hasattr(interaction, "steps"):
        for step in getattr(interaction, "steps", []) or []:
            if getattr(step, "type", None) == "model_output":
                for item in getattr(step, "content", []) or []:
                    if getattr(item, "type", None) == "video" or getattr(item, "mime_type", "").startswith("video/"):
                        data = getattr(item, "data", None)
                        if isinstance(data, str):
                            video_bytes = base64.b64decode(data)
                        elif isinstance(data, bytes):
                            video_bytes = data
                        if video_bytes:
                            break

    if not video_bytes:
        return f"Error: Failed to generate video for '{recipe_name}'."

    clean_slug = re.sub(r"[^a-z0-9]", "_", recipe_name.lower().strip()).strip("_")
    filename = f"{clean_slug}.mp4"
    blob_name = f"videos/{filename}"

    # 1. Save artifact if tool_context is provided so it shows in Playground Artifacts panel
    if tool_context is not None:
        artifact_part = types.Part.from_bytes(data=video_bytes, mime_type="video/mp4")
        tool_context.save_artifact(filename=filename, artifact=artifact_part)

    # 2. Upload same video bytes directly to GCS bucket (without writing local file)
    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(STORAGE_BUCKET)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(video_bytes, content_type="video/mp4")

    public_url = f"https://storage.googleapis.com/{STORAGE_BUCKET}/{blob_name}"
    return public_url


def fetch_external_recipes(query: str) -> str:
    """Fetches real online recipes from TheMealDB public API.

    Args:
        query: The recipe or dish search query (e.g., 'pasta', 'chicken', 'tacos').

    Returns:
        A JSON string containing real recipes from the global culinary API.
    """
    api_key = os.environ.get("THEMEALDB_API_KEY", "1")
    encoded_query = urllib.parse.quote(query)
    url = f"https://www.themealdb.com/api/json/v1/{api_key}/search.php?s={encoded_query}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SmartChefApp/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        return f"Error fetching online recipes: {str(e)}"

    meals = data.get("meals")
    if not meals:
        return f"No online recipes found matching '{query}'."

    results = []
    for meal in meals[:3]:
        ingredients = []
        for i in range(1, 21):
            ing = meal.get(f"strIngredient{i}")
            meas = meal.get(f"strMeasure{i}")
            if ing and ing.strip():
                ingredients.append(f"{meas.strip() if meas else ''} {ing.strip()}".strip())

        results.append({
            "name": meal.get("strMeal"),
            "category": meal.get("strCategory"),
            "area": meal.get("strArea"),
            "instructions": meal.get("strInstructions", "")[:250] + "...",
            "thumbnail": meal.get("strMealThumb"),
            "ingredients": ingredients,
        })

    return json.dumps(results, indent=2)


def geocode_address(address: str) -> str:
    """Converts a street address or location name into geographical coordinates using Google Geocoding API.

    Args:
        address: The address or city name (e.g., '1600 Amphitheatre Pkwy, Mountain View, CA').

    Returns:
        A JSON string with formatted address, latitude, and longitude.
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return "Error: GOOGLE_MAPS_API_KEY is not set in environment."

    encoded = urllib.parse.quote(address)
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={encoded}&key={api_key}"

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        return f"Error calling Geocoding API: {str(e)}"

    results = data.get("results", [])
    if not results:
        return f"No geocoding results found for address '{address}'."

    first = results[0]
    loc = first.get("geometry", {}).get("location", {})
    return json.dumps({
        "formatted_address": first.get("formatted_address"),
        "latitude": loc.get("lat"),
        "longitude": loc.get("lng"),
    }, indent=2)


def find_nearby_places(
    latitude: float,
    longitude: float,
    place_type: str = "supermarket",
    radius_meters: float = 3000.0,
) -> str:
    """Finds nearby places (e.g., supermarkets, grocery stores) around coordinates using Google Places API (New).

    Args:
        latitude: Center latitude coordinate (e.g. 37.422).
        longitude: Center longitude coordinate (e.g. -122.084).
        place_type: Type of place to search for (e.g., 'supermarket', 'grocery_store', 'bakery').
        radius_meters: Search radius in meters (default 3000.0).

    Returns:
        A JSON string listing nearby places with name, address, and location coordinates.
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return "Error: GOOGLE_MAPS_API_KEY is not set in environment."

    lat_val = float(latitude)
    lng_val = float(longitude)
    rad_val = float(radius_meters)

    url = "https://places.googleapis.com/v1/places:searchNearby"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location",
    }
    body = {
        "includedTypes": [place_type],
        "maxResultCount": 5,
        "locationRestriction": {
            "circle": {
                "center": {
                    "latitude": lat_val,
                    "longitude": lng_val,
                },
                "radius": rad_val,
            }
        },
    }

    try:
        req_data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        return f"Error calling Places API (New): {str(e)}"

    places = data.get("places", [])
    if not places:
        return f"No nearby '{place_type}' places found within {rad_val}m."

    results = []
    for p in places:
        disp_name = p.get("displayName", {}).get("text", "Unknown")
        results.append({
            "name": disp_name,
            "address": p.get("formattedAddress"),
            "location": p.get("location"),
        })

    return json.dumps(results, indent=2)


# Build A2UI system prompt using A2uiSchemaManager version 0.8
a2ui_schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

a2ui_prompt = a2ui_schema_manager.generate_system_prompt(
    role_description="Smart Chef, an AI culinary assistant that plans meals, manages pantry inventory, searches recipes, generates dish photos, and finds nearby grocery stores.",
    workflow_description="Analyze the user request, call necessary tools, and return structured A2UI display cards when appropriate.",
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        "{\"Image\": {\"url\": {\"literalString\": \"https://...\"}}}. Never point an "
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)

domain_instruction = (
    "CRITICAL SAFETY RULE: You MUST pay strict attention to any food allergies, intolerances, or dietary restrictions "
    "(e.g., peanut allergy, tree nut allergy, shellfish, gluten-free, lactose intolerance, celiac disease, vegan) "
    "stated by the user. Always extract and remember ALL user allergies across sessions using your Memory Bank, "
    "and proactively enforce them across all recipe recommendations, pantry meal suggestions, and ingredient substitutions. "
    "NEVER recommend recipes or ingredients containing stated user allergens. "
    "Use your tools to check pantry inventory, update pantry items, search local recipes, "
    "fetch real online recipes from TheMealDB, generate dish SVG images or AI photos with gemini-3.1-flash-lite-image, "
    "generate short culinary videos with gemini-omni-flash-preview (`generate_recipe_video`), "
    "geocode addresses using `geocode_address`, and find nearby grocery stores using `find_nearby_places`. "
    "You have a code executor enabled to run Python code safely in a sandbox environment when needed."
)

full_instruction = f"{domain_instruction}\n\n{a2ui_prompt}"

root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-2.5-flash",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=full_instruction,
    code_executor=code_executor,
    tools=[
        PreloadMemoryTool(),
        check_pantry_inventory,
        update_pantry_item,
        search_recipes,
        generate_dish_image,
        generate_recipe_photo,
        generate_recipe_video,
        fetch_external_recipes,
        geocode_address,
        find_nearby_places,
    ],
    after_model_callback=a2ui_callback,
    after_agent_callback=generate_memories_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)
