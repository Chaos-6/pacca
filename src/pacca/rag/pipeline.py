"""
RAG (Retrieval-Augmented Generation) pipeline for clinical guidelines.

Provides semantic search over clinical guidelines using ChromaDB for vector
storage. Embeddings are produced by the embedding function attached to the
ChromaDB collection (the default all-MiniLM model unless one is injected).

Collection ownership
--------------------
This module does NOT name a collection of its own. The production caller
(`pacca.integrations.vector_store.GuidelineRetriever`) injects its already-open
`nccn_guidelines` collection, so the pipeline reads exactly the governed
collection the scope guard checks and the seeder writes.

Historically `GuidelineVectorStore` defaulted to a `clinical_guidelines`
collection that nothing else ever wrote to or governed. That default is gone:
callers either inject a collection or name one explicitly.
"""

from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

from pacca.config import get_logger, get_settings
from pacca.models import ClinicalGuideline, GuidelineChunk, GuidelineSearchResult

logger = get_logger(__name__)

# Chunking defaults. Exposed as module constants so tests and callers assert
# against one definition rather than repeating literals.
DEFAULT_CHUNK_SIZE = 1000
DEFAULT_CHUNK_OVERLAP = 200


def specialty_key(specialty: str) -> str:
    """Metadata flag key marking a chunk as applying to one specialty."""
    return f"specialty_{specialty.strip().lower()}"


def category_key(category: str) -> str:
    """Metadata flag key marking a chunk as applying to one treatment category."""
    return f"category_{category.strip().lower()}"


def _collection_space(collection: Any) -> str:
    """
    Report the distance space a ChromaDB collection was created with.

    Needed because the distance->similarity conversion is only correct for the
    space actually in use. `configuration_json` reflects the real space
    (defaulting to "l2"); `metadata` only carries it when it was set explicitly.
    """
    config = getattr(collection, "configuration_json", None) or {}
    hnsw = config.get("hnsw") or {}
    space = hnsw.get("space")
    if not space:
        metadata = getattr(collection, "metadata", None) or {}
        space = metadata.get("hnsw:space")
    return str(space or "l2").lower()


def similarity_from_distance(distance: float, space: str) -> float:
    """
    Convert a ChromaDB distance into a 0..1 similarity score.

    Each space needs its own conversion:
      cosine — Chroma returns 1 - cos_sim in [0, 2]; similarity is 1 - distance.
      ip     — inner product; Chroma returns 1 - ip, so similarity is 1 - distance.
      l2     — squared euclidean in [0, inf); 1 / (1 + d) is monotonic in [0, 1].

    The previous implementation applied the l2 formula unconditionally while the
    docstrings claimed cosine scoring, so a cosine collection was scored wrong.
    """
    if space in ("cosine", "ip"):
        return max(0.0, min(1.0, 1.0 - distance))
    return 1.0 / (1.0 + distance)


