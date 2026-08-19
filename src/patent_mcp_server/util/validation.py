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
