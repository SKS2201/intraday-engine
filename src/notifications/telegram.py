from __future__ import annotations

from pathlib import Path
import sys
import time

import requests


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, timeout_sec: float = 8.0, retries: int = 2) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout_sec = timeout_sec
        self.retries = retries

    def send(
        self,
        text: str,
        dry_run: bool = False,
        parse_mode: str | None = None,
        disable_web_page_preview: bool = True,
    ) -> None:
        if dry_run:
            _safe_print(text)
            return
        if not self.bot_token or not self.chat_id:
            raise RuntimeError("telegram_credentials_missing")
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        last_err: Exception | None = None
        for i in range(self.retries + 1):
            try:
                r = requests.post(url, json=payload, timeout=self.timeout_sec)
                if r.status_code >= 400:
                    raise RuntimeError(f"telegram_send_failed:{r.status_code}:{r.text[:180]}")
                return
            except Exception as exc:
                last_err = exc
                if i < self.retries:
                    time.sleep(0.5 * (i + 1))
        raise RuntimeError(str(last_err))

    def send_chunked(
        self,
        text: str,
        max_chars: int = 3900,
        dry_run: bool = False,
        parse_mode: str | None = None,
        disable_web_page_preview: bool = True,
    ) -> None:
        chunks = split_message_chunks(text, max_chars=max_chars)
        for idx, chunk in enumerate(chunks):
            chunk_text = chunk
            if len(chunks) > 1:
                chunk_text = f"[{idx + 1}/{len(chunks)}]\n{chunk}"
            self.send(
                chunk_text,
                dry_run=dry_run,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview,
            )

    def send_document(self, file_path: str, caption: str = "", dry_run: bool = False) -> None:
        if dry_run:
            _safe_print(f"[document] {file_path}")
            if caption:
                _safe_print(caption)
            return
        if not self.bot_token or not self.chat_id:
            raise RuntimeError("telegram_credentials_missing")
        path = Path(file_path)
        if not path.exists():
            raise RuntimeError(f"telegram_document_missing:{file_path}")
        url = f"https://api.telegram.org/bot{self.bot_token}/sendDocument"
        data = {"chat_id": self.chat_id}
        if caption:
            data["caption"] = caption
        last_err: Exception | None = None
        for i in range(self.retries + 1):
            try:
                with path.open("rb") as fh:
                    files = {"document": (path.name, fh)}
                    r = requests.post(url, data=data, files=files, timeout=self.timeout_sec)
                if r.status_code >= 400:
                    raise RuntimeError(f"telegram_send_document_failed:{r.status_code}:{r.text[:180]}")
                return
            except Exception as exc:
                last_err = exc
                if i < self.retries:
                    time.sleep(0.5 * (i + 1))
        raise RuntimeError(str(last_err))


def split_message_chunks(text: str, max_chars: int = 3900) -> list[str]:
    if max_chars <= 0:
        raise ValueError("max_chars_must_be_positive")
    if len(text) <= max_chars:
        return [text]

    lines = text.split("\n")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            chunks.append("\n".join(current).strip())
            current = []
            current_len = 0

    for line in lines:
        addition = len(line) + (1 if current else 0)
        if addition > max_chars:
            flush()
            start = 0
            while start < len(line):
                end = min(start + max_chars, len(line))
                chunks.append(line[start:end])
                start = end
            continue
        if current_len + addition > max_chars:
            flush()
        current.append(line)
        current_len += len(line) + (1 if len(current) > 1 else 0)
    flush()
    return [c for c in chunks if c]


def _safe_print(text: str) -> None:
    encoding = sys.stdout.encoding or "utf-8"
    safe_text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
    print(safe_text)
