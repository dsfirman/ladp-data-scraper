from pydantic import BaseModel


class Message(BaseModel):
    detail: str


class ScrapeRequest(BaseModel):
    url: str


class ScrapeResponse(BaseModel):
    url: str
    s3_key: str = ""
    s3_bucket: str = ""
    local_path: str = ""
    character_count: int


class ProcessRequest(BaseModel):
    batch_size: int | None = None
    data_filename: str | None = None
