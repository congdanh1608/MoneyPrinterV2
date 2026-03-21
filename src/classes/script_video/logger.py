"""Log writer for script-based video generation."""

import os
from datetime import datetime
from config import *
from status import success


def write_script_log(
    output_dir: str,
    title: str,
    segments: list,
    image_prompts: list,
    style: dict,
    timing: list,
    audio_duration: float,
    profile_id: str,
) -> str:
    log_path = os.path.join(output_dir, "info.txt")
    duration_mins = int(audio_duration // 60)
    duration_secs = round(audio_duration % 60, 2)

    lines = [
        "=" * 60, "VIDEO GENERATION LOG (Script Mode)", "=" * 60, "",
        f"Title:             {title}",
        f"Generated at:      {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Output folder:     {output_dir}",
        "",
        "--- SEGMENTS ---",
    ]
    for i, seg in enumerate(segments, 1):
        hook = " [HOOK]" if seg.get("is_hook") else ""
        lines.append(f"  {i}. [{seg.get('duration_weight', 1.0)}x]{hook} {seg['text']}")
        lines.append(f"     instruct: {seg.get('instruct', '')}")

    lines.extend([
        "",
        "--- MEDIA ---",
        f"Voice profile:     {profile_id}",
        f"TTS segments:      {len(segments)}",
        f"Background music:  {'/'.join(style.get('song', '').split(os.sep)[-2:]) if style.get('song') else 'none'}",
        f"Subtitle font:     {style.get('font', 'bold_font.ttf')}",
        f"Subtitle color:    {style.get('color', '#FFFF00')}",
        f"Duration:          {duration_mins}m {duration_secs}s ({round(audio_duration, 2)}s)",
        f"Images:            {len(image_prompts)}",
        f"Image timing:      {timing}",
        f"Resolution:        1080x1920 (9:16)",
        "",
        "--- CONFIG ---",
        f"Image provider:    freepik ({get_freepik_model()})",
        f"LLM brain:         qwen3:8b + qwen3:14b",
        f"TTS:               VoiceBox ({get_voicebox_url()})",
        "", "--- IMAGE PROMPTS ---",
    ])
    for i, p in enumerate(image_prompts, 1):
        lines.append(f"  {i}. {p}")
    lines.extend(["", "=" * 60])

    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    success(f'Log saved to "{log_path}"')
    return log_path
