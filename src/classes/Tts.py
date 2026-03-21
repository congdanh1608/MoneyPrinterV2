import os
import requests
import soundfile as sf
from kittentts import KittenTTS as KittenModel

from config import ROOT_DIR, get_tts_voice, get_voicebox_url, get_voicebox_profile_id, get_voicebox_language

KITTEN_MODEL = "KittenML/kitten-tts-mini-0.8"
KITTEN_SAMPLE_RATE = 24000


class TTS:
    def __init__(self) -> None:
        self._model = KittenModel(KITTEN_MODEL)
        self._voice = get_tts_voice()

    def synthesize(self, text, output_file=os.path.join(ROOT_DIR, ".mp", "audio.wav")):
        audio = self._model.generate(text, voice=self._voice)
        sf.write(output_file, audio, KITTEN_SAMPLE_RATE)
        return output_file


class VoiceBoxTTS:
    def __init__(self) -> None:
        self._url = get_voicebox_url().rstrip("/")
        self._profile_id = get_voicebox_profile_id()
        self._language = get_voicebox_language()

        if not self._profile_id:
            raise ValueError("voicebox_profile_id is not set in config.json. Create a profile in VoiceBox first.")

    def synthesize(self, text, output_file=os.path.join(ROOT_DIR, ".mp", "audio.wav")):
        # Generate
        r = requests.post(f"{self._url}/generate", json={
            "profile_id": self._profile_id,
            "text": text,
            "language": self._language,
        }, timeout=120)
        r.raise_for_status()
        gen_id = r.json()["id"]

        # Download audio
        audio = requests.get(f"{self._url}/audio/{gen_id}", timeout=60)
        audio.raise_for_status()

        with open(output_file, "wb") as f:
            f.write(audio.content)

        return output_file
