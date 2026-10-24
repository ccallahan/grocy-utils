"""
Import recipes from Facebook reels (or any yt-dlp supported video) into Grocy.

Pipeline:
  1. Download the reel with yt-dlp (video, caption/description, thumbnail)
  2. Extract audio and a handful of frames with ffmpeg
  3. Transcribe the audio with OpenAI
  4. Ask an OpenAI model to turn transcript + caption + frames into a structured recipe
  5. Create missing products, then the recipe and its ingredients in Grocy

Usage:
  python reel_to_recipe.py https://www.facebook.com/reel/123456789
  python reel_to_recipe.py -f reels.txt --dry-run
  python reel_to_recipe.py URL --cookies-from-browser chrome

Requires yt-dlp and ffmpeg on PATH, and these environment variables
(or a .env file next to this script):
  GROCY_URL, GROCY_API_KEY, OPENAI_API_KEY
"""

import argparse
import base64
import glob
import html
import json
import os
import subprocess
import sys
import tempfile

import requests


def load_dotenv(path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")):
    """Load KEY=VALUE lines from a .env file without overriding variables already set."""
    if not os.path.isfile(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().removeprefix("export ").strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ.setdefault(key, value)


load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
GROCY_URL = os.getenv("GROCY_URL", "http://localhost:9283")  # Replace with your URL
GROCY_API_KEY = os.getenv("GROCY_API_KEY", "YOUR_API_KEY_HERE")  # Replace with your API key
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

OPENAI_URL = "https://api.openai.com/v1"
TRANSCRIBE_MODEL = os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-transcribe")
EXTRACT_MODEL = os.getenv("OPENAI_EXTRACT_MODEL", "gpt-4o")

HEADERS = {
    "GROCY-API-KEY": GROCY_API_KEY,
    "Content-Type": "application/json",
    "Accept": "application/json",
}

# ---------------------------------------------------------------------------
# Recipe JSON schema (OpenAI structured output)
# ---------------------------------------------------------------------------
RECIPE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["is_recipe", "name", "summary", "servings", "ingredients", "steps", "notes"],
    "properties": {
        "is_recipe": {"type": "boolean", "description": "False if the video does not contain a recipe"},
        "name": {"type": "string"},
        "summary": {"type": "string", "description": "One or two sentence description of the dish"},
        "servings": {"type": "number"},
        "ingredients": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["product", "amount", "unit", "note", "group", "product_group"],
                "properties": {
                    "product": {"type": "string", "description": "Generic product name, singular"},
                    "amount": {"type": "number"},
                    "unit": {"type": "string", "description": "Must be one of the provided unit names"},
                    "note": {"type": "string", "description": "Original wording / prep, e.g. 'finely diced'"},
                    "group": {"type": "string", "description": "Ingredient group, e.g. 'Sauce', or ''"},
                    "product_group": {"type": "string", "description": "One of the provided product groups, or ''"},
                },
            },
        },
        "steps": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string", "description": "Tips, substitutions, or caveats about guessed amounts"},
    },
}

SYSTEM_PROMPT = """You extract cooking recipes from short-form social media videos.
You are given the video's caption/description, an audio transcript, and frames from the video
(which often show on-screen text with ingredient amounts). Combine all sources.

Rules:
- If amounts are not stated, make a sensible estimate and mention that in `notes`.
- `product` must be a generic grocery product name (e.g. "Garlic", "Chicken Thigh", "Soy Sauce"),
  title case, singular, without brand or preparation. Put preparation in `note`.
- If an existing product in the provided list matches, use its exact name.
- `unit` must be exactly one of the provided unit names. Use "Piece" for countable items.
- `product_group` must be exactly one of the provided product group names, or "".
- `steps` are clear, concise instructions in order.
- Set is_recipe=false (with empty fields) if the video does not contain a recipe."""


