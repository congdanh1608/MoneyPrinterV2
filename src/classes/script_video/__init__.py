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
from .progress import ProgressTracker, STEPS
from .tts_generator import generate_segment_tts
from .composer import compose_script_video
from .logger import write_script_log
from .smart_brain import pick_voice_profile, reorder_images, pick_music, pick_subtitle_style
from .image_generator import ScriptImageGenerator

AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".aac", ".ogg")


class ScriptVideoGenerator:
    """Generate YouTube Short from a segment-based script JSON."""

    def __init__(self, script_data: dict, script_path: str, image_provider=None) -> None:
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

        self._image_provider = image_provider  # Shared GoogleLabsProvider

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

    # ── Pipeline steps ──

    def _select_profile(self) -> str:
        if self._progress.is_step_done("profile"):
            profile_id = self._progress.get().get("profile_id", "")
            info(f" => Voice profile (resumed): {profile_id[:20]}...")
            return profile_id

        voicebox_url = get_voicebox_url().rstrip("/")
        instructs = [seg.get("instruct", "") for seg in self._segments if seg.get("instruct")]

        # Retry connection to VoiceBox (may be starting up)
        profiles = None
        for attempt in range(3):
            try:
                r = requests.get(f"{voicebox_url}/profiles", timeout=10)
                r.raise_for_status()
                profiles = r.json()
                break
            except Exception as e:
                if attempt < 2:
                    warning(f"VoiceBox not reachable (attempt {attempt+1}/3): {e}. Retrying in 5s...")
                    _time.sleep(5)
                else:
                    raise RuntimeError(f"Cannot reach VoiceBox at {voicebox_url} after 3 attempts: {e}")

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
        remaining_prompts = prompts[start_from:]

        if start_from > 0:
            info(f" => Found {start_from} existing images, resuming from {start_from + 1}")

        if remaining_prompts:
            if not self._image_provider:
                raise RuntimeError("No image provider. Pass GoogleLabsProvider to constructor.")

            generator = ScriptImageGenerator(self._images_dir, self._image_provider)

            try:
                generator.generate_images(remaining_prompts)
            except Exception as e:
                self._progress.mark_error("images", str(e))
                raise

            self._project_url = generator.get_project_url()

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

            # Run TTS and image generation in parallel (they are independent)
            from concurrent.futures import ThreadPoolExecutor, as_completed

            tts_done = self._progress.is_step_done("tts")
            images_done = self._progress.is_step_done("images")

            audio_path = None
            images = None
            errors = []

            if tts_done and images_done:
                # Both resumed
                audio_path = self._generate_tts(profile_id)
                images = self._generate_images()
            elif tts_done or images_done:
                # One done, run the other
                if tts_done:
                    audio_path = self._generate_tts(profile_id)
                    images = self._generate_images()
                else:
                    images = self._generate_images()
                    audio_path = self._generate_tts(profile_id)
            else:
                # Neither done — run in parallel
                info(" => Running TTS and image generation in parallel...")

                with ThreadPoolExecutor(max_workers=2) as executor:
                    future_tts = executor.submit(self._generate_tts, profile_id)
                    future_images = executor.submit(self._generate_images)

                    for future in as_completed([future_tts, future_images]):
                        try:
                            future.result()
                        except Exception as e:
                            errors.append(str(e))

                if errors:
                    raise RuntimeError(f"Parallel step failed: {'; '.join(errors)}")

                audio_path = future_tts.result()
                images = future_images.result()

            if not images:
                self._progress.mark_error("images", "No images generated")
                error("No images were generated.")
                return None

            images = self._reorder_images(images)
            style = self._decide_style()

            # Compose with 1 retry on failure (e.g. RAM issue)
            compose_error = None
            for compose_attempt in range(2):
                try:
                    video_path, timing, duration = compose_script_video(
                        images, audio_path, self._output_dir, self._segments, style,
                    )
                    self._progress.mark_step("compose")
                    compose_error = None
                    break
                except Exception as e:
                    compose_error = e
                    if compose_attempt == 0:
                        warning(f"Compose failed: {e}. Retrying once...")
                        _time.sleep(3)
            if compose_error:
                raise compose_error

            # Save timing info
            prog = self._progress.get()
            prog["timing"] = timing
            prog["audio_duration"] = duration
            self._progress.save(prog)

            write_script_log(
                self._output_dir, self._title, self._segments,
                self._script.get("image_prompts", []),
                style, timing, duration, profile_id,
                project_url=getattr(self, '_project_url', ''),
            )
            self._progress.mark_complete()

            # Delete original script from source/scripts/
            source_path = self._script_path or self._progress.get().get("script_source_path", "")
            if source_path and os.path.exists(source_path):
                os.remove(source_path)
                info(f" => Deleted source script: {source_path}")

            success(f" => Video complete: {self._output_dir}")
            return self._output_dir

        except RuntimeError as e:
            # Known errors (VoiceBox down, image blocked, etc.)
            for step in STEPS:
                if not self._progress.is_step_done(step):
                    self._progress.mark_error(step, str(e))
                    break
            error(f"Video generation failed: {str(e)}")
            info("Run again to resume from the failed step.")
            return None
        except Exception as e:
            # Unexpected errors
            for step in STEPS:
                if not self._progress.is_step_done(step):
                    self._progress.mark_error(step, f"Unexpected: {str(e)}")
                    break
            error(f"Unexpected error: {str(e)}")
            info("Run again to resume from the failed step.")
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
    def from_output_folder(cls, folder_name: str, image_provider=None) -> "ScriptVideoGenerator":
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
        instance._image_provider = image_provider
        instance._progress = ProgressTracker(output_dir, instance._title)
        os.makedirs(instance._images_dir, exist_ok=True)
        return instance
