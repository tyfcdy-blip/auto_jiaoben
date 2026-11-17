"""节点文件控制协议；均拒绝未声明字段。"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class FileOptions(Strict):
    inputs: dict[str, str | list[str]] = Field(default_factory=dict, max_length=100)
    output_dir: str | None = Field(default=None, min_length=1, max_length=2000)


class FileOptionsUpdate(FileOptions):
    base_config_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class OutputGrant(Strict):
    project_id: str
    output_roots: list[str] = Field(default_factory=list, max_length=30)


class FileConfiguration(Strict):
    base_revision: int = Field(ge=0)
    output_roots: list[str] = Field(default_factory=list, max_length=30)
    grants: list[OutputGrant] = Field(default_factory=list, max_length=500)


class BrowseCreate(Strict):
    project_id: str
    purpose: Literal["input", "output"]
    path: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=200, ge=1, le=200)
    offset: int = Field(default=0, ge=0, le=10000)


class FileEntry(Strict):
    name: str = Field(min_length=1, max_length=180)
    path: str = Field(min_length=1, max_length=2000)
    kind: Literal["file", "directory"]
    size: int | None = Field(default=None, ge=0)


class BrowseCompletion(Strict):
    config_revision: int = Field(ge=1)
    output_config_revision: int = Field(default=0, ge=0)
    entries: list[FileEntry] = Field(default_factory=list, max_length=200)
    truncated: bool = False
    next_offset: int | None = Field(default=None, ge=0, le=10000)
    error: str = Field(default="", max_length=1000)


class SavedFile(Strict):
    name: str = Field(min_length=1, max_length=180)
    path: str = Field(min_length=1, max_length=2000)


class SaveCompletion(Strict):
    attempt_id: str
    retry_number: int = Field(default=0, ge=0, le=10)
    status: Literal["succeeded", "failed"]
    files: list[SavedFile] = Field(default_factory=list, max_length=100)
    error: str = Field(default="", max_length=1000)
