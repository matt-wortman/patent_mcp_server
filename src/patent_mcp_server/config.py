"""
Configuration management for USPTO Patent MCP Server.

This module provides centralized configuration loading from environment
variables with sensible defaults.
"""

from dotenv import load_dotenv
import os
import tempfile
from importlib.metadata import PackageNotFoundError, version
from typing import Optional
import logging

# Load environment variables from .env file
load_dotenv()

# Single source of truth for the package version (pyproject.toml). Falls
# back when running from an uninstalled source checkout (no dist-info),
# so importing config never crashes the server just to render a version.
try:
    PACKAGE_VERSION: str = version("patent_mcp_server")
except PackageNotFoundError:
    PACKAGE_VERSION = "0.0.0+uninstalled"

# Claude Code saves any MCP text result over 50,000 characters to a file and
# shows the agent only a file-path notice, unless the tool declares a larger
# size in its tools/list ``_meta["anthropic/maxResultSizeChars"]``. Claude Code
# honours that declaration up to a hard ceiling of 500,000 characters.
CLAUDE_CODE_RESULT_CEILING_CHARS = 500_000

# Inline replies to Claude Code stay this far under the declared size, so the
# reply plus any notice fields the server adds still fits.
CLAUDE_CODE_MAX_RESPONSE_CHARS_CAP = 490_000


def claude_code_max_response_chars(raw: Optional[str]) -> int:
    """Return the inline character limit for Claude Code from its setting.

    Defaults to 450,000 and is clamped to at most 490,000.
    """
    value = int(raw) if raw is not None else 450_000
    return min(value, CLAUDE_CODE_MAX_RESPONSE_CHARS_CAP)


class Config:
    """Centralized configuration class."""

    # API Keys
    USPTO_API_KEY: Optional[str] = os.getenv("USPTO_API_KEY")

    # API Endpoints
    PPUBS_BASE_URL: str = os.getenv("PPUBS_BASE_URL", "https://ppubs.uspto.gov")
    API_BASE_URL: str = os.getenv("API_BASE_URL", "https://api.uspto.gov")

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # HTTP Settings
    USER_AGENT: str = os.getenv("USER_AGENT", f"patent-mcp-server/{PACKAGE_VERSION}")
    REQUEST_TIMEOUT: float = float(os.getenv("REQUEST_TIMEOUT", "30.0"))

    # Rate Limiting & Retry
    MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "3"))
    RETRY_DELAY: float = float(os.getenv("RETRY_DELAY", "1.0"))
    RETRY_MIN_WAIT: int = int(os.getenv("RETRY_MIN_WAIT", "2"))
    RETRY_MAX_WAIT: int = int(os.getenv("RETRY_MAX_WAIT", "10"))

    # Session Management
    SESSION_EXPIRY_MINUTES: int = int(os.getenv("SESSION_EXPIRY_MINUTES", "30"))

    # Caching
    ENABLE_CACHING: bool = os.getenv("ENABLE_CACHING", "true").lower() == "true"

    # Response Size Management (for LLM context windows)
    MAX_RESPONSE_TOKENS: int = int(os.getenv("MAX_RESPONSE_TOKENS", "8000"))
    TRUNCATE_LARGE_RESPONSES: bool = os.getenv("TRUNCATE_LARGE_RESPONSES", "true").lower() == "true"

    # Every tool declares this result size to Claude Code (see above).
    DECLARED_MAX_RESULT_CHARS: int = CLAUDE_CODE_RESULT_CEILING_CHARS
    # MCP clients (initialize clientInfo.name, compared case-insensitively)
    # that get the character limit below instead of MAX_RESPONSE_TOKENS.
    # "claude-code" is the name Claude Code 2.1.316 sends, observed directly.
    LARGE_REPLY_CLIENTS: tuple = ("claude-code",)
    # Inline limit for those clients, measured on the indented JSON text
    # FastMCP actually sends.
    CLAUDE_CODE_MAX_RESPONSE_CHARS: int = claude_code_max_response_chars(
        os.getenv("CLAUDE_CODE_MAX_RESPONSE_CHARS")
    )

    # Download directory for large binary payloads (PDFs) and oversized JSON
    # dumps. Defaults to <tempdir>/patent_docs; override via env.
    DOWNLOAD_DIR: str = os.getenv(
        "PATENT_MCP_DOWNLOAD_DIR",
        os.path.join(tempfile.gettempdir(), "patent_docs"),
    )

    @classmethod
    def get_log_level(cls) -> int:
        """Convert LOG_LEVEL string to logging constant."""
        levels = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL
        }
        return levels.get(cls.LOG_LEVEL.upper(), logging.INFO)

    @classmethod
    def validate(cls) -> None:
        """Validate configuration and log warnings for missing optional settings."""
        logger = logging.getLogger(__name__)

        if not cls.USPTO_API_KEY:
            logger.warning(
                "USPTO_API_KEY not set. ODP API tools (api.uspto.gov) will return 403. "
                "Register at https://data.uspto.gov and visit 'My ODP' to get your API key."
            )

        logger.info(f"Configuration loaded: LOG_LEVEL={cls.LOG_LEVEL}, "
                   f"TIMEOUT={cls.REQUEST_TIMEOUT}s, "
                   f"CACHING={'enabled' if cls.ENABLE_CACHING else 'disabled'}")


# Create singleton instance
config = Config()
