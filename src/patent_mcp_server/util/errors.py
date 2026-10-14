"""
Error handling utilities for USPTO Patent MCP Server.

This module provides consistent error response structures and error handling
utilities used throughout the application.
"""

from typing import Optional, Dict, Any


class ApiError:
    """Utility class for creating consistent error responses."""

    @staticmethod
    def create(
        message: str,
        status_code: Optional[int] = None,
        error_code: Optional[str] = None,
        details: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        Create a standardized error response dictionary.

        Args:
            message: Human-readable error message
            status_code: HTTP status code if applicable
            error_code: Application-specific error code
            details: Additional error details

        Returns:
            Dictionary with standardized error structure
        """
        error_dict = {
            "error": True,
            "message": message
        }

        if status_code is not None:
            error_dict["status_code"] = status_code

        if error_code is not None:
            error_dict["error_code"] = error_code

        if details is not None:
            error_dict["details"] = details

        return error_dict

    @staticmethod
    def from_http_error(status_code: int, response_text: str, response_json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Create an error response from an HTTP error.

        Args:
            status_code: HTTP status code
            response_text: Raw response text
            response_json: Parsed JSON response if available

        Returns:
            Standardized error dictionary
        """
        message = response_text  # Default fallback
        error_code = None
        details = None

        if response_json:
            # Try to extract message - prefer "message" field, then "error" if it's a string
            potential_message = response_json.get("message")
            if not isinstance(potential_message, str) or not potential_message:
                potential_message = response_json.get("error")

            # Only use if it's a non-empty string (not a boolean like {"error": true})
            if isinstance(potential_message, str) and potential_message:
                message = potential_message

            error_code = response_json.get("errorCode")
            details = response_json.get("errorDetails")

        return ApiError.create(
            message=message,
            status_code=status_code,
            error_code=error_code,
            details=details
        )

    @staticmethod
    def from_exception(exception: Exception, context: Optional[str] = None) -> Dict[str, Any]:
        """
        Create an error response from an exception.

        Args:
            exception: The exception that was raised
            context: Optional context about where the error occurred

        Returns:
            Standardized error dictionary
        """
        message = f"{context}: {str(exception)}" if context else str(exception)
        return ApiError.create(
            message=message,
            error_code=exception.__class__.__name__
        )

    @staticmethod
    def not_found(resource_type: str, identifier: str) -> Dict[str, Any]:
        """
        Create a 'not found' error response.

        Args:
            resource_type: Type of resource (e.g., "Patent", "Application")
            identifier: The identifier that was not found

        Returns:
            Standardized error dictionary
        """
        return ApiError.create(
            message=f"{resource_type} {identifier} not found",
            status_code=404,
            error_code="NOT_FOUND"
        )

    @staticmethod
    def validation_error(message: str, field: Optional[str] = None) -> Dict[str, Any]:
        """
        Create a validation error response.

        Args:
            message: Description of the validation error
            field: The field that failed validation

        Returns:
            Standardized error dictionary
        """
        return ApiError.create(
            message=message,
            status_code=400,
            error_code="VALIDATION_ERROR",
            details={"field": field} if field else None
        )


def is_error(response: Dict[str, Any]) -> bool:
    """
    Check if a response dictionary represents an error.

    Args:
        response: Response dictionary to check

    Returns:
        True if the response contains an error, False otherwise
    """
    return response.get("error", False) is True


def is_no_match(response: Dict[str, Any]) -> bool:
    """
    Check if a search response is USPTO's 404 for "no matching records".

    ODP and PTAB search endpoints answer a search with zero matches with
    HTTP 404 and a body of just {"error": "Not Found"} — the same reply a
    lookup gets for a record that does not exist. Only search tools should
    use this: for a lookup, a 404 really is an error.

    Args:
        response: Response dictionary from a search request

    Returns:
        True if the response is a 404 error, False otherwise
    """
    return is_error(response) and response.get("status_code") == 404


def is_unreadable_search(response: Dict[str, Any]) -> bool:
    """
    Check if a search response is USPTO's reply to a search it could not parse.

    ODP and PTAB do not reject a malformed q (an unbalanced bracket or
    quotation mark, a stray colon): they answer HTTP 200 with a count of 0
    and no records list at all (live-verified 2026-10-14). A real search
    always carries a records list, and zero matches come back as a 404
    (see is_no_match), so a success with no list means the search was not
    understood.

    Args:
        response: Response dictionary from a search request

    Returns:
        True if the response is a success that carries no records list
    """
    if is_error(response):
        return False
    return not any(isinstance(value, list) for value in response.values())
