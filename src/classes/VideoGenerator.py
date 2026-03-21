import re
import base64
import json
import os
import shutil
import requests
import assemblyai as aai

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


class VideoGenerator:
    """
    Generates YouTube Shorts-style videos without browser automation.
    Videos are saved to the output/ folder at the project root.
    """

    def __init__(self, niche: str, language: str) -> None:
        self._niche = niche
        self._language = language
        self.images = []

        # Ensure output directory exists
        self._output_dir = os.path.join(ROOT_DIR, "output")
        if not os.path.exists(self._output_dir):
            os.makedirs(self._output_dir)

    @property
    def niche(self) -> str:
        return self._niche

    @property
    def language(self) -> str:
        return self._language

    def generate_response(self, prompt: str) -> str:
        return generate_text(prompt)

    def generate_topic(self) -> str:
        completion = self.generate_response(
            f"Please generate a specific video idea that takes about the following topic: {self.niche}. Make it exactly one sentence. Only return the topic, nothing else."
        )

        if not completion:
            error("Failed to generate Topic.")

        self.subject = completion
        return completion

    def generate_script(self) -> str:
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

        # Enforce ~80 word limit - retry if too long
        if len(completion.split()) > 100:
            if get_verbose():
                warning(f"Generated script too long ({len(completion.split())} words). Retrying...")
            return self.generate_script()

        self.script = completion
        return completion

    def generate_metadata(self) -> dict:
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
        return self.metadata

    def generate_prompts(self) -> List[str]:
        # For short videos (~80 words), 3-5 images is optimal
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
                    warning(
                        "LLM returned an unformatted response. Attempting to clean..."
                    )

                r = re.compile(r"\[.*\]")
                image_prompts = r.findall(completion)
                if len(image_prompts) == 0:
                    if get_verbose():
                        warning("Failed to generate Image Prompts. Retrying...")
                    return self.generate_prompts()

        if len(image_prompts) > n_prompts:
            image_prompts = image_prompts[: int(n_prompts)]

        self.image_prompts = image_prompts
        success(f"Generated {len(image_prompts)} Image Prompts.")
        return image_prompts

    def _persist_image(self, image_bytes: bytes, provider_label: str) -> str:
        image_path = os.path.join(ROOT_DIR, ".mp", str(uuid4()) + ".png")

        with open(image_path, "wb") as image_file:
            image_file.write(image_bytes)

        if get_verbose():
            info(f' => Wrote image from {provider_label} to "{image_path}"')

        self.images.append(image_path)
        return image_path

    def generate_image(self, prompt: str, max_retries: int = 3) -> str:
        print(f"Generating Image using Nano Banana 2 API: {prompt[:80]}...")

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

        import time as _time

        for attempt in range(max_retries):
            try:
                response = requests.post(
                    endpoint,
                    headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                    json=payload,
                    timeout=300,
                )

                if response.status_code == 429:
                    wait = 15 * (attempt + 1)
                    warning(f"Rate limited. Waiting {wait}s before retry ({attempt+1}/{max_retries})...")
                    _time.sleep(wait)
                    continue

                response.raise_for_status()
                body = response.json()

                candidates = body.get("candidates", [])
                for candidate in candidates:
                    content = candidate.get("content", {})
                    for part in content.get("parts", []):
                        inline_data = part.get("inlineData") or part.get("inline_data")
                        if not inline_data:
                            continue
                        data = inline_data.get("data")
                        mime_type = inline_data.get("mimeType") or inline_data.get("mime_type", "")
                        if data and str(mime_type).startswith("image/"):
                            image_bytes = base64.b64decode(data)
                            return self._persist_image(image_bytes, "Nano Banana 2 API")

                if get_verbose():
                    warning(f"Nano Banana 2 did not return an image payload. Response: {body}")
                return None
            except Exception as e:
                if get_verbose():
                    warning(f"Failed to generate image with Nano Banana 2 API: {str(e)}")
                if attempt < max_retries - 1:
                    _time.sleep(5)
                    continue
                return None

    def generate_script_to_speech(self, tts_instance: TTS) -> str:
        path = os.path.join(ROOT_DIR, ".mp", str(uuid4()) + ".wav")
        self.script = re.sub(r"[^\w\s.?!]", "", self.script)
        tts_instance.synthesize(self.script, path)
        self.tts_path = path

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
            return self.generate_subtitles_local_whisper(audio_path)

        if provider == "third_party_assemblyai":
            return self.generate_subtitles_assemblyai(audio_path)

        warning(f"Unknown stt_provider '{provider}'. Falling back to local_whisper.")
        return self.generate_subtitles_local_whisper(audio_path)

    def generate_subtitles_assemblyai(self, audio_path: str) -> str:
        aai.settings.api_key = get_assemblyai_api_key()
        config = aai.TranscriptionConfig()
        transcriber = aai.Transcriber(config=config)
        transcript = transcriber.transcribe(audio_path)
        subtitles = transcript.export_subtitles_srt()

        srt_path = os.path.join(ROOT_DIR, ".mp", str(uuid4()) + ".srt")
        with open(srt_path, "w") as file:
            file.write(subtitles)

        return srt_path

    def generate_subtitles_local_whisper(self, audio_path: str) -> str:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            error(
                "Local STT selected but 'faster-whisper' is not installed. "
                "Install it or switch stt_provider to third_party_assemblyai."
            )
            raise

        model = WhisperModel(
            get_whisper_model(),
            device=get_whisper_device(),
            compute_type=get_whisper_compute_type(),
        )
        segments, _ = model.transcribe(audio_path, vad_filter=True)

        lines = []
        for idx, segment in enumerate(segments, start=1):
            start = self._format_srt_timestamp(segment.start)
            end = self._format_srt_timestamp(segment.end)
            text = str(segment.text).strip()

            if not text:
                continue

            lines.append(str(idx))
            lines.append(f"{start} --> {end}")
            lines.append(text)
            lines.append("")

        subtitles = "\n".join(lines)
        srt_path = os.path.join(ROOT_DIR, ".mp", str(uuid4()) + ".srt")
        with open(srt_path, "w", encoding="utf-8") as file:
            file.write(subtitles)

        return srt_path

    def combine(self) -> str:
        combined_image_path = os.path.join(ROOT_DIR, ".mp", str(uuid4()) + ".mp4")
        threads = get_threads()
        tts_clip = AudioFileClip(self.tts_path)
        max_duration = tts_clip.duration
        req_dur = max_duration / len(self.images)

        generator = lambda txt: TextClip(
            txt,
            font=os.path.join(get_fonts_dir(), get_font()),
            fontsize=100,
            color="#FFFF00",
            stroke_color="black",
            stroke_width=5,
            size=(1080, 1920),
            method="caption",
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
                    if get_verbose():
                        info(f" => Resizing Image: {image_path} to 1080x1920")
                    clip = crop(
                        clip,
                        width=clip.w,
                        height=round(clip.w / 0.5625),
                        x_center=clip.w / 2,
                        y_center=clip.h / 2,
                    )
                else:
                    if get_verbose():
                        info(f" => Resizing Image: {image_path} to 1920x1080")
                    clip = crop(
                        clip,
                        width=round(0.5625 * clip.h),
                        height=clip.h,
                        x_center=clip.w / 2,
                        y_center=clip.h / 2,
                    )
                clip = clip.resize((1080, 1920))

                clips.append(clip)
                tot_dur += clip.duration

        final_clip = concatenate_videoclips(clips)
        final_clip = final_clip.set_fps(30)
        random_song = choose_random_song()
        self._bg_song = random_song
        self._duration = max_duration

        subtitles = None
        try:
            subtitles_path = self.generate_subtitles(self.tts_path)
            equalize_subtitles(subtitles_path, 10)
            subtitles = SubtitlesClip(subtitles_path, generator)
            subtitles.set_pos(("center", "center"))
        except Exception as e:
            warning(f"Failed to generate subtitles, continuing without subtitles: {e}")

        random_song_clip = AudioFileClip(random_song).set_fps(44100)
        random_song_clip = random_song_clip.fx(afx.volumex, 0.1)
        comp_audio = CompositeAudioClip([tts_clip.set_fps(44100), random_song_clip])

        final_clip = final_clip.set_audio(comp_audio)
        final_clip = final_clip.set_duration(tts_clip.duration)

        if subtitles is not None:
            final_clip = CompositeVideoClip([final_clip, subtitles])

        final_clip.write_videofile(combined_image_path, threads=threads)

        success(f'Wrote Video to "{combined_image_path}"')
        return combined_image_path

    def _write_log(self, output_video_path: str) -> str:
        log_path = output_video_path.replace(".mp4", "_info.txt")

        duration_mins = int(self._duration // 60)
        duration_secs = round(self._duration % 60, 2)

        lines = [
            "=" * 60,
            "VIDEO GENERATION LOG",
            "=" * 60,
            "",
            f"Generated at:      {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Output file:       {output_video_path}",
            "",
            "--- CONTENT ---",
            f"Niche:             {self._niche}",
            f"Language:          {self._language}",
            f"Topic:             {self.subject}",
            f"Title:             {self.metadata['title']}",
            f"Description:       {self.metadata['description']}",
            "",
            "--- SCRIPT ---",
            self.script,
            "",
            "--- MEDIA ---",
            f"TTS voice:         {get_tts_voice()}",
            f"Background music:  {os.path.basename(self._bg_song)}",
            f"Duration:          {duration_mins}m {duration_secs}s ({round(self._duration, 2)}s)",
            f"Images generated:  {len(self.images)}",
            f"Resolution:        1080x1920 (9:16)",
            f"FPS:               30",
            "",
            "--- CONFIG ---",
            f"Ollama model:      {get_ollama_model() or 'selected at startup'}",
            f"Image model:       {get_nanobanana2_model()}",
            f"Image aspect:      {get_nanobanana2_aspect_ratio()}",
            f"STT provider:      {get_stt_provider()}",
            f"Whisper model:     {get_whisper_model()}",
            f"Font:              {get_font()}",
            f"ImageMagick:       {get_imagemagick_path()}",
            f"Threads:           {get_threads()}",
            "",
            "--- IMAGE PROMPTS ---",
        ]

        for i, prompt in enumerate(self.image_prompts, 1):
            lines.append(f"  {i}. {prompt}")

        lines.append("")
        lines.append("=" * 60)

        with open(log_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        success(f'Log saved to "{log_path}"')
        return log_path

    def generate_video(self, tts_instance: TTS) -> str:
        self.generate_topic()
        self.generate_script()
        self.generate_metadata()
        self.generate_prompts()

        for prompt in self.image_prompts:
            self.generate_image(prompt)

        if not self.images:
            error("No images were generated. Check your nanobanana2_api_key in config.json.")
            return None

        self.generate_script_to_speech(tts_instance)

        path = self.combine()

        # Copy final video to output/ with readable filename
        safe_title = re.sub(r'[^\w\s-]', '', self.metadata["title"])[:50].strip()
        safe_title = re.sub(r'\s+', '_', safe_title)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f"{safe_title}_{timestamp}.mp4"
        output_path = os.path.join(self._output_dir, output_filename)

        shutil.copy2(path, output_path)
        success(f'Video saved to "{output_path}"')

        # Write log file alongside video
        self._write_log(output_path)

        if get_verbose():
            info(f" => Generated Video: {output_path}")

        return output_path
