"""Google Resumable Upload Protocol implementation for YouTube videos and Shorts."""

import asyncio
import logging
import random
from typing import Any
import httpx

logger = logging.getLogger(__name__)

YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
DEFAULT_CHUNK_SIZE = 5 * 1024 * 1024  # 5 MB (must be a multiple of 256 KB)


def calculate_exponential_backoff(
    attempt: int,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
) -> float:
    """Calculate exponential backoff with jitter to prevent thundering herd."""
    delay = min(max_delay, base_delay * (2 ** attempt))
    jitter = random.uniform(0, 0.5 * delay)
    return delay + jitter


def is_youtube_short(width: int, height: int, duration_seconds: float) -> bool:
    """Determine if a video qualifies as a YouTube Short (vertical aspect ratio and <= 180s)."""
    # Vertical (9:16) or Square (1:1) and duration <= 180s (YouTube's extended Shorts limit)
    is_vertical_or_square = height >= width
    is_short_duration = duration_seconds <= 180.0
    return is_vertical_or_square and is_short_duration


class YouTubeResumableUploader:
    """Handles multi-chunk resumable uploads to YouTube Data API v3 with automatic recovery."""

    def __init__(
        self,
        access_token: str,
        timeout: float = 60.0,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> None:
        self.access_token = access_token
        self.timeout = timeout
        self.chunk_size = chunk_size

    def _get_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }

    async def initiate_upload_session(
        self,
        title: str,
        description: str,
        tags: list[str],
        file_size: int,
        content_type: str = "video/mp4",
        privacy_status: str = "public",
        made_for_kids: bool = False,
        client: httpx.AsyncClient | None = None,
    ) -> str:
        """Start a resumable upload session and obtain the unique session upload URI."""
        endpoint = f"{YOUTUBE_UPLOAD_URL}?uploadType=resumable&part=snippet,status"
        headers = {
            **self._get_headers(),
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(file_size),
            "X-Upload-Content-Type": content_type,
        }
        metadata = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "tags": tags,
                "categoryId": "22",  # People & Blogs default
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": made_for_kids,
            },
        }

        should_close = False
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout)
            should_close = True

        try:
            response = await client.post(endpoint, headers=headers, json=metadata)
            response.raise_for_status()

            session_url = response.headers.get("Location")
            if not session_url:
                raise RuntimeError("گوگل لینک ادامه آپلود (Location header) را برنگرداند.")
            return session_url
        finally:
            if should_close:
                await client.aclose()

    async def query_upload_status(
        self,
        session_url: str,
        file_size: int,
        client: httpx.AsyncClient | None = None,
    ) -> int:
        """Query Google server for the byte range received so far (handling 308 Resume Incomplete)."""
        headers = {
            **self._get_headers(),
            "Content-Range": f"bytes */{file_size}",
            "Content-Length": "0",
        }

        should_close = False
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout)
            should_close = True

        try:
            response = await client.put(session_url, headers=headers)
            if response.status_code == 308:
                range_header = response.headers.get("Range")
                if range_header and range_header.startswith("bytes=0-"):
                    last_byte = int(range_header.split("-")[1])
                    return last_byte + 1
            elif response.status_code in (200, 201):
                # Upload was already finished
                return file_size
            return 0
        finally:
            if should_close:
                await client.aclose()

    async def upload_chunk(
        self,
        session_url: str,
        chunk_data: bytes,
        start_byte: int,
        file_size: int,
        client: httpx.AsyncClient | None = None,
    ) -> tuple[bool, dict[str, Any] | None, int]:
        """Upload a single chunk.

        Returns:
            (is_complete, video_response_json_or_none, next_byte_offset)
        """
        end_byte = start_byte + len(chunk_data) - 1
        headers = {
            **self._get_headers(),
            "Content-Range": f"bytes {start_byte}-{end_byte}/{file_size}",
            "Content-Length": str(len(chunk_data)),
        }

        should_close = False
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout)
            should_close = True

        try:
            response = await client.put(session_url, headers=headers, content=chunk_data)

            # 308 Resume Incomplete: Chunk received, upload ongoing
            if response.status_code == 308:
                range_header = response.headers.get("Range")
                if range_header and range_header.startswith("bytes=0-"):
                    last_byte = int(range_header.split("-")[1])
                    return False, None, last_byte + 1
                return False, None, end_byte + 1

            # 200 or 201: Entire video uploaded successfully
            if response.status_code in (200, 201):
                return True, response.json(), file_size

            response.raise_for_status()
            return False, None, end_byte + 1
        finally:
            if should_close:
                await client.aclose()
