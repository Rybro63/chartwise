"""Client for the encounter service's internal API, as the transcriber (SERVICE role)."""

import threading
import time

import httpx

from .errors import EncounterApiError, EncounterNotFound


class EncounterClient:
    def __init__(self, base_url: str, username: str, password: str, *, timeout: float = 60.0, attempts: int = 4,
                 transport: httpx.BaseTransport | None = None, sleep=time.sleep):
        self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport)
        self._username = username
        self._password = password
        self._attempts = attempts
        self._sleep = sleep
        self._token: str | None = None
        self._token_expires = 0.0
        self._lock = threading.Lock()

    def recording(self, encounter_id: str) -> dict:
        return self._call("GET", f"/internal/encounters/{encounter_id}/recording").json()

    def audio(self, encounter_id: str) -> bytes:
        return self._call("GET", f"/internal/encounters/{encounter_id}/recording/audio").content

    def submit(self, encounter_id: str, transcript: str, model: str, audio_seconds: float) -> None:
        self._call("POST", f"/internal/encounters/{encounter_id}/transcript",
                   json={"transcript": transcript, "model": model, "audioSeconds": audio_seconds})

    def _call(self, method: str, path: str, **kwargs) -> httpx.Response:
        for attempt in range(1, self._attempts + 1):
            try:
                resp = self._http.request(method, path, headers={"Authorization": f"Bearer {self._bearer()}"}, **kwargs)
            except httpx.TransportError:
                resp = None
            if resp is not None:
                if resp.status_code in (200, 204):
                    return resp
                if resp.status_code == 404:
                    raise EncounterNotFound()
                if resp.status_code == 401:
                    self._invalidate()
                elif resp.status_code < 500 and resp.status_code != 429:
                    raise EncounterApiError(f"encounter service returned HTTP {resp.status_code}")
            if attempt < self._attempts:
                self._sleep(min(0.5 * 2 ** (attempt - 1), 5.0))
        raise EncounterApiError("encounter service unavailable after retries")

    def _bearer(self) -> str:
        with self._lock:
            if self._token and time.time() < self._token_expires - 60:
                return self._token
            try:
                resp = self._http.post("/api/auth/login", json={"username": self._username, "password": self._password})
            except httpx.TransportError:
                raise EncounterApiError("encounter service unreachable") from None
            if resp.status_code != 200:
                raise EncounterApiError(f"transcriber login failed with HTTP {resp.status_code}")
            self._token = resp.json()["token"]
            self._token_expires = time.time() + 3600
            return self._token

    def _invalidate(self) -> None:
        with self._lock:
            self._token = None

    def close(self) -> None:
        self._http.close()
