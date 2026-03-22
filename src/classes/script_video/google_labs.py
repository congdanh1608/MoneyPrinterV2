"""Google Labs Flow image generation — Selenium Firefox UI automation."""

import os
import time as _time
import base64
import requests

from config import (
    ROOT_DIR,
    get_google_labs_project_id,
    get_google_labs_image_model,
    get_google_labs_aspect_ratio,
    get_google_labs_images_per_prompt,
    get_firefox_profile_path,
    get_verbose,
)
from status import info, warning, error, success

_LABS_URL = "https://labs.google/fx/tools/flow"


class GoogleLabsProvider:
    """Google Labs image generation via Selenium Firefox with existing profile.

    Opens Firefox (visible) → enters prompt → clicks Generate → downloads images.
    Requires Firefox profile already logged into Google.
    """

    def __init__(self):
        self._project_id = get_google_labs_project_id()
        self._model = get_google_labs_image_model()
        self._profile_path = get_firefox_profile_path()

    def is_available(self) -> bool:
        if not self._project_id:
            error("google_labs_project_id is not configured in script_video_config.json.")
            return False
        if not self._profile_path:
            error("firefox_profile_path is not configured in script_video_config.json.")
            return False
        if not os.path.isdir(self._profile_path):
            error(f"Firefox profile path does not exist: {self._profile_path}")
            return False
        return True

    def _check_firefox_not_running(self):
        import subprocess
        result = subprocess.run(['pgrep', '-x', 'firefox'], capture_output=True)
        if result.returncode == 0:
            raise RuntimeError(
                "Firefox is running. Please close Firefox before generating images "
                "(Selenium needs exclusive access to the profile). Then run again."
            )

    def generate(self, prompt: str, aspect_ratio: str = "portrait") -> list:
        """Generate images via Google Labs. Returns list of image bytes."""
        self._check_firefox_not_running()

        from selenium import webdriver
        from selenium.webdriver.firefox.options import Options
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.action_chains import ActionChains
        from selenium.webdriver.common.keys import Keys

        num_images = get_google_labs_images_per_prompt()

        options = Options()
        options.add_argument("-profile")
        options.add_argument(self._profile_path)
        # Note: NOT headless — reCAPTCHA blocks headless mode

        driver = None
        try:
            driver = webdriver.Firefox(options=options)
            driver.set_window_size(800, 600)
            driver.set_window_position(0, 0)

            # Navigate to Flow project
            url = f"{_LABS_URL}/project/{self._project_id}"
            if get_verbose():
                info(f" => Navigating to {url}")
            driver.get(url)
            _time.sleep(6)

            # Step 1: Type prompt FIRST
            prompt_el = self._find_prompt_input(driver)
            if not prompt_el:
                raise ImageGenerationError("Could not find prompt input on page")

            prompt_el.click()
            _time.sleep(0.5)
            actions = ActionChains(driver)
            actions.key_down(Keys.COMMAND).send_keys('a').key_up(Keys.COMMAND).perform()
            _time.sleep(0.3)
            actions.send_keys(Keys.DELETE).perform()
            _time.sleep(0.3)
            actions.send_keys(prompt).perform()
            _time.sleep(1)

            # Step 2: Configure settings AFTER prompt (so settings stick when clicking generate)
            self._configure_settings(driver)

            if get_verbose():
                typed = prompt_el.text[:50]
                info(f" => Typed prompt: {typed}...")

            # Install fetch intercept to capture batchGenerateImages response
            # Use bind(window) to preserve correct context
            driver.execute_script("""
                window.__batchGenResponse = null;
                const origFetch = window.fetch.bind(window);
                window.fetch = function(input, init) {
                    var url = typeof input === 'string' ? input : (input && input.url ? input.url : '');
                    var promise = origFetch(input, init);
                    if (url && url.includes('batchGenerateImages')) {
                        promise.then(function(resp) {
                            resp.clone().json().then(function(data) {
                                window.__batchGenResponse = data;
                            }).catch(function(){});
                        }).catch(function(){});
                    }
                    return promise;
                };
            """)
            _time.sleep(0.5)

            # Click generate (arrow_forward) button
            generate_btn = self._find_generate_button(driver)
            if not generate_btn:
                raise ImageGenerationError("Could not find Generate button on page")

            generate_btn.click()
            if get_verbose():
                info(f" => Clicked Generate, waiting for response...")

            # Wait for batchGenerateImages response via intercepted fetch
            all_images = self._wait_for_response_images(driver, timeout=90)

            if not all_images:
                raise ImageGenerationError("Image generation failed — no images in API response. Prompt may have been blocked by content policy.")

            return all_images

        except (AuthError, RateLimitError, ImageGenerationError):
            raise
        except Exception as e:
            error(f"Selenium error: {e}")
            raise ImageGenerationError(f"Browser automation failed: {e}")
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

    # ── Settings configuration ────────────────────────────────────────

    def _configure_settings(self, driver):
        """Check settings bar text — only open panel if something needs changing."""
        from selenium.webdriver.common.by import By

        model_map = {"NARWHAL": "Nano Banana 2", "UNICORN": "Nano Banana Pro"}
        ratio_icon_map = {"9:16": "crop_9_16", "16:9": "crop_16_9", "4:3": "crop_landscape",
                          "3:4": "crop_portrait", "1:1": "crop_square"}
        count_map = {1: "x1", 2: "x2", 3: "x3", 4: "x4"}

        target_model = model_map.get(self._model, "Nano Banana 2")
        target_ratio = get_google_labs_aspect_ratio()
        target_ratio_icon = ratio_icon_map.get(target_ratio, "crop_9_16")
        target_count = count_map.get(get_google_labs_images_per_prompt(), "x2")

        # Find settings bar button
        settings_btn = None
        for btn in driver.find_elements(By.TAG_NAME, "button"):
            if not btn.is_displayed():
                continue
            text = btn.text.strip().lower()
            if "banana" in text and ("crop_" in text or "x1" in text or "x2" in text or "x3" in text or "x4" in text):
                if "arrow_drop_down" not in text:
                    settings_btn = btn
                    break

        if not settings_btn:
            warning(" => Could not find settings bar button")
            return

        # Quick check: read settings bar text to see if already correct
        # Format: "🍌 Nano Banana 2\ncrop_9_16\nx2"
        btn_text = settings_btn.text.strip()
        current_model = btn_text.split('\n')[0].replace('🍌 ', '').strip()
        has_correct_ratio = target_ratio_icon in btn_text.lower()
        has_correct_count = target_count in btn_text
        has_correct_model = current_model == target_model

        if has_correct_model and has_correct_ratio and has_correct_count:
            if get_verbose():
                info(f" => Settings OK: {target_model}, {target_ratio}, {target_count}")
            return

        # Need to change — open settings panel
        if get_verbose():
            info(f" => Settings need update (current: {btn_text.replace(chr(10), ', ')})")

        settings_btn.click()
        _time.sleep(1.5)

        # Set ratio if wrong
        if not has_correct_ratio:
            for el in driver.find_elements(By.XPATH, f"//button[contains(text(), '{target_ratio}')]"):
                if el.is_displayed():
                    el.click()
                    if get_verbose():
                        info(f" => Set ratio: {target_ratio}")
                    _time.sleep(0.5)
                    break

        # Set count if wrong
        if not has_correct_count:
            for el in driver.find_elements(By.XPATH, f"//button[text()='{target_count}']"):
                if el.is_displayed() and "flow_tab_slider_trigger" in (el.get_attribute("class") or ""):
                    el.click()
                    if get_verbose():
                        info(f" => Set count: {target_count}")
                    _time.sleep(0.5)
                    break

        # Set model if wrong
        if not has_correct_model:
            for btn in driver.find_elements(By.TAG_NAME, "button"):
                if btn.is_displayed() and "arrow_drop_down" in btn.text.strip():
                    btn.click()
                    _time.sleep(1)
                    break
            for el in driver.find_elements(By.CSS_SELECTOR, '[role="menuitem"]'):
                if el.is_displayed() and target_model in el.text.strip():
                    el.click()
                    if get_verbose():
                        info(f" => Set model: {target_model}")
                    _time.sleep(1)
                    break

        _time.sleep(0.5)

    # ── DOM helpers ───────────────────────────────────────────────────

    def _find_prompt_input(self, driver):
        """Find the visible contenteditable prompt div."""
        from selenium.webdriver.common.by import By

        for el in driver.find_elements(By.CSS_SELECTOR, 'div[contenteditable="true"]'):
            if el.is_displayed():
                if get_verbose():
                    info(f" => Found prompt input")
                return el
        return None

    def _find_generate_button(self, driver):
        """Find the → (arrow_forward Create) button at bottom of page."""
        from selenium.webdriver.common.by import By

        buttons = driver.find_elements(By.TAG_NAME, "button")
        for btn in reversed(buttons):
            if not btn.is_displayed():
                continue
            text = btn.text.strip().lower()
            if "arrow_forward" in text:
                return btn
        return None

    def _wait_for_response_images(self, driver, timeout: int = 90) -> list:
        """Wait for batchGenerateImages response and extract image URLs from it."""
        start = _time.time()

        while _time.time() - start < timeout:
            _time.sleep(3)

            response_data = driver.execute_script("return window.__batchGenResponse")
            if response_data:
                return self._extract_images_from_response(driver, response_data)

            elapsed = int(_time.time() - start)
            if get_verbose() and elapsed % 15 == 0:
                info(f" => Waiting for API response... ({elapsed}s)")

        return []

    def _extract_images_from_response(self, driver, data: dict) -> list:
        """Parse batchGenerateImages response and download images."""
        results = []

        # Build session with browser cookies for authenticated downloads
        session = requests.Session()
        for c in driver.get_cookies():
            session.cookies.set(c["name"], c["value"])

        # Response structure: {generatedMedia: [{mediaGenerateInfo: {mediaName: "uuid"}, ...}]}
        generated = data.get("generatedMedia", [])
        if not generated:
            # Try alternative structures
            generated = data.get("results", []) or data.get("images", [])

        for item in generated:
            media_name = None
            # Structure: {mediaGenerateInfo: {mediaName: "uuid"}}
            mgi = item.get("mediaGenerateInfo", {})
            media_name = mgi.get("mediaName")

            if not media_name:
                # Try other keys
                media_name = item.get("name") or item.get("id") or item.get("mediaName")

            if not media_name:
                continue

            # Check if generation failed (policy violation etc.)
            status = mgi.get("mediaGenerateStatus", "")
            if status and "FAIL" in str(status).upper():
                if get_verbose():
                    warning(f" => Image blocked by policy: {media_name}")
                continue

            # Download via the redirect API
            img_url = f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={media_name}"

            try:
                resp = session.get(img_url, timeout=30)
                resp.raise_for_status()

                if resp.headers.get("content-type", "").startswith("image"):
                    results.append(resp.content)
                    if get_verbose():
                        info(f" => Downloaded image: {media_name[:20]}... ({len(resp.content)} bytes)")
                else:
                    # May be a redirect URL in JSON
                    try:
                        redirect_data = resp.json()
                        redirect_url = redirect_data.get("result", {}).get("data", {}).get("url", "")
                        if redirect_url:
                            img_resp = session.get(redirect_url, timeout=30)
                            img_resp.raise_for_status()
                            results.append(img_resp.content)
                            if get_verbose():
                                info(f" => Downloaded image via redirect: {media_name[:20]}...")
                    except Exception:
                        if get_verbose():
                            warning(f" => Could not parse redirect for {media_name}")
            except Exception as e:
                if get_verbose():
                    warning(f" => Failed to download {media_name}: {e}")

        if get_verbose():
            info(f" => Got {len(results)} images from API response ({len(generated)} total in response)")

        return results


class AuthError(Exception):
    pass

class RateLimitError(Exception):
    pass

class ImageGenerationError(Exception):
    pass
