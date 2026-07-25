"""
GuidelineRetriever — production RAG interface for PACCA.

This module provides the primary interface for clinical guideline retrieval.
It wraps RAGPipeline (the richer implementation in `pacca.rag.pipeline`) while
maintaining backward-compatible method names so existing routes and agents
continue to work without changes. This is the Adapter pattern: GuidelineRetriever
is the stable contract; RAGPipeline is the implementation behind it.

Architecture history (important context for reviewers):
  The original implementation in this file was a direct ChromaDB wrapper —
  functional but without chunking, metadata filtering, or relevance scoring.

  rag/pipeline.py was written with those features. A v2.2 change claimed to
  delegate to it, but the delegation never actually ran: `pacca.rag.pipeline`
  could not be imported (a bad `from uuid7 import uuid7` in models/guidelines.py),
  the import error was swallowed by a bare `except`, and the pipeline handle was
  latched to None for the life of the process. Every query silently took the
  direct-ChromaDB fallback. Worse, the pipeline it tried to build pointed at a
  `clinical_guidelines` collection that nothing seeds and the scope guard does
  not govern — so even a successful import would have queried an empty,
  ungoverned store.

  Current state: the pipeline is built per retriever instance over that
  instance's own `nccn_guidelines` collection — same client, same embedding
  function, same collection the scope guard governs and the seeder writes.
  Pipeline availability is observable via `pipeline_available`, and the
  direct-ChromaDB fallback is retained for genuine degradation.
"""

import hashlib
import os
from typing import Any

import chromadb
from chromadb.utils import embedding_functions

# Use the project's structlog-backed logger — accepts arbitrary kwargs
# like logger.warning("event", error=str(e)). The previous
# logging.getLogger() returned a stdlib Logger which rejected such
# kwargs and crashed the authorization-submission endpoint with
# "Logger._log() got an unexpected keyword argument 'error'".
from pacca.config import get_logger

logger = get_logger(__name__)

# The two real ChromaDB collections this retriever reads (SSOT). The scope
# guard's allowed-collections model (models/intent.py) mirrors these; a drift
# test asserts they stay in sync. Previously the intent named a phantom
# "clinical_guidelines" that was never queried, leaving both real collections —
# including institutional-memory precedents — ungoverned (#2).
GUIDELINE_COLLECTION = "nccn_guidelines"
PRECEDENT_COLLECTION = "case_precedents"
RAG_COLLECTIONS = [GUIDELINE_COLLECTION, PRECEDENT_COLLECTION]


def _precedent_id(case_summary: str) -> str:
    """
    Deterministic content id for a precedent (stable across processes).

    Keyed on the case scenario so the same override maps to the same id every
    time — enabling idempotent upserts — unlike the old per-process-randomized
    hash(). Two precedents differ iff their scenarios differ.
    """
    digest = hashlib.sha256(case_summary.strip().encode("utf-8")).hexdigest()
    return f"prec_{digest[:16]}"


def _build_pipeline(collection: Any) -> Any:
    """
    Build a RAGPipeline over an already-open guidelines collection.

    The import is local so that `pacca.rag.pipeline` (which pulls in ChromaDB
    and the guideline models) is only loaded when a retriever is constructed.

    Passing the caller's collection — rather than letting the pipeline open its
    own — is what keeps retrieval pointed at the one governed `nccn_guidelines`
    collection, using the retriever's embedding function and db path.
    """
    from pacca.rag.pipeline import GuidelineVectorStore, RAGPipeline

    vector_store = GuidelineVectorStore(collection=collection)
    return RAGPipeline(vector_store=vector_store)


