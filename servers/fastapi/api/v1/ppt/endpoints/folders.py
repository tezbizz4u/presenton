import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.sql.folder import FolderModel
from models.sql.presentation import PresentationModel
from services.database import get_async_session
from utils.datetime_utils import get_current_utc_datetime


FOLDERS_ROUTER = APIRouter(prefix="/folders", tags=["Folders"])

MAX_FOLDER_NAME_LENGTH = 120
MAX_FOLDER_DEPTH = 8


class FolderResponse(BaseModel):
    id: uuid.UUID
    name: str
    parent_id: Optional[uuid.UUID] = None
    presentation_count: int = 0
    created_at: datetime
    updated_at: datetime


class FolderCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=MAX_FOLDER_NAME_LENGTH)
    parent_id: Optional[uuid.UUID] = None


class FolderUpdateRequest(BaseModel):
    name: Optional[str] = Field(
        default=None, min_length=1, max_length=MAX_FOLDER_NAME_LENGTH
    )
    # Send "parent_id": null to move a folder to the top level.
    parent_id: Optional[uuid.UUID] = None


class MovePresentationsRequest(BaseModel):
    presentation_ids: List[uuid.UUID] = Field(..., min_length=1, max_length=500)
    # null moves the presentations out of any folder.
    folder_id: Optional[uuid.UUID] = None


def _clean_name(name: str) -> str:
    cleaned = " ".join((name or "").split())
    if not cleaned:
        raise HTTPException(status_code=400, detail="Folder name cannot be empty")
    return cleaned[:MAX_FOLDER_NAME_LENGTH]


async def _all_folders(sql_session: AsyncSession) -> dict[uuid.UUID, FolderModel]:
    rows = (await sql_session.execute(select(FolderModel))).scalars().all()
    return {row.id: row for row in rows}


def _depth(folders: dict[uuid.UUID, FolderModel], folder_id: Optional[uuid.UUID]) -> int:
    depth = 0
    seen: set[uuid.UUID] = set()
    while folder_id is not None and folder_id in folders and folder_id not in seen:
        seen.add(folder_id)
        depth += 1
        folder_id = folders[folder_id].parent_id
    return depth


def _subtree_height(
    folders: dict[uuid.UUID, FolderModel], folder_id: uuid.UUID
) -> int:
    children: dict[Optional[uuid.UUID], list[uuid.UUID]] = {}
    for folder in folders.values():
        children.setdefault(folder.parent_id, []).append(folder.id)

    def height(node: uuid.UUID, guard: set[uuid.UUID]) -> int:
        if node in guard:
            return 0
        guard = guard | {node}
        return 1 + max((height(c, guard) for c in children.get(node, [])), default=0)

    return height(folder_id, set())


def _assert_unique_sibling(
    folders: dict[uuid.UUID, FolderModel],
    name: str,
    parent_id: Optional[uuid.UUID],
    ignore_id: Optional[uuid.UUID] = None,
) -> None:
    lowered = name.casefold()
    for folder in folders.values():
        if (
            folder.id != ignore_id
            and folder.parent_id == parent_id
            and folder.name.casefold() == lowered
        ):
            raise HTTPException(
                status_code=409,
                detail="A folder with this name already exists here",
            )


@FOLDERS_ROUTER.get("", response_model=List[FolderResponse])
async def list_folders(sql_session: AsyncSession = Depends(get_async_session)):
    folders = await _all_folders(sql_session)
    counts_rows = await sql_session.execute(
        select(PresentationModel.folder_id, func.count(PresentationModel.id))
        .where(PresentationModel.folder_id.is_not(None))
        .group_by(PresentationModel.folder_id)
    )
    counts = {folder_id: count for folder_id, count in counts_rows.all()}
    ordered = sorted(folders.values(), key=lambda f: f.name.casefold())
    return [
        FolderResponse(
            id=f.id,
            name=f.name,
            parent_id=f.parent_id,
            presentation_count=counts.get(f.id, 0),
            created_at=f.created_at,
            updated_at=f.updated_at,
        )
        for f in ordered
    ]


