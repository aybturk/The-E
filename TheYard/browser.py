from pathlib import Path
import json
import time
import threading
from typing import Optional
from playwright.sync_api import sync_playwright, BrowserContext

BASE_DIR = Path(__file__).resolve().parent.parent  # repo root/TheYard/.. ayarla
PROFILES_DIR = BASE_DIR / "profiles"
SHOPS_CFG = PROFILES_DIR / "shops.json"

PROFILES_DIR.mkdir(parents=True, exist_ok=True)
if not SHOPS_CFG.exists():
    SHOPS_CFG.write_text("{}", encoding="utf-8")


class BrowserManager:
    """
    Manage persistent browser profiles per shop_key.
    Usage:
      mgr = BrowserManager()
      ctx = mgr.launch_persistent_context('Puraylen', headless=False)
      page = ctx.new_page()
      page.goto('https://www.etsy.com/your/shops/me/listing-editor/create')
      ...
      ctx.close()  # when done
    """

    def __init__(self, profiles_base: Path = PROFILES_DIR):
        self.profiles_base = Path(profiles_base)
        self._lock = threading.Lock()
        self._play = None
        self._browsers = {}  # shop_key -> (playwright, context)
        self._shops = json.loads(SHOPS_CFG.read_text(encoding="utf-8"))

    def get_profile_path(self, shop_key: str) -> Path:
        p = self.profiles_base / f"etsy_{shop_key}"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _save_shops_cfg(self):
        SHOPS_CFG.write_text(json.dumps(self._shops, indent=2), encoding="utf-8")

    def launch_persistent_context(self, shop_key: str, headless: bool = False, timeout: int = 30000) -> BrowserContext:
        with self._lock:
            if shop_key in self._browsers:
                _, ctx = self._browsers[shop_key]
                return ctx

            if self._play is None:
                self._play = sync_playwright().start()

            profile_dir = str(self.get_profile_path(shop_key))

            context = self._play.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                headless=headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-default-browser-check",
                    "--disable-notifications",
                    "--start-maximized",
                    # Crash/restore & first-run bubbles off
                    "--disable-session-crashed-bubble",
                    "--restore-last-session=false",
                    "--no-first-run",
                    "--disable-features=InfiniteSessionRestore,TranslateUI",
                ],
                viewport=None,  # OS pencere boyutu (start-maximized ile tam ekran)
                accept_downloads=True,
                timeout=timeout,
            )

            self._browsers[shop_key] = (self._play, context)
            self._shops.setdefault(shop_key, {"profile": profile_dir, "created": int(time.time())})
            self._save_shops_cfg()
            return context

    def close_context(self, shop_key: str):
        with self._lock:
            pair = self._browsers.pop(shop_key, None)
            if pair:
                _, ctx = pair
                try:
                    ctx.close()
                except Exception:
                    pass
            if not self._browsers and self._play:
                try:
                    self._play.stop()
                except Exception:
                    pass
                self._play = None

    def close_all(self):
        with self._lock:
            for k in list(self._browsers.keys()):
                self.close_context(k)

    def ensure_manual_login(self, shop_key: str, login_url: Optional[str] = "https://www.etsy.com/signin"):
        ctx = self.launch_persistent_context(shop_key, headless=False)
        page = ctx.new_page()
        page.goto(login_url)
        print(f"[BrowserManager] Please complete login for shop '{shop_key}' in the opened browser window.")
        print("[BrowserManager] After successful login (and 2FA if any), press ENTER here to continue and close the helper page.")
        input()
        time.sleep(1)
        page.close()
        print(f"[BrowserManager] Manual login finished for '{shop_key}'. Profile saved at: {self.get_profile_path(shop_key)}")