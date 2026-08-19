"""
Input validation for USPTO Patent MCP Server.

Plain-function validators for patent and application numbers. Both accept
formatted input (US prefixes, commas, slashes, spaces), strip everything
but digits, and raise ValueError with a clear message on bad input.
"""


def validate_patent_number(patent_number: str) -> str:
    """
    Validate and clean a patent number.

    Args:
        patent_number: Raw patent number input (e.g., "US 9,876,543")

    Returns:
        Cleaned patent number string (digits only)

    Raises:
        ValueError: If patent number is not a string or contains no digits
    """
    if not isinstance(patent_number, str):
        raise ValueError("Invalid patent number: input must be a string")
    cleaned = ''.join(c for c in patent_number if c.isdigit())
    if not cleaned:
        raise ValueError("Invalid patent number: must contain at least one digit")
    return cleaned


def validate_app_number(app_num: str) -> str:
    """
    Validate and clean an application number.

    Args:
        app_num: Raw application number input (e.g., "14/412,875")

    Returns:
        Cleaned application number string (digits only)

    Raises:
        ValueError: If application number is not a string, contains no
            digits, or has fewer than 6 digits
    """
    if not isinstance(app_num, str):
        raise ValueError("Invalid application number: input must be a string")
    cleaned = ''.join(c for c in app_num if c.isdigit())
    if not cleaned:
        raise ValueError("Invalid application number: must contain at least one digit")
    if len(cleaned) < 6:
        raise ValueError("Invalid application number: must be at least 6 digits")
    return cleaned


def validate_pagination(
    offset: int,
    limit: int,
    *,
    max_limit: int,
    offset_name: str = "offset",
    limit_name: str = "limit",
) -> tuple:
    """
    Validate pagination inputs at a tool boundary.

    Args:
        offset: Starting position (must be a non-negative int)
        limit: Page size (must be an int in [1, max_limit])
        max_limit: Source-appropriate maximum page size
        offset_name: Parameter name to use in error messages (e.g., "start")
        limit_name: Parameter name to use in error messages (e.g., "rows")

    Returns:
        (offset, limit) unchanged

    Raises:
        ValueError: If either value is not an int or is out of range
    """
    # bool is a subclass of int but is never a sensible pagination value
    if not isinstance(offset, int) or isinstance(offset, bool):
        raise ValueError(f"Invalid {offset_name}: must be an integer")
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise ValueError(f"Invalid {limit_name}: must be an integer")
    if offset < 0:
        raise ValueError(f"Invalid {offset_name}: must be non-negative")
    if limit < 1:
        raise ValueError(f"Invalid {limit_name}: must be at least 1")
    if limit > max_limit:
        raise ValueError(f"Invalid {limit_name}: must be at most {max_limit}")
    return offset, limit


def validate_status_code(status_code) -> str:
    """
    Validate a USPTO examination status code (digits only).

    Status codes are interpolated into quoted Lucene criteria, so anything
    other than plain digits is rejected.

    Args:
        status_code: Status code input (e.g., "30", "150")

    Returns:
        The status code as a digits-only string

    Raises:
        ValueError: If the input is not a string of digits
    """
    if not isinstance(status_code, str) or not status_code.isdigit():
        raise ValueError("Invalid status code: must be a string of digits")
    return status_code
