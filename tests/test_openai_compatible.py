from __future__ import annotations

import io
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from sugarfree.core.messages import Message, ModelRequest
from sugarfree.models.base import ModelError
from sugarfree.models.openai_compatible import OpenAICompatibleModel


class OpenAICompatibleModelTests(unittest.TestCase):
    def test_http_error_redacts_provider_rendered_api_key(self) -> None:
        response = b'{"error":{"message":"Your api key: ****e8ac is invalid"}}'
        error = HTTPError(
            "https://api.example.com/chat/completions",
            401,
            "Unauthorized",
            {},
            io.BytesIO(response),
        )
        model = OpenAICompatibleModel(api_key="sk-1234567890abcdef", model="demo")
        request = ModelRequest(messages=(Message(role="user", content="hello"),), tools=())

        with patch("sugarfree.models.openai_compatible.urlopen", side_effect=error):
            with self.assertRaises(ModelError) as raised:
                model._complete_sync(request)

        message = str(raised.exception)
        self.assertIn("model HTTP 401", message)
        self.assertIn("api key: [REDACTED]", message)
        self.assertNotIn("e8ac", message)
        self.assertNotIn("sk-1234567890abcdef", message)


if __name__ == "__main__":
    unittest.main()
