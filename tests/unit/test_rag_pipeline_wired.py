"""
The production RAG pipeline is importable, wired, and actually exercised.

Failure this locks down
-----------------------
``pacca.rag.pipeline`` was unimportable: ``models/guidelines.py`` did
``from uuid7 import uuid7``, but the ``uuid7`` distribution installs its module
as ``uuid_extensions``. ``GuidelineRetriever`` imported the pipeline inside a
bare ``try/except``, logged a warning, latched its handle to ``None`` for the
life of the process, and served every query from the direct-ChromaDB fallback.
Nothing failed, so nothing noticed — the "production-quality retrieval with
chunking and cosine scoring" in the docs never ran once.

The pipeline it *tried* to build was also pointed at a ``clinical_guidelines``
collection that no seeder writes and the P-4 scope guard does not govern, so
even a successful import would have queried an empty, ungoverned store.

These tests assert the positive path (pipeline present, invoked, reading the
governed collection) and keep regression coverage on the fallback, so a future
silent regression to fallback-forever fails the suite instead of passing it.

Hermetic: a fake embedder is injected so no ChromaDB model is downloaded.
"""

from __future__ import annotations

import itertools

import numpy as np
import pytest
from chromadb import EmbeddingFunction

from pacca.integrations.vector_store import (
    GUIDELINE_COLLECTION,
    GuidelineRetriever,
)
from pacca.rag.pipeline import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    GuidelineVectorStore,
    RAGPipeline,
    similarity_from_distance,
)


class _FakeEmbedding(EmbeddingFunction):
    """Deterministic bag-of-characters embedding — no model download."""

    def __init__(self) -> None:
        pass

    @staticmethod
    def name() -> str:
        return "fake-test-embedding"

    def __call__(self, input: list[str]) -> list[np.ndarray]:
        vectors = []
        for text in input:
            vector = np.zeros(16, dtype=np.float32)
            for char in text.lower():
                vector[ord(char) % 16] += 1.0
            vectors.append(vector)
        return vectors


@pytest.fixture
def retriever(tmp_path: object) -> GuidelineRetriever:
    return GuidelineRetriever(db_path=str(tmp_path), embedding_function=_FakeEmbedding())


# =============================================================================
# The import chain itself
# =============================================================================


def test_rag_package_imports() -> None:
    """
    `pacca.rag` must import cleanly.

    This is the root failure: an ImportError here is what silently disabled the
    whole pipeline. Importing the package (not just the module) also covers the
    `sample_guidelines` re-export, which pulls in the guideline models.
    """
    import pacca.rag

    assert pacca.rag.GuidelineVectorStore is GuidelineVectorStore
    assert pacca.rag.RAGPipeline is RAGPipeline
    assert len(pacca.rag.SAMPLE_GUIDELINES) > 0


def test_guideline_models_are_exported_from_pacca_models() -> None:
    """pipeline.py imports these from `pacca.models`; they must be re-exported."""
    from pacca.models import (
        ClinicalGuideline,
        ClinicalSpecialty,
        GuidelineChunk,
        GuidelineSearchResult,
        TreatmentCategory,
    )

    assert ClinicalGuideline is not None
    assert GuidelineChunk is not None
    assert GuidelineSearchResult is not None
    # The enums referenced by ClinicalGuideline's scope fields must exist too.
    assert ClinicalSpecialty.ONCOLOGY
    assert TreatmentCategory.IMAGING


# =============================================================================
# The pipeline is wired to the governed collection
# =============================================================================


def test_retriever_builds_a_real_pipeline(retriever: GuidelineRetriever) -> None:
    """A constructed retriever must have a live pipeline, not a None handle."""
    assert retriever.pipeline_available is True
    assert isinstance(retriever._pipeline, RAGPipeline)


def test_pipeline_reads_the_governed_guidelines_collection(
    retriever: GuidelineRetriever,
) -> None:
    """
    The pipeline must share the retriever's own nccn_guidelines collection.

    Sharing the object — rather than opening a second client on a second
    collection — is what keeps retrieval inside the scope guard's governed set
    and pointed at the data the seeder writes.
    """
    store = retriever._pipeline.vector_store
    assert store.collection_name == GUIDELINE_COLLECTION
    assert store._collection is retriever._guidelines


