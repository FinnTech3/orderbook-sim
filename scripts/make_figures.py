"""Generates the figures in docs/figures from an actual simulation run.

The numbers are produced here rather than typed in, so a chart cannot drift
away from the code behind it. Run after changing anything that affects fills:

    python3 scripts/make_figures.py

Emits a light and a dark variant. GitHub picks between them with <picture>.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from obsim.cli import _run  # noqa: E402
from obsim.queue_model import ALL_MODELS  # noqa: E402

OUT = ROOT / "docs" / "figures"
EVENTS = 40_000
SEED = 1


@dataclass(frozen=True)
class Theme:
    name: str
    surface: str
    text_primary: str
    text_secondary: str
    muted: str
    gridline: str
    baseline: str
    #: Ordinal blue ramp, light to dark. The models are ordered — pessimistic
    #: through optimistic — so a single hue stepped by lightness is the honest
    #: encoding. Categorical hues would imply they are unrelated categories.
    ramp: tuple[str, str, str]
    band: str


LIGHT = Theme("light", "#fcfcfb", "#0b0b0b", "#52514e", "#898781",
              "#e1e0d9", "#c3c2b7", ("#1c5cab", "#3987e5", "#86b6ef"), "#2a78d6")
DARK = Theme("dark", "#1a1a19", "#ffffff", "#c3c2b7", "#898781",
             "#2c2c2a", "#383835", ("#1c5cab", "#3987e5", "#86b6ef"), "#3987e5")

FONT = "system-ui,-apple-system,'Segoe UI',sans-serif"


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def range_chart(rows: list[tuple[str, float]], theme: Theme) -> str:
    """The fill rate under each assumption, and the span between them."""
    width, height = 700, 300
    left, right = 148, 118
    plot_w = width - left - right
    top = 96
    row_h = 46

    values = [value for _, value in rows]
    lo = min(values) - 0.045
    hi = max(values) + 0.045

    def x(value: float) -> float:
        return left + (value - lo) / (hi - lo) * plot_w

    span_lo, span_hi = min(values), max(values)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}" '
        f'font-family="{FONT}" role="img" '
        f'aria-label="Fill rate under three queue-position assumptions, on '
        f'identical market data">',
        f'<rect width="{width}" height="{height}" fill="{theme.surface}"/>',
        f'<text x="24" y="32" font-size="15" font-weight="600" '
        f'fill="{theme.text_primary}">The same data gives three different '
        f'answers</text>',
        f'<text x="24" y="52" font-size="12" fill="{theme.text_secondary}">'
        f'Fill rate for one passive strategy. Only the assumption about where '
        f'cancellations sat changes.</text>',
        # The span the data cannot resolve.
        f'<rect x="{x(span_lo):.1f}" y="{top - 14:.1f}" '
        f'width="{x(span_hi) - x(span_lo):.1f}" '
        f'height="{len(rows) * row_h + 4:.1f}" fill="{theme.band}" '
        f'opacity="0.10"/>',
    ]

    for index, (label, value) in enumerate(rows):
        cy = top + index * row_h
        colour = theme.ramp[index % len(theme.ramp)]
        parts.append(
            f'<text x="{left - 16}" y="{cy + 5:.1f}" font-size="12.5" '
            f'fill="{theme.text_primary}" text-anchor="end">{esc(label)}</text>'
        )
        parts.append(
            f'<line x1="{left}" y1="{cy:.1f}" x2="{x(value):.1f}" '
            f'y2="{cy:.1f}" stroke="{theme.gridline}" stroke-width="1"/>'
        )
        parts.append(
            f'<circle cx="{x(value):.1f}" cy="{cy:.1f}" r="8" fill="{colour}" '
            f'stroke="{theme.surface}" stroke-width="2"/>'
        )
        parts.append(
            f'<text x="{x(value) + 16:.1f}" y="{cy + 5:.1f}" font-size="13" '
            f'font-weight="600" fill="{theme.text_primary}">'
            f'{value:.1%}</text>'
        )

    bracket_y = top + len(rows) * row_h + 6
    parts.append(
        f'<line x1="{x(span_lo):.1f}" y1="{bracket_y:.1f}" '
        f'x2="{x(span_hi):.1f}" y2="{bracket_y:.1f}" '
        f'stroke="{theme.baseline}" stroke-width="1.5"/>'
    )
    for edge in (span_lo, span_hi):
        parts.append(
            f'<line x1="{x(edge):.1f}" y1="{bracket_y - 5:.1f}" '
            f'x2="{x(edge):.1f}" y2="{bracket_y + 5:.1f}" '
            f'stroke="{theme.baseline}" stroke-width="1.5"/>'
        )
    parts.append(
        f'<text x="{(x(span_lo) + x(span_hi)) / 2:.1f}" '
        f'y="{bracket_y + 24:.1f}" font-size="12" fill="{theme.text_secondary}" '
        f'text-anchor="middle">{(span_hi - span_lo) * 100:.1f} points the feed '
        f'cannot resolve</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


def main() -> int:
    rows = []
    for model in ALL_MODELS:
        replay = _run(model, SEED, EVENTS, 0, 10, 40, 0)
        rows.append((model.name, replay.sim.stats.fill_ratio))

    OUT.mkdir(parents=True, exist_ok=True)
    for theme in (LIGHT, DARK):
        (OUT / f"queue-range-{theme.name}.svg").write_text(
            range_chart(rows, theme)
        )

    print(f"wrote 2 figures to {OUT.relative_to(ROOT)}")
    for label, value in rows:
        print(f"  {label:<14} {value:.2%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
