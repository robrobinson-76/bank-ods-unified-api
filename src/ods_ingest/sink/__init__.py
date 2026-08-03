"""Generic Kafka→Mongo sink.

One consumer lands every raw topic into its raw-tier collection. It knows only
what the registry tells it — which model a topic carries, and that model's
natural key — so adding a feed is a TopicSpec row plus a model, never a new
loader. Everything it writes is validated against the same Pydantic model the
ODS serves from, so "what was landed" and "what is served" cannot drift.
"""
