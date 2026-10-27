from core.paper_ingestor import extract_metadata


def test_extract_metadata_from_markdown():
    text = """# Smart Kitchen Analytics

## Abstract
This study develops a machine learning approach to predict food waste
and improve kitchen resource management.

## Introduction
Food waste is an important challenge.
"""
    metadata = extract_metadata(text)

    assert metadata.title == "Smart Kitchen Analytics"
    assert metadata.abstract is not None
    assert "machine learning approach" in metadata.abstract


def test_extract_metadata_without_abstract_heading():
    text = """# Food Waste Prediction

This research studies food waste patterns using machine learning methods.
"""
    metadata = extract_metadata(text)

    assert metadata.title == "Food Waste Prediction"
    assert metadata.abstract is not None
    assert "food waste patterns" in metadata.abstract


def test_extract_metadata_empty_text():
    metadata = extract_metadata("")

    assert metadata.title == "No Title Found"
    assert metadata.abstract == "No abstract available."
