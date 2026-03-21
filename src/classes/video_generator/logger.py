"""Write video generation log file."""

import os
from datetime import datetime
from config import *
from status import *


def write_log(output_dir: str, video_id: str, source_dir: str,
              niche: str, language: str, subject: str, script: str,
              metadata: dict, image_prompts: list, images: list,
              bg_song: str, duration: float, mood: str = None) -> str:

    log_path = os.path.join(output_dir, "info.txt")
    duration_mins = int(duration // 60)
    duration_secs = round(duration % 60, 2)

    provider = get_image_provider()
    image_model = dict(
        replicate='flux-dev',
        freepik=f'mystic-{get_freepik_model()}',
        gemini=get_nanobanana2_model()
    ).get(provider, 'unknown')

    lines = [
        "=" * 60, "VIDEO GENERATION LOG", "=" * 60, "",
        f"Video ID:          {video_id}",
        f"Generated at:      {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Source folder:     {source_dir}",
        f"Output folder:     {output_dir}",
        "",
        "--- CONTENT ---",
        f"Niche:             {niche}",
        f"Language:          {language}",
        f"Topic:             {subject}",
        f"Title:             {metadata.get('title', '')}",
        f"Description:       {metadata.get('description', '')}",
        "", "--- SCRIPT ---", script, "",
        "--- MEDIA ---",
        f"TTS provider:      {get_tts_provider() or 'kittentts'}",
            f"TTS voice:         {get_tts_voice() if get_tts_provider() != 'voicebox' else 'voicebox profile'}",
        f"Mood:              {mood or 'random'}",
        f"Background music:  {'/'.join(bg_song.split(os.sep)[-2:])}",
        f"Duration:          {duration_mins}m {duration_secs}s ({round(duration, 2)}s)",
        f"Images:            {len(images)}",
        f"Resolution:        1080x1920 (9:16)",
        f"FPS:               30",
        "",
        "--- CONFIG ---",
        f"Ollama model:      {get_ollama_model() or 'selected at startup'}",
        f"Image provider:    {provider}",
        f"Image model:       {image_model}",
        f"STT provider:      {get_stt_provider()}",
        f"Font:              {get_font()}",
        f"Threads:           {get_threads()}",
        "", "--- IMAGE PROMPTS ---",
    ]
    for i, p in enumerate(image_prompts, 1):
        lines.append(f"  {i}. {p}")
    lines.extend(["", "=" * 60])

    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    success(f'Log saved to "{log_path}"')
    return log_path
