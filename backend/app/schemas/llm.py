from pydantic import BaseModel, ConfigDict, Field


class ProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    protocol: str = Field(default="openai", pattern="^(openai|anthropic)$")
    base_url: str = Field(min_length=1, max_length=200)
    api_key: str = Field(min_length=1, max_length=400)
    model: str = Field(min_length=1, max_length=64)
    vision_model: str | None = Field(default=None, max_length=64)
    is_default: bool = False


class ProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    protocol: str | None = Field(default=None, pattern="^(openai|anthropic)$")
    base_url: str | None = Field(default=None, min_length=1, max_length=200)
    api_key: str | None = Field(default=None, min_length=1, max_length=400)
    model: str | None = Field(default=None, min_length=1, max_length=64)
    vision_model: str | None = Field(default=None, max_length=64)
    enabled: bool | None = None


class ProviderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    protocol: str
    base_url: str
    key_masked: str = ""
    model: str
    vision_model: str | None
    is_default: bool
    enabled: bool


class ChatDebugIn(BaseModel):
    provider_id: int | None = None
    messages: list[dict] = Field(min_length=1)
    json_mode: bool = False
    max_tokens: int = Field(default=512, ge=16, le=8192)