# ---------------------------------------------------------------------------
# Video handling
# ---------------------------------------------------------------------------
def download_reel(source, workdir, cookies_from_browser=None, cookies=None):
    """Download the video with yt-dlp. Returns (video_path, metadata, thumbnail_path)."""
    if os.path.isfile(source):
        return source, {"title": os.path.basename(source), "description": ""}, None

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--quiet",
        "--no-warnings",
        "-f", "best[ext=mp4]/best",
        "--write-info-json",
        "--write-thumbnail",
        "--convert-thumbnails", "jpg",
        "-o", os.path.join(workdir, "reel.%(ext)s"),
    ]
    if cookies_from_browser:
        cmd += ["--cookies-from-browser", cookies_from_browser]
    if cookies:
        cmd += ["--cookies", cookies]
    cmd.append(source)
    subprocess.run(cmd, check=True)

    with open(os.path.join(workdir, "reel.info.json")) as f:
        info = json.load(f)
    video = next(p for p in glob.glob(os.path.join(workdir, "reel.*"))
                 if not p.endswith((".json", ".jpg", ".webp", ".png")))
    thumb = os.path.join(workdir, "reel.jpg")
    return video, info, thumb if os.path.exists(thumb) else None


def extract_audio(video, workdir):
    """Extract mono, low bitrate audio (keeps us well under OpenAI's 25 MB upload limit)."""
    audio = os.path.join(workdir, "audio.mp3")
    # Fails when the video has no audio track; we then rely on caption + frames only
    result = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", video, "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", audio],
    )
    return audio if result.returncode == 0 and os.path.getsize(audio) > 0 else None


def extract_frames(video, workdir, count):
    """Grab `count` evenly spaced frames to catch on-screen ingredient text."""
    if count <= 0:
        return []
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video],
        capture_output=True, text=True, check=True,
    )
    duration = float(probe.stdout.strip() or 0)
    frames = []
    for i in range(count):
        ts = duration * (i + 0.5) / count
        path = os.path.join(workdir, f"frame_{i:02d}.jpg")
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{ts:.2f}", "-i", video,
             "-frames:v", "1", "-vf", "scale=768:-2", "-q:v", "4", path],
            check=True,
        )
        if os.path.exists(path):
            frames.append(path)
    return frames


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------
def openai_headers():
    return {"Authorization": f"Bearer {OPENAI_API_KEY}"}


def transcribe(audio):
    with open(audio, "rb") as f:
        res = requests.post(
            f"{OPENAI_URL}/audio/transcriptions",
            headers=openai_headers(),
            data={"model": TRANSCRIBE_MODEL, "response_format": "text"},
            files={"file": ("audio.mp3", f, "audio/mpeg")},
            timeout=300,
        )
    res.raise_for_status()
    return res.text.strip()


def extract_recipe(caption, transcript, frames, units, product_groups, product_names):
    context = (
        f"Allowed unit names: {json.dumps(units)}\n"
        f"Allowed product groups: {json.dumps(product_groups)}\n"
        f"Existing products: {json.dumps(product_names)}\n\n"
        f"--- CAPTION ---\n{caption or '(none)'}\n\n"
        f"--- TRANSCRIPT ---\n{transcript or '(no speech)'}"
    )
    content = [{"type": "text", "text": context}]
    for path in frames:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"}})

    res = requests.post(
        f"{OPENAI_URL}/chat/completions",
        headers={**openai_headers(), "Content-Type": "application/json"},
        json={
            "model": EXTRACT_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "recipe", "strict": True, "schema": RECIPE_SCHEMA},
            },
        },
        timeout=300,
    )
    res.raise_for_status()
    return json.loads(res.json()["choices"][0]["message"]["content"])


# ---------------------------------------------------------------------------
# Grocy
# ---------------------------------------------------------------------------
def grocy(method, path, **kwargs):
    res = requests.request(method, f"{GROCY_URL.rstrip('/')}/api{path}", headers=kwargs.pop("headers", HEADERS), **kwargs)
    if res.status_code >= 400:
        raise RuntimeError(f"Grocy {method} {path} failed: {res.status_code} - {res.text}")
    return res.json() if res.content else None


