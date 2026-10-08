from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sugarfree.evals.live_cli import _safe_url, run_cli


class LiveCliTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_key_fails_before_network(self) -> None:
        root = Path(__file__).resolve().parents[1]
        args = type(
            "Args",
            (),
            {
                "suite": root / "evals" / "deepseek-smoke.json",
                "output_dir": None,
                "model": "deepseek-chat",
                "base_url": "https://api.deepseek.com",
                "temperature": 0.0,
                "timeout_seconds": 1.0,
            },
        )()
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "DEEPSEEK_API_KEY"):
                await run_cli(args)

    def test_base_url_metadata_is_sanitized(self) -> None:
        self.assertEqual(
            _safe_url("https://user:secret@example.com/v1?api_key=hidden"),
            "https://example.com/v1",
        )


if __name__ == "__main__":
    unittest.main()

