"""Sloty canonical weekday contract.

    Saturday = 0    Sunday = 1    Monday = 2    Tuesday = 3
    Wednesday = 4   Thursday = 5  Friday = 6

Python's ``date.weekday()`` returns Monday = 0 … Sunday = 6.
These helpers convert at the boundary.
"""


def to_sloty_weekday(python_weekday: int) -> int:
    """Convert Python ``date.weekday()`` (Mon=0) to Sloty weekday (Sat=0)."""
    return (python_weekday + 2) % 7


def to_python_weekday(sloty_weekday: int) -> int:
    """Convert Sloty weekday (Sat=0) to Python ``date.weekday()`` (Mon=0)."""
    return (sloty_weekday + 5) % 7
