from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .common import MAX_TEXT


class SpeechInput(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)

    @field_validator('text')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('text must not be blank')
        return value.strip()


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role: Literal['user', 'assistant']
    content: str = Field(min_length=1, max_length=12000)

    @field_validator('content')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('content must not be blank')
        return value.strip()


class ChatInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=4000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)

    @field_validator('text')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('text must not be blank')
        return value.strip()

    @model_validator(mode='after')
    def bounded_history(self):
        if len(self.history) % 2 or any(
            item.role != ('user' if i % 2 == 0 else 'assistant')
            for i, item in enumerate(self.history)
        ):
            raise ValueError('history must contain complete user/assistant pairs')
        if len(self.text) + sum(len(item.content) for item in self.history) > 32000:
            raise ValueError('conversation is too long')
        return self


class ChatOutput(BaseModel):
    text: str
    model: str
    elapsed_seconds: float
