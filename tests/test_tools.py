from __future__ import annotations

import unittest

from sugarfree.tools.base import ToolExecutionError
from sugarfree.tools.calculator import CalculatorTool


class CalculatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_arithmetic(self) -> None:
        result = await CalculatorTool().execute({"expression": "(7 + 3) * 4"})
        self.assertEqual(result.content, "40")

    async def test_code_execution_is_rejected(self) -> None:
        with self.assertRaises(ToolExecutionError):
            await CalculatorTool().execute({"expression": "__import__('os').getcwd()"})

    async def test_large_exponent_is_rejected(self) -> None:
        with self.assertRaises(ToolExecutionError):
            await CalculatorTool().execute({"expression": "2 ** 1000"})


if __name__ == "__main__":
    unittest.main()

