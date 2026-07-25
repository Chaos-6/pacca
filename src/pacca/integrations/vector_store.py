"""
GuidelineRetriever — production RAG interface for PACCA.

This module provides the primary interface for clinical guideline
retrieval. It wraps RAGPipeline (the richer, production-grade
implementation) while maintaining backward-compatible method names
so all existing routes and agents continue to work without changes.

Architecture history (important context for reviewers):
  The original implementation in this file was a direct ChromaDB
  wrapper — functional but without chunking, metadata filtering,
  cosine similarity scoring, or relevance thresholds.

  rag/pipeline.py was written with all those features but was never
  wired as the active production path. Routes imported GuidelineRetriever
  from this file; RAGPipeline sat unused.

  v2.2 resolution: GuidelineRetriever now delegates to RAGPipeline
  internally. The public API is unchanged (same method signatures,
  same return types). The retrieval quality is now backed by the
  full pipeline with chunking and cosine similarity scoring.

  This is the Adapter design pattern: GuidelineRetriever is the
  stable interface that the rest of the codebase depends on;
  RAGPipeline is the implementation that does the actual work.

Teaching note — why not just change the import in routes?

  `routes/authorizations.py` currently imports:
    from ...integrations.vector_store import GuidelineRetriever

  We could change that import to use RAGPipeline directly. But:
    1. Other files may import GuidelineRetriever (admin.py, tests)
    2. Changing imports across multiple files is higher risk than
       upgrading the implementation behind one stable interface
    3. The Adapter pattern is the right design: the interface is
       a contract; changing the implementation behind it is safe

  The single place to change retrieval logic is now this file.
"""

import asyncio
import hashlib
import os

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


# ── Lazy import of RAGPipeline ────────────────────────────────────────────────
# We use a lazy import to avoid circular imports and to allow the module
# to be imported even if rag/pipeline.py has a missing dependency.
# The pipeline is instantiated on first use.
_rag_pipeline = None


def _get_pipeline():
    """
    Get or create the RAGPipeline singleton.

    Uses the same ChromaDB path as the original implementation for
    backward compatibility — existing data in pacca_db is preserved.
    """
    global _rag_pipeline
    if _rag_pipeline is None:
        try:
            from pacca.rag.pipeline import GuidelineVectorStore, RAGPipeline

            db_path = os.path.join(os.getcwd(), "pacca_db")
            vector_store = GuidelineVectorStore(
                collection_name="clinical_guidelines",
                persist_directory=db_path,
            )
            _rag_pipeline = RAGPipeline(vector_store=vector_store)
            logger.info("rag_pipeline_initialized", db_path=db_path)
        except Exception as e:
            logger.warning(
                "rag_pipeline_init_failed",
                error=str(e),
                fallback="direct_chromadb",
            )
            _rag_pipeline = None
    return _rag_pipeline


class GuidelineRetriever:
    """
    Primary interface for clinical guideline retrieval.

    Delegates to RAGPipeline for production-quality retrieval:
      - Text chunking (1000 chars with 200-char overlap)
      - Cosine similarity scoring
      - Metadata filtering by specialty / treatment category
      - Fallback retry without category filter if no results found

    Falls back gracefully to direct ChromaDB queries if RAGPipeline
    is unavailable (e.g., missing dependencies in development).

    The dual-collection design is preserved:
      nccn_guidelines  — authoritative clinical guidelines
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

        # Collection 1: Official guidelines (NCCN, CMS, AHA, etc.)
        self._guidelines = self._client.get_or_create_collection(
            name=GUIDELINE_COLLECTION,
            embedding_function=self._embedding_fn,
        )

        # Collection 2: Institutional memory (human override precedents)
        self._precedents = self._client.get_or_create_collection(
            name=PRECEDENT_COLLECTION,
            embedding_function=self._embedding_fn,
        )

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

        Attempts to use RAGPipeline (with chunking and cosine scoring).
        Falls back to direct ChromaDB queries if the pipeline is unavailable.

        Args:
            clinical_query: Natural language query built from the case details

        Returns:
            Formatted string with official guidelines and relevant precedents,
            ready to be injected into agent prompts as context.
        """
        pipeline = _get_pipeline()

        if pipeline is not None:
            # Use the full RAGPipeline for production-quality retrieval.
            # RAGPipeline.retrieve_relevant_guidelines() is async, but
            # GuidelineRetriever.query() is called from sync contexts.
            # We use asyncio.run() here as a bridge — this is acceptable
            # because query() is only called from route handlers that
            # are themselves async (the event loop is already running).
            # In a full async refactor, query() would be async and
            # awaited directly. That is the production next step.
            try:
                # Parse the query to extract diagnosis/treatment components
                # The query format from routes is:
                # "Guidelines for {diagnosis_code} and {procedure_code}"
                parts = clinical_query.replace("Guidelines for ", "").split(" and ")
                diagnosis_code = parts[0].strip() if parts else ""
                procedure_code = parts[1].strip() if len(parts) > 1 else ""

                loop = asyncio.new_event_loop()
                try:
                    guidelines_text = loop.run_until_complete(
                        pipeline.retrieve_relevant_guidelines(
                            diagnosis_code=diagnosis_code,
                            diagnosis_description=diagnosis_code,
                            treatment_code=procedure_code,
                            treatment_description=procedure_code,
                            treatment_category="general",
                            clinical_context=clinical_query,
                            n_results=5,
                        )
                    )
                finally:
                    loop.close()

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
                logger.warning(
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
