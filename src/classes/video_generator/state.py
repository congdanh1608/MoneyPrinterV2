"""State management — 1 state.json per video in its source folder."""

import os
import json
from typing import List
from config import ROOT_DIR


def _state_path(source_dir: str) -> str:
    return os.path.join(source_dir, "state.json")


def load_video_state(source_dir: str) -> dict:
    path = _state_path(source_dir)
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {}


def save_video_state(source_dir: str, state: dict) -> None:
    with open(_state_path(source_dir), "w") as f:
        json.dump(state, f, indent=2)


def find_resumable() -> List[dict]:
    """Scan all source/<video>/ folders for incomplete videos."""
    source_root = os.path.join(ROOT_DIR, "source")
    if not os.path.exists(source_root):
        return []

    resumable = []
    for name in sorted(os.listdir(source_root)):
        folder = os.path.join(source_root, name)
        if not os.path.isdir(folder):
            continue
        state = load_video_state(folder)
        if not state:
            continue
        if state.get("status") != "complete":
            resumable.append({
                "video_id": name,
                "topic": state.get("topic", name),
                "completed_steps": state.get("completed_steps", []),
                "last_error_step": state.get("last_error_step", ""),
                "last_error": state.get("last_error", ""),
                "niche": state.get("niche", ""),
                "language": state.get("language", "English"),
            })
    return resumable
