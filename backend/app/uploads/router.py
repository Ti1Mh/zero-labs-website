"""Upload management endpoints: presigning, confirmation, listing, and deletion."""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TeamContext, require
from app.core.database import get_db
from app.storage.base import StorageBackend
from app.storage.factory import get_storage
from app.subscriptions.dependencies import require_active_subscription
from app.uploads.schemas import (
    ConfirmUploadRequest,
    MediaFileListResponse,
    MediaFileOut,
    PresignUploadRequest,
    PresignUploadResponse,
)
from app.uploads.service import (
    confirm_upload,
    create_presigned_upload,
    delete_media_file,
    list_media_files,
)

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.post("/presign", response_model=PresignUploadResponse, status_code=status.HTTP_200_OK)
async def presign_upload_endpoint(
    payload: PresignUploadRequest,
    team: TeamContext = Depends(require("content:create")),
    _sub: TeamContext = Depends(require_active_subscription),
    storage: StorageBackend = Depends(get_storage),
) -> PresignUploadResponse:
    """Generate a presigned PUT URL for direct client upload to MinIO/S3."""
    return await create_presigned_upload(
        storage=storage,
        owner_id=team.owner.id,
        payload=payload,
    )


@router.post("/confirm", response_model=MediaFileOut, status_code=status.HTTP_201_CREATED)
async def confirm_upload_endpoint(
    payload: ConfirmUploadRequest,
    team: TeamContext = Depends(require("content:create")),
    storage: StorageBackend = Depends(get_storage),
    db: AsyncSession = Depends(get_db),
) -> MediaFileOut:
    """Confirm that direct upload completed and persist media file metadata."""
    media = await confirm_upload(
        db=db,
        storage=storage,
        owner_id=team.owner.id,
        payload=payload,
    )
    await db.commit()
    return MediaFileOut.model_validate(media)


@router.get("", response_model=MediaFileListResponse)
async def list_uploads_endpoint(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    team: TeamContext = Depends(require("content:view")),
    db: AsyncSession = Depends(get_db),
) -> MediaFileListResponse:
    """List uploaded media files for the team."""
    items, total = await list_media_files(
        db=db,
        owner_id=team.owner.id,
        limit=limit,
        offset=offset,
    )
    return MediaFileListResponse(
        items=[MediaFileOut.model_validate(item) for item in items],
        total=total,
    )


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_upload_endpoint(
    file_id: int,
    team: TeamContext = Depends(require("content:create")),
    storage: StorageBackend = Depends(get_storage),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a media file from database and object storage."""
    await delete_media_file(
        db=db,
        storage=storage,
        owner_id=team.owner.id,
        file_id=file_id,
    )
    await db.commit()
