import asyncio
import logging
import traceback

from fastapi import APIRouter, Depends, HTTPException
from httpx import AsyncClient

from app.core.dependencies import get_http_client
from app.models.schemas import ProcessRequest, ScrapeRequest, ScrapeResponse
from app.services.agentic_rag import run_extraction
from app.services.scraper import scrape_website as scrape_and_save
from app.services.s3 import upload_text

logger = logging.getLogger(__name__)

router = APIRouter(tags=["scraper"])

SCRAPE_TIMEOUT = 120


@router.post("/scrape", response_model=ScrapeResponse)
async def scrape_website(
    body: ScrapeRequest,
    client: AsyncClient = Depends(get_http_client),
):
    try:
        logger.info("Scraping URL: %s", body.url)
        text, local_path, filename = await asyncio.wait_for(
            scrape_and_save(client, body.url), timeout=SCRAPE_TIMEOUT
        )
    except asyncio.TimeoutError:
        logger.error("Scrape timed out after %ds for %s", SCRAPE_TIMEOUT, body.url)
        raise HTTPException(status_code=504, detail="Scrape timed out")

    bucket, key = "", ""
    try:
        bucket, key = await upload_text(text, body.url)
    except Exception as e:
        logger.warning("S3 upload failed: %s", e)

    return ScrapeResponse(
        url=body.url,
        s3_key=key,
        s3_bucket=bucket,
        local_path=local_path,
        character_count=len(text),
    )


@router.post("/process")
async def process_extraction(body: ProcessRequest):
    try:
        logger.info("Starting extraction with batch_size=%s", body.batch_size)
        result = await asyncio.to_thread(run_extraction, body.batch_size)
        return result
    except Exception as e:
        logger.error("Extraction failed: %s\n%s", e, traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))
