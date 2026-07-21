"""
Router: embedding triggering.

Kicks off one embedding pass as a background task and returns
immediately — embedding + upserting a backlog of articles can take
several minutes (each article is chunked, embedded, and upserted into
Pinecone one at a time), far longer than any HTTP request should
reasonably block for. Progress is visible by reloading the article list:
articles show their embedding_status ("Not Embedded" -> "Embedded"/
"Failed"). There's no separate "job status" endpoint — same pattern as
/articles/extract.
"""

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from app.services import embedder

router = APIRouter()


class EmbeddingTriggerResponse(BaseModel):
    status: str  # "started" | "already_running"
    message: str


@router.post("/articles/embed", response_model=EmbeddingTriggerResponse)
def trigger_embedding(background_tasks: BackgroundTasks):
    """Starts an embedding run in the background. Safe to call
    repeatedly — if one's already running, this reports that instead of
    starting a second overlapping run."""
    if embedder.is_embedding_running():
        return EmbeddingTriggerResponse(
            status="already_running",
            message="An embedding run is already in progress. Reload in a bit to check progress.",
        )

    background_tasks.add_task(embedder.run_embedding_job)
    return EmbeddingTriggerResponse(
        status="started",
        message=(
            "Embedding started in the background. Reload the page in a "
            "minute or two to see progress — articles will show as "
            "'Not Embedded' until they settle into 'Embedded'/'Failed'."
        ),
    )