def test_no_phantom_clinical_guidelines_literal_in_code() -> None:
    """
    The ungoverned `clinical_guidelines` string literal must be gone.

    It named a collection nothing seeded and the scope guard never governed.
    Only executable string literals are checked (via the AST) — comments and
    docstrings are allowed to describe the historical name.
    """
    import ast
    import inspect

    from pacca.integrations import vector_store as vs_module
    from pacca.rag import pipeline as pipeline_module

    for module in (vs_module, pipeline_module):
        tree = ast.parse(inspect.getsource(module))
        docstrings = {
            node.body[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        }
        literals = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node not in docstrings
        ]
        assert "clinical_guidelines" not in literals, (
            f"{module.__name__} still references the phantom collection"
        )


def test_vector_store_refuses_to_guess_a_collection_name() -> None:
    """
    Constructing a store standalone requires an explicit collection name.

    The old default silently created the phantom collection; there is now no
    default to fall back to.
    """
    with pytest.raises(ValueError, match="collection_name"):
        GuidelineVectorStore()


# =============================================================================
# The pipeline is actually exercised on the query path
# =============================================================================


def test_query_uses_pipeline_output_not_the_fallback(
    retriever: GuidelineRetriever,
) -> None:
    """
    query() output must carry the pipeline's formatting.

    "Relevance Score" is emitted only by RAGPipeline; the direct fallback emits
    a bare "- {doc}" bullet list. This is the assertion that distinguishes the
    two paths — the property no previous test checked.
    """
    retriever.add_guideline(
        guideline_text=(
            "CRITERIA FOR MRI LUMBAR SPINE (72148): indicated only after "
            "6 weeks of documented conservative therapy."
        ),
        source_id="CMS-SPINE-002",
        metadata={"source": "CMS", "guideline_name": "CMS Lumbar Spine MRI"},
    )

    result = retriever.query("Guidelines for M54.5 and 72148")

    assert "Relevance Score" in result
    assert "6 weeks" in result
    assert "OFFICIAL GUIDELINES:" not in result, "fell back to the direct path"


def test_query_still_appends_precedents_alongside_pipeline_results(
    retriever: GuidelineRetriever,
) -> None:
    """Dual-collection behaviour survives: guidelines via pipeline + precedents."""
    retriever.add_guideline(
        guideline_text="CRITERIA FOR MRI LUMBAR SPINE (72148): 6 weeks conservative therapy.",
        source_id="CMS-SPINE-002",
        metadata={"source": "CMS"},
    )
    retriever.add_precedent(
        case_summary="MRI lumbar spine, foot drop, 3 weeks of symptoms",
        rationale="neurological emergency overrides the 6-week rule",
        outcome="AUTO_APPROVED",
    )

    result = retriever.query("Guidelines for M54.5 and 72148")

    assert "Relevance Score" in result, "guidelines did not come from the pipeline"
    assert "PAST MEDICAL DIRECTOR DECISIONS (PRECEDENTS)" in result
    assert "foot drop" in result


def test_query_on_empty_store_reports_no_guidelines(
    retriever: GuidelineRetriever,
) -> None:
    """An unseeded store must not raise, and must say so rather than inventing text."""
    result = retriever.query("Guidelines for M54.5 and 72148")
    assert "No specific guidelines found" in result


# =============================================================================
# Chunking: sentence-boundary awareness and overlap
# =============================================================================


def test_short_text_is_a_single_chunk() -> None:
    store = GuidelineVectorStore.__new__(GuidelineVectorStore)
    chunks = store._chunk_text("Short guideline text.", chunk_size=1000, overlap=200)
    assert chunks == ["Short guideline text."]


def test_chunking_prefers_sentence_boundaries() -> None:
    """Chunks should end at a sentence boundary, not mid-word."""
    store = GuidelineVectorStore.__new__(GuidelineVectorStore)
    text = ("This is a clinical sentence about therapy. " * 80).strip()

    chunks = store._chunk_text(text, chunk_size=200, overlap=40)

    assert len(chunks) > 1
    # Every chunk but the last should end on a sentence terminator.
    for chunk in chunks[:-1]:
        assert chunk.endswith("."), f"chunk ended mid-sentence: {chunk[-40:]!r}"


