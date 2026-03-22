"""Image generation orchestrator for script_video — uses Google Labs only."""

import os
import time as _time
import random

from status import info, warning, error, success
from config import get_verbose

from .google_labs import GoogleLabsProvider, ImageGenerationError


class ScriptImageGenerator:
    """Orchestrates image generation using a shared GoogleLabsProvider.

    Browser lifecycle is managed externally — caller opens/closes browser.
    Each video creates a new project within the same browser session.
    """

    MAX_RETRIES = 3

    def __init__(self, output_dir: str, provider: GoogleLabsProvider):
        self._output_dir = output_dir
        self._provider = provider

    def get_project_url(self) -> str:
        return self._provider.get_project_url()

    def generate_images(self, prompts: list) -> list:
        """Generate 1 image per prompt in a new project. Returns list of file paths."""
        paths = []

        # Create new project for this video
        self._provider.create_new_project()

        for i, prompt in enumerate(prompts):
            info(f" => Generating image {i+1}/{len(prompts)}: {prompt[:80]}...")
            start = _time.time()

            path = self._generate_with_retry(prompt, i + 1, is_first_image=(i == 0))
            if path:
                paths.append(path)
                elapsed = _time.time() - start
                success(f"Image {i+1}/{len(prompts)} done ({elapsed:.1f}s): {os.path.basename(path)}")
            else:
                raise ImageGenerationError(
                    f"Image generation failed at image {i+1}/{len(prompts)}. Run again to resume."
                )

            # No delay — generate next image immediately

        return paths

    def _generate_with_retry(self, prompt: str, index: int, is_first_image: bool) -> str:
        """Generate 1 image with retries."""
        for attempt in range(self.MAX_RETRIES):
            try:
                img_bytes = self._provider.generate(prompt, is_first_image=is_first_image)

                if not img_bytes:
                    if attempt < self.MAX_RETRIES - 1:
                        warning(f"No image generated. Retrying ({attempt+1}/{self.MAX_RETRIES})...")
                        _time.sleep(5)
                        continue
                    raise ImageGenerationError("No image returned — prompt may be blocked")

                path = os.path.join(self._output_dir, f"{index:02d}.png")
                with open(path, "wb") as f:
                    f.write(img_bytes)
                return path

            except ImageGenerationError:
                if attempt < self.MAX_RETRIES - 1:
                    warning(f"Image error (attempt {attempt+1}). Retrying...")
                    _time.sleep(5)
                else:
                    raise

            except Exception as e:
                if attempt < self.MAX_RETRIES - 1:
                    warning(f"Error (attempt {attempt+1}): {e}")
                    _time.sleep(5)
                else:
                    error(f"Failed after {self.MAX_RETRIES} attempts: {e}")
                    return None

        return None
