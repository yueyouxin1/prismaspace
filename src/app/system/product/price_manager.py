# src/app/system/product/price_manager.py

from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Product, Price
from app.dao.product.product_dao import ProductDao
from app.dao.product.price_dao import PriceDao
from app.schemas.product.product_schemas import PriceCreate
from app.services.exceptions import NotFoundError

class PriceManager:
    """[System Layer] Manages the core business logic for Prices."""
    def __init__(self, db: AsyncSession):
        self.db = db
        self.price_dao = PriceDao(db)
        self.product_dao = ProductDao(db)

    async def create_price_for_product(self, product_id: int, price_data: PriceCreate) -> Price:
        """Creates a new Price and associates it with a Product."""
        # The service layer is responsible for ensuring the product exists.
        new_price = Price(
            **price_data.model_dump(),
            product_id=product_id
        )
        return await self.price_dao.add(new_price)