import re
import base64
import json
import os
import requests
import assemblyai as aai
import time as _time

from utils import *
from cache import *
from .Tts import TTS
from llm_provider import generate_text
from config import *
from status import *
from uuid import uuid4
from typing import List
from moviepy.editor import *
from moviepy.video.fx.all import crop
from moviepy.config import change_settings
from moviepy.video.tools.subtitles import SubtitlesClip
from datetime import datetime
from termcolor import colored

# Set ImageMagick Path
change_settings({"IMAGEMAGICK_BINARY": get_imagemagick_path()})

# State file to track progress per video
STATE_FILE = os.path.join(ROOT_DIR, "source", ".state.json")


def _load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}


def _save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


class VideoGenerator:
    """
    Generates YouTube Shorts-style videos.

    Folder structure:
        source/<video_id>/       - images, audio, srt (persistent, never auto-deleted)
        output/<video_id>/       - final mp4 + log txt
        source/.state.json       - tracks progress for resume
    """

    STEPS = ["topic", "script", "metadata", "prompts", "images", "tts", "combine"]

    def __init__(self, niche: str, language: str, video_id: str = None) -> None:
        self._niche = niche
        self._language = language
        self.images = []
        self.image_prompts = []

        # Generate or use provided video_id
        self._video_id = video_id or datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + str(uuid4())[:8]

        # Setup directories
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

    # ── State management ──

    def _get_state(self) -> dict:
        all_state = _load_state()
        return all_state.get(self._video_id, {})

    def _update_state(self, **kwargs) -> None:
        all_state = _load_state()
        if self._video_id not in all_state:
            all_state[self._video_id] = {"niche": self._niche, "language": self._language}
        all_state[self._video_id].update(kwargs)
        _save_state(all_state)

    def _mark_step(self, step: str) -> None:
        state = self._get_state()
        completed = state.get("completed_steps", [])
        if step not in completed:
            completed.append(step)
        self._update_state(completed_steps=completed)

    def _is_step_done(self, step: str) -> bool:
        state = self._get_state()
        return step in state.get("completed_steps", [])

    def _mark_error(self, step: str, error_msg: str) -> None:
        self._update_state(last_error_step=step, last_error=error_msg, status="error")

    def _mark_complete(self) -> None:
        self._update_state(status="complete", last_error_step=None, last_error=None)

    def _restore_from_state(self) -> None:
        """Restore in-memory state from saved state + source files."""
        state = self._get_state()
        self.subject = state.get("topic_text", "")
        self.script = state.get("script_text", "")
        self.metadata = state.get("metadata", {"title": "", "description": ""})
        self.image_prompts = state.get("image_prompts", [])

        # Reload images from source dir
        supported = (".png", ".jpg", ".jpeg", ".webp")
        self.images = sorted([
            os.path.join(self._source_dir, f)
            for f in os.listdir(self._source_dir)
            if f.lower().endswith(supported)
        ])

        # Reload audio
        wavs = sorted([
            os.path.join(self._source_dir, f)
            for f in os.listdir(self._source_dir)
            if f.endswith(".wav")
        ])
        if wavs:
            self.tts_path = wavs[0]

    # ── Content generation ──

    def generate_response(self, prompt: str) -> str:
        return generate_text(prompt)

    def generate_topic(self) -> str:
        if self._is_step_done("topic"):
            self.subject = self._get_state().get("topic_text", "")
            info(f" => Topic (resumed): {self.subject}")
            return self.subject

        completion = self.generate_response(
            f"Please generate a specific video idea that takes about the following topic: {self.niche}. Make it exactly one sentence. Only return the topic, nothing else."
        )

        if not completion:
            error("Failed to generate Topic.")

        self.subject = completion
        self._update_state(topic_text=completion)
        self._mark_step("topic")
        return completion

    def generate_script(self) -> str:
        if self._is_step_done("script"):
            self.script = self._get_state().get("script_text", "")
            info(f" => Script (resumed): {self.script[:60]}...")
            return self.script

        prompt = f"""
You are a viral short-form storyteller.

Write a very short story with:
- A strong emotional hook in the first sentence
- A simple story (2-4 sentences)
- A surprising twist ending
- A powerful life lesson

Rules:
- Keep sentences very short (5-10 words each)
- Use simple, everyday words
- Make it emotional or shocking
- Sound like a real person telling a story, not AI
- Do NOT explain anything
- No introductions, no filler words
- No hashtags, no emojis, no quotation marks
- No markdown, no formatting, no titles

Total length: under 80 words.

ONLY RETURN THE RAW STORY TEXT. NOTHING ELSE.

Topic: {self.subject}
Language: {self.language}
"""
        completion = self.generate_response(prompt)
        completion = re.sub(r"[*\"#]", "", completion).strip()

        if not completion:
            error("The generated script is empty.")
            return

        if len(completion.split()) > 100:
            if get_verbose():
                warning(f"Generated script too long ({len(completion.split())} words). Retrying...")
            return self.generate_script()

        self.script = completion
        self._update_state(script_text=completion)
        self._mark_step("script")
        return completion

    def _detect_mood(self) -> str:
        """Use LLM to detect mood from script, matching available Songs/ subfolders."""
        moods = get_available_moods()
        if not moods:
            self._mood = None
            return None

        mood_list = ", ".join(moods)
        response = self.generate_response(
            f"Given this script, pick the single most fitting mood from: [{mood_list}]. "
            f"Return ONLY the mood word, nothing else.\n\nScript: {self.script}"
        ).strip().lower()

        # Match to actual folder name
        self._mood = response if response in [m.lower() for m in moods] else None

        if self._mood:
            info(f" => Detected mood: {self._mood}")
        else:
            if get_verbose():
                warning(f"LLM returned '{response}' which doesn't match any mood folder. Using random.")
            self._mood = None

        self._update_state(mood=self._mood)
        return self._mood

    def generate_metadata(self) -> dict:
        if self._is_step_done("metadata"):
            self.metadata = self._get_state().get("metadata", {})
            info(f" => Metadata (resumed): {self.metadata.get('title', '')[:60]}")
            return self.metadata

        title = self.generate_response(
            f"Please generate a YouTube Video Title for the following subject, including hashtags: {self.subject}. Only return the title, nothing else. Limit the title under 100 characters."
        )

        if len(title) > 100:
            if get_verbose():
                warning("Generated Title is too long. Retrying...")
            return self.generate_metadata()

        description = self.generate_response(
            f"Please generate a YouTube Video Description for the following script: {self.script}. Only return the description, nothing else."
        )

        self.metadata = {"title": title, "description": description}
        self._update_state(metadata=self.metadata)
        self._mark_step("metadata")
        return self.metadata

    def generate_prompts(self) -> List[str]:
        if self._is_step_done("prompts"):
            self.image_prompts = self._get_state().get("image_prompts", [])
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

        completion = (
            str(self.generate_response(prompt))
            .replace("```json", "")
            .replace("```", "")
        )

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
            image_prompts = image_prompts[: int(n_prompts)]

        self.image_prompts = image_prompts
        self._update_state(image_prompts=image_prompts)
        self._mark_step("prompts")
        success(f"Generated {len(image_prompts)} Image Prompts.")
        return image_prompts

    # ── Image generation ──

    def _persist_image(self, image_bytes: bytes, provider_label: str) -> str:
        idx = len(self.images) + 1
        image_path = os.path.join(self._source_dir, f"{idx:02d}.png")

        with open(image_path, "wb") as image_file:
            image_file.write(image_bytes)

        if get_verbose():
            info(f' => Wrote image from {provider_label} to "{image_path}"')

        self.images.append(image_path)
        return image_path

    def generate_images(self) -> None:
        """Generate all images, skipping already generated ones."""
        if self._is_step_done("images"):
            # Reload from source dir
            supported = (".png", ".jpg", ".jpeg", ".webp")
            self.images = sorted([
                os.path.join(self._source_dir, f)
                for f in os.listdir(self._source_dir)
                if f.lower().endswith(supported)
            ])
            info(f" => Images (resumed): {len(self.images)} images")
            return

        # Check how many images already exist (partial resume)
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
                self._generate_image_replicate(prompt)
            else:
                self._generate_image_gemini(prompt)
            if i < len(self.image_prompts) - 1:
                _time.sleep(10)

        if len(self.images) == len(self.image_prompts):
            self._mark_step("images")

    def _generate_image_replicate(self, prompt: str, max_retries: int = 3) -> str:
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
                            return self._persist_image(img_resp.content, "Replicate flux-dev")
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

    def _generate_image_gemini(self, prompt: str, max_retries: int = 3) -> str:
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
                            return self._persist_image(base64.b64decode(data), "Gemini API")

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

    # ── Audio & Subtitles ──

    def generate_script_to_speech(self, tts_instance: TTS) -> str:
        if self._is_step_done("tts"):
            wavs = [f for f in os.listdir(self._source_dir) if f.endswith(".wav")]
            if wavs:
                self.tts_path = os.path.join(self._source_dir, sorted(wavs)[0])
                info(f" => TTS (resumed): {self.tts_path}")
                return self.tts_path

        path = os.path.join(self._source_dir, "audio.wav")
        self.script = re.sub(r"[^\w\s.?!]", "", self.script)
        tts_instance.synthesize(self.script, path)
        self.tts_path = path
        self._mark_step("tts")

        if get_verbose():
            info(f' => Wrote TTS to "{path}"')
        return path

    def _format_srt_timestamp(self, seconds: float) -> str:
        total_millis = max(0, int(round(seconds * 1000)))
        hours = total_millis // 3600000
        minutes = (total_millis % 3600000) // 60000
        secs = (total_millis % 60000) // 1000
        millis = total_millis % 1000
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    def generate_subtitles(self, audio_path: str) -> str:
        provider = str(get_stt_provider() or "local_whisper").lower()
        if provider == "local_whisper":
            return self._generate_subtitles_local_whisper(audio_path)
        if provider == "third_party_assemblyai":
            return self._generate_subtitles_assemblyai(audio_path)
        warning(f"Unknown stt_provider '{provider}'. Falling back to local_whisper.")
        return self._generate_subtitles_local_whisper(audio_path)

    def _generate_subtitles_assemblyai(self, audio_path: str) -> str:
        aai.settings.api_key = get_assemblyai_api_key()
        transcript = aai.Transcriber(config=aai.TranscriptionConfig()).transcribe(audio_path)
        srt_path = os.path.join(self._source_dir, "subtitles.srt")
        with open(srt_path, "w") as file:
            file.write(transcript.export_subtitles_srt())
        return srt_path

    def _generate_subtitles_local_whisper(self, audio_path: str) -> str:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            error("Local STT selected but 'faster-whisper' is not installed.")
            raise

        model = WhisperModel(get_whisper_model(), device=get_whisper_device(), compute_type=get_whisper_compute_type())
        segments, _ = model.transcribe(audio_path, vad_filter=True)

        lines = []
        for idx, segment in enumerate(segments, start=1):
            text = str(segment.text).strip()
            if not text:
                continue
            lines.append(str(idx))
            lines.append(f"{self._format_srt_timestamp(segment.start)} --> {self._format_srt_timestamp(segment.end)}")
            lines.append(text)
            lines.append("")

        srt_path = os.path.join(self._source_dir, "subtitles.srt")
        with open(srt_path, "w", encoding="utf-8") as file:
            file.write("\n".join(lines))
        return srt_path

    # ── Video composition ──

    def combine(self) -> str:
        video_path = os.path.join(self._output_dir, "video.mp4")
        threads = get_threads()
        tts_clip = AudioFileClip(self.tts_path)
        max_duration = tts_clip.duration
        req_dur = max_duration / len(self.images)
        self._duration = max_duration

        generator = lambda txt: TextClip(
            txt,
            font=os.path.join(get_fonts_dir(), get_font()),
            fontsize=100, color="#FFFF00",
            stroke_color="black", stroke_width=5,
            size=(1080, 1920), method="caption",
        )

        print(colored("[+] Combining images...", "blue"))

        clips = []
        tot_dur = 0
        while tot_dur < max_duration:
            for image_path in self.images:
                clip = ImageClip(image_path)
                clip.duration = req_dur
                clip = clip.set_fps(30)

                if round((clip.w / clip.h), 4) < 0.5625:
                    clip = crop(clip, width=clip.w, height=round(clip.w / 0.5625),
                                x_center=clip.w / 2, y_center=clip.h / 2)
                else:
                    clip = crop(clip, width=round(0.5625 * clip.h), height=clip.h,
                                x_center=clip.w / 2, y_center=clip.h / 2)
                clip = clip.resize((1080, 1920))
                clips.append(clip)
                tot_dur += clip.duration

        final_clip = concatenate_videoclips(clips).set_fps(30)

        # Pick song by mood if subfolders exist
        moods = get_available_moods()
        if moods and hasattr(self, '_mood') and self._mood:
            song = choose_song_by_mood(self._mood)
        else:
            song = choose_random_song()
        self._bg_song = song

        subtitles = None
        try:
            subtitles_path = self.generate_subtitles(self.tts_path)
            equalize_subtitles(subtitles_path, 10)
            subtitles = SubtitlesClip(subtitles_path, generator)
            subtitles.set_pos(("center", "center"))
        except Exception as e:
            warning(f"Failed to generate subtitles, continuing without: {e}")

        random_song_clip = AudioFileClip(song).set_fps(44100)
        random_song_clip = random_song_clip.fx(afx.volumex, 0.1)
        comp_audio = CompositeAudioClip([tts_clip.set_fps(44100), random_song_clip])

        final_clip = final_clip.set_audio(comp_audio).set_duration(tts_clip.duration)

        if subtitles is not None:
            final_clip = CompositeVideoClip([final_clip, subtitles])

        final_clip.write_videofile(video_path, threads=threads)
        success(f'Wrote Video to "{video_path}"')
        return video_path

    # ── Log ──

    def _write_log(self) -> str:
        log_path = os.path.join(self._output_dir, "info.txt")
        duration_mins = int(self._duration // 60)
        duration_secs = round(self._duration % 60, 2)

        lines = [
            "=" * 60, "VIDEO GENERATION LOG", "=" * 60, "",
            f"Video ID:          {self._video_id}",
            f"Generated at:      {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Source folder:     {self._source_dir}",
            f"Output folder:     {self._output_dir}",
            "",
            "--- CONTENT ---",
            f"Niche:             {self._niche}",
            f"Language:          {self._language}",
            f"Topic:             {self.subject}",
            f"Title:             {self.metadata['title']}",
            f"Description:       {self.metadata['description']}",
            "", "--- SCRIPT ---", self.script, "",
            "--- MEDIA ---",
            f"TTS voice:         {get_tts_voice()}",
            f"Mood:              {getattr(self, '_mood', 'N/A') or 'random'}",
            f"Background music:  {os.path.basename(self._bg_song)}",
            f"Duration:          {duration_mins}m {duration_secs}s ({round(self._duration, 2)}s)",
            f"Images:            {len(self.images)}",
            f"Resolution:        1080x1920 (9:16)",
            f"FPS:               30",
            "",
            "--- CONFIG ---",
            f"Ollama model:      {get_ollama_model() or 'selected at startup'}",
            f"Image provider:    {get_image_provider()}",
            f"Image model:       {'flux-dev' if get_image_provider() == 'replicate' else get_nanobanana2_model()}",
            f"STT provider:      {get_stt_provider()}",
            f"Font:              {get_font()}",
            f"Threads:           {get_threads()}",
            "", "--- IMAGE PROMPTS ---",
        ]
        for i, p in enumerate(self.image_prompts, 1):
            lines.append(f"  {i}. {p}")
        lines.extend(["", "=" * 60])

        with open(log_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        success(f'Log saved to "{log_path}"')
        return log_path

    # ── Resume support ──

    @staticmethod
    def find_resumable() -> List[dict]:
        """Find videos that errored or didn't complete."""
        state = _load_state()
        resumable = []
        for vid, info_data in state.items():
            status = info_data.get("status", "")
            if status != "complete":
                completed = info_data.get("completed_steps", [])
                last_err = info_data.get("last_error_step", "")
                last_msg = info_data.get("last_error", "")
                topic = info_data.get("topic_text", vid)
                resumable.append({
                    "video_id": vid,
                    "topic": topic,
                    "completed_steps": completed,
                    "last_error_step": last_err,
                    "last_error": last_msg,
                    "niche": info_data.get("niche", ""),
                    "language": info_data.get("language", "English"),
                })
        return resumable

    # ── Main pipeline ──

    def generate_video(self, tts_instance: TTS) -> str:
        try:
            self.generate_topic()
            self.generate_script()
            self._detect_mood()
            self.generate_metadata()
            self.generate_prompts()
            self.generate_images()

            if not self.images:
                self._mark_error("images", "No images generated")
                error("No images were generated. Check your config (image_provider, API keys).")
                return None

            self.generate_script_to_speech(tts_instance)
            self.combine()
            self._mark_step("combine")
            self._write_log()
            self._mark_complete()

            if get_verbose():
                info(f" => Video complete: {self._output_dir}")

            return self._output_dir

        except Exception as e:
            # Find which step failed
            for step in self.STEPS:
                if not self._is_step_done(step):
                    self._mark_error(step, str(e))
                    break
            error(f"Video generation failed: {str(e)}")
            return None