class GuidelineVectorStore:
    """
    Vector store for clinical guidelines using ChromaDB.

    Handles chunking, storage, and semantic retrieval of guideline content.

    Construct it one of two ways:
      - `collection=<chromadb collection>` — reuse a collection someone else
        owns (the production path; keeps one client, one embedding function,
        and one governed collection).
      - `collection_name=...` (+ optional `persist_directory`) — open a
        collection standalone, for seeding scripts and tests.
    """

    def __init__(
        self,
        collection: Any | None = None,
        collection_name: str | None = None,
        persist_directory: str | None = None,
        embedding_function: Any | None = None,
    ):
        """
        Initialize the vector store.

        Args:
            collection: An already-open ChromaDB collection to read/write.
            collection_name: Name of the collection to open (if `collection`
                is not supplied). Required in that case — there is deliberately
                no default name.
            persist_directory: Directory for persistent storage (None for in-memory)
            embedding_function: Embedding function for a self-opened collection.

        Raises:
            ValueError: if neither `collection` nor `collection_name` is given.
        """
        self.settings = get_settings()

        if collection is not None:
            self._client = None
            self._collection = collection
            self.collection_name = collection.name
        else:
            if not collection_name:
                raise ValueError(
                    "GuidelineVectorStore requires either an existing `collection` "
                    "or an explicit `collection_name`."
                )
            self.collection_name = collection_name
            if persist_directory:
                self._client = chromadb.PersistentClient(
                    path=persist_directory,
                    settings=ChromaSettings(anonymized_telemetry=False),
                )
            else:
                self._client = chromadb.Client(
                    settings=ChromaSettings(anonymized_telemetry=False),
                )
            create_kwargs: dict[str, Any] = {"name": collection_name}
            if embedding_function is not None:
                create_kwargs["embedding_function"] = embedding_function
            self._collection = self._client.get_or_create_collection(**create_kwargs)

        self.space = _collection_space(self._collection)

        logger.info(
            "vector_store_initialized",
            collection=self.collection_name,
            space=self.space,
            injected=collection is not None,
        )

    @property
    def count(self) -> int:
        """Get the number of documents in the collection."""
        return self._collection.count()

    def add_guideline_sync(
        self,
        guideline: ClinicalGuideline,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    ) -> list[str]:
        """
        Add a clinical guideline to the vector store.

        The guideline is chunked into smaller pieces for effective
        semantic retrieval.

        Args:
            guideline: The guideline to add
            chunk_size: Maximum characters per chunk
            chunk_overlap: Overlap between consecutive chunks

        Returns:
            List of chunk IDs created
        """
        chunks = self._chunk_text(
            guideline.full_text,
            chunk_size=chunk_size,
            overlap=chunk_overlap,
        )

        documents = []
        metadatas = []
        ids = []

        for i, chunk_text in enumerate(chunks):
            chunk_id = f"{guideline.guideline_id}_chunk_{i}"
            documents.append(chunk_text)
            metadata: dict[str, Any] = {
                "guideline_id": guideline.guideline_id,
                "guideline_name": guideline.name,
                "source": guideline.source,
                "version": guideline.version,
                "chunk_index": i,
                # Human-readable, for display and debugging. Not filterable:
                # ChromaDB metadata values are scalars with no substring operator.
                "specialties": ",".join(s.value for s in guideline.specialties),
                "treatment_categories": ",".join(c.value for c in guideline.treatment_categories),
                "effective_date": guideline.effective_date.isoformat(),
            }
            # One boolean flag per specialty/category, because a guideline can
            # belong to several and ChromaDB can only match scalars. The former
            # {"specialties": {"$contains": ...}} filter matched *nothing* — Chroma
            # accepts $contains on metadata but never satisfies it (it is a
            # where_document operator), and the unfiltered retry hid that.
            for specialty in guideline.specialties:
                metadata[specialty_key(specialty.value)] = True
            for category in guideline.treatment_categories:
                metadata[category_key(category.value)] = True
            metadatas.append(metadata)
            ids.append(chunk_id)

        # upsert, not add: re-seeding the same guideline updates in place rather
        # than raising on duplicate ids.
        self._collection.upsert(
            documents=documents,
            metadatas=metadatas,
            ids=ids,
        )

        logger.info(
            "guideline_added_to_vector_store",
            guideline_id=guideline.guideline_id,
            chunks_created=len(ids),
        )

        return ids

    async def add_guideline(
        self,
        guideline: ClinicalGuideline,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    ) -> list[str]:
        """Async wrapper over `add_guideline_sync` (ChromaDB itself is sync)."""
        return self.add_guideline_sync(guideline, chunk_size, chunk_overlap)

    def search_sync(
        self,
        query: str,
        n_results: int = 5,
        specialty_filter: str | None = None,
        treatment_category_filter: str | None = None,
    ) -> list[GuidelineSearchResult]:
        """
        Search for relevant guideline content.

        Args:
            query: The search query (clinical context)
            n_results: Maximum number of results
            specialty_filter: Filter by specialty
            treatment_category_filter: Filter by treatment category

        Returns:
            List of search results with relevance scores
        """
        # Match the per-value boolean flags written at ingest (see
        # add_guideline_sync) — ChromaDB has no substring operator for metadata.
        where_clause: dict[str, Any] | None = None
        if specialty_filter or treatment_category_filter:
            conditions: list[dict[str, Any]] = []
            if specialty_filter:
                conditions.append({specialty_key(specialty_filter): True})
            if treatment_category_filter:
                conditions.append({category_key(treatment_category_filter): True})

            where_clause = conditions[0] if len(conditions) == 1 else {"$and": conditions}

        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=n_results,
                where=where_clause,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            logger.error("vector_search_failed", error=str(e))
            return []

        search_results = []

        if results and results.get("ids") and results["ids"][0]:
            for i, chunk_id in enumerate(results["ids"][0]):
                metadata = results["metadatas"][0][i] if results.get("metadatas") else {}
                metadata = metadata or {}
                document = results["documents"][0][i] if results.get("documents") else ""
                distance = results["distances"][0][i] if results.get("distances") else 1.0

                chunk = GuidelineChunk(
                    chunk_id=chunk_id,
                    guideline_id=str(metadata.get("guideline_id", "")),
                    guideline_name=str(metadata.get("guideline_name", "")),
                    source=str(metadata.get("source", "")),
                    content=document or "",
                    chunk_index=int(metadata.get("chunk_index", 0)),
                )

                search_results.append(
                    GuidelineSearchResult(
                        chunk=chunk,
                        similarity_score=similarity_from_distance(distance, self.space),
                        rank=i + 1,
                    )
                )

        logger.info(
            "vector_search_completed",
            query_length=len(query),
            results_count=len(search_results),
            filtered=where_clause is not None,
        )

        return search_results

    async def search(
        self,
        query: str,
        n_results: int = 5,
        specialty_filter: str | None = None,
        treatment_category_filter: str | None = None,
    ) -> list[GuidelineSearchResult]:
        """Async wrapper over `search_sync` (ChromaDB itself is sync)."""
        return self.search_sync(
            query=query,
            n_results=n_results,
            specialty_filter=specialty_filter,
            treatment_category_filter=treatment_category_filter,
        )

    def delete_guideline_sync(self, guideline_id: str) -> int:
        """
        Delete a guideline and all its chunks from the vector store.

        Args:
            guideline_id: The guideline to delete

        Returns:
            Number of chunks deleted
        """
        results = self._collection.get(
            where={"guideline_id": guideline_id},
            include=[],
        )

        if not results["ids"]:
            return 0

        self._collection.delete(ids=results["ids"])

        logger.info(
            "guideline_deleted_from_vector_store",
            guideline_id=guideline_id,
            chunks_deleted=len(results["ids"]),
        )

        return len(results["ids"])

    async def delete_guideline(self, guideline_id: str) -> int:
        """Async wrapper over `delete_guideline_sync` (ChromaDB itself is sync)."""
        return self.delete_guideline_sync(guideline_id)

    def _chunk_text(
        self,
        text: str,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        overlap: int = DEFAULT_CHUNK_OVERLAP,
    ) -> list[str]:
        """
        Split text into overlapping chunks.

        Prefers sentence/line boundaries in the last 20% of a chunk so a chunk
        rarely ends mid-sentence.
        """
        if len(text) <= chunk_size:
            return [text]

        # Overlap must leave room to advance, or the loop below cannot make
        # forward progress and would spin forever on long input.
        overlap = max(0, min(overlap, chunk_size - 1))

        chunks = []
        start = 0

        while start < len(text):
            end = start + chunk_size

            if end < len(text):
                # Look for sentence endings within the last 20% of the chunk
                search_start = end - int(chunk_size * 0.2)
                last_period = text.rfind(". ", search_start, end)
                last_newline = text.rfind("\n", search_start, end)

                boundary = max(last_period, last_newline)
                if boundary > search_start:
                    end = boundary + 1

            chunks.append(text[start:end].strip())

            next_start = max(end - overlap, 0)
            # Guard against a boundary adjustment that would move `start`
            # backwards or leave it put.
            if next_start <= start:
                next_start = start + 1
            start = next_start

            # Everything past this point is already covered by the previous
            # chunk's tail.
            if start >= len(text) - overlap:
                break

        return chunks


