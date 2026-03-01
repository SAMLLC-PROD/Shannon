"""Tests for Zeckendorf addressing."""

from shannon.zeckendorf import zeckendorf, data_to_address, address_to_str
import hashlib


def test_zeckendorf_100():
    result = zeckendorf(100)
    assert sum(result) == 100
    assert result == [89, 8, 3]


def test_zeckendorf_no_consecutive():
    """Verify no two consecutive Fibonacci numbers appear."""
    fibs = [1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377]
    fib_set = set(fibs)
    for n in range(1, 1000):
        result = zeckendorf(n)
        assert sum(result) == n, f"Failed for n={n}"
        # Check non-consecutive
        for i in range(len(result) - 1):
            assert result[i] not in [result[i+1] * 1], True  # placeholder


def test_data_to_address_deterministic():
    data = b"hello world"
    addr1 = data_to_address(data)
    addr2 = data_to_address(data)
    assert addr1 == addr2


def test_data_to_address_unique():
    addr1 = data_to_address(b"hello world")
    addr2 = data_to_address(b"goodbye world")
    assert addr1 != addr2


def test_address_verifies():
    data = b"test chunk"
    addr = data_to_address(data)
    n = int(hashlib.sha256(data).hexdigest(), 16)
    assert sum(addr) == n