def test_chunking_overlaps_consecutive_chunks() -> None:
    """Consecutive chunks must share text, so a fact spanning a cut is retrievable."""
    store = GuidelineVectorStore.__new__(GuidelineVectorStore)
    text = "".join(f"Sentence number {i} about the clinical criteria. " for i in range(200))

    chunks = store._chunk_text(text, chunk_size=300, overlap=100)

    assert len(chunks) > 2
    # The tail of chunk N must appear in chunk N+1.
    for first, second in itertools.pairwise(chunks):
        tail = first[-40:]
        assert tail in second, "consecutive chunks do not overlap"


def test_chunking_covers_the_whole_document() -> None:
    """No text may be silently dropped between chunks."""
    store = GuidelineVectorStore.__new__(GuidelineVectorStore)
    text = "".join(f"Fact {i} matters for authorization. " for i in range(300))

    chunks = store._chunk_text(text, chunk_size=400, overlap=80)

    for i in range(300):
        assert any(f"Fact {i} matters" in chunk for chunk in chunks), f"Fact {i} was dropped"


def test_chunking_terminates_when_overlap_exceeds_chunk_size() -> None:
    """
    A degenerate overlap must not hang.

    overlap >= chunk_size made `start` fail to advance, so the loop spun forever
    on any text longer than one chunk.
    """
    store = GuidelineVectorStore.__new__(GuidelineVectorStore)
    text = "word " * 500

    chunks = store._chunk_text(text, chunk_size=50, overlap=500)

    assert len(chunks) > 1


def test_add_guideline_chunks_a_long_document(tmp_path: object) -> None:
    """Ingest must produce multiple chunk ids for a document larger than one chunk."""
    from datetime import date

    from pacca.models import ClinicalGuideline

    store = GuidelineVectorStore(
        collection_name="chunk_test",
        persist_directory=str(tmp_path),
        embedding_function=_FakeEmbedding(),
    )
    guideline = ClinicalGuideline(
        guideline_id="TEST-CHUNK-1",
        name="Chunking Test Guideline",
        version="1.0",
        effective_date=date(2026, 1, 1),
        source="TEST",
        summary="summary",
        full_text="".join(f"Criterion {i} must be documented. " for i in range(300)),
    )

    chunk_ids = store.add_guideline_sync(guideline, chunk_size=DEFAULT_CHUNK_SIZE)

    assert len(chunk_ids) > 1
    assert chunk_ids[0] == "TEST-CHUNK-1_chunk_0"
    assert store.count == len(chunk_ids)
    # Re-ingesting the same guideline updates in place rather than raising.
    store.add_guideline_sync(guideline, chunk_size=DEFAULT_CHUNK_SIZE)
    assert store.count == len(chunk_ids)


def test_chunk_defaults_are_the_documented_values() -> None:
    """The docs promise 1000-char chunks with 200-char overlap."""
    assert DEFAULT_CHUNK_SIZE == 1000
    assert DEFAULT_CHUNK_OVERLAP == 200


# =============================================================================
# Scoring: cosine, and space-awareness
# =============================================================================


def test_new_collections_use_cosine_space(retriever: GuidelineRetriever) -> None:
    """
    A freshly created store must use cosine — the space the docs always claimed.

    ChromaDB only applies the space at creation time, so a pre-existing l2 store
    keeps l2; scoring reads the actual space rather than assuming.
    """
    assert retriever._pipeline.vector_store.space == "cosine"


def test_similarity_conversion_matches_the_collection_space() -> None:
    """
    Each distance space needs its own conversion.

    The old code applied the l2 formula unconditionally while claiming cosine,
    so a cosine collection was scored wrong (a perfect match scored 1.0, and an
    orthogonal one 0.5, instead of 1.0 and 0.0).
    """
    # cosine: distance 0 is identical, 1 is orthogonal
    assert similarity_from_distance(0.0, "cosine") == pytest.approx(1.0)
    assert similarity_from_distance(1.0, "cosine") == pytest.approx(0.0)
    # l2: monotonically decreasing, bounded in (0, 1]
    assert similarity_from_distance(0.0, "l2") == pytest.approx(1.0)
    assert similarity_from_distance(1.0, "l2") == pytest.approx(0.5)
    assert 0.0 < similarity_from_distance(100.0, "l2") < 0.1
    # An unknown space must not crash; it falls back to the l2 formula.
    assert similarity_from_distance(1.0, "something_else") == pytest.approx(0.5)


