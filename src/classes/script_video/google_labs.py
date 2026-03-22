"""Google Labs Flow image generation — Selenium Firefox UI automation."""

import os
import time as _time
import requests

from config import (
    ROOT_DIR,
    get_google_labs_project_id,
    get_google_labs_image_model,
    get_google_labs_aspect_ratio,
    get_firefox_profile_path,
    get_verbose,
)
from status import info, warning, error, success

_LABS_URL = "https://labs.google/fx/tools/flow"


class GoogleLabsProvider:
    """Google Labs image generation via Selenium Firefox.

    Opens Firefox ONCE, creates a new project, generates all images
    in that project, then closes Firefox.
    """

    def __init__(self):
        self._model = get_google_labs_image_model()
        self._profile_path = get_firefox_profile_path()
        self._driver = None
        self._project_url = None

    def is_available(self) -> bool:
        if not self._profile_path:
            error("firefox_profile_path is not configured in script_video_config.json.")
            return False
        if not os.path.isdir(self._profile_path):
            error(f"Firefox profile path does not exist: {self._profile_path}")
            return False
        return True

    def get_project_url(self) -> str:
        return self._project_url

    # ── Browser lifecycle ──────────────────────────────────────────

    def _check_firefox_not_running(self):
        import subprocess
        result = subprocess.run(['pgrep', '-x', 'firefox'], capture_output=True)
        if result.returncode == 0:
            raise RuntimeError(
                "Firefox is running. Please close Firefox before generating images. Then run again."
            )
        for lock in [".parentlock", "lock"]:
            lp = os.path.join(self._profile_path, lock)
            if os.path.exists(lp):
                os.remove(lp)

    def open_browser(self):
        """Open Firefox. Call once before generating any images."""
        if self._driver:
            return

        self._check_firefox_not_running()

        from selenium import webdriver
        from selenium.webdriver.firefox.options import Options

        options = Options()
        options.add_argument("-profile")
        options.add_argument(self._profile_path)

        for attempt in range(3):
            try:
                self._driver = webdriver.Firefox(options=options)
                break
            except Exception as e:
                if attempt < 2:
                    warning(f"Firefox failed to open (attempt {attempt+1}/3): {e}")
                    for lock in [".parentlock", "lock"]:
                        lp = os.path.join(self._profile_path, lock)
                        if os.path.exists(lp):
                            os.remove(lp)
                    _time.sleep(3)
                else:
                    raise RuntimeError(f"Firefox failed to open after 3 attempts: {e}")

        self._driver.set_window_size(720, 1200)
        self._driver.set_window_position(0, 0)
        info(" => Firefox opened")

    def close_browser(self):
        """Close Firefox. Call after all images are done."""
        if self._driver:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None

    # ── Generate single image (browser already open) ───────────────

    def generate(self, prompt: str, is_first_image: bool = True) -> bytes:
        """Generate 1 image in the current project. Browser must be open.

        For image 2+, adds the previous image as reference asset.
        Returns image bytes or None.
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.action_chains import ActionChains
        from selenium.webdriver.common.keys import Keys

        driver = self._driver
        if not driver:
            raise RuntimeError("Browser not open. Call open_browser() first.")

        # For image 2+, add previous image as reference
        if not is_first_image:
            self._add_reference_asset()

        # Find prompt input
        prompt_el = None
        for wait_attempt in range(3):
            prompt_el = self._find_prompt_input()
            if prompt_el:
                break
            if get_verbose():
                info(f" => Prompt input not found, retrying in 5s ({wait_attempt+1}/3)...")
            _time.sleep(5)

        if not prompt_el:
            raise ImageGenerationError("Could not find prompt input on page")

        # Type prompt
        prompt_el.click()
        _time.sleep(0.5)
        actions = ActionChains(driver)
        actions.key_down(Keys.COMMAND).send_keys('a').key_up(Keys.COMMAND).perform()
        _time.sleep(0.3)
        actions.send_keys(Keys.DELETE).perform()
        _time.sleep(0.3)
        actions.send_keys(prompt).perform()
        _time.sleep(1)

        # Configure settings (only changes if needed)
        self._configure_settings()

        if get_verbose():
            info(f" => Typed prompt: {prompt_el.text[:50]}...")

        # Install fetch intercept (reset for each image)
        driver.execute_script("""
            window.__batchGenResponses = [];
            const origFetch = window.fetch.bind(window);
            window.fetch = function(input, init) {
                var url = typeof input === 'string' ? input : (input && input.url ? input.url : '');
                var promise = origFetch(input, init);
                if (url && url.includes('batchGenerateImages')) {
                    promise.then(function(resp) {
                        resp.clone().json().then(function(data) {
                            window.__batchGenResponses.push(data);
                        }).catch(function(){});
                    }).catch(function(){});
                }
                return promise;
            };
        """)
        _time.sleep(0.5)

        # Click generate
        generate_btn = self._find_generate_button()
        if not generate_btn:
            raise ImageGenerationError("Could not find Generate button")

        generate_btn.click()
        if get_verbose():
            info(" => Clicked Generate, waiting for response...")

        # Wait for response
        image_bytes = self._wait_for_response_image(timeout=180)
        if not image_bytes:
            raise ImageGenerationError("No image in API response. Prompt may be blocked by content policy.")

        return image_bytes

    # ── Project creation ──────────────────────────────────────────

    def create_new_project(self) -> str:
        """Navigate to Flow homepage, click + New project, return project URL."""
        from selenium.webdriver.common.by import By

        driver = self._driver
        if get_verbose():
            info(" => Creating new Google Labs project...")

        driver.get(_LABS_URL)
        _time.sleep(6)

        # Find and click "+ New project"
        new_btn = None
        for btn in driver.find_elements(By.TAG_NAME, "button"):
            if btn.is_displayed() and "new project" in btn.text.strip().lower():
                new_btn = btn
                break

        if not new_btn:
            for btn in driver.find_elements(By.XPATH, "//button[contains(., 'New')]"):
                if btn.is_displayed():
                    new_btn = btn
                    break

        if not new_btn:
            # Fallback to config project
            fallback_id = get_google_labs_project_id()
            if fallback_id:
                url = f"{_LABS_URL}/project/{fallback_id}"
                driver.get(url)
                _time.sleep(8)
                return url
            raise ImageGenerationError("No '+ New project' button and no fallback project.")

        new_btn.click()

        # Wait for redirect to /project/{id}
        for _ in range(20):
            _time.sleep(2)
            if "/project/" in driver.current_url:
                _time.sleep(3)
                return driver.current_url

        # Timeout fallback
        fallback_id = get_google_labs_project_id()
        if fallback_id:
            url = f"{_LABS_URL}/project/{fallback_id}"
            driver.get(url)
            _time.sleep(8)
            return url
        raise ImageGenerationError("Failed to create new project.")

    # ── Reference asset ───────────────────────────────────────────

    def _add_reference_asset(self):
        """Click + to open assets dialog, select most recent image as reference."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys

        driver = self._driver
        if get_verbose():
            info(" => Adding previous image as reference...")

        # Click + button (add_2 icon)
        add_btn = None
        for btn in driver.find_elements(By.TAG_NAME, "button"):
            if btn.is_displayed() and "add_2" in btn.text.strip() and "create" in btn.text.strip().lower():
                add_btn = btn
                break

        if not add_btn:
            if get_verbose():
                warning(" => Could not find + button, skipping reference")
            return

        add_btn.click()
        _time.sleep(2)

        # Verify filter is "Recent"
        recent_filter = driver.find_elements(By.XPATH, "//div[text()='Recent']")
        if not any(el.is_displayed() for el in recent_filter):
            sort_btns = driver.find_elements(By.XPATH, "//button[contains(., 'arrow_drop_down')]")
            for btn in sort_btns:
                if btn.is_displayed():
                    btn.click()
                    _time.sleep(1)
                    for opt in driver.find_elements(By.XPATH, "//*[text()='Recent']"):
                        if opt.is_displayed():
                            opt.click()
                            _time.sleep(1)
                            break
                    break

        # Find the dialog popover (contains asset list)
        dialog = None
        for el in driver.find_elements(By.CSS_SELECTOR, '[role="dialog"], .sc-903adef0-0'):
            if el.is_displayed():
                dialog = el
                break

        if not dialog:
            if get_verbose():
                warning(" => Assets dialog not found, skipping reference")
            return

        # Scroll the assets list inside dialog to load all items (virtual scroll)
        scrollers = dialog.find_elements(By.CSS_SELECTOR, '[data-virtuoso-scroller="true"]')
        if scrollers:
            for _ in range(10):
                driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", scrollers[0])
                _time.sleep(0.3)
            _time.sleep(1)

        # Find assets ONLY inside dialog (not project window)
        assets = dialog.find_elements(By.CSS_SELECTOR, '[data-item-index]')
        if not assets:
            if get_verbose():
                warning(" => No assets in dialog, skipping reference")
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            _time.sleep(1)
            return

        # Pick highest data-item-index = most recent
        latest = max(assets, key=lambda el: int(el.get_attribute("data-item-index") or "0"))

        # Click the img inside (class sc-903adef0-12)
        target = latest
        imgs = latest.find_elements(By.CSS_SELECTOR, 'img.sc-903adef0-12')
        if imgs:
            target = imgs[0]
        else:
            divs = latest.find_elements(By.CSS_SELECTOR, '.sc-903adef0-11')
            if divs:
                target = divs[0]

        if get_verbose():
            idx = latest.get_attribute('data-item-index')
            alt = target.get_attribute('alt') or ''
            info(f" => Selecting asset [{idx}]: {alt[:40]}")

        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", target)
        _time.sleep(0.5)
        driver.execute_script("arguments[0].click();", target)
        _time.sleep(2)

    # ── Settings ──────────────────────────────────────────────────

    def _configure_settings(self):
        """Check and configure model, ratio, count if needed."""
        from selenium.webdriver.common.by import By

        driver = self._driver
        model_map = {"NARWHAL": "Nano Banana 2", "UNICORN": "Nano Banana Pro"}
        ratio_icon_map = {"9:16": "crop_9_16", "16:9": "crop_16_9", "4:3": "crop_landscape",
                          "3:4": "crop_portrait", "1:1": "crop_square"}

        target_model = model_map.get(self._model, "Nano Banana 2")
        target_ratio = get_google_labs_aspect_ratio()
        target_ratio_icon = ratio_icon_map.get(target_ratio, "crop_9_16")
        target_count = "x1"

        # Find settings bar
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
            return

        btn_text = settings_btn.text.strip()
        current_model = btn_text.split('\n')[0].replace('🍌 ', '').strip()

        if (target_ratio_icon in btn_text.lower() and
                target_count in btn_text and
                current_model == target_model):
            if get_verbose():
                info(f" => Settings OK: {target_model}, {target_ratio}, {target_count}")
            return

        if get_verbose():
            info(f" => Updating settings...")

        settings_btn.click()
        _time.sleep(1.5)

        # Ratio
        if target_ratio_icon not in btn_text.lower():
            for el in driver.find_elements(By.XPATH, f"//button[contains(text(), '{target_ratio}')]"):
                if el.is_displayed():
                    el.click()
                    _time.sleep(0.5)
                    break

        # Count
        if target_count not in btn_text:
            for el in driver.find_elements(By.XPATH, f"//button[text()='{target_count}']"):
                if el.is_displayed() and "flow_tab_slider_trigger" in (el.get_attribute("class") or ""):
                    el.click()
                    _time.sleep(0.5)
                    break

        # Model
        if current_model != target_model:
            for btn in driver.find_elements(By.TAG_NAME, "button"):
                if btn.is_displayed() and "arrow_drop_down" in btn.text.strip():
                    btn.click()
                    _time.sleep(1)
                    break
            for el in driver.find_elements(By.CSS_SELECTOR, '[role="menuitem"]'):
                if el.is_displayed() and target_model in el.text.strip():
                    el.click()
                    _time.sleep(1)
                    break

        _time.sleep(0.5)

    # ── DOM helpers ───────────────────────────────────────────────

    def _find_prompt_input(self):
        from selenium.webdriver.common.by import By
        for el in self._driver.find_elements(By.CSS_SELECTOR, 'div[contenteditable="true"]'):
            if el.is_displayed():
                return el
        return None

    def _find_generate_button(self):
        from selenium.webdriver.common.by import By
        for btn in reversed(self._driver.find_elements(By.TAG_NAME, "button")):
            if btn.is_displayed() and "arrow_forward" in btn.text.strip().lower():
                return btn
        return None

    # ── Response handling ─────────────────────────────────────────

    def _wait_for_response_image(self, timeout: int = 180) -> bytes:
        """Wait for batchGenerateImages response, extract and download image."""
        start = _time.time()
        driver = self._driver

        while _time.time() - start < timeout:
            _time.sleep(3)

            responses = driver.execute_script("return window.__batchGenResponses") or []

            if responses:
                for resp_data in responses:
                    img_bytes = self._extract_image_from_response(resp_data)
                    if img_bytes:
                        return img_bytes

                # Response received but no image (policy block)
                # Wait a bit more in case another response comes
                _time.sleep(5)
                responses2 = driver.execute_script("return window.__batchGenResponses") or []
                if len(responses2) == len(responses):
                    # No new responses, all failed
                    return None

            elapsed = int(_time.time() - start)
            if get_verbose() and elapsed % 15 == 0:
                info(f" => Waiting for response... ({elapsed}s)")

        return None

    def _extract_image_from_response(self, data: dict) -> bytes:
        """Extract image bytes from a single batchGenerateImages response."""
        media_list = data.get("media", [])
        if not media_list:
            return None

        for item in media_list:
            image = item.get("image", {})
            gen_image = image.get("generatedImage", {})

            fail_reason = gen_image.get("failureReason") or item.get("error")
            if fail_reason:
                if get_verbose():
                    warning(f" => Image blocked: {fail_reason}")
                continue

            fife_url = gen_image.get("fifeUrl", "")
            if not fife_url:
                continue

            try:
                resp = requests.get(fife_url, timeout=30)
                resp.raise_for_status()
                if get_verbose():
                    info(f" => Downloaded image ({len(resp.content)} bytes)")
                return resp.content
            except Exception as e:
                if get_verbose():
                    warning(f" => Download failed: {e}")

        return None


class AuthError(Exception):
    pass

class RateLimitError(Exception):
    pass

class ImageGenerationError(Exception):
    pass
