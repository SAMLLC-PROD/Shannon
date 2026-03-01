"""Tests for QAM constellation generator."""

from shannon.qam import (
    address_to_constellation, render_ascii, render_svg,
    data_to_pattern, layer_grid_size
)
from shannon.zeckendorf import data_to_address


def test_layer_grid_sizes():
    assert layer_grid_size(1) == 10
    assert layer_grid_size(2) == 32
    assert layer_grid_size(3) == 100


def test_constellation_deterministic():
    data = b"hello world"
    addr = data_to_address(data)
    p1 = address_to_constellation(addr, layer=1)
    p2 = address_to_constellation(addr, layer=1)
    assert p1 == p2


def test_constellation_unique_per_input():
    p1 = data_to_pattern(b"hello world")["points"]
    p2 = data_to_pattern(b"goodbye world")["points"]
    assert p1 != p2


def test_all_points_within_grid():
    pattern = data_to_pattern(b"test data", layer=1)
    grid = pattern["grid_size"]
    for x, y in pattern["points"]:
        assert 0 <= x < grid
        assert 0 <= y < grid


def test_ascii_render_returns_string():
    pattern = data_to_pattern(b"test")
    assert isinstance(pattern["ascii"], str)
    assert "●" in pattern["ascii"] or "◉" in pattern["ascii"]


def test_svg_render_returns_valid_svg():
    pattern = data_to_pattern(b"test")
    assert pattern["svg"].startswith("<svg")
    assert "</svg>" in pattern["svg"]


def test_data_to_pattern_structure():
    pattern = data_to_pattern(b"Shannon", layer=1)
    assert "layer" in pattern
    assert "grid_size" in pattern
    assert "address_components" in pattern
    assert "points" in pattern
    assert "ascii" in pattern
    assert "svg" in pattern
    assert pattern["layer"] == 1
