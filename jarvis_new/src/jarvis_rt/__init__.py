"""In-repo voice runtime replacing livekit-agents."""

from . import tools
from .chat_context import (
    AgentConfigUpdate,
    AgentHandoff,
    AudioContent,
    ChatContent,
    ChatContext,
    ChatItem,
    ChatMessage,
    ChatRole,
    FunctionCall,
    FunctionCallOutput,
    ImageContent,
)
from .speech_handle import SpeechHandle
from .tools import (
    FunctionTool,
    RawFunctionTool,
    RunContext,
    StopResponse,
    ToolError,
    ToolFlag,
    find_function_tools,
    function_tool,
    get_function_info,
    is_function_tool,
    is_raw_function_tool,
    tool_schema,
)

__all__ = [
    "AgentConfigUpdate",
    "AgentHandoff",
    "AudioContent",
    "ChatContent",
    "ChatContext",
    "ChatItem",
    "ChatMessage",
    "ChatRole",
    "FunctionCall",
    "FunctionCallOutput",
    "FunctionTool",
    "ImageContent",
    "RawFunctionTool",
    "RunContext",
    "SpeechHandle",
    "StopResponse",
    "ToolError",
    "ToolFlag",
    "find_function_tools",
    "function_tool",
    "get_function_info",
    "is_function_tool",
    "is_raw_function_tool",
    "tool_schema",
    "tools",
]

from .agent import Agent
from .session import AgentSession

__all__ += ["Agent", "AgentSession"]

from . import io, room_io  # noqa: E402
from .agent import NOT_GIVEN, is_given  # noqa: E402
from .events import (  # noqa: E402
    CloseEvent,
    CloseReason,
    FunctionToolsExecutedEvent,
    UserStateChangedEvent,
)
from .room_io import AudioInputOptions, RoomOptions, TextOutputOptions  # noqa: E402
from .worker import AgentServer, JobContext, get_job_context, run_app  # noqa: E402

__all__ += [
    "NOT_GIVEN",
    "AgentServer",
    "AudioInputOptions",
    "ChatContext",
    "CloseEvent",
    "CloseReason",
    "FunctionToolsExecutedEvent",
    "JobContext",
    "RoomOptions",
    "TextOutputOptions",
    "UserStateChangedEvent",
    "get_job_context",
    "io",
    "is_given",
    "room_io",
    "run_app",
]
