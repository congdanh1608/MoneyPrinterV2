"""VideoGenerator package - generates YouTube Shorts-style videos."""

import re
import os
import json
import time as _time

from uuid import uuid4
from typing import List
from datetime import datetime

from config import *
from status import *
from utils import get_available_moods
from llm_provider import generate_text
from .state import load_video_state, save_video_state, find_resumable
from .image_providers import generate_image_replicate, generate_image_gemini, generate_image_freepik
from .composer import compose_video
from .logger import write_log


class VideoGenerator:
    """
    Generates YouTube Shorts-style videos.

    Folder structure:
        source/<video_id>/       - images, audio, srt (persistent)
        output/<video_id>/       - final mp4 + log txt
        source/.state.json       - tracks progress for resume
    """

    STEPS = ["topic", "script", "metadata", "prompts", "images", "tts", "combine"]

    def __init__(self, niche: str, language: str, video_id: str = None) -> None:
        self._niche = niche
        self._language = language
        self.images = []
        self.image_prompts = []
        self._mood = None

        self._video_id = video_id or datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + str(uuid4())[:8]
        self._source_dir = os.path.join(ROOT_DIR, "source", self._video_id)
        self._output_dir = os.path.join(ROOT_DIR, "output", self._video_id)
        os.makedirs(self._source_dir, exist_ok=True)
        os.makedirs(self._output_dir, exist_ok=True)

    @property
    def niche(self) -> str:
        return self._niche

    @property
    def language(self) -> str:
        return self._language

    # ── State helpers ──

    def _get_state(self) -> dict:
        return load_video_state(self._source_dir)

    def _save_state(self, state: dict) -> None:
        save_video_state(self._source_dir, state)

    def _mark_step(self, step: str) -> None:
        state = self._get_state()
        completed = state.get("completed_steps", [])
        if step not in completed:
            completed.append(step)
        state["completed_steps"] = completed
        self._save_state(state)

    def _is_step_done(self, step: str) -> bool:
        return step in self._get_state().get("completed_steps", [])

    def _mark_error(self, step: str, error_msg: str) -> None:
        state = self._get_state()
        state.update(last_error_step=step, last_error=error_msg, status="error")
        self._save_state(state)

    def _mark_complete(self) -> None:
        state = self._get_state()
        state.update(status="complete", last_error_step=None, last_error=None)
        self._save_state(state)

    def _init_state(self) -> None:
        """Initialize state.json with minimal info."""
        state = self._get_state()
        if not state:
            self._save_state({
                "niche": self._niche,
                "language": self._language,
                "status": "in_progress",
                "completed_steps": [],
            })

    def restore_from_state(self) -> None:
        """Restore in-memory data from source files (not state)."""
        state = self._get_state()
        self.subject = state.get("topic", "")

        # Read script from source file
        script_path = os.path.join(self._source_dir, "script.txt")
        if os.path.exists(script_path):
            with open(script_path, "r") as f:
                self.script = f.read().strip()
        else:
            self.script = ""

        # Read metadata from source file
        meta_path = os.path.join(self._source_dir, "metadata.json")
        if os.path.exists(meta_path):
            with open(meta_path, "r") as f:
                self.metadata = json.load(f)
        else:
            self.metadata = {"title": "", "description": ""}

        # Read prompts from source file
        prompts_path = os.path.join(self._source_dir, "prompts.json")
        if os.path.exists(prompts_path):
            with open(prompts_path, "r") as f:
                self.image_prompts = json.load(f)
        else:
            self.image_prompts = []

        # Reload images
        supported = (".png", ".jpg", ".jpeg", ".webp")
        self.images = sorted([
            os.path.join(self._source_dir, f)
            for f in os.listdir(self._source_dir)
            if f.lower().endswith(supported)
        ])

        # Reload audio
        wavs = [f for f in os.listdir(self._source_dir) if f.endswith(".wav")]
        if wavs:
            self.tts_path = os.path.join(self._source_dir, sorted(wavs)[0])

    def _make_folder_name(self) -> str:
        """Create a readable folder name from title only."""
        title = self.metadata.get("title", "")
        safe_title = re.sub(r'[^\w\s-]', '', title)[:60].strip()
        safe_title = re.sub(r'\s+', '_', safe_title)
        if not safe_title:
            return self._video_id
        # Avoid collision
        base = safe_title
        source_root = os.path.join(ROOT_DIR, "source")
        counter = 2
        while os.path.exists(os.path.join(source_root, safe_title)) and safe_title != os.path.basename(self._source_dir):
            safe_title = f"{base}_{counter}"
            counter += 1
        return safe_title

    def _rename_dirs_by_title(self) -> None:
        """Rename source/ and output/ subdirs to include title."""
        new_name = self._make_folder_name()
        new_source = os.path.join(ROOT_DIR, "source", new_name)
        new_output = os.path.join(ROOT_DIR, "output", new_name)

        if new_source == self._source_dir:
            return

        if os.path.exists(self._source_dir) and not os.path.exists(new_source):
            os.rename(self._source_dir, new_source)
            self._source_dir = new_source

        if os.path.exists(self._output_dir) and not os.path.exists(new_output):
            os.rename(self._output_dir, new_output)
            self._output_dir = new_output

        # Update video_id in state
        all_state = load_state()
        if self._video_id in all_state:
            data = all_state.pop(self._video_id)
            self._video_id = new_name
            all_state[new_name] = data
            save_state(all_state)
        else:
            self._video_id = new_name

        if get_verbose():
            info(f" => Renamed folders to: {new_name}")

    # ── Content generation ──

    def generate_topic(self) -> str:
        if self._is_step_done("topic"):
            self.subject = self._get_state().get("topic", "")
            info(f" => Topic (resumed): {self.subject}")
            return self.subject

        completion = generate_text(
            f"Please generate a specific video idea that takes about the following topic: {self.niche}. Make it exactly one sentence. Only return the topic, nothing else."
        )
        if not completion:
            error("Failed to generate Topic.")
        self.subject = completion
        state = self._get_state()
        state["topic"] = completion
        self._save_state(state)
        self._mark_step("topic")
        return completion

    def generate_script(self) -> str:
        if self._is_step_done("script"):
            script_path = os.path.join(self._source_dir, "script.txt")
            if os.path.exists(script_path):
                with open(script_path, "r") as f:
                    self.script = f.read().strip()
            info(f" => Script (resumed): {self.script[:60]}...")
            return self.script

        prompt = f"""
You are a viral short-form storyteller.

Write a short story with:
- A strong emotional hook in the first sentence
- A compelling story (5-8 sentences)
- A surprising twist ending
- A powerful life lesson

Rules:
- Keep sentences short (5-12 words each)
- Write between {get_script_min_words()} and {get_script_max_words()} words
- Use simple, everyday words
- Make it emotional or shocking
- Sound like a real person telling a story, not AI
- Do NOT explain anything
- No introductions, no filler words
- No hashtags, no emojis, no quotation marks
- No markdown, no formatting, no titles

Total length: {get_script_min_words()}-{get_script_max_words()} words.

ONLY RETURN THE RAW STORY TEXT. NOTHING ELSE.

Topic: {self.subject}
Language: {self.language}
"""
        completion = generate_text(prompt)
        completion = re.sub(r"[*\"#]", "", completion).strip()

        if not completion:
            error("The generated script is empty.")
            return

        word_count = len(completion.split())
        min_words = get_script_min_words()
        max_words = int(get_script_max_words() * 1.25)

        if word_count < min_words:
            if get_verbose():
                warning(f"Generated script too short ({word_count} words, min {min_words}). Retrying...")
            return self.generate_script()

        if word_count > max_words:
            if get_verbose():
                warning(f"Generated script too long ({word_count} words, max {max_words}). Retrying...")
            return self.generate_script()

        self.script = completion
        with open(os.path.join(self._source_dir, "script.txt"), "w") as f:
            f.write(completion)
        self._mark_step("script")
        return completion

    def detect_mood(self) -> str:
        moods = get_available_moods()
        if not moods:
            self._mood = None
            return None

        response = generate_text(
            f"Given this script, pick the single most fitting mood from: [{', '.join(moods)}]. "
            f"Return ONLY the mood word, nothing else.\n\nScript: {self.script}"
        ).strip().lower()

        self._mood = response if response in [m.lower() for m in moods] else None

        if self._mood:
            info(f" => Detected mood: {self._mood}")
        else:
            if get_verbose():
                warning(f"LLM returned '{response}' which doesn't match any mood folder. Using random.")

        state = self._get_state()
        state["mood"] = self._mood
        self._save_state(state)
        return self._mood

    def generate_metadata(self) -> dict:
        if self._is_step_done("metadata"):
            meta_path = os.path.join(self._source_dir, "metadata.json")
            if os.path.exists(meta_path):
                with open(meta_path, "r") as f:
                    self.metadata = json.load(f)
            info(f" => Metadata (resumed): {self.metadata.get('title', '')[:60]}")
            return self.metadata

        max_len = get_title_max_length()
        title = generate_text(
            f"Write a short YouTube Shorts title (max {max_len} characters) with 2-3 hashtags for: {self.subject}. Return ONLY the title. Keep it short and catchy."
        )
        title = re.sub(r"[*\"]", "", title).strip()
        if len(title) > max_len:
            if get_verbose():
                warning(f"Generated Title too long ({len(title)} chars, max {max_len}). Retrying...")
            return self.generate_metadata()

        description = generate_text(
            f"Please generate a YouTube Video Description for the following script: {self.script}. Only return the description, nothing else."
        )

        self.metadata = {"title": title, "description": description}
        with open(os.path.join(self._source_dir, "metadata.json"), "w") as f:
            json.dump(self.metadata, f, indent=2)
        self._mark_step("metadata")
        self._rename_dirs_by_title()
        return self.metadata

    def generate_prompts(self) -> List[str]:
        if self._is_step_done("prompts"):
            prompts_path = os.path.join(self._source_dir, "prompts.json")
            if os.path.exists(prompts_path):
                with open(prompts_path, "r") as f:
                    self.image_prompts = json.load(f)
            info(f" => Image prompts (resumed): {len(self.image_prompts)} prompts")
            return self.image_prompts

        n_prompts = max(3, min(5, len(self.script.split('.')) - 1))
        prompt = f"""
Generate exactly {n_prompts} image prompts for AI image generation.
Each prompt should describe a vivid, cinematic scene that matches the story.

Subject: {self.subject}

Rules:
- Return ONLY a JSON array of strings, nothing else
- Each prompt should be one detailed sentence
- Make prompts emotional, dramatic, and visually striking
- Prompts should follow the story progression

Example format:
["scene 1 description", "scene 2 description", "scene 3 description"]

Story for context:
{self.script}
"""
        completion = str(generate_text(prompt)).replace("```json", "").replace("```", "")

        image_prompts = []
        if "image_prompts" in completion:
            image_prompts = json.loads(completion)["image_prompts"]
        else:
            try:
                image_prompts = json.loads(completion)
                if get_verbose():
                    info(f" => Generated Image Prompts: {image_prompts}")
            except Exception:
                if get_verbose():
                    warning("LLM returned an unformatted response. Attempting to clean...")
                r = re.compile(r"\[.*\]")
                image_prompts = r.findall(completion)
                if len(image_prompts) == 0:
                    if get_verbose():
                        warning("Failed to generate Image Prompts. Retrying...")
                    return self.generate_prompts()

        if len(image_prompts) > n_prompts:
            image_prompts = image_prompts[:int(n_prompts)]

        self.image_prompts = image_prompts
        with open(os.path.join(self._source_dir, "prompts.json"), "w") as f:
            json.dump(image_prompts, f, indent=2)
        self._mark_step("prompts")
        success(f"Generated {len(image_prompts)} Image Prompts.")
        return image_prompts

    # ── Image generation ──

    def _persist_image(self, image_bytes: bytes, provider_label: str) -> str:
        idx = len(self.images) + 1
        image_path = os.path.join(self._source_dir, f"{idx:02d}.png")
        with open(image_path, "wb") as f:
            f.write(image_bytes)
        if get_verbose():
            info(f' => Wrote image from {provider_label} to "{image_path}"')
        self.images.append(image_path)
        return image_path

    def generate_images(self) -> None:
        if self._is_step_done("images"):
            supported = (".png", ".jpg", ".jpeg", ".webp")
            self.images = sorted([
                os.path.join(self._source_dir, f)
                for f in os.listdir(self._source_dir)
                if f.lower().endswith(supported)
            ])
            info(f" => Images (resumed): {len(self.images)} images")
            return

        supported = (".png", ".jpg", ".jpeg", ".webp")
        existing = sorted([
            os.path.join(self._source_dir, f)
            for f in os.listdir(self._source_dir)
            if f.lower().endswith(supported)
        ])
        self.images = existing
        start_from = len(existing)

        if start_from > 0:
            info(f" => Found {start_from} existing images, resuming from image {start_from + 1}")

        provider = get_image_provider()
        for i, prompt in enumerate(self.image_prompts):
            if i < start_from:
                continue

            if provider == "replicate":
                result = generate_image_replicate(prompt, self._persist_image)
            elif provider == "freepik":
                result = generate_image_freepik(prompt, self._persist_image)
            else:
                result = generate_image_gemini(prompt, self._persist_image)

            if result is None:
                self._mark_error("images", f"Failed to generate image {i+1}/{len(self.image_prompts)}")
                raise RuntimeError(f"Image generation failed at image {i+1}. Run again to resume.")

            if i < len(self.image_prompts) - 1:
                _time.sleep(10)

        if len(self.images) == len(self.image_prompts):
            self._mark_step("images")

    # ── Audio ──

    def generate_script_to_speech(self, tts_instance) -> str:
        if self._is_step_done("tts"):
            wavs = [f for f in os.listdir(self._source_dir) if f.endswith(".wav")]
            if wavs:
                self.tts_path = os.path.join(self._source_dir, sorted(wavs)[0])
                info(f" => TTS (resumed): {self.tts_path}")
                return self.tts_path

        path = os.path.join(self._source_dir, "audio.wav")
        self.script = re.sub(r"[^\w\s.?!]", "", self.script)
        try:
            tts_instance.synthesize(self.script, path)
        except Exception as e:
            self._mark_error("tts", str(e))
            raise RuntimeError(f"TTS failed: {e}. Run again to resume.")

        if not os.path.exists(path) or os.path.getsize(path) == 0:
            self._mark_error("tts", "Audio file is empty or missing")
            raise RuntimeError("TTS produced no audio. Run again to resume.")

        self.tts_path = path
        self._mark_step("tts")
        if get_verbose():
            info(f' => Wrote TTS to "{path}"')
        return path

    # ── Main pipeline ──

    def generate_video(self, tts_instance) -> str:
        try:
            self._init_state()
            self.generate_topic()
            self.generate_script()
            self.detect_mood()
            self.generate_metadata()
            self.generate_prompts()
            self.generate_images()

            if not self.images:
                self._mark_error("images", "No images generated")
                error("No images were generated. Check your config (image_provider, API keys).")
                return None

            self.generate_script_to_speech(tts_instance)

            video_path, bg_song, duration = compose_video(
                self.images, self.tts_path,
                self._output_dir, self._source_dir,
                mood=self._mood,
            )
            self._mark_step("combine")

            write_log(
                self._output_dir, self._video_id, self._source_dir,
                self._niche, self._language, self.subject, self.script,
                self.metadata, self.image_prompts, self.images,
                bg_song, duration, mood=self._mood,
            )
            self._mark_complete()

            if get_verbose():
                info(f" => Video complete: {self._output_dir}")
            return self._output_dir

        except Exception as e:
            for step in self.STEPS:
                if not self._is_step_done(step):
                    self._mark_error(step, str(e))
                    break
            error(f"Video generation failed: {str(e)}")
            return None
