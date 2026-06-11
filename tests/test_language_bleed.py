"""Focused tests for crash language bleed in generic datasets.

These tests upload a non-crash dataset (sales) and verify that
responses never contain crash-specific terminology.
"""

import os
import pytest
from conftest import send_chat, upload_sample_data

if os.environ.get("RUN_EVAL", "0").strip().lower() not in {"1", "true", "yes"}:
    pytest.skip(
        "Language-bleed eval is opt-in. Set RUN_EVAL=1 to run tests/test_language_bleed.py.",
        allow_module_level=True,
    )

CRASH_BLEED_TERMS = [
    "REPORT FROM CRASH",
    "Crash dataset_id",
    "crash specialist",
    "accident",
    "severity",
]

_sales_uploaded = False


@pytest.fixture(scope="module", autouse=True)
def upload_sales(http_client, clean_session):
    """Upload sales data once for this module."""
    global _sales_uploaded
    if not _sales_uploaded:
        try:
            upload_sample_data(http_client, clean_session, "sales_50.csv")
            _sales_uploaded = True
        except Exception as e:
            pytest.skip(f"Could not upload sales data: {e}")


GENERIC_QUESTIONS = [
    "How many records total?",
    "What is the distribution by category?",
    "Show the trend of amount over time",
    "What is the average amount?",
    "Show me the top 5 products by quantity",
    "Summarize this dataset",
]


@pytest.mark.parametrize("question", GENERIC_QUESTIONS)
def test_no_crash_bleed_in_sales(http_client, clean_session, question):
    result = send_chat(http_client, clean_session, question)
    text = result.get("responseText", "")

    for term in CRASH_BLEED_TERMS:
        assert term.lower() not in text.lower(), (
            f"Crash bleed '{term}' found in sales response for: {question}\n"
            f"Response excerpt: {text[:500]}"
        )
