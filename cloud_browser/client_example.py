"""Minimal Python client for the cloud browser - drop this into your agent's toolset."""

import os
import sys

import httpx


class CloudBrowser:
    def __init__(self, base_url: str, api_key: str = ""):
        self.base = base_url.rstrip("/")
        self.headers = {"X-API-Key": api_key} if api_key else {}
        self.session_id: str | None = None

    def open(self, url: str, session_id: str | None = None) -> dict:
        r = httpx.post(f"{self.base}/open", headers=self.headers,
                       json={"url": url, "session_id": session_id}, timeout=120)
        r.raise_for_status()
        self.session_id = r.json()["session_id"]
        return r.json()

    def snapshot(self) -> dict:
        r = httpx.post(f"{self.base}/snapshot", headers=self.headers,
                       json={"session_id": self.session_id}, timeout=60)
        r.raise_for_status()
        return r.json()

    def click(self, selector: str) -> dict:
        r = httpx.post(f"{self.base}/click", headers=self.headers,
                       json={"session_id": self.session_id, "selector": selector}, timeout=60)
        r.raise_for_status()
        return r.json()

    def type(self, selector: str, text: str, submit: bool = False) -> dict:
        r = httpx.post(f"{self.base}/type", headers=self.headers,
                       json={"session_id": self.session_id, "selector": selector,
                             "text": text, "submit": submit}, timeout=60)
        r.raise_for_status()
        return r.json()

    def screenshot(self, path: str, full_page: bool = False) -> str:
        r = httpx.post(f"{self.base}/screenshot", headers=self.headers,
                       json={"session_id": self.session_id, "full_page": full_page}, timeout=120)
        r.raise_for_status()
        with open(path, "wb") as f:
            f.write(r.content)
        return path

    def eval(self, script: str):
        r = httpx.post(f"{self.base}/eval", headers=self.headers,
                       json={"session_id": self.session_id, "script": script}, timeout=60)
        r.raise_for_status()
        return r.json()["result"]

    def close(self) -> dict:
        r = httpx.post(f"{self.base}/close", headers=self.headers,
                       json={"session_id": self.session_id}, timeout=60)
        r.raise_for_status()
        return r.json()


if __name__ == "__main__":
    base = os.environ.get("CLOUD_BROWSER_URL", "http://localhost:8099")
    key = os.environ.get("CLOUD_BROWSER_API_KEY", "")
    browser = CloudBrowser(base, key)
    print(browser.open(sys.argv[1] if len(sys.argv) > 1 else "https://example.com"))
    snap = browser.snapshot()
    print(snap["title"], "-", snap["text"][:300])

