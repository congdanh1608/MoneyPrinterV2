"""Progress tracking for script video generation — per-video progress.json."""

import os
import json

PROGRESS_FILE = "progress.json"
STEPS = ["profile", "tts", "images", "image_order", "style", "compose"]


class ProgressTracker:
    """Tracks pipeline progress in output/<title>/progress.json."""

    def __init__(self, output_dir: str, title: str = "") -> None:
        self._output_dir = output_dir
        self._title = title

    def _path(self) -> str:
        return os.path.join(self._output_dir, PROGRESS_FILE)

    def get(self) -> dict:
        path = self._path()
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
        return {}

    def save(self, data: dict) -> None:
        with open(self._path(), "w") as f:
            json.dump(data, f, indent=2)

    def init(self) -> None:
        if not self.get():
            self.save({
                "title": self._title,
                "status": "in_progress",
                "completed_steps": [],
            })

    def mark_step(self, step: str) -> None:
        prog = self.get()
        completed = prog.get("completed_steps", [])
        if step not in completed:
            completed.append(step)
        prog["completed_steps"] = completed
        self.save(prog)

    def is_step_done(self, step: str) -> bool:
        return step in self.get().get("completed_steps", [])

    def mark_error(self, step: str, msg: str) -> None:
        prog = self.get()
        prog.update(last_error_step=step, last_error=msg, status="error")
        self.save(prog)

    def mark_complete(self) -> None:
        prog = self.get()
        prog.update(status="complete", last_error_step=None, last_error=None)
        self.save(prog)
