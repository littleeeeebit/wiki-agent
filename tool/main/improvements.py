"""Read experiment status only for the selected repository, without running models."""

from fastapi import APIRouter, HTTPException

import improvement

from .query import current_repo

router = APIRouter()


@router.get("/api/improvements")
def status() -> dict:
    try:
        return improvement.listing(current_repo())
    except (ValueError, OSError, KeyError) as exc:
        raise HTTPException(409, "자기개선 실험 상태를 확인할 수 없습니다") from exc
