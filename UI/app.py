# UI/app.py
import sys
import json
import pathlib
import tempfile
from typing import Dict, List, Optional
import streamlit as st

# --- Proje kökünü (THE E/) sys.path'e ekle ---
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from TheProd.ProductBuilder import ProductBuilder
from TheProd.PicPre import ALLOWED_RATIOS, PicPre

PRODUCTS_DIR = ROOT / "Products"

# ============== yardımcılar ==============
def _read_json(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _write_json(path: pathlib.Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def _product_dirs() -> List[pathlib.Path]:
    if not PRODUCTS_DIR.exists():
        return []
    dirs = []
    for p in sorted(PRODUCTS_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.is_dir() and (p / "product.json").exists():
            dirs.append(p)
    return dirs

def _cover_image(pdir: pathlib.Path) -> Optional[str]:
    gen_dir = pdir / "images" / "generated"
    src_dir = pdir / "images" / "source"
    def first_img(d: pathlib.Path) -> Optional[str]:
        if d.exists():
            imgs = [x for x in d.iterdir() if x.suffix.lower() in {".png",".jpg",".jpeg",".webp"}]
            if imgs:
                imgs.sort(key=lambda x: x.stat().st_mtime, reverse=True)
                return str(imgs[0])
        return None
    return first_img(gen_dir) or first_img(src_dir)

def _all_images(pdir: pathlib.Path) -> List[str]:
    out = []
    for sub in ["generated", "source"]:
        d = pdir / "images" / sub
        if d.exists():
            out += [str(x) for x in d.iterdir() if x.suffix.lower() in {".png",".jpg",".jpeg",".webp"}]
    return sorted(out, key=lambda p: pathlib.Path(p).stat().st_mtime, reverse=True)

def _append_generated_to_meta(product_dir: pathlib.Path, new_paths: List[str]) -> None:
    meta_path = product_dir / "product.json"
    meta = _read_json(meta_path)
    imgs = meta.setdefault("images", {})
    gen_list = imgs.setdefault("generated", [])
    gen_list.extend(new_paths)
    _write_json(meta_path, meta)

def _image_grid(paths: List[str], cols: int = 6, limit: int = 24) -> None:
    """Render images in a compact grid to avoid long scrolling."""
    paths = paths[:limit]
    if not paths:
        return
    # Break into rows of `cols`
    for i in range(0, len(paths), cols):
        row = paths[i:i+cols]
        c = st.columns(len(row))
        for j, p in enumerate(row):
            with c[j]:
                st.image(p, use_container_width=True)

# ============== UI ==============
st.set_page_config(page_title="The E • Products", page_icon="📦", layout="wide")
st.title("📦 The E")

tabs = st.tabs(["🗂 Products", "🛠 Build"])

# ----------------- TAB: Products -----------------
with tabs[0]:
    st.subheader("🗂 Products")
    st.caption("Ürün klasörlerini kapak görseli ve başlığıyla listele. 'View' ile detay sayfasına girersin.")

    # Arama
    q = st.text_input("Ara (title / klasör adı)", placeholder="örn. plate, bowl, blue, 23cm ...").strip().lower()

    selected: Optional[str] = st.session_state.get("selected_product")

    # --- Liste görünümü ---
    if not selected:
        pdirs = _product_dirs()
        if q:
            pdirs = [
                p for p in pdirs
                if q in (_read_json(p / "product.json").get("title","") or "").lower()
                or q in p.name.lower()
            ]

        if not pdirs:
            st.info("Kriterine uyan ürün klasörü bulunamadı.")
        else:
            for p in pdirs:
                meta = _read_json(p / "product.json")
                title = meta.get("title") or p.name
                cov = _cover_image(p)
                is_shared = bool(meta.get("shared", False))

                with st.container(border=True):
                    cols = st.columns([1, 3, 1])
                    if cov:
                        cols[0].image(cov, use_container_width=True)
                    # başlık + rozet
                    badge = (
                        "  <span style='background:#10b981;color:white;border-radius:6px;"
                        "padding:2px 6px;font-size:0.75rem;margin-left:8px;'>Shared</span>"
                    ) if is_shared else ""
                    cols[1].markdown(f"**{title}**{badge}", unsafe_allow_html=True)
                    if cols[2].button("View", key=f"view_{p.name}"):
                        st.session_state.selected_product = str(p)
                        st.rerun()

    # --- Detay görünümü ---
    else:
        sel = pathlib.Path(selected)
        if not sel.exists():
            st.warning("Seçili ürün bulunamadı.")
            st.session_state.selected_product = None
        else:
            if st.button("⬅ Back to Products"):
                st.session_state.selected_product = None
                st.rerun()

            meta_path = sel / "product.json"
            meta = _read_json(meta_path)
            title = meta.get("title") or sel.name
            description = meta.get("description") or ""
            provider_link = meta.get("provider_link") or ""
            shared = bool(meta.get("shared", False))
            cover = _cover_image(sel)

            st.markdown(f"### {title}")
            if cover:
                st.image(cover, use_container_width=True)

            st.markdown("**Provider link**")
            if provider_link:
                st.link_button("Open provider link", provider_link, use_container_width=True)
                st.code(provider_link, language="bash")
            else:
                st.caption("— hiç provider link kaydı yok —")

            st.markdown("**Description**")
            if description:
                st.write(description)
            else:
                st.caption("— açıklama yok —")

            new_shared = st.checkbox("Shared", value=shared)
            if new_shared != shared:
                meta["shared"] = new_shared
                _write_json(meta_path, meta)
                st.success("Kaydedildi (shared).")

            with st.expander("product.json"):
                st.code(json.dumps(meta, indent=2, ensure_ascii=False), language="json")

            # tüm görseller (ilk 18)
            st.markdown("**All images**")
            imgs = _all_images(sel)
            if imgs:
                _image_grid(imgs, cols=6, limit=24)
            else:
                st.caption("— görsel yok —")

            st.divider()

            # ---------------- Ek üretim (source'tan seç → adet → üret) ----------------
            st.subheader("➕ Generate more images for this product")
            src_dir = sel / "images" / "source"
            src_options = []
            if src_dir.exists():
                src_options = [x for x in src_dir.iterdir() if x.suffix.lower() in {".png",".jpg",".jpeg",".webp"}]

            if not src_options:
                st.caption("Kaynak (source) görsel bulunamadı.")
            else:
                # Satırı 3 sütuna bölelim; solda selectbox + mini preview, ortada adet, sağda ratios
                col_a, col_b, col_c = st.columns([2.5, 1, 2])

                with col_a:
                    c1, c2 = st.columns([3, 1])  # c1: selectbox, c2: küçük thumbnail
                    chosen_src = c1.selectbox(
                        "Source image",
                        options=src_options,
                        format_func=lambda p: p.name,
                        index=0,
                        key="src_select",
                    )
                    with c2:
                        st.caption("Preview")
                        st.image(str(chosen_src), use_container_width=True)

                with col_b:
                    add_qty = st.number_input("How many?", min_value=1, max_value=5, value=2, step=1)

                with col_c:
                    add_ratios = st.multiselect("Aspect ratios (optional)", sorted(ALLOWED_RATIOS), default=[])

                if st.button("▶️ Generate & append to product"):
                    try:
                        with st.spinner("Yeni sahneler üretiliyor…"):
                            # PicPre ile üret
                            res = PicPre().run_auto(
                                str(chosen_src),
                                quantity=int(add_qty),
                                ratios=add_ratios if add_ratios else None
                            )
                            # çıktıları ürün klasörüne kopyala
                            gen_dir = sel / "images" / "generated"
                            gen_dir.mkdir(parents=True, exist_ok=True)
                            new_paths = []
                            for local_path in res.get("saved_files", {}).values():
                                lp = pathlib.Path(local_path)
                                if lp.exists():
                                    dst = gen_dir / lp.name
                                    if lp != dst:
                                        import shutil
                                        shutil.copy2(lp, dst)
                                    new_paths.append(str(dst))
                            if new_paths:
                                _append_generated_to_meta(sel, new_paths)
                        st.success(f"{len(new_paths)} yeni görsel eklendi.")
                        st.session_state.selected_product = str(sel)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Hata: {e}")

# ----------------- TAB: Build -----------------
with tabs[1]:
    st.subheader("🛠 Product Builder (tek tık ürün klasörü)")
    st.caption("Birden fazla görsel seç, her biri için üretilecek sahne adedini gir; ratio/hints/provider link ekle; tek tıkla oluştur.")

    pb_files = st.file_uploader(
        "Görseller (aynı ürüne ait)",
        type=["jpg", "jpeg", "png", "webp"],
        accept_multiple_files=True,
        key="pb_files",
    )

    per_qty: Dict[str, int] = {}
    if pb_files:
        st.write("**Her görsel için adet (1–5)**")
        cols = st.columns(min(3, len(pb_files)))
        for idx, f in enumerate(pb_files):
            with cols[idx % len(cols)]:
                per_qty[f.name] = st.number_input(
                    f"Qty • {f.name}", min_value=1, max_value=5, value=2, step=1, key=f"qty_{f.name}"
                )

    ratios = st.multiselect("Aspect ratios (opsiyonel)", sorted(ALLOWED_RATIOS), default=[])
    provider_link = st.text_input("Provider link (opsiyonel)")
    hints = st.text_area(
        "Hints (opsiyonel) — örn: 'plate 23 cm, matte white, ceramic'",
        placeholder="Boş bırakılırsa sadece görsellerden çıkarım yapılır."
    )

    if st.button("📦 Build Product Folder", use_container_width=True, type="primary"):
        if not pb_files:
            st.warning("En az 1 görsel seçmelisin.")
        else:
            import pathlib as _pl
            tmp_list = []
            qty_by_path: Dict[str, int] = {}
            for f in pb_files:
                p = _pl.Path(tempfile.gettempdir()) / f.name
                p.write_bytes(f.read())
                tmp_list.append(str(p))
                if f.name in per_qty:
                    qty_by_path[str(p)] = int(per_qty[f.name])

            try:
                with st.spinner("Ürün klasörü oluşturuluyor…"):
                    built = ProductBuilder().build(
                        image_paths=tmp_list,
                        qty=qty_by_path if qty_by_path else 2,
                        ratios=ratios if ratios else None,
                        hints=hints if hints else None,
                        provider_link=provider_link or None,
                    )
                st.success("Ürün klasörü hazır 🎉")
                st.session_state.selected_product = built.product_dir  # Products’a döndüğünde direkt detay aç
                st.rerun()
            except Exception as e:
                st.error(f"Hata: {e}")