def load_grocy_data():
    return {
        "units": grocy("GET", "/objects/quantity_units"),
        "groups": grocy("GET", "/objects/product_groups"),
        "products": grocy("GET", "/objects/products"),
        "locations": grocy("GET", "/objects/locations"),
        "recipes": grocy("GET", "/objects/recipes"),
    }


def create_product(name, qu_id, group_id, location_id):
    payload = {
        "name": name,
        "location_id": location_id,
        "qu_id_purchase": qu_id,
        "qu_id_stock": qu_id,
        "qu_id_consume": qu_id,
        "qu_id_price": qu_id,
    }
    if group_id:
        payload["product_group_id"] = group_id
    try:
        return grocy("POST", "/objects/products", json=payload)["created_object_id"]
    except RuntimeError as e:
        if "qu_id_consume" not in str(e) and "qu_id_price" not in str(e):
            raise
        # Grocy < 4.0 doesn't know qu_id_consume / qu_id_price
        payload.pop("qu_id_consume")
        payload.pop("qu_id_price")
        return grocy("POST", "/objects/products", json=payload)["created_object_id"]


def get_or_create_product(name, unit, group, location_id, data):
    """Find a product by (case-insensitive) name, creating it if needed. Keeps data["products"] in sync."""
    for p in data["products"]:
        if p["name"].strip().lower() == name.lower():
            return p, False
    try:
        product_id = create_product(name, unit["id"], group["id"] if group else None, location_id)
    except RuntimeError as e:
        if "UNIQUE constraint failed: products.name" not in str(e):
            raise
        # Created elsewhere since we loaded the product list; refresh and use it
        data["products"] = grocy("GET", "/objects/products")
        for p in data["products"]:
            if p["name"].strip().lower() == name.lower():
                return p, False
        raise
    product = {"id": product_id, "name": name, "qu_id_stock": unit["id"]}
    data["products"].append(product)
    return product, True


def upload_picture(thumb, recipe_id):
    filename = f"recipe_{recipe_id}.jpg"
    encoded = base64.b64encode(filename.encode()).decode()
    with open(thumb, "rb") as f:
        grocy("PUT", f"/files/recipepictures/{encoded}", data=f.read(),
              headers={"GROCY-API-KEY": GROCY_API_KEY, "Content-Type": "application/octet-stream"})
    grocy("PUT", f"/objects/recipes/{recipe_id}", json={"picture_file_name": filename})


def build_description(recipe, source_url):
    parts = []
    if recipe["summary"]:
        parts.append(f"<p>{html.escape(recipe['summary'])}</p>")
    parts.append("<ol>" + "".join(f"<li>{html.escape(s)}</li>" for s in recipe["steps"]) + "</ol>")
    if recipe["notes"]:
        parts.append(f"<p><em>{html.escape(recipe['notes'])}</em></p>")
    if source_url:
        parts.append(f'<p>Source: <a href="{html.escape(source_url)}">{html.escape(source_url)}</a></p>')
    return "".join(parts)


