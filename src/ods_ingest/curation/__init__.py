"""Curation — raw topic records to curated semantic-tier documents.

Every judgement call in the pipeline lives here: decoding wire formats,
resolving source keys to ODS identifiers, mapping source code lists to the
domain enums, and deciding what a source delete means. Adapters stay
mechanical so that all of this is replayable against landed raw data.

Curation consumers subscribe to the raw TOPICS (not the raw collections), each
in its own consumer group, so landing and curation fail and replay
independently.
"""
