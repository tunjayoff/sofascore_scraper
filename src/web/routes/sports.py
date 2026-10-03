"""Desteklenen sporlar ve maç detay dilimleri: src/sports.py'deki kayıt defterinin salt okunur görünümü."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter
from pydantic import BaseModel

from src import sports

router = APIRouter(prefix="/api", tags=["api"])


class DetailSliceModel(BaseModel):
    key: str
    path: str
    required: bool
    default_enabled: bool


class SportModel(BaseModel):
    slug: str
    name: str
    i18n_key: str
    score_family: str
    slices: List[DetailSliceModel]


def _sport_model(spec: sports.SportSpec) -> SportModel:
    return SportModel(
        slug=spec.slug,
        name=spec.name,
        i18n_key=spec.i18n_key,
        score_family=spec.score_family,
        slices=[
            DetailSliceModel(key=s.key, path=s.path, required=s.counts_in(spec.slug),
                             default_enabled=s.default_enabled)
            for s in sports.DETAIL_SLICES
            if s.applies_to(spec.slug)
        ],
    )


@router.get("/sports", response_model=List[SportModel])
def get_sports() -> List[SportModel]:
    """
    Kayıtlı sporlar, kayıt sırasıyla. `slices`, o sporda geçerli her dilimdir (kapalı olanlar dahil;
    `default_enabled` hangisinin istendiğini söyler).
    """
    return [_sport_model(spec) for spec in sports.SPORTS]
