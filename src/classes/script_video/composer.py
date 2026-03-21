"""Video composition for script-based videos with custom timing."""

import os
from typing import List

from config import *
from status import *
from classes.video_generator.subtitles import generate_subtitles
from .smart_brain import calculate_image_timing


def compose_script_video(
    images: List[str],
    audio_path: str,
    output_dir: str,
    segments: list,
    style: dict,
) -> tuple:
    """Compose video with weight-based timing and styled subtitles.

    Returns (video_path, timing, audio_duration).
    """
    from moviepy.editor import (
        ImageClip, AudioFileClip, CompositeVideoClip, CompositeAudioClip,
        concatenate_videoclips, TextClip, afx,
    )
    from moviepy.video.fx.all import crop
    from moviepy.video.tools.subtitles import SubtitlesClip
    from moviepy.config import change_settings
    from termcolor import colored

    change_settings({"IMAGEMAGICK_BINARY": get_imagemagick_path()})

    video_path = os.path.join(output_dir, "video.mp4")
    threads = get_threads()
    tts_clip = AudioFileClip(audio_path)
    audio_duration = tts_clip.duration

    # Timing from duration_weight
    timing = calculate_image_timing(segments, audio_duration, len(images))

    # Subtitle style
    font_file = os.path.join(get_fonts_dir(), style.get("font", "bold_font.ttf"))
    sub_color = style.get("color", "#FFFF00")

    gen = lambda txt: TextClip(
        txt, font=font_file, fontsize=100,
        color=sub_color, stroke_color="black", stroke_width=5,
        size=(1080, 1920), method="caption",
    )

    print(colored("[+] Composing video...", "blue"))

    # Build image clips with custom timing
    clips = []
    for i, image_path in enumerate(images):
        dur = timing[i] if i < len(timing) else timing[-1]
        clip = ImageClip(image_path)
        clip.duration = dur
        clip = clip.set_fps(30)

        if round((clip.w / clip.h), 4) < 0.5625:
            clip = crop(clip, width=clip.w, height=round(clip.w / 0.5625),
                        x_center=clip.w / 2, y_center=clip.h / 2)
        else:
            clip = crop(clip, width=round(0.5625 * clip.h), height=clip.h,
                        x_center=clip.w / 2, y_center=clip.h / 2)
        clip = clip.resize((1080, 1920))
        clips.append(clip)

    final_clip = concatenate_videoclips(clips).set_fps(30)

    # Subtitles
    subtitles = None
    try:
        srt_path = generate_subtitles(audio_path, output_dir)
        equalize_subtitles(srt_path, 10)
        subtitles = SubtitlesClip(srt_path, gen)
        subtitles.set_pos(("center", "bottom"))
    except Exception as e:
        warning(f"Subtitles failed, continuing without: {e}")

    # Music
    song_path = style.get("song", "")
    if song_path and os.path.exists(song_path):
        song_clip = AudioFileClip(song_path).set_fps(44100)
        song_clip = song_clip.fx(afx.volumex, 0.1)
        comp_audio = CompositeAudioClip([tts_clip.set_fps(44100), song_clip])
    else:
        comp_audio = tts_clip.set_fps(44100)

    final_clip = final_clip.set_audio(comp_audio).set_duration(tts_clip.duration)

    if subtitles is not None:
        final_clip = CompositeVideoClip([final_clip, subtitles])

    final_clip.write_videofile(video_path, threads=threads)
    success(f'Video saved to "{video_path}"')

    return video_path, timing, audio_duration
