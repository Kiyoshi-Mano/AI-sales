import os
import pickle
import hashlib
from pathlib import Path
from typing import List, Dict, Tuple, Optional
import numpy as np
import pandas as pd
import faiss
from openai import OpenAI
import logging
import re
import pymupdf as fitz

from .models.schemas import DocumentChunk, SearchResult, QAResponse, MachineSpecs

logger = logging.getLogger(__name__)


class VectorSearchEngine:
    """FAISS-based vector search engine for catalog documents"""

    def __init__(self, cache_dir: Path = Path("cache")):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(exist_ok=True)

        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.embedding_model = "text-embedding-3-small"
        self.embedding_dim = 1536

        self.index = None
        self.chunks = []
        self.chunk_metadata = {}

    def create_chunks(self, pdf_path: Path, chunk_size: int = 600, overlap: int = 150) -> List[DocumentChunk]:
        """Create text chunks from PDF with sliding window"""
        try:
            doc = fitz.open(str(pdf_path))
            chunks = []

            # Extract brand info from filename
            brand = self._extract_brand_from_filename(pdf_path.name)

            for page_num in range(len(doc)):
                page = doc[page_num]
                text = page.get_text()

                if not text.strip():
                    continue

                # Create sliding window chunks
                page_chunks = self._create_sliding_chunks(
                    text, chunk_size, overlap, pdf_path.name, page_num + 1, brand
                )
                chunks.extend(page_chunks)

            doc.close()
            logger.info(f"Created {len(chunks)} chunks from {pdf_path.name}")
            return chunks

        except Exception as e:
            logger.error(f"Error creating chunks from {pdf_path}: {e}")
            return []

    def _create_sliding_chunks(self, text: str, chunk_size: int, overlap: int,
                              source: str, page: int, brand: str) -> List[DocumentChunk]:
        """Create sliding window text chunks"""
        chunks = []
        text_length = len(text)

        if text_length <= chunk_size:
            # Single chunk for short text
            chunk_id = hashlib.md5(f"{source}_{page}_0".encode()).hexdigest()
            chunks.append(DocumentChunk(
                content=text.strip(),
                source=source,
                page=page,
                brand=brand,
                chunk_id=chunk_id
            ))
        else:
            # Multiple overlapping chunks
            start = 0
            chunk_idx = 0

            while start < text_length:
                end = min(start + chunk_size, text_length)
                chunk_text = text[start:end]

                # Find better break points (sentence endings)
                if end < text_length:
                    # Look for sentence ending within the last 100 characters
                    break_candidates = [
                        chunk_text.rfind('。'),
                        chunk_text.rfind('．'),
                        chunk_text.rfind('\n\n'),
                        chunk_text.rfind('\n')
                    ]

                    best_break = max([bc for bc in break_candidates if bc > len(chunk_text) - 100])
                    if best_break > 0:
                        chunk_text = chunk_text[:best_break + 1]

                if chunk_text.strip():
                    chunk_id = hashlib.md5(f"{source}_{page}_{chunk_idx}".encode()).hexdigest()
                    chunks.append(DocumentChunk(
                        content=chunk_text.strip(),
                        source=source,
                        page=page,
                        brand=brand,
                        chunk_id=chunk_id
                    ))

                start += chunk_size - overlap
                chunk_idx += 1

        return chunks

    def _extract_brand_from_filename(self, filename: str) -> str:
        """Extract brand from filename"""
        filename_lower = filename.lower()
        if 'brother' in filename_lower:
            return 'BROTHER'
        elif 'fanac' in filename_lower or 'fanuc' in filename_lower:
            return 'FANUC'
        elif 'okuma' in filename_lower:
            return 'OKUMA'
        else:
            return 'UNKNOWN'

    def get_embeddings(self, texts: List[str], batch_size: int = 100) -> np.ndarray:
        """Get embeddings from OpenAI API in batches"""
        all_embeddings = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]

            try:
                response = self.client.embeddings.create(
                    model=self.embedding_model,
                    input=batch
                )

                batch_embeddings = [emb.embedding for emb in response.data]
                all_embeddings.extend(batch_embeddings)

                logger.info(f"Processed embeddings for batch {i//batch_size + 1}/{(len(texts)-1)//batch_size + 1}")

            except Exception as e:
                logger.error(f"Error getting embeddings for batch {i//batch_size + 1}: {e}")
                # Add zero embeddings as fallback
                all_embeddings.extend([[0.0] * self.embedding_dim] * len(batch))

        return np.array(all_embeddings, dtype=np.float32)

    def build_index(self, pdf_directory: Path, force_rebuild: bool = False) -> None:
        """Build FAISS index from PDF documents"""
        index_path = self.cache_dir / "faiss_catalog.index"
        metadata_path = self.cache_dir / "chunk_metadata.pkl"

        # Check if cached index exists and is recent
        if not force_rebuild and index_path.exists() and metadata_path.exists():
            try:
                self.load_index()
                logger.info("Loaded existing FAISS index from cache")
                return
            except Exception as e:
                logger.warning(f"Failed to load cached index: {e}. Rebuilding...")

        logger.info("Building new FAISS index...")

        # Create chunks from all PDFs
        all_chunks = []
        pdf_files = list(pdf_directory.glob("*.pdf"))

        for pdf_path in pdf_files:
            pdf_chunks = self.create_chunks(pdf_path)
            all_chunks.extend(pdf_chunks)

        if not all_chunks:
            logger.error("No chunks created from PDFs")
            return

        # Get embeddings
        chunk_texts = [chunk.content for chunk in all_chunks]
        embeddings = self.get_embeddings(chunk_texts)

        # Build FAISS index
        self.index = faiss.IndexFlatIP(self.embedding_dim)  # Inner product for cosine similarity

        # Normalize embeddings for cosine similarity
        faiss.normalize_L2(embeddings)
        self.index.add(embeddings)

        # Store chunks and metadata
        self.chunks = all_chunks
        for i, chunk in enumerate(all_chunks):
            self.chunk_metadata[i] = {
                'source': chunk.source,
                'page': chunk.page,
                'brand': chunk.brand,
                'model': chunk.model,
                'series': chunk.series,
                'content': chunk.content
            }

        # Save to cache
        self.save_index()

        logger.info(f"Built FAISS index with {len(all_chunks)} chunks")

    def save_index(self) -> None:
        """Save FAISS index and metadata to cache"""
        try:
            index_path = self.cache_dir / "faiss_catalog.index"
            metadata_path = self.cache_dir / "chunk_metadata.pkl"

            faiss.write_index(self.index, str(index_path))

            with open(metadata_path, 'wb') as f:
                pickle.dump({
                    'chunks': self.chunks,
                    'metadata': self.chunk_metadata
                }, f)

            logger.info("Saved FAISS index and metadata to cache")

        except Exception as e:
            logger.error(f"Error saving index: {e}")

    def load_index(self) -> None:
        """Load FAISS index and metadata from cache"""
        index_path = self.cache_dir / "faiss_catalog.index"
        metadata_path = self.cache_dir / "chunk_metadata.pkl"

        if not index_path.exists() or not metadata_path.exists():
            raise FileNotFoundError("Index files not found in cache")

        self.index = faiss.read_index(str(index_path))

        with open(metadata_path, 'rb') as f:
            data = pickle.load(f)
            self.chunks = data['chunks']
            self.chunk_metadata = data['metadata']

        logger.info(f"Loaded FAISS index with {len(self.chunks)} chunks")

    def search(self, query: str, top_k: int = 5, brand_filter: Optional[str] = None) -> List[SearchResult]:
        """Search for relevant chunks"""
        if self.index is None:
            logger.error("Index not built. Please build index first.")
            return []

        try:
            # Get query embedding
            query_embedding = self.get_embeddings([query])
            faiss.normalize_L2(query_embedding)

            # Search
            scores, indices = self.index.search(query_embedding, top_k * 2)  # Get more results for filtering

            results = []
            for score, idx in zip(scores[0], indices[0]):
                if idx == -1:  # Invalid index
                    continue

                metadata = self.chunk_metadata.get(idx, {})

                # Apply brand filter if specified
                if brand_filter and metadata.get('brand') != brand_filter:
                    continue

                result = SearchResult(
                    content=metadata.get('content', ''),
                    source=metadata.get('source', ''),
                    page=metadata.get('page', 0),
                    brand=metadata.get('brand', ''),
                    model=metadata.get('model'),
                    score=float(score)
                )
                results.append(result)

                if len(results) >= top_k:
                    break

            return results

        except Exception as e:
            logger.error(f"Error during search: {e}")
            return []

    def answer_question(self, question: str, brand_filter: Optional[str] = None,
                       top_k: int = 5) -> QAResponse:
        """Answer question using RAG"""
        # Search for relevant context
        search_results = self.search(question, top_k=top_k, brand_filter=brand_filter)

        if not search_results:
            return QAResponse(
                answer="カタログに根拠となる情報が見つからないため、回答できません。",
                citations=[],
                confidence=0.0
            )

        # Prepare context
        context_parts = []
        for i, result in enumerate(search_results):
            context_parts.append(f"[{i+1}] {result.source} p.{result.page}: {result.content}")

        context = "\n\n".join(context_parts)

        # Generate answer using OpenAI
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": """あなたは工作機械の営業支援AIです。カタログ資料を基に正確で有用な回答を提供してください。

