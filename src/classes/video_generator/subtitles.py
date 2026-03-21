"""Subtitle generation: local Whisper and AssemblyAI."""

import os
import assemblyai as aai

from config import *
from status import *


def format_srt_timestamp(seconds: float) -> str:
    total_millis = max(0, int(round(seconds * 1000)))
    hours = total_millis // 3600000
    minutes = (total_millis % 3600000) // 60000
    secs = (total_millis % 60000) // 1000
    millis = total_millis % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def generate_subtitles(audio_path: str, output_dir: str) -> str:
    provider = str(get_stt_provider() or "local_whisper").lower()
    if provider == "local_whisper":
        return _generate_local_whisper(audio_path, output_dir)
    if provider == "third_party_assemblyai":
        return _generate_assemblyai(audio_path, output_dir)
    warning(f"Unknown stt_provider '{provider}'. Falling back to local_whisper.")
    return _generate_local_whisper(audio_path, output_dir)


def _generate_assemblyai(audio_path: str, output_dir: str) -> str:
    aai.settings.api_key = get_assemblyai_api_key()
    transcript = aai.Transcriber(config=aai.TranscriptionConfig()).transcribe(audio_path)
    srt_path = os.path.join(output_dir, "subtitles.srt")
    with open(srt_path, "w") as file:
        file.write(transcript.export_subtitles_srt())
    return srt_path


def _generate_local_whisper(audio_path: str, output_dir: str) -> str:
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
        lines.append(f"{format_srt_timestamp(segment.start)} --> {format_srt_timestamp(segment.end)}")
        lines.append(text)
        lines.append("")

    srt_path = os.path.join(output_dir, "subtitles.srt")
    with open(srt_path, "w", encoding="utf-8") as file:
        file.write("\n".join(lines))
    return srt_path
