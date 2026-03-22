"""LLM brain for smart decisions — uses Ollama with specific models."""

import os
import json
import re
from typing import List

from llm_provider import generate_text
from config import ROOT_DIR, get_verbose
from status import info, warning, error

FAST_MODEL = "qwen3:8b"
STRONG_MODEL = "qwen3:14b"


def _ask_llm(prompt: str, model: str) -> str:
    return generate_text(prompt, model_name=model)


def pick_voice_profile(instruct_samples: list, profiles: list) -> str:
    """Pick best VoiceBox profile. Uses qwen3:8b.
    instruct_samples: list of instruct strings from segments.
    """
    if not profiles:
        error("No VoiceBox profiles available.")
        return None

    if len(profiles) == 1:
        info(f" => Only 1 profile: {profiles[0].get('name', profiles[0]['id'])}")
        return profiles[0]["id"]

    profiles_desc = "\n".join([
        f"- id: {p['id']}, name: {p.get('name', 'unnamed')}, language: {p.get('language', '?')}"
        for p in profiles
    ])

    # Show a sample of instructs for context
    sample = "; ".join(instruct_samples[:5])

    response = _ask_llm(
        f"These are voice instructions for a storytelling video:\n'{sample}'\n\n"
        f"Pick the best matching voice profile from:\n{profiles_desc}\n\n"
        f"Return ONLY the profile id, nothing else.",
        FAST_MODEL
    ).strip()

    for p in profiles:
        if p["id"] in response:
            info(f" => LLM picked voice profile: {p.get('name', p['id'])}")
            return p["id"]

    warning(f"LLM returned '{response}' — no match. Using first profile.")
    return profiles[0]["id"]


def reorder_images(segments: list, image_prompts: list) -> list:
    """Review image prompts vs story segments and return optimal order. Uses qwen3:14b."""
    story_text = "\n".join([f"{i+1}. {s['text']}" for i, s in enumerate(segments)])
    prompts_list = "\n".join([f"{i+1}. {p}" for i, p in enumerate(image_prompts)])

    for attempt in range(3):
        response = _ask_llm(
            f"You are arranging images for a short video.\n\n"
            f"Story segments:\n{story_text}\n\n"
            f"Image prompts (current order):\n{prompts_list}\n\n"
            f"Return the optimal order as a JSON array of numbers (1-indexed).\n"
            f"All {len(image_prompts)} images must appear exactly once.\n"
            f"Return ONLY the JSON array.",
            STRONG_MODEL
        ).strip()

        try:
            cleaned = response.replace("```json", "").replace("```", "").strip()
            match = re.search(r'\[[\d\s.,]+\]', cleaned)
            if match:
                order = json.loads(match.group())
            else:
                order = json.loads(cleaned)

            if sorted(order) == list(range(1, len(image_prompts) + 1)):
                reordered = [image_prompts[i - 1] for i in order]
                if order != list(range(1, len(image_prompts) + 1)):
                    info(f" => LLM reordered images: {order}")
                else:
                    info(" => LLM kept original image order")
                return reordered
        except (json.JSONDecodeError, TypeError, IndexError):
            if get_verbose():
                warning(f"Invalid reorder response (attempt {attempt+1}): {response[:100]}")

    warning("Failed to get valid image order. Keeping original.")
    return image_prompts


def plan_pause_timing(segments: list) -> list:
    """Decide pause duration after each segment. Uses qwen3:8b.

    Returns list of floats (seconds) — one per segment.
    """
    seg_list = "\n".join([
        f"{i+1}. \"{seg['text']}\" (instruct: {seg.get('instruct', 'none')})"
        for i, seg in enumerate(segments)
    ])

    for attempt in range(3):
        response = _ask_llm(
            f"You are timing pauses between narration sentences for a YouTube Short video.\n\n"
            f"Segments:\n{seg_list}\n\n"
            f"Decide how many seconds of silence to add AFTER each segment.\n"
            f"Guidelines:\n"
            f"- Normal sentences: 0.2-0.4s\n"
            f"- Emphasis/emotional sentences: 0.4-0.6s\n"
            f"- Before twist or mood change: 0.6-0.9s\n"
            f"- Before final punchline/moral: 0.8-1.2s\n"
            f"- After the very last sentence: 1.0-1.5s (let it land)\n\n"
            f"Return ONLY a JSON array of {len(segments)} floats.\n"
            f"Example: [0.3, 0.3, 0.5, 0.8, 0.3, 1.2]",
            FAST_MODEL
        ).strip()

        try:
            cleaned = response.replace("```json", "").replace("```", "").strip()
            match = re.search(r'\[[\d\s.,]+\]', cleaned)
            if match:
                pauses = json.loads(match.group())
            else:
                pauses = json.loads(cleaned)

            if len(pauses) == len(segments) and all(0 <= p <= 2.0 for p in pauses):
                info(f" => LLM planned pauses: {pauses}")
                return pauses
        except (json.JSONDecodeError, TypeError):
            if get_verbose():
                warning(f"Invalid pause response (attempt {attempt+1}): {response[:100]}")

    # Fallback: simple rule-based
    warning("Failed to get LLM pauses. Using rule-based fallback.")
    pauses = []
    for i, seg in enumerate(segments):
        weight = seg.get("duration_weight", 1.0)
        if i == len(segments) - 1:
            pauses.append(1.2)
        elif seg.get("is_hook"):
            pauses.append(0.6)
        elif weight >= 1.5:
            pauses.append(0.7)
        else:
            pauses.append(0.3)
    return pauses


