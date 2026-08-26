"""BYOK CAPTCHA solvers — 2Captcha & CapSolver.

Solves image/reCAPTCHA/hCaptcha challenges through the provider's HTTP API.
The solvers are async and fully optional: nothing is called unless
``scraping.captcha.enabled`` is true and a key is configured.

Integration points:
* ``solve_image(base64)`` — classic image captcha.
* ``solve_recaptcha(sitekey, page_url)`` — invisible/v2 reCAPTCHA token.
"""

from __future__ import annotations

import asyncio
import base64
import time
from typing import Any, Dict

import httpx

from jiro.config import Settings
from jiro.errors import JiroError


class CaptchaError(JiroError):
    code = "captcha_error"
    status_code = 502


class CaptchaSolver:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        cfg = settings.captcha
        self.enabled = bool(cfg.get("enabled", False))
        self.provider = (cfg.get("provider") or "capsolver").lower()
        self.api_key = (cfg.get("api_key") or "").strip()
        self.timeout = float(cfg.get("timeout", 180))

    @property
    def ready(self) -> bool:
        return self.enabled and bool(self.api_key) and self.provider in ("capsolver", "2captcha")

    # ------------------------------------------------------------- dispatcher
    async def solve_image(self, image_bytes: bytes) -> str:
        b64 = base64.b64encode(image_bytes).decode()
        if self.provider == "capsolver":
            return await self._capsolver_image(b64)
        if self.provider == "2captcha":
            return await self._twocaptcha_image(b64)
        raise CaptchaError(f"unsupported captcha provider '{self.provider}'")

    async def solve_recaptcha(self, sitekey: str, page_url: str) -> str:
        if self.provider == "capsolver":
            return await self._capsolver_recaptcha(sitekey, page_url)
        if self.provider == "2captcha":
            return await self._twocaptcha_recaptcha(sitekey, page_url)
        raise CaptchaError(f"unsupported captcha provider '{self.provider}'")

    # ---------------------------------------------------------------- capsolver
    async def _capsolver_image(self, b64: str) -> str:
        payload = {
            "clientKey": self.api_key,
            "task": {"type": "ImageToTextTask", "body": b64},
        }
        task_id = await self._capsolver_create(payload)
        return await self._capsolver_wait(task_id)

    async def _capsolver_recaptcha(self, sitekey: str, page_url: str) -> str:
        payload = {
            "clientKey": self.api_key,
            "task": {
                "type": "ReCaptchaV2TaskProxyLess",
                "websiteURL": page_url,
                "websiteKey": sitekey,
            },
        }
        task_id = await self._capsolver_create(payload)
        return await self._capsolver_wait(task_id)

    async def _capsolver_create(self, task: Dict[str, Any]) -> str:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post("https://api.capsolver.com/createTask", json=task)
            resp.raise_for_status()
            data = resp.json()
        if data.get("errorId"):
            raise CaptchaError(f"capsolver createTask failed: {data.get('errorDescription')}")
        return data["taskId"]

    async def _capsolver_wait(self, task_id: str) -> str:
        deadline = time.monotonic() + self.timeout
        async with httpx.AsyncClient(timeout=30) as client:
            while time.monotonic() < deadline:
                resp = await client.post("https://api.capsolver.com/getTaskResult", json={
                    "clientKey": self.api_key, "taskId": task_id,
                })
                data = resp.json()
                if data.get("errorId"):
                    raise CaptchaError(f"capsolver getTaskResult failed: {data.get('errorDescription')}")
                status = data.get("status")
                if status == "ready":
                    return data["solution"].get("text") or data["solution"].get("gRecaptchaResponse") or ""
                await asyncio.sleep(3)
        raise CaptchaError("captcha solve timed out")

    # ---------------------------------------------------------------- 2captcha
    async def _twocaptcha_image(self, b64: str) -> str:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post("http://2captcha.com/in.php", data={
                "key": self.api_key, "method": "base64", "body": b64, "json": 1,
            })
            data = resp.json()
        if data.get("status") != 1:
            raise CaptchaError(f"2captcha in.php failed: {data}")
        return await self._twocaptcha_poll(data["request"])

    async def _twocaptcha_recaptcha(self, sitekey: str, page_url: str) -> str:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post("http://2captcha.com/in.php", data={
                "key": self.api_key, "method": "userrecaptcha",
                "googlekey": sitekey, "pageurl": page_url, "json": 1,
            })
            data = resp.json()
        if data.get("status") != 1:
            raise CaptchaError(f"2captcha in.php failed: {data}")
        return await self._twocaptcha_poll(data["request"])

    async def _twocaptcha_poll(self, captcha_id: str) -> str:
        deadline = time.monotonic() + self.timeout
        async with httpx.AsyncClient(timeout=30) as client:
            while time.monotonic() < deadline:
                resp = await client.get("http://2captcha.com/res.php", params={
                    "key": self.api_key, "action": "get", "id": captcha_id, "json": 1,
                })
                data = resp.json()
                if data.get("status") == 1:
                    return data["request"]
                if data.get("request") != "CAPCHA_NOT_READY":
                    raise CaptchaError(f"2captcha failed: {data.get('request')}")
                await asyncio.sleep(5)
        raise CaptchaError("captcha solve timed out")
