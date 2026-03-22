"""ScriptVideoGenerator — orchestrates YouTube Short creation from script JSON."""

import os
import json
import re
import shutil
import requests
import time as _time

from typing import List

from config import *
from status import *
from utils import get_available_moods
from classes.video_generator.image_providers import generate_image_freepik
from .progress import ProgressTracker, STEPS
from .tts_generator import generate_segment_tts
from .composer import compose_script_video
from .logger import write_script_log
from .smart_brain import pick_voice_profile, reorder_images, pick_music, pick_subtitle_style

AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".aac", ".ogg")


class ScriptVideoGenerator:
    """Generate YouTube Short from a segment-based script JSON."""

    def __init__(self, script_data: dict, script_path: str) -> None:
        self._script = script_data
        self._script_path = script_path
        self._title = script_data.get("title", "Untitled")
        self._segments = script_data.get("segments", [])

        safe_title = re.sub(r'[^\w\s-]', '', self._title)[:60].strip()
        safe_title = re.sub(r'\s+', '_', safe_title) or "Untitled"
        self._folder_name = safe_title
        self._output_dir = os.path.join(ROOT_DIR, "output", safe_title)
        self._images_dir = os.path.join(self._output_dir, "images")
        os.makedirs(self._images_dir, exist_ok=True)

        self._progress = ProgressTracker(self._output_dir, self._title)

        # Copy script to output
        script_copy = os.path.join(self._output_dir, "script.json")
        if not os.path.exists(script_copy):
            with open(script_copy, "w") as f:
                json.dump(script_data, f, indent=2)

        # Save script_path in progress for resume cleanup
        prog = self._progress.get()
        if script_path and not prog.get("script_source_path"):
            prog["script_source_path"] = script_path
            self._progress.save(prog)

    def _full_narration(self) -> str:
        return " ".join(seg["text"] for seg in self._segments if seg.get("text"))

    def _load_existing_images(self) -> List[str]:
        supported = (".png", ".jpg", ".jpeg", ".webp")
        return sorted([
            os.path.join(self._images_dir, f)
            for f in os.listdir(self._images_dir)
            if f.lower().endswith(supported)
        ])

    def _persist_image(self, image_bytes: bytes, provider_label: str) -> str:
        existing = self._load_existing_images()
        idx = len(existing) + 1
        image_path = os.path.join(self._images_dir, f"{idx:02d}.png")
        with open(image_path, "wb") as f:
            f.write(image_bytes)
        if get_verbose():
            info(f' => Wrote image from {provider_label} to "{image_path}"')
        return image_path

    # ── Pipeline steps ──

    def _select_profile(self) -> str:
        if self._progress.is_step_done("profile"):
            profile_id = self._progress.get().get("profile_id", "")
            info(f" => Voice profile (resumed): {profile_id[:20]}...")
            return profile_id

        voicebox_url = get_voicebox_url().rstrip("/")
        instructs = [seg.get("instruct", "") for seg in self._segments if seg.get("instruct")]

        r = requests.get(f"{voicebox_url}/profiles", timeout=10)
        r.raise_for_status()
        profiles = r.json()

        if not profiles:
            raise RuntimeError("No VoiceBox profiles found.")

        profile_id = pick_voice_profile(instructs, profiles)
        if not profile_id:
            raise RuntimeError("Failed to select voice profile.")

        prog = self._progress.get()
        prog["profile_id"] = profile_id
        self._progress.save(prog)
        self._progress.mark_step("profile")
        return profile_id

    def _generate_tts(self, profile_id: str) -> str:
        audio_path = os.path.join(self._output_dir, "audio.wav")

        if self._progress.is_step_done("tts") and os.path.exists(audio_path):
            info(" => TTS audio (resumed)")
            return audio_path

        try:
            result = generate_segment_tts(
                self._segments,
                self._script.get("voice_config", {}),
                profile_id,
                self._output_dir,
            )
        except Exception as e:
            self._progress.mark_error("tts", str(e))
            raise

        self._progress.mark_step("tts")
        return result

    def _generate_images(self) -> List[str]:
        prompts = self._script.get("image_prompts", [])

        if self._progress.is_step_done("images"):
            existing = self._load_existing_images()
            info(f" => Images (resumed): {len(existing)}")
            return existing

        existing = self._load_existing_images()
        start_from = len(existing)

        if start_from > 0:
            info(f" => Found {start_from} existing images, resuming from {start_from + 1}")

        for i, prompt in enumerate(prompts):
            if i < start_from:
                continue

            result = None
            for attempt in range(3):
                result = generate_image_freepik(prompt, self._persist_image)
                if result:
                    break
                if attempt < 2:
                    warning(f"Image {i+1} failed (attempt {attempt+1}/3). Retrying in 10s...")
                    _time.sleep(10)

            if result is None:
                self._progress.mark_error("images", f"Failed image {i+1}/{len(prompts)} after 3 retries")
                raise RuntimeError(f"Image generation failed at image {i+1}. Run again to resume.")

            if i < len(prompts) - 1:
                _time.sleep(10)

        self._progress.mark_step("images")
        return self._load_existing_images()

    def _reorder_images(self, images: List[str]) -> List[str]:
        if self._progress.is_step_done("image_order"):
            info(" => Image order (resumed)")
            return self._load_existing_images()

        prompts = self._script.get("image_prompts", [])
        new_order = reorder_images(self._segments, prompts)

        if new_order != prompts:
            temp_dir = os.path.join(self._output_dir, "_temp_images")
            os.makedirs(temp_dir, exist_ok=True)

            prompt_to_file = {p: images[i] for i, p in enumerate(prompts) if i < len(images)}

            for i, p in enumerate(new_order):
                if p in prompt_to_file:
                    shutil.copy2(prompt_to_file[p], os.path.join(temp_dir, f"{i+1:02d}.png"))

            for f in os.listdir(self._images_dir):
                os.remove(os.path.join(self._images_dir, f))
            for f in os.listdir(temp_dir):
                shutil.move(os.path.join(temp_dir, f), os.path.join(self._images_dir, f))
            shutil.rmtree(temp_dir)

        self._progress.mark_step("image_order")
        return self._load_existing_images()

    def _decide_style(self) -> dict:
        if self._progress.is_step_done("style"):
            style = self._progress.get().get("style", {})
            info(f" => Style (resumed): {style}")
            return style

        story_text = self._full_narration()

        songs_by_mood = {}
        songs_dir = os.path.join(ROOT_DIR, "Songs")
        for mood in get_available_moods():
            mood_dir = os.path.join(songs_dir, mood)
            files = [f for f in os.listdir(mood_dir) if f.lower().endswith(AUDIO_EXTENSIONS)]
            if files:
                songs_by_mood[mood] = files

        song_path = pick_music(story_text, songs_by_mood)

        fonts_dir = get_fonts_dir()
        fonts = [f for f in os.listdir(fonts_dir) if f.endswith((".ttf", ".otf"))] if os.path.exists(fonts_dir) else []
        subtitle_style = pick_subtitle_style(story_text, fonts)

        style = {
            "song": song_path or "",
            "font": subtitle_style["font"],
            "color": subtitle_style["color"],
        }

        prog = self._progress.get()
        prog["style"] = style
        self._progress.save(prog)
        self._progress.mark_step("style")
        return style

    # ── Main pipeline ──

    def generate(self) -> str:
        try:
            self._progress.init()

            profile_id = self._select_profile()
            audio_path = self._generate_tts(profile_id)

            images = self._generate_images()
            if not images:
                self._progress.mark_error("images", "No images generated")
                error("No images were generated.")
                return None

            images = self._reorder_images(images)
            style = self._decide_style()

            video_path, timing, duration = compose_script_video(
                images, audio_path, self._output_dir, self._segments, style,
            )
            self._progress.mark_step("compose")

            # Save timing info
            prog = self._progress.get()
            prog["timing"] = timing
            prog["audio_duration"] = duration
            self._progress.save(prog)

            write_script_log(
                self._output_dir, self._title, self._segments,
                self._script.get("image_prompts", []),
                style, timing, duration, profile_id,
            )
            self._progress.mark_complete()

            # Delete original script from source/scripts/
            source_path = self._script_path or self._progress.get().get("script_source_path", "")
            if source_path and os.path.exists(source_path):
                os.remove(source_path)
                info(f" => Deleted source script: {source_path}")

            success(f" => Video complete: {self._output_dir}")
            return self._output_dir

        except Exception as e:
            for step in STEPS:
                if not self._progress.is_step_done(step):
                    self._progress.mark_error(step, str(e))
                    break
            error(f"Video generation failed: {str(e)}")
            return None

    # ── Static helpers ──

    @staticmethod
    def scan_scripts() -> List[dict]:
        scripts_dir = os.path.join(ROOT_DIR, "source", "scripts")
        if not os.path.exists(scripts_dir):
            os.makedirs(scripts_dir, exist_ok=True)
            return []

        files = [f for f in os.listdir(scripts_dir) if f.endswith(".json")]
        files.sort(key=lambda f: os.path.getmtime(os.path.join(scripts_dir, f)), reverse=True)

        results = []
        for f in files:
            path = os.path.join(scripts_dir, f)
            try:
                with open(path, "r") as fh:
                    data = json.load(fh)
                results.append({"filename": f, "path": path, "title": data.get("title", f), "data": data})
            except (json.JSONDecodeError, IOError):
                warning(f"Skipping invalid JSON: {f}")
        return results

    @staticmethod
    def find_resumable() -> List[dict]:
        from .progress import PROGRESS_FILE
        output_root = os.path.join(ROOT_DIR, "output")
        if not os.path.exists(output_root):
            return []

        resumable = []
        for name in sorted(os.listdir(output_root)):
            prog_path = os.path.join(output_root, name, PROGRESS_FILE)
            if not os.path.exists(prog_path):
                continue
            with open(prog_path, "r") as f:
                prog = json.load(f)
            if prog.get("status") != "complete":
                resumable.append({
                    "folder_name": name,
                    "title": prog.get("title", name),
                    "completed_steps": prog.get("completed_steps", []),
                    "last_error_step": prog.get("last_error_step", ""),
                    "last_error": prog.get("last_error", ""),
                })
        return resumable

    @classmethod
    def from_output_folder(cls, folder_name: str) -> "ScriptVideoGenerator":
        output_dir = os.path.join(ROOT_DIR, "output", folder_name)
        script_path = os.path.join(output_dir, "script.json")
        with open(script_path, "r") as f:
            data = json.load(f)
        instance = cls.__new__(cls)
        instance._script = data
        instance._script_path = ""
        instance._title = data.get("title", "Untitled")
        instance._segments = data.get("segments", [])
        instance._folder_name = folder_name
        instance._output_dir = output_dir
        instance._images_dir = os.path.join(output_dir, "images")
        instance._progress = ProgressTracker(output_dir, instance._title)
        os.makedirs(instance._images_dir, exist_ok=True)
        return instance