def calculate_image_timing(segments: list, audio_duration: float, num_images: int) -> list:
    """Calculate timing from duration_weight. No LLM needed."""
    weights = [seg.get("duration_weight", 1.0) for seg in segments]

    # If more segments than images, group weights
    if len(weights) > num_images:
        group_size = len(weights) / num_images
        grouped = []
        for i in range(num_images):
            start = int(i * group_size)
            end = int((i + 1) * group_size)
            grouped.append(sum(weights[start:end]))
        weights = grouped
    elif len(weights) < num_images:
        # Pad with 1.0
        weights.extend([1.0] * (num_images - len(weights)))

    total_weight = sum(weights)
    timing = [round(w / total_weight * audio_duration, 2) for w in weights]

    # Ensure minimum 1.5s per image
    timing = [max(1.5, t) for t in timing]
    # Re-normalize
    total = sum(timing)
    timing = [round(t * audio_duration / total, 2) for t in timing]

    info(f" => Image timing (from weights): {timing}")
    return timing


def pick_music(script_text: str, songs_by_mood: dict) -> str:
    """Pick specific song that best fits the story. Uses qwen3:8b."""
    if not songs_by_mood:
        return None

    song_list = []
    for mood, files in songs_by_mood.items():
        for f in files:
            song_list.append(f"{mood}/{f}")

    if not song_list:
        return None

    response = _ask_llm(
        f"Given this story:\n{script_text}\n\n"
        f"Pick the single best background music from:\n" +
        "\n".join([f"- {s}" for s in song_list]) + "\n\n"
        f"Consider the emotional tone.\n"
        f"Return ONLY the path (e.g. 'calm/Lost Frontier - Kevin MacLeod.mp3'), nothing else.",
        FAST_MODEL
    ).strip().strip("'\"")

    songs_dir = os.path.join(ROOT_DIR, "Songs")
    full_path = os.path.join(songs_dir, response)
    if os.path.exists(full_path):
        info(f" => LLM picked music: {response}")
        return full_path

    for s in song_list:
        if s.lower() in response.lower() or response.lower() in s.lower():
            full_path = os.path.join(songs_dir, s)
            if os.path.exists(full_path):
                info(f" => LLM picked music (partial match): {s}")
                return full_path

    first = song_list[0]
    warning(f"LLM returned '{response}' — not found. Using {first}")
    return os.path.join(songs_dir, first)


def pick_subtitle_style(script_text: str, fonts_list: list) -> dict:
    """Pick font and subtitle color. Uses qwen3:8b."""
    fonts_str = ", ".join(fonts_list) if fonts_list else "bold_font.ttf"

    response = _ask_llm(
        f"Given this story:\n{script_text}\n\n"
        f"Available fonts: {fonts_str}\n\n"
        f"Pick the best font and subtitle color.\n"
        f'Return ONLY a JSON object: {{"font": "bold_font.ttf", "color": "#FFFF00"}}\n'
        f"Color should be readable on dark backgrounds.\n"
        f"Warm yellow for emotional, white for dramatic, soft blue for calm.",
        FAST_MODEL
    ).strip()

    try:
        cleaned = response.replace("```json", "").replace("```", "").strip()
        match = re.search(r'\{[^}]+\}', cleaned)
        if match:
            result = json.loads(match.group())
            font = result.get("font", "bold_font.ttf")
            color = result.get("color", "#FFFF00")

            if font not in fonts_list:
                font = fonts_list[0] if fonts_list else "bold_font.ttf"
            if not re.match(r'^#[0-9A-Fa-f]{6}$', color):
                color = "#FFFF00"

            info(f" => LLM subtitle style: font={font}, color={color}")
            return {"font": font, "color": color}
    except (json.JSONDecodeError, TypeError):
        pass

    warning("Failed to parse subtitle style. Using defaults.")
    return {"font": fonts_list[0] if fonts_list else "bold_font.ttf", "color": "#FFFF00"}
