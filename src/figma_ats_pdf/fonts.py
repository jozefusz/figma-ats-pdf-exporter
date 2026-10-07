"""Resolve Figma (family, weight, italic) triples to real embedded TTF fonts.

Both fonts this project cares about (Bricolage Grotesque, Fragment Mono) are
free/open Google Fonts, pulled straight from the public `google/fonts` repo
rather than relying on Figma to embed anything.

Bricolage Grotesque ships only as a variable font (wght axis, no static
instances) -- so for any non-default weight we instantiate a static TTF at
the exact weight with fontTools' varLib.instancer and cache the result.
Fragment Mono ships as plain static regular/italic files.
"""

from __future__ import annotations

import re
from pathlib import Path

import requests
from fontTools.varLib.instancer import instantiateVariableFont
from fontTools.ttLib import TTFont as FTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont as RLFont

CACHE_DIR = Path(__file__).parent / "fonts_cache"
CACHE_DIR.mkdir(exist_ok=True)

GITHUB_RAW = "https://raw.githubusercontent.com/google/fonts/main"

# family match key (lowercased, spaces stripped) -> source description
_FONT_SOURCES = {
    "bricolagegrotesque": {
        "kind": "variable",
        "repo_path": "ofl/bricolagegrotesque/BricolageGrotesque[opsz,wdth,wght].ttf",
        "axis": "wght",
    },
    "fragmentmono": {
        "kind": "static",
        "regular": "ofl/fragmentmono/FragmentMono-Regular.ttf",
        "italic": "ofl/fragmentmono/FragmentMono-Italic.ttf",
    },
}


def _family_key(family: str) -> str:
    return re.sub(r"[^a-z0-9]", "", family.lower())


def _download(repo_path: str, dest: Path) -> Path:
    if dest.exists():
        return dest
    url = f"{GITHUB_RAW}/{repo_path}"
    resp = requests.get(url, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to download font {url}: HTTP {resp.status_code}")
    dest.write_bytes(resp.content)
    return dest


def _named_instance_axes(font: FTFont, weight: int) -> dict[str, float]:
    """Pick the non-wght axis coordinates (opsz, wdth, ...) from the fvar
    named instance closest to `weight`, instead of leaving those axes at
    the *raw* fvar default.

    Google Fonts variable files often declare a raw axis default (e.g.
    Bricolage Grotesque's `opsz` defaults to 96 -- its heaviest, most
    "display"-oriented cut) that no named style in the font's own weight
    dropdown actually uses. Every real named instance (Regular, Bold,
    ExtraBold, ...) pins opsz to a much lower value (14) tuned for text
    sizes. Figma's font-weight picker resolves to one of those named
    instances, so replicating "weight N" by pinning only `wght` and
    leaving `opsz` at the raw default bakes in a heavier, blockier cut
    than Figma ever actually renders -- text comes out visibly bolder at
    every size, not just small ones.
    """
    fvar = font.get("fvar")
    if fvar is None or not fvar.instances:
        return {}
    best = min(fvar.instances, key=lambda inst: abs(inst.coordinates.get("wght", weight) - weight))
    return {tag: val for tag, val in best.coordinates.items() if tag != "wght"}


def _instantiate_weight(variable_path: Path, weight: int) -> Path:
    dest = CACHE_DIR / f"{variable_path.stem}-{weight}.ttf"
    if dest.exists():
        return dest
    font = FTFont(str(variable_path))
    axes = {"wght": weight, **_named_instance_axes(font, weight)}
    # updateFontNames=True is essential: without it every instantiated
    # weight keeps the *source* variable font's original name-table
    # entries (family/PostScript name), all identical. reportlab's font
    # cache keys off that PostScript name, so distinct weight files with
    # the same name silently collapse to whichever got parsed first --
    # every weight rendering as the default instance's weight.
    instantiateVariableFont(font, axes, inplace=True, updateFontNames=True)
    name_table = font["name"]
    unique_name = f"{variable_path.stem.split('[')[0]}-{weight}"
    for name_id in (1, 3, 4, 6):
        name_table.setName(unique_name, name_id, 3, 1, 0x409)
        name_table.setName(unique_name, name_id, 1, 0, 0)
    font.save(str(dest))
    return dest


class FontRegistry:
    """Resolves (family, weight, italic) -> a reportlab-registered font name,
    downloading/instantiating/caching TTFs as needed."""

    def __init__(self) -> None:
        self._registered: dict[tuple[str, int, bool], str] = {}
        self._unsupported: set[str] = set()

    def resolve(self, family: str, weight: int, italic: bool) -> str:
        key = (family, weight, italic)
        if key in self._registered:
            return self._registered[key]

        fkey = _family_key(family)
        source = _FONT_SOURCES.get(fkey)
        if source is None:
            self._unsupported.add(family)
            # Fall back to a core PDF font rather than hard-failing --
            # keeps the pipeline runnable while flagging the gap loudly.
            fallback = "Helvetica-Bold" if weight >= 600 else "Helvetica"
            self._registered[key] = fallback
            return fallback

        if source["kind"] == "variable":
            variable_path = CACHE_DIR / Path(source["repo_path"]).name
            _download(source["repo_path"], variable_path)
            static_path = _instantiate_weight(variable_path, weight)
        else:
            repo_path = source["italic" if italic else "regular"]
            static_path = CACHE_DIR / Path(repo_path).name
            _download(repo_path, static_path)

        reg_name = f"{fkey}-{weight}-{'i' if italic else 'r'}"
        pdfmetrics.registerFont(RLFont(reg_name, str(static_path)))
        self._registered[key] = reg_name
        return reg_name

    @property
    def unsupported_families(self) -> set[str]:
        return set(self._unsupported)
