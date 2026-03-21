"""Image generation providers: Replicate, Gemini, Freepik."""

import base64
import requests
import time as _time

from config import *
from status import *


def generate_image_replicate(prompt: str, persist_fn, max_retries: int = 3) -> str:
    token = get_replicate_api_token()
    if not token:
        error("replicate_api_token is not configured in config.json.")
        return None

    print(f"Generating Image using Replicate flux-dev: {prompt[:80]}...")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "input": {
            "prompt": prompt,
            "aspect_ratio": "9:16",
            "num_outputs": 1,
            "output_format": "png",
        }
    }

    for attempt in range(max_retries):
        try:
            response = requests.post(
                "https://api.replicate.com/v1/models/black-forest-labs/flux-dev/predictions",
                headers=headers, json=payload, timeout=30,
            )
            if response.status_code == 429:
                wait = 15 * (attempt + 1)
                warning(f"Rate limited. Waiting {wait}s before retry ({attempt+1}/{max_retries})...")
                _time.sleep(wait)
                continue

            response.raise_for_status()
            prediction_id = response.json()["id"]

            poll_url = f"https://api.replicate.com/v1/predictions/{prediction_id}"
            for _ in range(60):
                _time.sleep(2)
                result = requests.get(poll_url, headers=headers, timeout=10).json()
                status = result["status"]
                if status == "succeeded":
                    output = result.get("output", [])
                    if output:
                        image_url = output[0] if isinstance(output, list) else output
                        img_resp = requests.get(image_url, timeout=60)
                        img_resp.raise_for_status()
                        return persist_fn(img_resp.content, "Replicate flux-dev")
                    return None
                elif status == "failed":
                    warning(f"Replicate failed: {result.get('error')}")
                    return None
            return None
        except Exception as e:
            if get_verbose():
                warning(f"Replicate error: {str(e)}")
            if attempt < max_retries - 1:
                _time.sleep(5)
                continue
            return None


def generate_image_gemini(prompt: str, persist_fn, max_retries: int = 3) -> str:
    print(f"Generating Image using Gemini API: {prompt[:80]}...")

    api_key = get_nanobanana2_api_key()
    if not api_key:
        error("nanobanana2_api_key is not configured.")
        return None

    base_url = get_nanobanana2_api_base_url().rstrip("/")
    model = get_nanobanana2_model()
    aspect_ratio = get_nanobanana2_aspect_ratio()

    endpoint = f"{base_url}/models/{model}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": aspect_ratio},
        },
    }

    for attempt in range(max_retries):
        try:
            response = requests.post(
                endpoint,
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json=payload, timeout=300,
            )
            if response.status_code == 429:
                wait = 15 * (attempt + 1)
                warning(f"Rate limited. Waiting {wait}s before retry ({attempt+1}/{max_retries})...")
                _time.sleep(wait)
                continue

            response.raise_for_status()
            body = response.json()
            for candidate in body.get("candidates", []):
                for part in candidate.get("content", {}).get("parts", []):
                    inline_data = part.get("inlineData") or part.get("inline_data")
                    if not inline_data:
                        continue
                    data = inline_data.get("data")
                    mime_type = inline_data.get("mimeType") or inline_data.get("mime_type", "")
                    if data and str(mime_type).startswith("image/"):
                        return persist_fn(base64.b64decode(data), "Gemini API")

            if get_verbose():
                warning(f"Gemini did not return image. Response: {body}")
            return None
        except Exception as e:
            if get_verbose():
                warning(f"Gemini error: {str(e)}")
            if attempt < max_retries - 1:
                _time.sleep(5)
                continue
            return None


def generate_image_freepik(prompt: str, persist_fn, max_retries: int = 3) -> str:
    api_key = get_freepik_api_key()
    if not api_key:
        error("freepik_api_key is not configured in config.json.")
        return None

    print(f"Generating Image using Freepik Mystic: {prompt[:80]}...")

    headers = {
        "x-freepik-api-key": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "prompt": prompt,
        "resolution": "2k",
        "aspect_ratio": "social_story_9_16",
        "model": get_freepik_model(),
    }

    for attempt in range(max_retries):
        try:
            response = requests.post(
                "https://api.freepik.com/v1/ai/mystic",
                headers=headers, json=payload, timeout=30,
            )
            if response.status_code == 429:
                wait = 15 * (attempt + 1)
                warning(f"Rate limited. Waiting {wait}s before retry ({attempt+1}/{max_retries})...")
                _time.sleep(wait)
                continue

            response.raise_for_status()
            task = response.json().get("data", {})
            task_id = task.get("task_id")
            if not task_id:
                warning(f"Freepik did not return task_id. Response: {response.json()}")
                return None

            poll_url = f"https://api.freepik.com/v1/ai/mystic/{task_id}"
            for _ in range(90):
                _time.sleep(2)
                poll_resp = requests.get(poll_url, headers=headers, timeout=10)
                poll_resp.raise_for_status()
                result = poll_resp.json().get("data", {})
                status = result.get("status", "")

                if status == "COMPLETED":
                    generated = result.get("generated", [])
                    if generated:
                        img_resp = requests.get(generated[0], timeout=60)
                        img_resp.raise_for_status()
                        return persist_fn(img_resp.content, "Freepik Mystic")
                    warning("Freepik returned no images.")
                    return None
                elif status == "FAILED":
                    warning("Freepik task failed.")
                    return None

            warning("Freepik task timed out.")
            return None
        except Exception as e:
            if get_verbose():
                warning(f"Freepik error: {str(e)}")
            if attempt < max_retries - 1:
                _time.sleep(5)
                continue
            return None
