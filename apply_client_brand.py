#!/usr/bin/env python3
"""
apply_client_brand.py: deterministic white-label brand pass for a built Meirvo bundle.

Run AFTER build_meirvo_bundle.py and BEFORE qc_bundle.py. This script replaces the
old prose instructions ("remove the spec stack, inject a logo img"). It is the ONLY
sanctioned way to white-label a bundle: do not hand-edit index.html.

  python3 apply_client_brand.py \
      --bundle ~/workspace/{session}/render_bundle \
      --logo   ~/workspace/{session}/assets/client_logo.png \
      --headline "SOLD" \
      --name "Jane Doe Realty" \
      [--cta "CALL 512 555 0100"] [--address "123 Main St, Austin TX"] \
      [--accent "#F4F0E8"] [--logo-width 260] [--no-endcard-logo]

What it does (idempotent, safe to re-run):
  1. Validates the logo: real PNG, alpha channel present, >= 600px wide. Hard fail otherwise.
  2. Copies it to {bundle}/assets/client_logo.png.
  3. Empties the top-right wordmark text overlay (id="wordmark"): logo replaces it.
  4. Injects a persistent top-right logo (clip-gated to the content section only).
  5. Injects a centered logo on the black end card (omit with --no-endcard-logo).
  6. Rewrites end-card text: #price -> --headline, #agent_name -> --name,
     #cta_line -> --cta (empty string hides it), #cta_address -> --address
     (defaults to existing text).
  7. Recolors the headline from Meirvo Rust to --accent (default Ivory #F4F0E8).
  8. Injects .scrim-top / .scrim-bottom + 3-layer text shadow + z-order
     video < scrims(4) < vignette(5) < text/logo(6).
  9. Re-reads the patched file and VERIFIES every change landed. Prints a JSON
     report. Exit 0 only when all checks pass. Exit 1 = do not ship, read the report.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import struct
import sys
from pathlib import Path


def log(msg: str) -> None:
    print(f"[client-brand] {msg}", file=sys.stderr)


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------------------
# Logo validation (stdlib PNG header parse: no PIL dependency)
# ---------------------------------------------------------------------------


def png_info(path: Path) -> dict | None:
    data = path.read_bytes()
    if len(data) < 26 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width, height = struct.unpack(">II", data[16:24])
    color_type = data[25]
    return {
        "width": width,
        "height": height,
        "color_type": color_type,
        "has_alpha": color_type in (4, 6),  # 4 = gray+alpha, 6 = RGBA
    }


def validate_logo(path: Path, min_width: int = 600) -> dict:
    if not path.exists():
        sys.exit(f"[client-brand] FATAL: logo not found at {path}")
    info = png_info(path)
    if info is None:
        sys.exit(
            "[client-brand] FATAL: logo is not a PNG (JPEG or other format). "
            "Get a transparent PNG from the client before any production run."
        )
    if not info["has_alpha"]:
        sys.exit(
            f"[client-brand] FATAL: logo PNG has no alpha channel (color_type={info['color_type']}). "
            "A baked background will show as a box over footage. Get a transparent PNG."
        )
    if info["width"] < min_width:
        sys.exit(
            f"[client-brand] FATAL: logo is {info['width']}px wide, minimum {min_width}px. "
            "It will look soft at render size. Get a larger file."
        )
    log(f"logo OK: {info['width']}x{info['height']} RGBA")
    return info


# ---------------------------------------------------------------------------
# index.html patch helpers (the builder emits each overlay as one <div> line)
# ---------------------------------------------------------------------------


def set_overlay_text(html: str, overlay_id: str, new_text: str) -> tuple[str, bool]:
    pattern = re.compile(
        r'(<div id="' + re.escape(overlay_id) + r'" class="clip overlay-text"[^>]*>)(.*?)(</div>)',
        re.DOTALL,
    )
    if not pattern.search(html):
        return html, False
    html = pattern.sub(lambda m: m.group(1) + esc(new_text) + m.group(3), html, count=1)
    return html, True


def get_overlay_attr(html: str, overlay_id: str, attr: str) -> float | None:
    m = re.search(
        r'<div id="' + re.escape(overlay_id) + r'"[^>]*data-' + attr + r'="([\d.]+)"', html
    )
    return float(m.group(1)) if m else None


def recolor_overlay(html: str, overlay_id: str, new_color: str) -> tuple[str, bool]:
    pattern = re.compile(r'(<div id="' + re.escape(overlay_id) + r'"[^>]*style="[^"]*?color: )([^;"]+)')
    if not pattern.search(html):
        return html, False
    return pattern.sub(lambda m: m.group(1) + new_color, html, count=1), True


def max_track_index(html: str) -> int:
    return max(int(t) for t in re.findall(r'data-track-index="(\d+)"', html))


SCRIM_CSS = """      .scrim-top {
        position: absolute; top: 0; left: 0; right: 0; height: 240px;
        background: linear-gradient(to bottom, rgba(0,0,0,0.65), rgba(0,0,0,0));
        z-index: 4; pointer-events: none;
      }
      .scrim-bottom {
        position: absolute; bottom: 0; left: 0; right: 0; height: 420px;
        background: linear-gradient(to top, rgba(0,0,0,0.78), rgba(0,0,0,0));
        z-index: 4; pointer-events: none;
      }
