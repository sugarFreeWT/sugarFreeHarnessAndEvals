"""Public API for the sugar-free agent harness."""

from sugarfree.core.agent import Agent, AgentConfig
from sugarfree.core.result import AgentResult, TerminationReason
from sugarfree.plugins import Plugin, PluginContext, PluginManager
from sugarfree.security import AllowlistPolicy, ApprovalPolicy
from sugarfree.fs import WorkspaceFileStore, WorkspaceSnapshot

__all__ = [
    "Agent",
    "AgentConfig",
    "AgentResult",
    "AllowlistPolicy",
    "ApprovalPolicy",
    "WorkspaceFileStore",
    "WorkspaceSnapshot",
    "Plugin",
    "PluginContext",
    "PluginManager",
    "TerminationReason",
]
