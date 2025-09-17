# TheBridge/EtsyFunc.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional
import time

from playwright.sync_api import TimeoutError as PWTimeoutError
from TheYard.browser import BrowserManager


# --- Data model (ileri adımlarda genişleyecek) ---
@dataclass
class ProductInput:
    # Zorunlular (Etsy için genel gereksinimler)
    category_query: str  # UI kategori arama metni (örn. "plate")
    title: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    quantity: Optional[int] = None
    who_made: Optional[str] = None  # i_did | someone_else | collective
    when_made: Optional[str] = None  # 2020_2025 vb.
    type: Optional[str] = None       # physical | download | both
    is_supply: Optional[bool] = None

    # Opsiyoneller (şimdilik kullanılmıyor; ileride dolduracağız)
    tags: List[str] = field(default_factory=list)
    materials: List[str] = field(default_factory=list)
    styles: List[str] = field(default_factory=list)
    images: List[str] = field(default_factory=list)
    shipping_profile: Optional[str] = None  # UI'da görünen profil adı


class EtsyFunc:
    """UI tabanlı Etsy akışları. Her mağaza için TheYard.browser'daki kalıcı profili kullanır."""

    LISTING_CREATE_URL = "https://www.etsy.com/your/shops/me/listing-editor/create"

    def __init__(self, shop_key: str):
        self.shop_key = shop_key
        self.mgr = BrowserManager()

    def _scroll_to_bottom(self, page):
        try:
            page.evaluate("""
                () => new Promise(resolve => {
                    let y = 0; const step = () => {
                        const max = document.body.scrollHeight || document.documentElement.scrollHeight;
                        if (y >= max - 2) return resolve();
                        y += 600; window.scrollTo(0, y); setTimeout(step, 60);
                    }; step();
                })
            """)
        except Exception:
            pass

    def _fill_title_desc_photos(self, page, product: ProductInput):
        # Title
        if product.title:
            filled = False
            for loc in [
                page.get_by_label("Title", exact=False),
                page.locator("xpath=//label[contains(., 'Title')]/following::*[self::input or self::textarea][1]"),
            ]:
                try:
                    loc.first.fill(product.title)
                    filled = True
                    break
                except Exception:
                    continue
            if not filled:
                page.screenshot(path=f"etsy_{self.shop_key}_title_fill_error.png")
                raise RuntimeError("Title field not found/fillable.")
            time.sleep(0.2)

        # Photos (if any)
        if product.images:
            # Try to scroll the photos section into view (various anchors)
            for anchor in [
                page.get_by_text("Photos", exact=False),
                page.get_by_text("Photos and video", exact=False),
                page.get_by_text("Add up to 10 photos", exact=False),
            ]:
                try:
                    anchor.first.scroll_into_view_if_needed()
                    time.sleep(0.1)
                    break
                except Exception:
                    continue

            # normalize paths (strip accidental quotes/spaces)
            img_paths = [p.strip().strip("'\"") for p in product.images if p and p.strip()]

            uploaded = False
            # broad set of selectors; Etsy keeps file input hidden inside the dropzone
            selectors = [
                "input[type='file'][multiple]",
                "input[type='file'][accept*='image']",
                "input[type='file']",
                "xpath=//input[@type='file' and @multiple]",
                "xpath=//div[contains(@class,'photo') or contains(@class,'drop')]/descendant::input[@type='file']",
            ]
            for sel in selectors:
                try:
                    inp = page.locator(sel).first
                    # will work even if input is hidden
                    inp.set_input_files(img_paths)
                    uploaded = True
                    break
                except Exception:
                    continue

            if not uploaded:
                page.screenshot(path=f"etsy_{self.shop_key}_photo_upload_error.png")
                raise RuntimeError("Photo upload input not found or not interactable.")

            # allow thumbnails to render
            time.sleep(1.5)

        # Description
        if product.description:
            filled = False
            for loc in [
                page.get_by_label("Description", exact=False),
                page.get_by_placeholder("Describe", exact=False),
                page.locator("xpath=//label[contains(., 'Description')]/following::*[self::textarea or self::div[@contenteditable='true']][1]"),
            ]:
                try:
                    loc.first.fill(product.description)
                    filled = True
                    break
                except Exception:
                    continue
            if not filled:
                page.screenshot(path=f"etsy_{self.shop_key}_description_fill_error.png")
                raise RuntimeError("Description field not found/fillable.")
            time.sleep(0.2)
    def _restore_scroll(self, page):
        try:
            page.evaluate(
                """
                () => {
                    const html = document.documentElement;
                    const body = document.body;
                    if (html) { html.style.overflow = 'auto'; html.style.removeProperty('position'); }
                    if (body) { body.style.overflow = 'auto'; body.style.removeProperty('position'); }
                    ;[html, body].forEach(el => el && el.classList && (
                        el.classList.remove('no-scroll','wt-no-scroll','overlay-lock','wt-overlay-open')
                    ));
                    window.scrollTo(0, 0);
                }
                """
            )
        except Exception:
            pass

    def _dismiss_overlays(self, page):
        # Try to close any leftover dialogs/popovers that might lock body scroll
        for _ in range(4):
            try:
                visible_dialogs = page.locator("css=div[role='dialog']:not([aria-hidden='true'])")
                if visible_dialogs.count() == 0:
                    break
                page.keyboard.press("Escape")
                time.sleep(0.2)
            except Exception:
                break
        # Restore scrolling in any case
        self._restore_scroll(page)

    def _map_when_made_to_ui(self, when_made: str) -> str:
        mapping = {
            "made_to_order": "Made To Order",
            "2020_2025": "2020 - 2025",
            "before_2006": "Before 2006",
            "1990s": "1990s",
            "1980s": "1980s",
            "1970s": "1970s",
            "1960s": "1960s",
            "1950s": "1950s",
            "1940s": "1940s",
            "1930s": "1930s",
            "1920s": "1920s",
            "1910s": "1910s",
            "1900s": "1900s",
        }
        return mapping.get(when_made, when_made)

    def _when_made_index(self, when_made: str) -> int:
        order = [
            "Made To Order",
            "2020 - 2025",
            "2010 - 2019",
            "2006 - 2009",
            "Before 2006",
            "2000 - 2005",
            "1990s",
            "1980s",
            "1970s",
            "1960s",
            "1950s",
            "1940s",
            "1930s",
            "1920s",
            "1910s",
            "1900s",
        ]
        target = self._map_when_made_to_ui(when_made)
        try:
            return order.index(target)
        except ValueError:
            return 0

    def _click_radio_by_label(self, scope, label_text: str):
        """Clicks a radio by its visible label text inside given scope (modal or section). Robust to nested spans.
        """
        # try label element first
        candidates = [
            scope.locator(f"xpath=.//label[normalize-space()[contains(., '{label_text}')]]"),
            scope.get_by_label(label_text),
            scope.get_by_text(label_text, exact=False),
        ]
        for cand in candidates:
            try:
                cand.first.scroll_into_view_if_needed()
                cand.first.click(force=True)
                return True
            except Exception:
                continue
        # fallbacks: click the input tied to that label
        try:
            inp = scope.locator(
                f"xpath=.//label[contains(., '{label_text}')]/descendant-or-self::label//input[@type='radio']"
            )
            inp.first.scroll_into_view_if_needed()
            inp.first.check(force=True)
            return True
        except Exception:
            return False

    def _pick_from_open_listbox(self, page, text: str) -> bool:
        """Pick an option from the currently open listbox/overlay by visible text.
        Scrolls INSIDE the listbox to avoid freezing the page-level scroll."""
        label = text.strip()
        # try to get the last visible listbox (Etsy renders overlay near the end of body)
        lb = page.locator("css=[role='listbox']:not([aria-hidden='true'])").last
        try:
            lb.wait_for(timeout=2000)
        except Exception:
            # fallback: any element that looks like an options overlay
            lb = page.locator("xpath=(//*[@role='listbox' or contains(@class,'wt-popover') or contains(@class,'menu')])[last()]")

        def try_once() -> bool:
            candidates = [
                lb.get_by_role("option", name=label),
                lb.get_by_text(label, exact=True),
                lb.locator(f"xpath=.//*[(@role='option' or self::li or self::div) and normalize-space()='{label}']"),
            ]
            for c in candidates:
                try:
                    c.first.scroll_into_view_if_needed()
                    c.first.click(force=True)
                    return True
                except Exception:
                    continue
            return False

        # attempt + scroll inside the listbox
        for _ in range(10):
            if try_once():
                return True
            try:
                lb.evaluate("el => el.scrollBy(0, 300)")
            except Exception:
                try:
                    page.keyboard.press("PageDown")
                except Exception:
                    pass
            time.sleep(0.12)
        return False

    def _fill_about_modal(self, page, product: ProductInput):
        # Locate the visible modal (avoid GDPR hidden overlays)
        modal = None
        try:
            # Prefer the modal with the specific heading text
            modal = page.get_by_role("dialog").filter(has=page.get_by_role("heading", name="Next, tell us about your listing"))
            modal.wait_for(timeout=5000)
        except Exception:
            try:
                # Fallback: any visible dialog (not aria-hidden)
                modal = page.locator("css=div[role='dialog']:not([aria-hidden='true'])").first
                modal.wait_for(timeout=5000)
            except Exception:
                page.screenshot(path=f"etsy_{self.shop_key}_about_modal_not_visible.png")
                raise RuntimeError("About modal not visible.")

        time.sleep(0.2)

        # Ensure content is scrolled into view for lower controls
        try:
            modal.evaluate("el => { el.scrollTo({top: 0}); }")
        except Exception:
            pass

        # --- Who made it? ---
        who_made_map = {
            "i_did": "I did",
            "someone_else": "Another company or person",
            "collective": "A member of my shop",
        }
        if product.who_made:
            label = who_made_map.get(product.who_made)
            if label and not self._click_radio_by_label(modal, label):
                page.screenshot(path=f"etsy_{self.shop_key}_about_who_made_error.png")
                raise RuntimeError(f"Failed to select 'Who made it?' option: {label}")
            time.sleep(0.2)

        # --- What is it? ---
        what_is_it_label = "A supply or tool to make things" if product.is_supply else "A finished product"
        if not self._click_radio_by_label(modal, what_is_it_label):
            page.screenshot(path=f"etsy_{self.shop_key}_about_what_is_it_error.png")
            raise RuntimeError(f"Failed to select 'What is it?' option: {what_is_it_label}")
        time.sleep(0.2)

        # --- When was it made? ---
        if product.when_made:
            ui_label = self._map_when_made_to_ui(product.when_made)
            # open the dropdown (button / combobox)
            opened = False
            for locator in [
                modal.get_by_role("combobox", name="When was it made?"),
                modal.get_by_label("When was it made?"),
                modal.locator("xpath=.//label[contains(., 'When was it made?')]/following-sibling::*[self::div or self::button]//button"),
                modal.locator("xpath=.//button[contains(., 'When did you make it?')]")
            ]:
                try:
                    locator.click()
                    opened = True
                    break
                except Exception:
                    continue
            if not opened:
                page.screenshot(path=f"etsy_{self.shop_key}_about_when_made_dropdown_error.png")
                raise RuntimeError("Failed to open 'When was it made?' dropdown.")
            time.sleep(0.2)

            # keyboard-only fallback (stable even when overlay is huge)
            try:
                # go to top of the list
                page.keyboard.press("Home")
                time.sleep(0.1)
                steps = self._when_made_index(product.when_made)
                for _ in range(steps):
                    page.keyboard.press("ArrowDown")
                    time.sleep(0.05)
                page.keyboard.press("Enter")
                time.sleep(0.15)
                keyboard_selected = True
            except Exception:
                keyboard_selected = False

            if not keyboard_selected:
                if not self._pick_from_open_listbox(page, ui_label):
                    page.screenshot(path=f"etsy_{self.shop_key}_about_when_made_option_error.png")
                    raise RuntimeError(f"Failed to select 'When was it made?' option: {ui_label}")
            time.sleep(0.2)

        # Continue
        continued = False
        for locator in [
            modal.get_by_role("button", name="Continue"),
            modal.get_by_text("Continue"),
            modal.locator("xpath=.//button[contains(., 'Continue')]")
        ]:
            try:
                locator.click()
                continued = True
                break
            except Exception:
                try:
                    page.keyboard.press("Enter")
                except Exception:
                    pass
                time.sleep(0.2)
        if not continued:
            page.screenshot(path=f"etsy_{self.shop_key}_about_continue_error.png")
            raise RuntimeError("Continue button in about modal did not become clickable.")

        time.sleep(0.4)
        self._dismiss_overlays(page)

    # -------------------- PUBLIC --------------------
    def create_product(self, product: ProductInput, headless: bool = False) -> None:
        """
        İlk adım: listing-editor/create sayfasına git, kategori modalında arat, ilk sonucu seç ve Continue de.
        İlerleyen adımlarda aynı fonksiyonda (veya alt fonksiyonlarda) title/desc/price/qty vb. dolduracağız.
        """
        ctx = self.mgr.launch_persistent_context(self.shop_key, headless=headless)
        page = ctx.new_page()

        page.goto(self.LISTING_CREATE_URL, wait_until="domcontentloaded")

        # Kategori modalı bazen otomatik açılır, bazen de kategori alanına tıklamak gerekir.
        # 0.5 sn bekleyip modal gelmediyse About sekmesindeki Category alanına bir tıklama deneyebiliriz.
        time.sleep(0.5)

        # --- 1) Kategori input'una yaz ---
        category_filled = False
        input_candidates = [
            # Robust: placeholder üzerinden
            lambda: page.get_by_placeholder("Search for a category").fill(product.category_query),
            # Fallback: kullanıcıdan gelen mutlak XPath (portal değişirse kırılabilir)
            lambda: page.locator("xpath=/html/body/div[7]/div[2]/div[2]/div[2]/div[2]/div[1]/div[2]/div[1]/input").fill(product.category_query),
        ]
        for attempt in input_candidates:
            try:
                attempt()
                category_filled = True
                break
            except PWTimeoutError:
                continue
            except Exception:
                # Diğer hatalarda da sıradakine geç.
                continue

        if not category_filled:
            page.screenshot(path=f"etsy_{self.shop_key}_category_input_error.png")
            raise RuntimeError("Category input not found or not interactable.")

        # Küçük bir bekleme: öneri listesi gelsin
        time.sleep(0.4)

        # --- 2) İlk öneriyi seç ---
        selected = False
        # a) Önce: öneri paneli gelsin
        try:
            # herhangi bir option/listbox görününceye kadar bekle
            page.wait_for_selector("[role='option'], [role='listbox'], //div[@role='listbox']", timeout=3000)
        except Exception:
            pass

        # b) Klavye ile seç (combobox'larda en stabil yol)
        try:
            page.keyboard.press("ArrowDown")
            time.sleep(0.1)
            page.keyboard.press("Enter")
            selected = True
        except Exception:
            selected = False

        # c) Eğer olmadıysa: çeşitli locator’larla ilk öğeyi tıkla
        if not selected:
            click_candidates = [
                # role=option ilk öğe
                lambda: page.get_by_role("option").first.click(),
                # listbox içindeki ilk öğe (div/li)
                lambda: page.locator("xpath=(//div[@role='listbox']//div | //ul[@role='listbox']//li)[1]").click(),
                # genel dropdown içinde ilk selectable div
                lambda: page.locator("xpath=(//div[contains(@class,'dropdown') or contains(@class,'popover') or contains(@class,'menu')]//div)[1]").click(),
            ]
            for c in click_candidates:
                try:
                    c()
                    selected = True
                    break
                except Exception:
                    continue

        if not selected:
            page.screenshot(path=f"etsy_{self.shop_key}_category_select_error.png")
            raise RuntimeError("Could not select the first category suggestion.")

        # seçim sonrası butonun enable olmasına küçük bir zaman tanıyalım
        time.sleep(0.3)

        # --- 3) Continue butonuna bas ---
        clicked = False
        try:
            btn = page.get_by_role("button", name="Continue")
            time.sleep(0.1)
            for _ in range(8):
                try:
                    btn.click()
                    clicked = True
                    break
                except Exception:
                    # bazen focus Enter ile çalışır
                    try:
                        page.keyboard.press("Enter")
                    except Exception:
                        pass
                    time.sleep(0.25)
            if not clicked:
                raise RuntimeError("Continue button did not become clickable.")
        except Exception:
            # Fallback: mutlak xpath ile dene
            try:
                page.locator("xpath=/html/body/div[7]/div[2]/div[2]/div[2]/div[3]/div/button").click()
                clicked = True
            except Exception:
                page.screenshot(path=f"etsy_{self.shop_key}_category_continue_error.png")
                raise

        # Bu noktada kategori seçilmiş ve ana forma dönmüş olmamız gerekir
        # Birkaç yüz ms bekleyelim
        time.sleep(0.4)
        page.screenshot(path=f"etsy_{self.shop_key}_after_category.png")

        # Call new about modal fill helper
        self._fill_about_modal(page, product)
        self._restore_scroll(page)
        self._fill_title_desc_photos(page, product)
        self._scroll_to_bottom(page)
        page.screenshot(path=f"etsy_{self.shop_key}_after_about.png")

        # kısa gösterim süresi
        time.sleep(50)
        # temiz kapanış (restore bubble engellemek için)
        try:
            self.mgr.close_context(self.shop_key)
        except Exception:
            pass
