# engine/vector/milvus_engine.py

import logging
import re
import json
import asyncio
from typing import List, Dict, Any

from pymilvus import (
    MilvusClient,
    CollectionSchema,
    FieldSchema,
    DataType,
    exceptions,
    utility
)

from .base import VectorEngineService, VectorChunk, SearchResult, VectorEngineError

# 使用标准日志记录器
logger = logging.getLogger(__name__)

# 定义特定于此引擎的异常
class MilvusEngineError(VectorEngineError):
    """Base exception for all Milvus engine errors, abstracting pymilvus specifics."""
    pass

class MilvusEngine(VectorEngineService):
    """
    A stateless, robust, and production-ready implementation of VectorEngineService using Milvus.

    This class is designed to be a lightweight, request-scoped service that operates on a
    globally managed, long-lived MilvusClient instance. It embodies the following principles:
    - **Stateless**: It does not hold any in-memory state about collections (e.g., load status).
    - **Dependency Injected**: It receives a connected MilvusClient instance upon initialization.
    - **Error Resilient**: Implements a retry mechanism for transient "collection not loaded" errors.
    - **Schema-Driven**: Uses a consistent, predefined schema for all managed collections.
    """
    PK_FIELD = "pk"
    VECTOR_FIELD = "vector"
    PAYLOAD_FIELD = "payload"
    METRIC_TYPE = "IP"

    def __init__(self, client: MilvusClient):
        """
        Initializes the engine with a pre-configured MilvusClient.

        Args:
            client: A connected instance of pymilvus.MilvusClient.
        """
        self.client = client

    def _validate_collection_name(self, name: str):
        """
        Validates the collection name against Milvus naming conventions to prevent errors.
        """
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]{0,254}$", name):
            raise ValueError(f"Invalid collection name: '{name}'. Name must start with a letter or underscore, "
                             "followed by letters, numbers, or underscores, and be at most 255 characters long.")

    def _prepare_data_for_milvus(self, chunks: List[VectorChunk]) -> List[Dict[str, Any]]:
        """[新增] 内部辅助函数，用于数据转换，避免代码重复。"""
        return [
            {
                self.PK_FIELD: c.id,
                self.VECTOR_FIELD: c.vector,
                self.PAYLOAD_FIELD: c.payload
            }
            for c in chunks
        ]

    def _build_filter_expr(self, pks: List[str] = None, filter_expr: str = None) -> str:
        """[内部辅助] 安全地构建最终的 filter 表达式。"""
        if pks and filter_expr:
            raise ValueError("Cannot provide both 'pks' and 'filter_expr'.")
        
        if pks:
            if not all(isinstance(pk, (str, int)) for pk in pks):
                raise TypeError("All primary keys in 'pks' must be strings or integers.")
            # 正确格式化 for 'in' operator
            formatted_pks = json.dumps(pks)
            return f'{self.PK_FIELD} in {formatted_pks}'
        
        if filter_expr:
            # [未来] 在这里可以添加对 filter_expr 的安全校验
            return filter_expr
            
        raise ValueError("Must provide either 'pks' or 'filter_expr'.")

    async def create_collection(self, name: str, vector_size: int):
        """
        Creates a new collection in Milvus if it doesn't exist, including schema, index, and loading.
        This operation is idempotent.
        """
        self._validate_collection_name(name)
        try:
            if self.client.has_collection(collection_name=name):
                logger.info(f"Collection '{name}' already exists. Ensuring it is loaded.")
                # Even if it exists, a load command is idempotent and ensures it's ready.
                self.client.load_collection(collection_name=name)
                return

            logger.info(f"Collection '{name}' does not exist. Creating...")
            
            # Define a consistent schema for all our collections
            fields = [
                FieldSchema(name=self.PK_FIELD, dtype=DataType.VARCHAR, is_primary=True, max_length=64,
                            description="Unique identifier for the vector chunk"),
                FieldSchema(name=self.VECTOR_FIELD, dtype=DataType.FLOAT_VECTOR, dim=vector_size,
                            description="The vector embedding"),
                FieldSchema(name=self.PAYLOAD_FIELD, dtype=DataType.JSON,
                            description="Business-specific metadata")
            ]
            schema = CollectionSchema(fields=fields, description=f"PrismaSpace managed collection: {name}")
            
            self.client.create_collection(collection_name=name, schema=schema)
            logger.info(f"Collection '{name}' created with schema. Proceeding to create index...")

            # Prepare and create the index
            index_params = self.client.prepare_index_params()
            index_params.add_index(
                field_name=self.VECTOR_FIELD,
                index_type="IVF_FLAT",  # A balanced choice for general purpose use
                metric_type=self.METRIC_TYPE,
                params={"nlist": 1024} # A common starting point for nlist
            )
            self.client.create_index(collection_name=name, index_params=index_params)
            logger.info(f"Index created for collection '{name}'. Loading collection into memory...")

            # Load the collection into memory to make it searchable
            self.client.load_collection(collection_name=name)
            logger.info(f"Collection '{name}' created, indexed, and loaded successfully.")

        except exceptions.MilvusException as e:
            logger.error(f"A Milvus error occurred while creating collection '{name}': {e}")
            raise VectorEngineError(f"Failed to create collection '{name}': {e}")
        except Exception as e:
            logger.error(f"An unexpected error occurred during collection creation '{name}': {e}")
            raise VectorEngineError(f"An unexpected error occurred: {e}")

    async def delete_collection(self, name: str):
        """
        Drops a collection from Milvus. This operation is idempotent.
        It's good practice to release the collection from memory before dropping.
        """
        self._validate_collection_name(name)
        try:
            if self.client.has_collection(collection_name=name):
                # Release is not strictly required before drop, but it's a clean pattern
                self.client.release_collection(collection_name=name)
                self.client.drop_collection(collection_name=name)
                logger.info(f"Collection '{name}' released and dropped successfully.")
            else:
                logger.info(f"Collection '{name}' does not exist. Skipping deletion.")
        except exceptions.MilvusException as e:
            logger.error(f"A Milvus error occurred while dropping collection '{name}': {e}")
            raise VectorEngineError(f"Failed to drop collection '{name}': {e}")

    async def list_collections(self) -> List[str]:
        try:
            return self.client.list_collections()
        except Exception as e:
            logger.error(f"Failed to list collections: {e}")
            raise VectorEngineError(f"List collections failed: {e}")
            
    async def insert(self, collection_name: str, chunks: List[VectorChunk]) -> int:
        """For bulk ingestion of new data. More performant for large scale."""
        if not chunks:
            return 0
        self._validate_collection_name(collection_name)
        try:
            data_to_insert = self._prepare_data_for_milvus(chunks)
            res = self.client.insert(collection_name=collection_name, data=data_to_insert)
            logger.info(f"Inserted {res.get('insert_count')} chunks into '{collection_name}'.")
            return res.get('insert_count')
        except exceptions.MilvusException as e:
            # 特别是主键冲突的异常
            logger.error(f"A Milvus error occurred while inserting chunks into '{collection_name}': {e}")
            raise VectorEngineError(f"Failed to insert chunks into '{collection_name}': {e}")

    async def upsert(self, collection_name: str, chunks: List[VectorChunk]) -> int:
        """For updating existing data or inserting small amounts of data."""
        if not chunks:
            return 0
        self._validate_collection_name(collection_name)
        try:
            data_to_upsert = self._prepare_data_for_milvus(chunks)
            res = self.client.upsert(collection_name=collection_name, data=data_to_upsert)
            logger.info(f"Upserted {res.get('upsert_count')} chunks into '{collection_name}'.")
            return res.get('upsert_count')
        except exceptions.MilvusException as e:
            logger.error(f"A Milvus error occurred while upserting chunks into '{collection_name}': {e}")
            raise VectorEngineError(f"Failed to upsert chunks into '{collection_name}': {e}")

    async def delete(self, collection_name: str, pks: List[str] = None, filter_expr: str = None) -> int:
        # [NEW] Implementation from above
        self._validate_collection_name(collection_name)
        expr = self._build_filter_expr(pks=pks, filter_expr=filter_expr)
        try:
            res = self.client.delete(collection_name=collection_name, filter=expr)
            logger.info(f"Delete on '{collection_name}' successful.")
            return res.get('delete_count')
        except exceptions.MilvusException as e:
            raise VectorEngineError(f"Failed to delete from '{collection_name}': {e}")

    async def query(self, collection_name: str, pks: List[str] = None, filter_expr: str = None, output_fields: List[str] = None) -> List[VectorChunk]:
        # [NEW] Implementation from above
        self._validate_collection_name(collection_name)
        expr = self._build_filter_expr(pks=pks, filter_expr=filter_expr)
        final_output_fields = output_fields or [self.PK_FIELD, self.VECTOR_FIELD, self.PAYLOAD_FIELD]
        try:
            results = self.client.query(collection_name=collection_name, filter=expr, output_fields=final_output_fields)
            return [
                VectorChunk(
                    id=res.get(self.PK_FIELD),
                    vector=res.get(self.VECTOR_FIELD),
                    payload=res.get(self.PAYLOAD_FIELD)
                ) for res in results
            ]
        except exceptions.MilvusException as e:
            raise VectorEngineError(f"Failed to query chunks in '{collection_name}': {e}")
            
    async def search(
        self,
        collection_name: str,
        query_vector: List[float],
        top_k: int,
        filter_expr: str = None,
        max_retries: int = 3
    ) -> List[SearchResult]:
        """
        Performs a vector similarity search with optional metadata filtering.
        Includes a retry mechanism for "collection not loaded" errors.
        """
        self._validate_collection_name(collection_name)
        
        for attempt in range(max_retries + 1):
            try:
                # The search parameters are now part of the search call itself
                search_params = {
                    "metric_type": self.METRIC_TYPE, # Assuming IP, could be dynamically fetched if needed
                    "params": {"nprobe": 10},
                }

                results = self.client.search(
                    collection_name=collection_name,
                    data=[query_vector],
                    anns_field=self.VECTOR_FIELD,
                    limit=top_k,
                    filter=filter_expr,
                    search_params=search_params,
                    # Bounded consistency is a good default for RAG, ensuring reads see recent writes.
                    consistency_level="Bounded",
                    output_fields=[self.PK_FIELD, self.PAYLOAD_FIELD]
                )
                
                # The new MilvusClient returns a list of lists of Hit objects
                hits = results[0]
                search_results = []
                for hit in hits:
                    # For IP metric, distance is the inner product (higher is better),
                    # so we can use it directly as the score.
                    # For L2, hit.distance is Euclidean distance (lower is better).
                    # We will assume IP and directly use the score.
                    # A more advanced version could check metric_type and convert score if L2.
                    search_results.append(SearchResult(
                        id=hit.entity.get(self.PK_FIELD),
                        score=hit.distance,
                        payload=hit.entity.get(self.PAYLOAD_FIELD)
                    ))
                return search_results

            except exceptions.MilvusException as e:
                # This specific error message indicates the collection is in the cluster but not in memory
                if "collection not loaded" in str(e):
                    if attempt < max_retries:
                        logger.warning(f"Attempt {attempt + 1}: Collection '{collection_name}' not loaded. "
                                       f"Attempting to load and retry search...")
                        try:
                            self.client.load_collection(collection_name=collection_name)
                            await asyncio.sleep(0.2) # Small delay to allow loading to propagate
                            continue # Retry the search
                        except exceptions.MilvusException as load_e:
                            logger.error(f"Failed to explicitly load collection '{collection_name}' during retry: {load_e}")
                            # If loading fails, there's no point in retrying further
                            raise VectorEngineError(f"Failed to load and search collection '{collection_name}': {load_e}")
                
                # For all other Milvus errors, or if retries are exhausted, raise the wrapped error
                logger.error(f"A Milvus error occurred while searching in '{collection_name}' with expr='{filter_expr}': {e}")
                raise VectorEngineError(f"Failed to search in collection '{collection_name}': {e}")
        
        # This line should theoretically not be reached, but as a fallback:
        raise VectorEngineError(f"Search in '{collection_name}' failed after {max_retries + 1} attempts.")