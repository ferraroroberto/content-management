"""Imgur upload for the illustration copyright check.

Google Lens searches by URL, so each illustration needs to be reachable on the
open web once. Ported from the sibling repo's ``linkedin/check_ip/imgur_client.py``
with its retry behaviour intact — Imgur rate-limits hard and the original's
exponential backoff is what makes a 1,000-image run survive it.

An image is uploaded once; the URL is kept on the ``images`` row and reused by
every later search.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger("check_ip.imgur")

API_URL = "https://api.imgur.com/3/image"
INITIAL_DELAY_S = 5
MAX_DELAY_S = 1800


class ImgurClient:
    """Uploads images to Imgur, backing off when told to."""

    def __init__(self, client_id: str, access_token: str):
        self.client_id = client_id
        self.access_token = access_token
        self.headers = {"Authorization": f"Bearer {access_token}"}

    def validate(self) -> bool:
        """Cheap credential check. Never fatal — the caller decides."""
        try:
            response = requests.get("https://api.imgur.com/3/account/me",
                                    headers=self.headers, timeout=30)
        except requests.RequestException as err:
            logger.warning("⚠️ could not reach Imgur to validate credentials: %s", err)
            return False
        if response.status_code == 200:
            logger.info("✅ Imgur credentials accepted")
            return True
        logger.warning("⚠️ Imgur rejected the credentials: HTTP %s", response.status_code)
        return False

    def upload(self, image_path: Path, retries: int = 20) -> Optional[str]:
        """Upload one image, returning its URL.

        Retries on 503 and on network errors with a doubling delay, and honours
        the ``Retry-After`` header on a 429. Any other status is a real failure
        — retrying a 400 just burns the clock — so it returns ``None`` at once.
        """
        delay = INITIAL_DELAY_S

        for attempt in range(1, retries + 1):
            try:
                with open(image_path, "rb") as handle:
                    response = requests.post(API_URL, headers=self.headers,
                                             files={"image": handle}, timeout=120)
            except requests.RequestException as err:
                logger.warning("⚠️ network error uploading %s (attempt %s/%s): %s",
                               image_path.name, attempt, retries, err)
            else:
                if response.status_code == 200:
                    url = response.json()["data"]["link"]
                    logger.info("✅ uploaded %s → %s", image_path.name, url)
                    return url
                if response.status_code == 429:
                    delay = int(response.headers.get("Retry-After", delay))
                    logger.warning("⚠️ Imgur rate limit; waiting %ss (attempt %s/%s)",
                                   delay, attempt, retries)
                elif response.status_code == 503:
                    logger.warning("⚠️ Imgur busy (503); retrying in %ss (attempt %s/%s)",
                                   delay, attempt, retries)
                else:
                    logger.error("❌ Imgur refused %s: HTTP %s %s", image_path.name,
                                 response.status_code, response.text[:200])
                    return None

            time.sleep(delay)
            delay = min(delay * 2, MAX_DELAY_S)

        logger.error("❌ gave up on %s after %s attempts", image_path.name, retries)
        return None