def test_similarity_scores_stay_in_range(retriever: GuidelineRetriever) -> None:
    """GuidelineSearchResult constrains the score to 0..1; conversion must comply."""
    retriever.add_guideline(
        guideline_text="Totally unrelated text about ophthalmology screening.",
        source_id="X-1",
        metadata={"source": "TEST"},
    )
    results = retriever._pipeline.vector_store.search_sync(query="lumbar spine MRI", n_results=5)
    assert results
    for result in results:
        assert 0.0 <= result.similarity_score <= 1.0


# =============================================================================
# Metadata filtering
# =============================================================================


def _filterable_store(tmp_path: object, name: str) -> GuidelineVectorStore:
    """A store holding one imaging/radiology guideline and one medication/oncology one."""
    from datetime import date

    from pacca.models import ClinicalGuideline, ClinicalSpecialty, TreatmentCategory

    store = GuidelineVectorStore(
        collection_name=name,
        persist_directory=str(tmp_path),
        embedding_function=_FakeEmbedding(),
    )
    store.add_guideline_sync(
        ClinicalGuideline(
            guideline_id="IMG-1",
            name="Lumbar MRI",
            version="1.0",
            effective_date=date(2026, 1, 1),
            source="CMS",
            specialties=[ClinicalSpecialty.RADIOLOGY],
            treatment_categories=[TreatmentCategory.IMAGING],
            summary="s",
            full_text="imaging guidance for lumbar MRI",
        )
    )
    store.add_guideline_sync(
        ClinicalGuideline(
            guideline_id="MED-1",
            name="Pembrolizumab",
            version="1.0",
            effective_date=date(2026, 1, 1),
            source="NCCN",
            specialties=[ClinicalSpecialty.ONCOLOGY],
            treatment_categories=[TreatmentCategory.MEDICATION],
            summary="s",
            full_text="medication guidance for pembrolizumab",
        )
    )
    return store


def test_metadata_filter_narrows_results(tmp_path: object) -> None:
    """
    Category and specialty filtering must actually exclude non-matching chunks.

    Regression guard: the old filter used {"specialties": {"$contains": ...}}.
    ChromaDB accepts $contains on metadata but never satisfies it (it is a
    where_document operator), so every filtered search returned zero rows —
    and the unfiltered retry silently papered over it. A filter that excludes
    everything is indistinguishable from no filter at all, which is why this
    asserts the *unfiltered* count differs from the filtered one.
    """
    store = _filterable_store(tmp_path, "filter_test")

    assert len(store.search_sync(query="guidance", n_results=5)) == 2

    imaging = store.search_sync(query="guidance", n_results=5, treatment_category_filter="imaging")
    assert len(imaging) == 1
    assert "imaging guidance" in imaging[0].chunk.content

    specialty = store.search_sync(query="guidance", n_results=5, specialty_filter="oncology")
    assert len(specialty) == 1
    assert "pembrolizumab" in specialty[0].chunk.content


def test_combined_filters_are_anded(tmp_path: object) -> None:
    """Specialty + category together must both have to match."""
    store = _filterable_store(tmp_path, "and_filter_test")

    both_match = store.search_sync(
        query="guidance",
        n_results=5,
        specialty_filter="radiology",
        treatment_category_filter="imaging",
    )
    assert len(both_match) == 1

    mismatched = store.search_sync(
        query="guidance",
        n_results=5,
        specialty_filter="oncology",
        treatment_category_filter="imaging",
    )
    assert mismatched == []


def test_unmatched_category_filter_retries_unfiltered(tmp_path: object) -> None:
    """
    A category that excludes everything must fall back to an unfiltered search
    rather than returning no guidelines at all.
    """
    store = _filterable_store(tmp_path, "retry_test")
    pipeline = RAGPipeline(vector_store=store)

    text = pipeline.retrieve_relevant_guidelines_sync(
        diagnosis_code="M54.5",
        diagnosis_description="low back pain",
        treatment_code="72148",
        treatment_description="MRI lumbar spine",
        treatment_category="no_such_category",
    )

    assert "guidance" in text