@FOLDERS_ROUTER.post("", response_model=FolderResponse)
async def create_folder(
    body: FolderCreateRequest,
    sql_session: AsyncSession = Depends(get_async_session),
):
    name = _clean_name(body.name)
    folders = await _all_folders(sql_session)
    if body.parent_id is not None and body.parent_id not in folders:
        raise HTTPException(status_code=404, detail="Parent folder not found")
    if _depth(folders, body.parent_id) + 1 > MAX_FOLDER_DEPTH:
        raise HTTPException(
            status_code=400,
            detail=f"Folders can be nested at most {MAX_FOLDER_DEPTH} levels deep",
        )
    _assert_unique_sibling(folders, name, body.parent_id)

    now = get_current_utc_datetime()
    folder = FolderModel(
        id=uuid.uuid4(),
        name=name,
        parent_id=body.parent_id,
        created_at=now,
        updated_at=now,
    )
    sql_session.add(folder)
    await sql_session.commit()
    return FolderResponse(
        id=folder.id,
        name=folder.name,
        parent_id=folder.parent_id,
        presentation_count=0,
        created_at=folder.created_at,
        updated_at=folder.updated_at,
    )


@FOLDERS_ROUTER.patch("/{folder_id}", response_model=FolderResponse)
async def update_folder(
    folder_id: uuid.UUID,
    body: FolderUpdateRequest,
    sql_session: AsyncSession = Depends(get_async_session),
):
    folders = await _all_folders(sql_session)
    folder = folders.get(folder_id)
    if folder is None:
        raise HTTPException(status_code=404, detail="Folder not found")

    new_name = _clean_name(body.name) if body.name is not None else folder.name
    new_parent = folder.parent_id
    if "parent_id" in body.model_fields_set:
        new_parent = body.parent_id
        if new_parent is not None and new_parent not in folders:
            raise HTTPException(status_code=404, detail="Parent folder not found")
        # A folder cannot be moved into itself or one of its own sub-folders.
        cursor, seen = new_parent, set()
        while cursor is not None and cursor not in seen:
            if cursor == folder_id:
                raise HTTPException(
                    status_code=400,
                    detail="A folder cannot be moved inside itself",
                )
            seen.add(cursor)
            cursor = folders[cursor].parent_id if cursor in folders else None
        if _depth(folders, new_parent) + _subtree_height(folders, folder_id) > MAX_FOLDER_DEPTH:
            raise HTTPException(
                status_code=400,
                detail=f"Folders can be nested at most {MAX_FOLDER_DEPTH} levels deep",
            )

    _assert_unique_sibling(folders, new_name, new_parent, ignore_id=folder_id)
    folder.name = new_name
    folder.parent_id = new_parent
    folder.updated_at = get_current_utc_datetime()
    sql_session.add(folder)
    await sql_session.commit()

    count = (
        await sql_session.execute(
            select(func.count(PresentationModel.id)).where(
                PresentationModel.folder_id == folder.id
            )
        )
    ).scalar_one()
    return FolderResponse(
        id=folder.id,
        name=folder.name,
        parent_id=folder.parent_id,
        presentation_count=count,
        created_at=folder.created_at,
        updated_at=folder.updated_at,
    )


@FOLDERS_ROUTER.delete("/{folder_id}")
async def delete_folder(
    folder_id: uuid.UUID,
    sql_session: AsyncSession = Depends(get_async_session),
):
    """Delete a folder. Presentations and sub-folders are never deleted: they are
    moved up to the deleted folder's parent (or to the top level)."""
    folders = await _all_folders(sql_session)
    folder = folders.get(folder_id)
    if folder is None:
        raise HTTPException(status_code=404, detail="Folder not found")

    await sql_session.execute(
        update(PresentationModel)
        .where(PresentationModel.folder_id == folder_id)
        .values(folder_id=folder.parent_id)
    )
    await sql_session.execute(
        update(FolderModel)
        .where(FolderModel.parent_id == folder_id)
        .values(parent_id=folder.parent_id)
    )
    await sql_session.delete(folder)
    await sql_session.commit()
    return {"success": True, "moved_to": folder.parent_id}


@FOLDERS_ROUTER.post("/move")
async def move_presentations(
    body: MovePresentationsRequest,
    sql_session: AsyncSession = Depends(get_async_session),
):
    if body.folder_id is not None:
        folders = await _all_folders(sql_session)
        if body.folder_id not in folders:
            raise HTTPException(status_code=404, detail="Folder not found")

    rows = (
        await sql_session.execute(
            select(PresentationModel).where(
                PresentationModel.id.in_(body.presentation_ids)
            )
        )
    ).scalars().all()
    found = {row.id for row in rows}
    missing = [str(pid) for pid in body.presentation_ids if pid not in found]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Presentations not found: {', '.join(missing[:5])}",
        )
    for presentation in rows:
        presentation.folder_id = body.folder_id
        sql_session.add(presentation)
    await sql_session.commit()
    return {"moved": len(rows), "folder_id": body.folder_id}
