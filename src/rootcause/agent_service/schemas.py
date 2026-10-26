"""Request and response bodies of the agent service.

The run body mirrors what the LangGraph SDK's ``FetchStreamTransport`` posts:
``{input, context, command, config, streamSubgraphs}``. Only ``input``,
``context`` and ``command`` are read; ``config`` and every other extra field are
ignored, so a client can never choose the thread, the callbacks or any other run
configuration. ``input`` may carry only ``messages`` and ``command`` only
``resume``: a client cannot write other state channels or jump to a node.
"""

from typing import Any, Final, Self

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

MAX_INPUT_MESSAGES: Final = 50
CONNECTION_ID_PATTERN: Final = r"^[A-Za-z0-9._\-]{1,128}$"


class RunInput(BaseModel):
    """New graph input: the messages to append to the thread."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    messages: list[dict[str, Any]] = Field(max_length=MAX_INPUT_MESSAGES)


class RunContext(BaseModel):
    """Per-run context; ``connection_id`` is an opaque id, never a DSN."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    connection_id: str | None = Field(default=None, pattern=CONNECTION_ID_PATTERN)


class RunCommand(BaseModel):
    """Resume a pending interrupt with ``resume`` (the approval decisions)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    resume: Any


class RunRequest(BaseModel):
    """Body of ``POST /threads/{thread_id}/runs/stream``; exactly one of input or command."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    input: RunInput | None = None
    context: RunContext | None = None
    command: RunCommand | None = None
    # The SDK sends ``streamSubgraphs``; subagent events are streamed unless it is false.
    stream_subgraphs: bool = Field(
        default=True, validation_alias=AliasChoices("streamSubgraphs", "stream_subgraphs")
    )

    @model_validator(mode="after")
    def _one_of_input_or_command(self) -> Self:
        if (self.input is None) == (self.command is None):
            raise ValueError("send exactly one of 'input' or 'command'")
        return self


class ThreadCreated(BaseModel):
    """Response of ``POST /threads``."""

    model_config = ConfigDict(frozen=True)

    thread_id: str


class ThreadSearch(BaseModel):
    """Body of ``POST /threads/search`` (mirrors LangGraph's ``threads.search``)."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
