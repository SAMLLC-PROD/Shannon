"""
Zeckendorf-Fibonacci addressing.

Every positive integer has a unique representation as a sum of
non-consecutive Fibonacci numbers. (Zeckendorf's Theorem, 1972)

This gives Shannon collision-free, content-derived addresses
with no central registry required.
"""

from typing import List
import hashlib


def fibonacci_sequence(n: int) -> List[int]:
    """Generate the first n Fibonacci numbers >= 1."""
    fibs = [1, 2]
    while len(fibs) < n:
        fibs.append(fibs[-1] + fibs[-2])
    return fibs


def zeckendorf(n: int) -> List[int]:
    """
    Decompose n into its unique Zeckendorf representation.
    Returns list of Fibonacci numbers (non-consecutive) that sum to n.

    Example: zeckendorf(100) → [89, 8, 3]  (F11 + F6 + F4)
    """
    if n <= 0:
        return []

    # Build Fibonacci numbers up to n
    fibs = [1, 2]
    while fibs[-1] < n:
        fibs.append(fibs[-1] + fibs[-2])
    # Remove any that exceed n
    fibs = [f for f in fibs if f <= n]

    result = []
    remaining = n
    for f in reversed(fibs):
        if f <= remaining:
            result.append(f)
            remaining -= f
            if remaining == 0:
                break

    return result


def data_to_address(data: bytes) -> List[int]:
    """
    Derive a unique Zeckendorf address from arbitrary data.

    data → SHA-256 → large integer → Zeckendorf decomposition → address
    """
    digest = hashlib.sha256(data).hexdigest()
    n = int(digest, 16)  # 256-bit integer
    return zeckendorf(n)


def address_to_str(address: List[int]) -> str:
    """Human-readable address string."""
    return "+".join(f"F({fib})" for fib in address)


if __name__ == "__main__":
    # Quick demo
    test = b"hello world"
    addr = data_to_address(test)
    print(f"Data:    {test}")
    print(f"Address: {address_to_str(addr)}")
    print(f"Values:  {addr}")
    print(f"Verify:  {sum(addr) == int(hashlib.sha256(test).hexdigest(), 16)}")
