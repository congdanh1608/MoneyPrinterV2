# Generate Youtube Short (No Upload)

## Quick Start

```bash
# 1. Start Ollama
ollama serve

# 2. Run
source venv/bin/activate
python src/main.py
# Select option 5 → enter video count → wait
```

## Required Config (config.json)

```json
{
  "ollama_model": "llama3.2:3b",
  "default_niche": "short emotional stories with twist ending",
  "default_language": "English",
  "image_provider": "replicate",
  "replicate_api_token": "r8_xxx",
  "tts_voice": "Jasper",
  "imagemagick_path": "/opt/homebrew/bin/magick"
}
```

## Before Running

- Ollama running with at least one model pulled
- `Songs/` has at least 1 .mp3 file (optional: mood subfolders like `Songs/sad/`, `Songs/happy/`)
- `replicate_api_token` is set

## Output

```
source/<video_id>/   → images, audio, srt (persistent, never deleted)
output/<video_id>/   → video.mp4 + info.txt
```

## Resume on Error

Run `python src/main.py` → option 5 → app detects incomplete videos → choose to resume → skips completed steps.

## Pipeline

```
Niche → Topic → Script → Mood → Title → Image Prompts → Images → TTS → Subtitles → Video
```
