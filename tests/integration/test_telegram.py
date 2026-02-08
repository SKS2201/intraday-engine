from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from src.notifications.telegram import TelegramNotifier, split_message_chunks


def test_telegram_payload_correctness(mocker):
    notifier = TelegramNotifier(bot_token="abc", chat_id="123")
    mocked = Mock()
    mocked.status_code = 200
    mocked.text = "ok"
    post = mocker.patch("src.notifications.telegram.requests.post", return_value=mocked)
    notifier.send("hello world", dry_run=False, parse_mode="HTML")
    _, kwargs = post.call_args
    assert kwargs["json"]["chat_id"] == "123"
    assert kwargs["json"]["text"] == "hello world"
    assert kwargs["json"]["parse_mode"] == "HTML"


def test_telegram_dry_run_prints(capsys):
    notifier = TelegramNotifier(bot_token="", chat_id="")
    notifier.send("dry run message", dry_run=True)
    out = capsys.readouterr().out
    assert "dry run message" in out


def test_chunked_send_splits_large_text(mocker):
    notifier = TelegramNotifier(bot_token="abc", chat_id="123")
    mocked = Mock()
    mocked.status_code = 200
    mocked.text = "ok"
    post = mocker.patch("src.notifications.telegram.requests.post", return_value=mocked)
    notifier.send_chunked("A" * 120, max_chars=50, dry_run=False, parse_mode="HTML")
    assert post.call_count == 3


def test_send_document_payload(mocker, tmp_path: Path):
    path = tmp_path / "report.xlsx"
    path.write_bytes(b"fake")
    notifier = TelegramNotifier(bot_token="abc", chat_id="123")
    mocked = Mock()
    mocked.status_code = 200
    mocked.text = "ok"
    post = mocker.patch("src.notifications.telegram.requests.post", return_value=mocked)
    notifier.send_document(str(path), caption="report", dry_run=False)
    _, kwargs = post.call_args
    assert kwargs["data"]["chat_id"] == "123"
    assert kwargs["data"]["caption"] == "report"
    assert "document" in kwargs["files"]


def test_split_message_chunks_is_deterministic():
    text = "line1\nline2\nline3\nline4\nline5"
    chunks_a = split_message_chunks(text, max_chars=10)
    chunks_b = split_message_chunks(text, max_chars=10)
    assert chunks_a == chunks_b
    assert len(chunks_a) >= 2
