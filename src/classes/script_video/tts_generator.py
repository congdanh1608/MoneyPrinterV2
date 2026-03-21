"""Per-segment TTS generation using VoiceBox API."""

import os
import time as _time
import requests
import numpy as np
import soundfile as sf

from config import get_voicebox_url
from status import info, success, warning


def _cleanup_voicebox(voicebox_url: str, gen_id: str) -> None:
    """Delete generated audio + clear cache on VoiceBox server."""
    try:
        requests.delete(f"{voicebox_url}/history/{gen_id}", timeout=5)
    except Exception:
        pass
    try:
        requests.post(f"{voicebox_url}/cache/clear", timeout=5)
    except Exception:
        pass


def generate_segment_tts(
    segments: list,
    voice_config: dict,
    profile_id: str,
    output_dir: str,
) -> str:
    """Generate TTS for each segment, concatenate into audio.wav.

    Returns path to combined audio file.
    """
    audio_path = os.path.join(output_dir, "audio.wav")
    voicebox_url = get_voicebox_url().rstrip("/")
    language = voice_config.get("language", "en")
    model_size = voice_config.get("model_size", "1.7B")

    segments_dir = os.path.join(output_dir, "segments")
    os.makedirs(segments_dir, exist_ok=True)

    # Check existing segments for resume
    existing = sorted([f for f in os.listdir(segments_dir) if f.endswith(".wav")])
    start_from = len(existing)

    if start_from > 0:
        info(f" => Found {start_from} existing segment audio files, resuming...")

    all_audio = []
    total_start = _time.time()

    for i, seg in enumerate(segments):
        seg_path = os.path.join(segments_dir, f"seg_{i:03d}.wav")

        if i < start_from and os.path.exists(seg_path):
            data, sr = sf.read(seg_path)
            all_audio.append(data)
            continue

        text = seg.get("text", "")
        instruct = seg.get("instruct", "")

        if not text.strip():
            continue

        seg_start = _time.time()
        info(f" => TTS segment {i+1}/{len(segments)}: {text[:50]}...")

        try:
            r = requests.post(f"{voicebox_url}/generate", json={
                "profile_id": profile_id,
                "text": text,
                "language": language,
                "model_size": model_size,
                "instruct": instruct,
            }, timeout=120)
            r.raise_for_status()
            gen_id = r.json()["id"]

            audio_resp = requests.get(f"{voicebox_url}/audio/{gen_id}", timeout=60)
            audio_resp.raise_for_status()

            with open(seg_path, "wb") as f:
                f.write(audio_resp.content)

            data, sr = sf.read(seg_path)
            all_audio.append(data)

            elapsed = round(_time.time() - seg_start, 1)
            duration = round(len(data) / 24000, 1)
            info(f"    Done in {elapsed}s (audio: {duration}s)")

            # Cleanup VoiceBox server
            _cleanup_voicebox(voicebox_url, gen_id)

        except Exception as e:
            raise RuntimeError(f"TTS failed at segment {i+1}: {e}. Run again to resume.")

    if not all_audio:
        raise RuntimeError("No audio segments generated.")

    combined = np.concatenate(all_audio)
    sf.write(audio_path, combined, 24000)
    total_elapsed = round(_time.time() - total_start, 1)
    total_duration = round(len(combined) / 24000, 1)
    success(f" => Combined {len(all_audio)} segments -> {audio_path} ({total_duration}s audio, took {total_elapsed}s)")

    return audio_path
