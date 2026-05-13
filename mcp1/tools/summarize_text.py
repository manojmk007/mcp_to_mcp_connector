"""
mcp1/tools/summarize_text.py - summarize_text tool implementation.
Uses simple extractive summarization (sentence scoring).
"""
from __future__ import annotations

import asyncio
import re
from typing import Optional


async def summarize_text(text: str, max_sentences: int = 3) -> str:
    """
    Extractive text summarization: returns the top N most 'important' sentences.
    Importance = number of unique significant words in the sentence.
    """
    # Simulate processing delay
    await asyncio.sleep(0.1)

    if not text or not text.strip():
        return "No content to summarize."

    # Split into sentences
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    if len(sentences) <= max_sentences:
        return text.strip()

    # Build word frequency map (ignore common stop words)
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "in", "on", "at",
        "to", "for", "of", "and", "or", "but", "it", "this", "that",
        "with", "as", "be", "by", "from", "not", "we", "you", "i",
    }
    word_freq: dict[str, int] = {}
    for sentence in sentences:
        for word in re.findall(r"\b\w+\b", sentence.lower()):
            if word not in stop_words and len(word) > 2:
                word_freq[word] = word_freq.get(word, 0) + 1

    # Score each sentence
    def score(sentence: str) -> float:
        words = re.findall(r"\b\w+\b", sentence.lower())
        if not words:
            return 0.0
        return sum(word_freq.get(w, 0) for w in words if w not in stop_words) / len(words)

    scored = sorted(enumerate(sentences), key=lambda x: score(x[1]), reverse=True)
    top_indices = sorted([idx for idx, _ in scored[:max_sentences]])
    summary = " ".join(sentences[i] for i in top_indices)
    return summary
