from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class CompleteRequest(_message.Message):
    __slots__ = ("client_id", "request_id", "system_prompt", "user_prompt", "json_schema", "max_tokens")
    CLIENT_ID_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    SYSTEM_PROMPT_FIELD_NUMBER: _ClassVar[int]
    USER_PROMPT_FIELD_NUMBER: _ClassVar[int]
    JSON_SCHEMA_FIELD_NUMBER: _ClassVar[int]
    MAX_TOKENS_FIELD_NUMBER: _ClassVar[int]
    client_id: str
    request_id: str
    system_prompt: str
    user_prompt: str
    json_schema: str
    max_tokens: int
    def __init__(self, client_id: _Optional[str] = ..., request_id: _Optional[str] = ..., system_prompt: _Optional[str] = ..., user_prompt: _Optional[str] = ..., json_schema: _Optional[str] = ..., max_tokens: _Optional[int] = ...) -> None: ...

class CompleteResponse(_message.Message):
    __slots__ = ("text", "model", "stop_reason", "input_tokens", "output_tokens", "attempts")
    TEXT_FIELD_NUMBER: _ClassVar[int]
    MODEL_FIELD_NUMBER: _ClassVar[int]
    STOP_REASON_FIELD_NUMBER: _ClassVar[int]
    INPUT_TOKENS_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_TOKENS_FIELD_NUMBER: _ClassVar[int]
    ATTEMPTS_FIELD_NUMBER: _ClassVar[int]
    text: str
    model: str
    stop_reason: str
    input_tokens: int
    output_tokens: int
    attempts: int
    def __init__(self, text: _Optional[str] = ..., model: _Optional[str] = ..., stop_reason: _Optional[str] = ..., input_tokens: _Optional[int] = ..., output_tokens: _Optional[int] = ..., attempts: _Optional[int] = ...) -> None: ...
