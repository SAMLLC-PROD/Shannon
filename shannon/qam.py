"""
QAM Constellation Pattern Generator for Shannon.

Inspired by Quadrature Amplitude Modulation — encodes data as dot patterns
in 2D constellation space. Higher layers = higher-order QAM = more positions.

Layer 1: 2^100 positions (conceptually — rendered as scalable grid)
Layer N: dot density increases, compounding address space.

Each dictionary entry gets a unique visual dot pattern derived from its
Zeckendorf address. The constellation is human-inspectable and renderable.
"""

import math
import json
import hashlib
from typing import List, Tuple
from .zeckendorf import data_to_address


# QAM orders by layer (like QAM-4, QAM-16, QAM-64, QAM-256...)
# Each layer doubles the bits per axis → squares the constellation size
QAM_LAYERS = {
    1: 10,   # 10x10 grid = 100 points (base)
    2: 32,   # 32x32 = 1,024 points
    3: 100,  # 100x100 = 10,000 points
    4: 316,  # ~316x316 ≈ 100,000 points
}


def layer_grid_size(layer: int) -> int:
    """Grid dimension for a given layer."""
    return QAM_LAYERS.get(layer, 10 * (2 ** (layer - 1)))


def address_to_constellation(address: List[int], layer: int = 1) -> List[Tuple[int, int]]:
    """
    Map a Zeckendorf address to a set of (x, y) constellation points.

    Each Fibonacci component in the address maps to a unique grid position.
    The grid size is determined by the layer.
    """
    grid = layer_grid_size(layer)
    points = []
    for fib_val in address:
        # Fold the Fibonacci value into the grid using a space-filling curve approach
        idx = fib_val % (grid * grid)
        x = idx % grid
        y = idx // grid
        points.append((x, y))
    return points


def render_ascii(points: List[Tuple[int, int]], grid_size: int = 10) -> str:
    """
    Render constellation points as ASCII art.
    Used for debugging and human inspection.
    """
    grid = [['·'] * grid_size for _ in range(grid_size)]
    for i, (x, y) in enumerate(points):
        if 0 <= x < grid_size and 0 <= y < grid_size:
            # Use different symbols for overlapping points
            existing = grid[y][x]
            if existing == '·':
                grid[y][x] = '●'
            else:
                grid[y][x] = '◉'  # overlap marker

    lines = []
    lines.append('┌' + '─' * (grid_size * 2 - 1) + '┐')
    for row in grid:
        lines.append('│' + ' '.join(row) + '│')
    lines.append('└' + '─' * (grid_size * 2 - 1) + '┘')
    return '\n'.join(lines)


def render_svg(points: List[Tuple[int, int]], grid_size: int = 10,
               cell_px: int = 30) -> str:
    """
    Render constellation as SVG — suitable for UI display or export.
    """
    size = grid_size * cell_px
    margin = cell_px

    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{size + margin*2}" height="{size + margin*2}" '
        f'style="background:#0a0a0a">',
        # Grid lines
        *[
            f'<line x1="{margin}" y1="{margin + i*cell_px}" '
            f'x2="{margin + size}" y2="{margin + i*cell_px}" '
            f'stroke="#1a1a2e" stroke-width="0.5"/>'
            for i in range(grid_size + 1)
        ],
        *[
            f'<line x1="{margin + i*cell_px}" y1="{margin}" '
            f'x2="{margin + i*cell_px}" y2="{margin + size}" '
            f'stroke="#1a1a2e" stroke-width="0.5"/>'
            for i in range(grid_size + 1)
        ],
    ]

    # Plot constellation points
    plotted = set()
    for x, y in points:
        if 0 <= x < grid_size and 0 <= y < grid_size:
            cx = margin + x * cell_px + cell_px // 2
            cy = margin + y * cell_px + cell_px // 2
            color = "#00d4ff" if (x, y) not in plotted else "#ff6b35"
            svg_parts.append(
                f'<circle cx="{cx}" cy="{cy}" r="{cell_px//4}" '
                f'fill="{color}" opacity="0.9"/>'
            )
            plotted.add((x, y))

    svg_parts.append('</svg>')
    return '\n'.join(svg_parts)


def data_to_pattern(data: bytes, layer: int = 1) -> dict:
    """
    Full pipeline: data → Zeckendorf address → QAM constellation.
    Returns a dict with address, points, ascii art, and SVG.
    """
    address = data_to_address(data)
    grid_size = layer_grid_size(layer)
    points = address_to_constellation(address, layer)

    return {
        "layer": layer,
        "grid_size": grid_size,
        "address_components": len(address),
        "points": points,
        "ascii": render_ascii(points, min(grid_size, 20)),
        "svg": render_svg(points, min(grid_size, 20)),
    }


if __name__ == "__main__":
    # Demo
    samples = [
        b"hello world",
        b"Guy Shannon remembers everything",
        b"Lattice Network - Byzantine consensus",
    ]

    for data in samples:
        print(f"\n{'='*50}")
        print(f"Data: {data.decode()}")
        pattern = data_to_pattern(data, layer=1)
        print(f"Layer: {pattern['layer']} ({pattern['grid_size']}x{pattern['grid_size']} grid)")
        print(f"Address components: {pattern['address_components']} Fibonacci numbers")
        print(f"Points plotted: {len(pattern['points'])}")
        print(f"\nConstellation:\n{pattern['ascii']}")
        # Save SVG
        fname = f"/tmp/shannon_{hashlib.sha256(data).hexdigest()[:8]}.svg"
        with open(fname, 'w') as f:
            f.write(pattern['svg'])
        print(f"SVG saved: {fname}")
