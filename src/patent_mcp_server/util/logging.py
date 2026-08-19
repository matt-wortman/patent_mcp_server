import httpx
import logging
import json

logger = logging.getLogger('logging_transport')

# Headers safe to log verbatim. Anything not listed is masked — allowlist
# semantics, so a new credential-bearing header can never leak by default.
SAFE_HEADERS = frozenset({
    "accept",
    "accept-encoding",
    "accept-language",
    "cache-control",
    "connection",
    "content-encoding",
    "content-length",
    "content-type",
    "date",
    "etag",
    "host",
    "last-modified",
    "pragma",
    "referer",
    "server",
    "transfer-encoding",
    "user-agent",
    "vary",
})

REDACTED = "[REDACTED]"


def redact_headers(headers) -> dict:
    """Return a copy of ``headers`` with every non-allowlisted value masked."""
    return {
        name: value if name.lower() in SAFE_HEADERS else REDACTED
        for name, value in dict(headers).items()
    }


# Define custom transport that logs all requests and responses
class LoggingTransport(httpx.AsyncBaseTransport):

    def __init__(self, transport):
        self.transport = transport

    async def handle_async_request(self, request):
        # Log the request
        logger.debug(f"REQUEST: {request.method} {request.url}")
        logger.debug(f"REQUEST HEADERS: {redact_headers(request.headers)}")

        try:
            # For body logging, convert to string if possible
            if request.content:
                body = request.content
                try:
                    if isinstance(body, bytes):
                        body = body.decode('utf-8')
                    # Try to parse and pretty-print JSON
                    try:
                        json_body = json.loads(body)
                        logger.debug(f"REQUEST BODY: \n{json.dumps(json_body, indent=2)}")
                    except:
                        logger.debug(f"REQUEST BODY: {body}")
                except:
                    logger.debug(f"REQUEST BODY: {body}")
        except Exception as e:
            logger.debug(f"Error logging request body: {e}")

        # Get the response
        response = await self.transport.handle_async_request(request)

        # Log the response
        logger.debug(f"RESPONSE: {response.status_code} from {request.url}")
        logger.debug(f"RESPONSE HEADERS: {redact_headers(response.headers)}")

        return response

    async def aclose(self):
        await self.transport.aclose()
