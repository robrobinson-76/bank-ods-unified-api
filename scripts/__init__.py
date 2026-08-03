"""Operational scripts.

A package so the ingestion tests can import the data generators directly
(tests/ingest/test_e2e_file.py drives scripts.generate_custody_file rather than
duplicating the file format). Each module remains runnable as a plain script:
`python scripts/seed_data.py`.
"""