"""

THREE_LAYER_SHADOW = (
    "text-shadow: 0 1px 2px rgba(0, 0, 0, 0.8), 0 2px 12px rgba(0, 0, 0, 0.55), "
    "0 6px 32px rgba(0, 0, 0, 0.4);\n        z-index: 6;"
)


def main() -> None:
    ap = argparse.ArgumentParser(description="White-label brand pass for a built Meirvo bundle")
    ap.add_argument("--bundle", required=True, help="render_bundle directory (already built)")
    ap.add_argument("--logo", required=True, help="client logo: transparent PNG >= 600px wide")
    ap.add_argument("--headline", default="SOLD", help="end card headline (replaces price)")
    ap.add_argument("--name", required=True, help="client agent/brokerage display name")
    ap.add_argument("--cta", default="", help="end card CTA line (empty = hidden)")
    ap.add_argument("--address", default=None, help="override end card address line")
    ap.add_argument("--accent", default="#F4F0E8", help="headline color (white-label, default Ivory)")
    ap.add_argument("--logo-width", type=int, default=260, help="persistent logo width px (default 260)")
    ap.add_argument("--no-endcard-logo", action="store_true", help="skip centered end card logo")
    args = ap.parse_args()

    bundle = Path(args.bundle).expanduser()
    index_path = bundle / "index.html"
    if not index_path.exists():
        sys.exit(f"[client-brand] FATAL: {index_path} not found. Build the bundle first.")

    logo_src = Path(args.logo).expanduser()
    info = validate_logo(logo_src)

    assets = bundle / "assets"
    assets.mkdir(exist_ok=True)
    shutil.copyfile(logo_src, assets / "client_logo.png")
    log("logo copied into bundle assets/")

    html = index_path.read_text()
    report: dict = {"checks": {}, "ok": False}

    # Idempotency: strip any previous injection of ours, then re-inject fresh.
    html = re.sub(r'\n? *<div id="client-logo(?:-endcard)?"[^>]*></div>', "", html)
    html = re.sub(r' *\.scrim-top \{.*?\n      \}\n *\.scrim-bottom \{.*?\n      \}\n', "", html, flags=re.DOTALL)
    html = re.sub(r'\n? *<div class="scrim-(?:top|bottom)"></div>', "", html)

    # 1. Empty the wordmark (top-right text). The logo replaces it.
    html, ok = set_overlay_text(html, "wordmark", "")
    report["checks"]["wordmark_emptied"] = ok

    # 2. End card text swaps.
    html, ok = set_overlay_text(html, "price", args.headline)
    report["checks"]["headline_set"] = ok
    html, ok = set_overlay_text(html, "agent_name", args.name)
    report["checks"]["name_set"] = ok
    html, ok = set_overlay_text(html, "cta_line", args.cta)
    report["checks"]["cta_set"] = ok
    if args.address is not None:
        html, ok = set_overlay_text(html, "cta_address", args.address)
        report["checks"]["address_set"] = ok

    # 3. Recolor headline away from Meirvo Rust.
    html, ok = recolor_overlay(html, "price", args.accent)
    report["checks"]["headline_recolored"] = ok

    # 4. Timing math from the live file (never hardcode).
    total = get_overlay_attr(html, "wordmark", "duration")
    price_start = get_overlay_attr(html, "price", "start")
    root_m = re.search(r'data-composition-id="main"[\s\S]*?data-duration="([\d.]+)"', html)
    if total is None and root_m:
        total = float(root_m.group(1))
    if total is None or price_start is None:
        sys.exit("[client-brand] FATAL: could not read composition timing from index.html")
    end_card_start = round(price_start - 0.1, 3)
    content_dur = end_card_start

    # 5. Inject logo divs (background-image divs: same element type the template
    #    already uses, clip-gated by data-start/data-duration like every overlay).
    t = max_track_index(html)
    logo_w = args.logo_width
    logo_h = max(int(logo_w * info["height"] / info["width"]), 24)
    persistent = (
        f'      <div id="client-logo" class="clip" data-start="0" data-duration="{content_dur}" '
        f'data-track-index="{t + 1}" style="position: absolute; top: 232px; right: 144px; '
        f"width: {logo_w}px; height: {logo_h}px; background-image: url('assets/client_logo.png'); "
        f"background-size: contain; background-repeat: no-repeat; background-position: top right; "
        f'z-index: 6; pointer-events: none; filter: drop-shadow(0 2px 14px rgba(0,0,0,0.55));"></div>'
    )
    endcard_logo = ""
    if not args.no_endcard_logo:
        ec_start = round(end_card_start + 0.1, 3)
        ec_dur = round(total - ec_start, 3)
        endcard_logo = (
            f'\n      <div id="client-logo-endcard" class="clip" data-start="{ec_start}" '
            f'data-duration="{ec_dur}" data-track-index="{t + 2}" style="position: absolute; '
            f"top: 540px; left: 100px; right: 144px; height: 190px; "
            f"background-image: url('assets/client_logo.png'); background-size: contain; "
            f'background-repeat: no-repeat; background-position: center top; z-index: 6; pointer-events: none;"></div>'
        )

    scrim_divs = '      <div class="scrim-top"></div>\n      <div class="scrim-bottom"></div>\n'
    vignette_anchor = '      <div class="vignette"></div>'
    if vignette_anchor not in html:
        sys.exit("[client-brand] FATAL: vignette anchor not found; template changed, do not hand-patch")
    html = html.replace(vignette_anchor, persistent + endcard_logo + "\n" + scrim_divs + vignette_anchor, 1)

    # 6. Scrim CSS + 3-layer text shadow + z-order.
    html = html.replace("      .vignette {", SCRIM_CSS + "      .vignette {", 1)
    html = html.replace("text-shadow: 0 2px 18px rgba(0, 0, 0, 0.45);", THREE_LAYER_SHADOW, 1)

    index_path.write_text(html)

    # 7. VERIFY from disk (never trust in-memory state).
    final = index_path.read_text()
    report["checks"]["logo_asset_in_bundle"] = (assets / "client_logo.png").exists()
    report["checks"]["persistent_logo_div"] = 'id="client-logo"' in final
    report["checks"]["endcard_logo_div"] = args.no_endcard_logo or 'id="client-logo-endcard"' in final
    wm = re.search(r'<div id="wordmark"[^>]*>(.*?)</div>', final, re.DOTALL)
    report["checks"]["wordmark_is_empty"] = bool(wm) and wm.group(1).strip() == ""
    pr = re.search(r'<div id="price"[^>]*>(.*?)</div>', final, re.DOTALL)
    report["checks"]["headline_text_present"] = bool(pr) and pr.group(1).strip() == esc(args.headline)
    report["checks"]["scrims_present"] = ".scrim-top" in final and 'class="scrim-bottom"' in final
    report["checks"]["three_layer_shadow"] = "0 6px 32px" in final
    report["checks"]["no_meirvo_rust_on_headline"] = "#C65A2E" not in (pr.group(0) if pr else "#C65A2E")

    report["ok"] = all(report["checks"].values())
    report["end_card_start"] = end_card_start
    report["total_duration"] = total
    print(json.dumps(report, indent=2))
    if not report["ok"]:
        log("ONE OR MORE CHECKS FAILED. Do not ship this bundle.")
        sys.exit(1)
    log("all checks passed: bundle is white-labeled")


if __name__ == "__main__":
    main()