def upload_recipe(recipe, data, source_url, thumb, location_id, force):
    if not force and any(r["name"].lower() == recipe["name"].lower() for r in data["recipes"]):
        print(f"Skipping existing recipe: '{recipe['name']}' (use --force to add anyway)")
        return

    units = {u["name"].lower(): u for u in data["units"]}
    groups = {g["name"].lower(): g for g in data["groups"]}
    fallback_unit = units.get("piece") or data["units"][0]

    recipe_id = grocy("POST", "/objects/recipes", json={
        "name": recipe["name"],
        "description": build_description(recipe, source_url),
        "base_servings": recipe["servings"] or 1,
        "desired_servings": recipe["servings"] or 1,
    })["created_object_id"]
    print(f"Created recipe: '{recipe['name']}' (id {recipe_id})")

    for ing in recipe["ingredients"]:
        ing["product"] = ing["product"].strip()
        unit = units.get(ing["unit"].strip().lower(), fallback_unit)
        group = groups.get(ing["product_group"].strip().lower())
        product, created = get_or_create_product(ing["product"], unit, group, location_id, data)
        if created:
            print(f"  Created product: '{ing['product']}'")

        grocy("POST", "/objects/recipes_pos", json={
            "recipe_id": recipe_id,
            "product_id": product["id"],
            "amount": ing["amount"] or 1,
            "qu_id": unit["id"],
            "note": ing["note"],
            "ingredient_group": ing["group"],
            # Without a unit conversion Grocy can't compare amounts, so only check "is any in stock"
            "only_check_single_unit_in_stock": 0 if str(product["qu_id_stock"]) == str(unit["id"]) else 1,
        })
        print(f"  Added ingredient: {ing['amount']:g} {unit['name']} {ing['product']}")

    if thumb:
        try:
            upload_picture(thumb, recipe_id)
        except RuntimeError as e:
            print(f"  Warning: couldn't upload picture: {e}")

    data["recipes"].append({"name": recipe["name"]})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def process(source, data, args):
    print(f"\n=== {source} ===")
    with tempfile.TemporaryDirectory() as workdir:
        video, info, thumb = download_reel(source, workdir, args.cookies_from_browser, args.cookies)
        caption = "\n".join(filter(None, [info.get("title"), info.get("description")]))

        audio = extract_audio(video, workdir)
        transcript = transcribe(audio) if audio else ""
        frames = extract_frames(video, workdir, args.frames)

        recipe = extract_recipe(
            caption, transcript, frames,
            units=[u["name"] for u in data["units"]],
            product_groups=[g["name"] for g in data["groups"]],
            product_names=[p["name"] for p in data["products"]],
        )

        if not recipe["is_recipe"]:
            print("No recipe found in this video, skipping.")
            return
        if args.dry_run:
            print(json.dumps(recipe, indent=2))
            return

        upload_recipe(recipe, data, info.get("webpage_url", source), thumb, args.location_id, args.force)


def main():
    parser = argparse.ArgumentParser(description="Import recipes from Facebook reels into Grocy using OpenAI.")
    parser.add_argument("urls", nargs="*", help="Reel URLs (or local video files)")
    parser.add_argument("-f", "--file", help="Text file with one URL per line")
    parser.add_argument("--dry-run", action="store_true", help="Print the extracted recipe instead of uploading")
    parser.add_argument("--force", action="store_true", help="Add the recipe even if one with the same name exists")
    parser.add_argument("--frames", type=int, default=8, help="Number of video frames to send to the model (default 8)")
    parser.add_argument("--location-id", type=int, help="Grocy location id for newly created products")
    parser.add_argument("--cookies-from-browser", metavar="BROWSER",
                        help="Pass browser cookies to yt-dlp (e.g. chrome, firefox, safari) for login-only reels")
    parser.add_argument("--cookies", metavar="FILE", help="Netscape-format cookies file for yt-dlp (useful in CI)")
    args = parser.parse_args()

    sources = list(args.urls)
    if args.file:
        with open(args.file) as f:
            sources += [line.strip() for line in f if line.strip() and not line.startswith("#")]
    if not sources:
        parser.error("provide at least one URL or --file")
    if not OPENAI_API_KEY:
        sys.exit("OPENAI_API_KEY is not set")

    try:
        data = load_grocy_data()
    except (RuntimeError, requests.exceptions.RequestException) as e:
        sys.exit(f"Error connecting to Grocy API: {e}")
    if not data["units"]:
        sys.exit("Grocy has no quantity units; run uom.py first")
    if not data["locations"] and not args.dry_run:
        sys.exit("Grocy has no locations; create one first (needed for new products)")
    if args.location_id is None and data["locations"]:
        args.location_id = data["locations"][0]["id"]

    failed = 0
    for source in sources:
        try:
            process(source, data, args)
        except (subprocess.CalledProcessError, requests.exceptions.RequestException, RuntimeError) as e:
            print(f"Failed to process {source}: {e}")
            failed += 1

    print(f"\nSummary: processed {len(sources) - failed} of {len(sources)} reels.")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
