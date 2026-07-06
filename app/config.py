from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "ladp-data-scraper"
    api_version: str = "v1"
    debug: bool = False
    allowed_hosts: list[str] = ["*"]

    s3_uri: str = "s3://ladp-output/ladp-data-scraper-output/poi-data"
    s3_region: str = "us-east-1"
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None

    data_dir: str = "D:/data"

    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_api_version: str = ""
    azure_openai_embedding_deployment: str = ""
    azure_openai_embedding_api_version: str = ""
    azure_openai_chat_deployment_name: str = ""
    azure_openai_embedding_api_key: str = ""
    tavily_api_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_public_key: str = ""
    langfuse_base_url: str = ""
    huggingfacehub_api_token: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
