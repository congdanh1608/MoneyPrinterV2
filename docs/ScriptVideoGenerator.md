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

## Config — `script_video_config.json`

Script Video has its own config file at project root, separate from `config.json`.

```json
{
  "voicebox_url": "http://127.0.0.1:17493",
  "voicebox_profile_id": "create-via-voicebox-ui",
  "voicebox_language": "en",
  "google_labs_project_id": "your-project-id",
  "google_labs_image_model": "NARWHAL",
  "imagemagick_path": "/opt/homebrew/bin/magick",
  "stt_provider": "local_whisper",
  "whisper_model": "base",
  "whisper_device": "auto",
  "whisper_compute_type": "int8"
}
```

### Config fields

| Field | Description | Example |
|-------|-------------|---------|
| `voicebox_url` | VoiceBox server URL | `http://127.0.0.1:17493` |
| `voicebox_profile_id` | Voice profile UUID (create via VoiceBox `/docs`) | `dbf87c9e-...` |
| `voicebox_language` | TTS language code | `en`, `zh`, `ja`, `ko` |
| `google_labs_project_id` | Google Labs Flow project ID | from URL: `labs.google/fx/tools/flow/project/{id}` |
| `google_labs_image_model` | Image model name | `NARWHAL` (Nano Banana 2) or `UNICORN` (Pro) |
| `imagemagick_path` | Path to ImageMagick binary | `/opt/homebrew/bin/magick` |

### Google cookies (automatic)

Cookies are read automatically from **Firefox** via `browser_cookie3` (read-only, no Keychain needed).
Just make sure Firefox is logged into your Google account that has access to Google Labs.

### Google Labs image models

| Model name | Display name | Quality |
|------------|-------------|---------|
| `NARWHAL` | Nano Banana 2 (Gemini 3.1 Flash Image) | Good, fast |
| `UNICORN` | Nano Banana Pro (Gemini 3 Pro Image) | Higher quality, slower |

## Required Services

- **Ollama** with `qwen3:8b` + `qwen3:14b` pulled
- **VoiceBox** server running with at least 1 voice profile
- **Firefox** logged into Google account (for image generation cookies)
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
→ Generate Images (Google Labs) → Reorder Images (14b)
→ Pick Music + Style (8b) → Compose Video
```

## Image Generation Flow

```
1. Read Google cookies from Firefox (read-only, no browser opened)
2. Cache cookies for 1 hour
3. API call: POST batchGenerateImages with cookies → signed image URL
4. Download image → save to output/images/
5. Token expired? → auto re-read from Firefox
```

## LLM Model Assignment

| Task | Model | Why |
|------|-------|-----|
| Voice profile selection | qwen3:8b | Fast, simple matching |
| Pause timing between sentences | qwen3:8b | Context-aware pacing |
| Image reordering | qwen3:14b | Needs story comprehension |
| Music + font + color | qwen3:8b | Fast, preference-based |
| Image timing | No LLM | Calculated from `duration_weight` |