重要なルール:
1. 提供されたカタログ情報のみを根拠として回答してください
2. カタログに記載がない情報は推測や一般知識で補完しないでください
3. 回答の根拠となった資料名とページ番号を必ず明示してください
4. 数値は正確に引用し、単位も含めてください
5. 比較質問では、具体的な数値や仕様の違いを明確に示してください"""
                    },
                    {
                        "role": "user",
                        "content": f"質問: {question}\n\nカタログ情報:\n{context}\n\n上記のカタログ情報のみを根拠として質問に回答してください。"
                    }
                ],
                temperature=0.1,
                max_tokens=1000
            )

            answer = response.choices[0].message.content

            # Calculate confidence based on search scores
            avg_score = np.mean([r.score for r in search_results])
            confidence = min(avg_score * 2, 1.0)  # Scale to 0-1

            return QAResponse(
                answer=answer,
                citations=search_results,
                confidence=confidence
            )

        except Exception as e:
            logger.error(f"Error generating answer: {e}")
            return QAResponse(
                answer="回答の生成中にエラーが発生しました。",
                citations=search_results,
                confidence=0.0
            )


def create_dataframe_from_specs(specs_list: List[MachineSpecs]) -> pd.DataFrame:
    """Convert machine specs to pandas DataFrame for comparison"""
    data = []

    for spec in specs_list:
        row = {
            'brand': spec.metadata.brand,
            'series': spec.metadata.series,
            'model': spec.metadata.model,
            'doc_source': spec.metadata.doc_source,
            'cnc': spec.metadata.cnc,

            # Axis travel
            'X_mm': spec.axis_travel.X_mm,
            'Y_mm': spec.axis_travel.Y_mm,
            'Z_mm': spec.axis_travel.Z_mm,
            'Z_alt_mm': spec.axis_travel.Z_alt_mm,

            # Table
            'table_size_X': spec.table.size_mm[0] if spec.table.size_mm and len(spec.table.size_mm) >= 1 else None,
            'table_size_Y': spec.table.size_mm[1] if spec.table.size_mm and len(spec.table.size_mm) >= 2 else None,
            'table_load_kg': spec.table.load_kg,

            # Spindle
            'spindle_max_rpm': spec.spindle.max_rpm,
            'spindle_options': spec.spindle.options_rpm,

            # Feed
            'rapid_XY_mpm': spec.feed.rapid_XY_mpm,
            'rapid_Z_mpm': spec.feed.rapid_Z_mpm,

            # Tool magazine
            'tool_count': spec.tool_mag.tools,
            'tool_to_tool_s': spec.tool_mag.tool_to_tool_s,
            'chip_to_chip_s': spec.tool_mag.chip_to_chip_s,
            'max_tool_mass_kg': spec.tool_mag.max_tool_mass_kg,

            # Others
            'footprint_mm': spec.footprint_mm,
            'notes': '; '.join(spec.notes) if spec.notes else None
        }

        data.append(row)

    return pd.DataFrame(data)