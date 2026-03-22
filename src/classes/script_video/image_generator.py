"""Image generation orchestrator for script_video — uses Google Labs only."""

import os
import time as _time
import random

from status import info, warning, error, success
from config import get_verbose

from .google_labs import GoogleLabsProvider, AuthError, RateLimitError, ImageGenerationError
from .smart_brain import pick_best_image


class ScriptImageGenerator:
    """Orchestrates image generation for script_video using Google Labs.

    When multiple images are generated per prompt, uses LLM (qwen3:14b)
    to select the best image that fits the story context.
    """

    MAX_RETRIES = 3
    DELAY_MIN = 5   # seconds between images
    DELAY_MAX = 10

    def __init__(self, output_dir: str):
        self._output_dir = output_dir
        self._provider = GoogleLabsProvider()

    def is_available(self) -> bool:
        return self._provider.is_available()

    def generate_images(self, prompts: list, aspect_ratio: str = "portrait") -> list:
        """Generate images for all prompts. Returns list of file paths.

        When >1 image per prompt, LLM picks the best fit based on
        previous images and story context.
        """
        paths = []

        for i, prompt in enumerate(prompts):
            info(f" => Generating image {i+1}/{len(prompts)}: {prompt[:80]}...")
            start = _time.time()

            path = self._generate_with_retry(prompt, i + 1, aspect_ratio, prompts, paths)
            if path:
                paths.append(path)
                elapsed = _time.time() - start
                success(f"Image {i+1}/{len(prompts)} done ({elapsed:.1f}s): {os.path.basename(path)}")
            else:
                raise ImageGenerationError(
                    f"Image generation failed at image {i+1}/{len(prompts)}. "
                    f"Run again to resume."
                )

            # Delay between images to avoid rate limiting
            if i < len(prompts) - 1:
                delay = random.uniform(self.DELAY_MIN, self.DELAY_MAX)
                if get_verbose():
                    info(f" => Waiting {delay:.1f}s before next image...")
                _time.sleep(delay)

        return paths

    def _generate_with_retry(self, prompt: str, index: int, aspect_ratio: str,
                              all_prompts: list, previous_paths: list) -> str:
        """Try generating images with retries. Pick best if multiple."""
        for attempt in range(self.MAX_RETRIES):
            try:
                image_list = self._provider.generate(prompt, aspect_ratio)

                if not image_list:
                    if attempt < self.MAX_RETRIES - 1:
                        warning(f"No images generated (all may be policy-blocked). Retrying ({attempt+1}/{self.MAX_RETRIES})...")
                        _time.sleep(5)
                        continue
                    raise ImageGenerationError("No images returned after retries — prompt may be blocked by content policy")

                if len(image_list) == 1:
                    # Only one image, use it directly
                    path = os.path.join(self._output_dir, f"{index:02d}.png")
                    with open(path, "wb") as f:
                        f.write(image_list[0])
                    return path

                # Multiple images — save all as candidates, then LLM picks best
                candidate_paths = []
                for j, img_bytes in enumerate(image_list):
                    candidate_path = os.path.join(self._output_dir, f"{index:02d}_candidate_{j+1}.png")
                    with open(candidate_path, "wb") as f:
                        f.write(img_bytes)
                    candidate_paths.append(candidate_path)

                if get_verbose():
                    info(f" => Got {len(candidate_paths)} candidates, LLM selecting best...")

                # LLM picks best image based on context
                best_idx = pick_best_image(
                    current_prompt=prompt,
                    all_prompts=all_prompts,
                    previous_images=previous_paths,
                    candidate_paths=candidate_paths,
                )

                # Keep best, delete rest
                best_path = candidate_paths[best_idx]
                final_path = os.path.join(self._output_dir, f"{index:02d}.png")
                os.rename(best_path, final_path)

                for cp in candidate_paths:
                    if cp != best_path and os.path.exists(cp):
                        os.remove(cp)

                if get_verbose():
                    info(f" => LLM selected candidate {best_idx + 1}/{len(candidate_paths)}")

                return final_path

            except RateLimitError:
                wait = 30 * (attempt + 1)
                warning(f"Rate limited. Waiting {wait}s before retry ({attempt+1}/{self.MAX_RETRIES})...")
                _time.sleep(wait)

            except AuthError as e:
                if attempt < self.MAX_RETRIES - 1:
                    warning(f"Auth error: {e}. Retrying...")
                else:
                    error(f"Auth failed after {self.MAX_RETRIES} attempts: {e}")
                    return None

            except ImageGenerationError as e:
                error(f"Image generation error: {e}")
                return None

            except Exception as e:
                if attempt < self.MAX_RETRIES - 1:
                    warning(f"Error generating image (attempt {attempt+1}): {e}")
                    _time.sleep(5)
                else:
                    error(f"Failed after {self.MAX_RETRIES} attempts: {e}")
                    return None

        return None
