# Short Youtube (Provide Script)

## Quick Start

```bash
# 1. Start Ollama + VoiceBox
ollama serve
# VoiceBox server at http://127.0.0.1:17493

# 2. Drop script JSON into source/scripts/
cp my_story.json source/scripts/

# 3. Run
source venv/bin/activate
python src/main.py
# Select option 6 → pick script → wait
```

## Required Config (config.json)

```json
{
  "freepik_api_key": "FPSX...",
  "freepik_model": "realism",
  "voicebox_url": "http://127.0.0.1:17493",
  "voicebox_profile_id": "create-via-voicebox-ui",
  "imagemagick_path": "/opt/homebrew/bin/magick"
}
```

## Required Services

- **Ollama** with `qwen3:8b` + `qwen3:14b` pulled
- **VoiceBox** server running with at least 1 voice profile
- **Freepik** API key
- `Songs/` with mood subfolders (`calm/`, `dramatic/`, `happy/`, `sad/`)

## Script JSON Format

```json
{
  "title": "He Thought He Knew Everything",
  "duration": 50,
  "segments": [
    {
      "text": "He thought he knew everything...",
      "instruct": "Calm storytelling. Slight pause.",
      "duration_weight": 1.2,
      "is_hook": true
    },
    {
      "text": "People believed he was smart.",
      "instruct": "Neutral tone.",
      "duration_weight": 1.0
    }
  ],
  "image_prompts": [
    "confident man small town cinematic",
    "man speaking to others"
  ],
  "voice_config": {
    "language": "en",
    "model_size": "1.7B"
  }
}
```

### Segment fields
- `text` — sentence to narrate
- `instruct` — VoiceBox voice direction per sentence
- `duration_weight` — controls how long the matching image shows (higher = longer)
- `is_hook` — larger subtitle font for emphasis

## Output

```
output/He_Thought_He_Knew_Everything/
├── script.json       ← copy of input
├── progress.json     ← resume state
├── audio.wav         ← combined TTS
├── segments/         ← per-sentence audio
│   ├── seg_000.wav
│   └── seg_001.wav
├── images/
│   ├── 01.png
│   └── 02.png
├── video.mp4         ← final video
└── info.txt          ← full log
```

Original script in `source/scripts/` is deleted after successful video creation.

## Resume on Error

Run again → option 6 → app detects incomplete videos → choose to resume → skips completed steps.

## Pipeline

```
Load Script → Pick Voice (8b) → TTS per segment (VoiceBox)
→ Generate Images (Freepik) → Reorder Images (14b)
→ Pick Music + Style (8b) → Compose Video
```

## LLM Model Assignment

| Task | Model | Why |
|------|-------|-----|
| Voice profile selection | qwen3:8b | Fast, simple matching |
| Image reordering | qwen3:14b | Needs story comprehension |
| Music + font + color | qwen3:8b | Fast, preference-based |
| Image timing | No LLM | Calculated from `duration_weight` |
