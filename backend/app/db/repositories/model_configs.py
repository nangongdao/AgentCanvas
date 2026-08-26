"""Model-provider configuration persistence."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ModelConfig


class ModelConfigRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(self, row: ModelConfig) -> ModelConfig:
        existing = await self.session.get(ModelConfig, row.id)
        if existing is None:
            self.session.add(row)
            await self.session.flush()
            return row
        existing.name = row.name
        existing.provider = row.provider
        existing.model_name = row.model_name
        existing.base_url = row.base_url
        existing.api_key_encrypted = row.api_key_encrypted
        existing.params_json = row.params_json
        existing.capabilities_json = row.capabilities_json
        existing.prompt_price_per_million_usd = row.prompt_price_per_million_usd
        existing.completion_price_per_million_usd = row.completion_price_per_million_usd
        existing.pricing_version = row.pricing_version
        existing.kind = row.kind
        existing.is_default = row.is_default
        await self.session.flush()
        return existing

    async def get(self, model_id: str) -> ModelConfig | None:
        return await self.session.get(ModelConfig, model_id)

    async def get_many(self, model_ids: set[str]) -> dict[str, ModelConfig]:
        if not model_ids:
            return {}
        result = await self.session.execute(
            select(ModelConfig).where(ModelConfig.id.in_(model_ids))
        )
        return {row.id: row for row in result.scalars().all()}

    async def get_default(self, kind: str = "chat") -> ModelConfig | None:
        stmt = select(ModelConfig).where(ModelConfig.is_default.is_(True), ModelConfig.kind == kind)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def list(self) -> list[ModelConfig]:
        result = await self.session.execute(select(ModelConfig).order_by(ModelConfig.name))
        return list(result.scalars().all())

    async def delete(self, model_id: str) -> bool:
        row = await self.session.get(ModelConfig, model_id)
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.flush()
        return True

    async def unset_defaults(self, kind: str) -> None:
        """Clear the default flag for all rows of the given kind."""
        result = await self.session.execute(
            select(ModelConfig).where(ModelConfig.is_default.is_(True), ModelConfig.kind == kind)
        )
        for row in result.scalars().all():
            row.is_default = False
        await self.session.flush()
