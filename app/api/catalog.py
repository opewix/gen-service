from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import GenerationModel, ModelLeg
from app.schemas import LegOut, ModelOut

router = APIRouter(tags=["catalog"])


@router.get("/models", response_model=list[ModelOut])
async def list_models(session: AsyncSession = Depends(get_session)) -> list[ModelOut]:
    legs: dict[int, list[LegOut]] = defaultdict(list)
    for leg in await session.scalars(select(ModelLeg).order_by(ModelLeg.model_id, ModelLeg.priority)):
        legs[leg.model_id].append(
            LegOut(
                provider=leg.provider,
                priority=leg.priority,
                max_ref_images=leg.max_ref_images,
                skip_on_moderation=leg.skip_on_moderation,
            )
        )
    models = await session.scalars(select(GenerationModel).order_by(GenerationModel.slug))
    return [ModelOut(slug=m.slug, price=m.price, legs=legs[m.id]) for m in models]