class GuidelineRetriever:
    """
    Primary interface for clinical guideline retrieval.

    Delegates to RAGPipeline for retrieval over the guidelines collection:
      - Text chunking (1000 chars with 200-char overlap) on ingest
      - Distance-to-similarity scoring matched to the collection's space
      - Optional metadata filtering by specialty / treatment category
      - Retry without the category filter when it excludes everything

    Falls back to direct ChromaDB queries if RAGPipeline is unavailable.
    That fallback is a genuine degradation path, not the normal path:
    `pipeline_available` reports which one is in force.

    The dual-collection design is preserved:
      nccn_guidelines  — authoritative clinical guidelines (via RAGPipeline)
      case_precedents  — human override decisions (institutional memory)
    """

    def __init__(
        self,
        db_path: str | None = None,
        embedding_function: object | None = None,
    ) -> None:
        # B4: db_path and embedding_function are injectable so the store can be
        # pointed at a temp dir and seeded with a fake embedder in tests, without
        # the ~80MB default-model download. Both default to production behaviour.
        db_path = db_path or os.path.join(os.getcwd(), "pacca_db")
        self._client = chromadb.PersistentClient(path=db_path)
        self._embedding_fn = embedding_function or embedding_functions.DefaultEmbeddingFunction()

        # Cosine is the appropriate space for sentence-embedding similarity, and
        # it is what the retrieval docs have always claimed. ChromaDB applies this
        # only when creating a collection — an existing store keeps the space it
        # was built with (l2), with no error and no migration. Scoring reads the
        # collection's actual space (see rag.pipeline.similarity_from_distance),
        # so both old and new stores score correctly.
        _COSINE = {"hnsw:space": "cosine"}

        # Collection 1: Official guidelines (NCCN, CMS, AHA, etc.)
        self._guidelines = self._client.get_or_create_collection(
            name=GUIDELINE_COLLECTION,
            embedding_function=self._embedding_fn,
            metadata=_COSINE,
        )

        # Collection 2: Institutional memory (human override precedents)
        self._precedents = self._client.get_or_create_collection(
            name=PRECEDENT_COLLECTION,
            embedding_function=self._embedding_fn,
            metadata=_COSINE,
        )

        # Built per instance (not as a module singleton) so a retriever pointed
        # at a temp db_path gets a pipeline over *that* store. A failure here is
        # logged at error level and degrades to _query_direct; it is not silent.
        try:
            self._pipeline = _build_pipeline(self._guidelines)
            logger.info(
                "rag_pipeline_initialized",
                db_path=db_path,
                collection=GUIDELINE_COLLECTION,
            )
        except Exception as e:
            self._pipeline = None
            logger.error(
                "rag_pipeline_init_failed",
                error=str(e),
                fallback="direct_chromadb",
            )

    @property
    def pipeline_available(self) -> bool:
        """
        Whether the RAGPipeline path is in force for this retriever.

        False means every query is silently degrading to the direct-ChromaDB
        fallback — the exact condition that went unnoticed before, so it is
        exposed rather than left to log archaeology.
        """
        return self._pipeline is not None

    def guideline_count(self) -> int:
        """Number of official guidelines in the store. 0 means unseeded (B4)."""
        return self._guidelines.count()

    def precedent_count(self) -> int:
        """Number of institutional-memory precedents in the store."""
        return self._precedents.count()

    def add_guideline(
        self,
        guideline_text: str,
        source_id: str,
        metadata: dict,
    ) -> None:
        """
        Add or update a guideline in the official guidelines collection.

        Used by the policy evolution governance pipeline when a human-approved
        amendment is deployed.

        Args:
            guideline_text: Full text of the guideline
            source_id:      Unique identifier (used as ChromaDB document ID)
            metadata:       Structured metadata (source, change_id, etc.)
        """
        self._guidelines.upsert(
            documents=[guideline_text],
            metadatas=[metadata],
            ids=[source_id],
        )
        logger.info(
            "guideline_upserted",
            source_id=source_id,
            source=metadata.get("source", "unknown"),
        )

    def add_precedent(
        self,
        case_summary: str,
        rationale: str,
        outcome: str,
    ) -> None:
        """
        Add a human override decision to the institutional memory collection.

        Called by the /feedback endpoint when a provider or reviewer submits
        a corrected decision. The precedent is embedded and stored — future
        semantically similar cases will retrieve it alongside official guidelines.

        This is how PACCA learns from human expertise without model retraining.

        Args:
            case_summary: Brief description of the clinical scenario
            rationale:    Why the human made this decision
            outcome:      The correct outcome (e.g., "AUTO_APPROVED")
        """
        document = f"SCENARIO: {case_summary}\nOUTCOME: {outcome}\nREASON: {rationale}"
        # upsert (not add) with a deterministic content id: the same scenario is
        # idempotent and a corrected override overwrites in place. The old
        # f"prec_{abs(hash(case_summary))}" used Python's per-process-randomized
        # hash(), so the same override got a new id on every restart (silent
        # duplicates) and a same-process repeat collided on add() — the B6
        # unstable-key anti-pattern applied to institutional memory.
        self._precedents.upsert(
            documents=[document],
            metadatas=[{"type": "human_override", "outcome": outcome}],
            ids=[_precedent_id(case_summary)],
        )
        logger.info("precedent_added", outcome=outcome)

    def query(self, clinical_query: str) -> str:
        """
        Retrieve relevant guidelines and precedents for a clinical query.

        Uses RAGPipeline for the guidelines collection, then appends
        institutional-memory precedents. Falls back to direct ChromaDB queries
        if the pipeline is unavailable or errors.

        Args:
            clinical_query: Natural language query built from the case details

        Returns:
            Formatted string with official guidelines and relevant precedents,
            ready to be injected into agent prompts as context.
        """
        if self._pipeline is not None:
            try:
                # Parse the query to extract diagnosis/treatment components.
                # The query format from routes is:
                # "Guidelines for {diagnosis_code} and {procedure_code}"
                parts = clinical_query.replace("Guidelines for ", "").split(" and ")
                diagnosis_code = parts[0].strip() if parts else ""
                procedure_code = parts[1].strip() if len(parts) > 1 else ""

                # Called synchronously: ChromaDB is a sync library, so the
                # pipeline's sync entry point is used directly rather than
                # spinning up a throwaway event loop inside the running one.
                # No treatment_category is passed — the route's query string
                # carries no category, and inventing one ("general") only
                # produced a filter that matched nothing plus a wasted retry.
                guidelines_text = self._pipeline.retrieve_relevant_guidelines_sync(
                    diagnosis_code=diagnosis_code,
                    diagnosis_description=diagnosis_code,
                    treatment_code=procedure_code,
                    treatment_description=procedure_code,
                    clinical_context=clinical_query,
                    n_results=5,
                )

                # Append precedents from the institutional memory collection
                precedents_text = self._query_precedents(clinical_query)
                if precedents_text:
                    guidelines_text += f"\n\n{precedents_text}"

                logger.info(
                    "rag_pipeline_query_completed",
                    query_length=len(clinical_query),
                )
                return guidelines_text

            except Exception as e:
                logger.error(
                    "rag_pipeline_query_failed",
                    error=str(e),
                    fallback="direct_chromadb",
                )
                # Fall through to direct ChromaDB fallback

        # Fallback: direct ChromaDB queries (original behavior)
        return self._query_direct(clinical_query)

    def _query_direct(self, clinical_query: str) -> str:
        """
        Fallback: query ChromaDB collections directly.

        This is the original GuidelineRetriever.query() implementation,
        retained as a fallback when RAGPipeline is unavailable.
        """
        context = "OFFICIAL GUIDELINES:\n"

        try:
            rules = self._guidelines.query(
                query_texts=[clinical_query],
                n_results=2,
            )
            if rules["documents"]:
                for doc in rules["documents"][0]:
                    context += f"- {doc}\n"
        except Exception as e:
            logger.warning("guidelines_query_failed", error=str(e))
            context += "(No guidelines retrieved)\n"

        try:
            memories = self._precedents.query(
                query_texts=[clinical_query],
                n_results=1,
            )
            if memories["documents"] and memories["documents"][0]:
                context += "\nPAST MEDICAL DIRECTOR DECISIONS (PRECEDENTS):\n"
                for doc in memories["documents"][0]:
                    context += f"- {doc}\n"
        except Exception as e:
            logger.warning("precedents_query_failed", error=str(e))

        return context

    def _query_precedents(self, clinical_query: str) -> str:
        """
        Query the institutional memory (precedents) collection.

        This supplements RAGPipeline results with human override decisions.
        RAGPipeline queries the guidelines collection; this adds the precedents.
        """
        try:
            memories = self._precedents.query(
                query_texts=[clinical_query],
                n_results=2,
            )
            if memories["documents"] and memories["documents"][0]:
                precedents = "\nPAST MEDICAL DIRECTOR DECISIONS (PRECEDENTS):\n"
                for doc in memories["documents"][0]:
                    precedents += f"- {doc}\n"
                return precedents
        except Exception as e:
            logger.warning("precedents_query_failed", error=str(e))
        return ""
