"""Video composition: combine images, audio, subtitles, and music into MP4."""

import os

from utils import *
from config import *
from status import *
from moviepy.editor import *
from moviepy.video.fx.all import crop
from moviepy.config import change_settings
from moviepy.video.tools.subtitles import SubtitlesClip
from termcolor import colored
from .subtitles import generate_subtitles

change_settings({"IMAGEMAGICK_BINARY": get_imagemagick_path()})


def compose_video(images: list, tts_path: str, output_dir: str, source_dir: str, mood: str = None) -> tuple:
    """
    Combine images + audio + subtitles + background music into final MP4.

    Returns:
        (video_path, bg_song_path, duration)
    """
    video_path = os.path.join(output_dir, "video.mp4")
    threads = get_threads()
    tts_clip = AudioFileClip(tts_path)
    max_duration = tts_clip.duration
    req_dur = max_duration / len(images)

    gen = lambda txt: TextClip(
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
        for image_path in images:
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

    # Pick song by mood
    moods = get_available_moods()
    if moods and mood:
        song = choose_song_by_mood(mood)
    else:
        song = choose_random_song()

    # Subtitles
    subtitles = None
    try:
        subtitles_path = generate_subtitles(tts_path, source_dir)
        equalize_subtitles(subtitles_path, 10)
        subtitles = SubtitlesClip(subtitles_path, gen)
        subtitles.set_pos(("center", "center"))
    except Exception as e:
        warning(f"Failed to generate subtitles, continuing without: {e}")

    # Audio mix
    song_clip = AudioFileClip(song).set_fps(44100)
    song_clip = song_clip.fx(afx.volumex, 0.1)
    comp_audio = CompositeAudioClip([tts_clip.set_fps(44100), song_clip])

    final_clip = final_clip.set_audio(comp_audio).set_duration(tts_clip.duration)

    if subtitles is not None:
        final_clip = CompositeVideoClip([final_clip, subtitles])

    final_clip.write_videofile(video_path, threads=threads)
    success(f'Wrote Video to "{video_path}"')

    return video_path, song, max_duration
