"""The example config sends transcription through the hub, not a host-local whisper port."""
import json
import unittest
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parents[2] / "config" / "config_example.json"


class WhisperUrlExampleTests(unittest.TestCase):
    def test_example_whisper_url_is_the_hub_endpoint(self) -> None:
        cfg = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        self.assertEqual(cfg["podcast"]["whisper_url"],
                         "http://127.0.0.1:8000/v1/audio/transcriptions")


if __name__ == "__main__":
    unittest.main()
