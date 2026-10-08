from __future__ import annotations

from sugarfree.fs.workspace import WorkspaceFileStore
from sugarfree.tools.calculator import CalculatorTool
from sugarfree.tools.lookup import LookupTool
from sugarfree.tools.read_file import ReadFileTool
from sugarfree.tools.registry import ToolRegistry
from sugarfree.tools.write_file import WriteFileTool


def default_tools(workspace: WorkspaceFileStore | None = None) -> ToolRegistry:
    """Tools shared by the first offline and live evaluation suites."""
    tools = [
        CalculatorTool(),
        LookupTool(
            {
                "apple_price": "7",
                "banana_price": "4",
                "tax_rate": "0.1",
            }
        ),
    ]
    if workspace is not None:
        tools.extend([ReadFileTool(workspace), WriteFileTool(workspace)])
    return ToolRegistry(tools)