class RAGPipeline:
    """
    RAG pipeline for clinical decision support.

    Turns the structured facts of a case into a retrieval query, runs it
    against the guideline vector store, and formats the hits as prompt context.
    """

    def __init__(
        self,
        vector_store: GuidelineVectorStore,
    ):
        """
        Initialize the RAG pipeline.

        Args:
            vector_store: The guideline vector store to retrieve from. Required —
                the pipeline does not open a collection of its own, so that the
                collection it reads is always the caller's governed one.
        """
        self.vector_store = vector_store
        self.settings = get_settings()

    def retrieve_relevant_guidelines_sync(
        self,
        diagnosis_code: str,
        diagnosis_description: str,
        treatment_code: str,
        treatment_description: str,
        treatment_category: str | None = None,
        clinical_context: str | None = None,
        n_results: int = 5,
    ) -> str:
        """
        Retrieve relevant guidelines for a clinical case.

        Args:
            diagnosis_code: ICD-10 diagnosis code
            diagnosis_description: Diagnosis description
            treatment_code: Treatment/procedure code
            treatment_description: Treatment description
            treatment_category: Category of treatment; when given, used as a
                metadata filter, with an unfiltered retry if it excludes
                everything. Pass None to skip filtering entirely.
            clinical_context: Additional clinical context
            n_results: Maximum guidelines to retrieve

        Returns:
            Formatted string of relevant guideline excerpts
        """
        query_parts = [
            f"Diagnosis: {diagnosis_code} {diagnosis_description}",
            f"Treatment: {treatment_code} {treatment_description}",
        ]
        if treatment_category:
            query_parts.append(f"Category: {treatment_category}")
        if clinical_context:
            query_parts.append(f"Context: {clinical_context}")

        query = "\n".join(query_parts)

        results = self.vector_store.search_sync(
            query=query,
            n_results=n_results,
            treatment_category_filter=treatment_category.lower() if treatment_category else None,
        )

        if not results and treatment_category:
            # The category filter matched nothing — retry unfiltered rather than
            # returning no guidelines at all.
            results = self.vector_store.search_sync(query=query, n_results=n_results)

        if not results:
            return "No specific guidelines found for this clinical scenario."

        formatted_results = []

        for result in results:
            chunk = result.chunk
            heading = chunk.guideline_name or chunk.guideline_id or "Guideline"
            source = f" ({chunk.source})" if chunk.source else ""
            formatted_results.append(
                f"### {heading}{source}\n"
                f"Relevance Score: {result.similarity_score:.2f}\n\n"
                f"{chunk.content}\n"
            )

        return "\n---\n".join(formatted_results)

    async def retrieve_relevant_guidelines(
        self,
        diagnosis_code: str,
        diagnosis_description: str,
        treatment_code: str,
        treatment_description: str,
        treatment_category: str | None = None,
        clinical_context: str | None = None,
        n_results: int = 5,
    ) -> str:
        """Async wrapper over `retrieve_relevant_guidelines_sync`."""
        return self.retrieve_relevant_guidelines_sync(
            diagnosis_code=diagnosis_code,
            diagnosis_description=diagnosis_description,
            treatment_code=treatment_code,
            treatment_description=treatment_description,
            treatment_category=treatment_category,
            clinical_context=clinical_context,
            n_results=n_results,
        )

    def add_sample_guidelines_sync(self) -> int:
        """
        Add the bundled sample clinical guidelines to the store.

        Returns:
            Number of guidelines added
        """
        from pacca.rag.sample_guidelines import SAMPLE_GUIDELINES

        count = 0
        for guideline in SAMPLE_GUIDELINES:
            self.vector_store.add_guideline_sync(guideline)
            count += 1

        logger.info("sample_guidelines_loaded", count=count)
        return count

    async def add_sample_guidelines(self) -> int:
        """Async wrapper over `add_sample_guidelines_sync`."""
        return self.add_sample_guidelines_sync()
