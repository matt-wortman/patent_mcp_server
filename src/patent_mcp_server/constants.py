"""
Constants used throughout the USPTO Patent MCP Server.

This module defines all constants, magic strings, and enumerations used
across the application to avoid duplication and improve maintainability.
"""

class Sources:
    """Patent data source types."""
    GRANTED_PATENTS = "USPAT"
    PUBLISHED_APPLICATIONS = "US-PGPUB"
    OCR = "USOCR"
    ALL = [GRANTED_PATENTS, PUBLISHED_APPLICATIONS, OCR]


class Fields:
    """Common field names in API responses."""
    GUID = "guid"
    TYPE = "type"
    IMAGE_LOCATION = "imageLocation"
    PAGE_COUNT = "pageCount"
    DOCUMENT_STRUCTURE = "document_structure"
    PATENTS = "patents"
    DOCS = "docs"
    ERROR = "error"
    MESSAGE = "message"
    STATUS_CODE = "status_code"
    ERROR_CODE = "errorCode"
    ERROR_MESSAGE = "errorMessage"
    NUM_FOUND = "numFound"
    RESULTS = "results"
    TOTAL = "total"


class PrintStatus:
    """PDF print job status values."""
    COMPLETED = "COMPLETED"
    PENDING = "PENDING"
    FAILED = "FAILED"


class HTTPMethods:
    """HTTP methods."""
    GET = "GET"
    POST = "POST"


class Defaults:
    """Default values for various operations."""
    SEARCH_START = 0
    SEARCH_LIMIT = 100
    SEARCH_LIMIT_MAX = 500
    API_LIMIT = 25
    RETRY_DELAY = 1.0
    RATE_LIMIT_RETRY_DELAY = 5


class PTABTrialTypes:
    """PTAB trial type codes."""
    IPR = "IPR"  # Inter Partes Review
    PGR = "PGR"  # Post Grant Review
    CBM = "CBM"  # Covered Business Method
    DER = "DER"  # Derivation proceeding

    ALL = [IPR, PGR, CBM, DER]


