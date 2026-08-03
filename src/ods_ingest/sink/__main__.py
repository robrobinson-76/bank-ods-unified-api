"""Generic sink entry point.

    python -m ods_ingest.sink            # consume continuously
    python -m ods_ingest.sink --once     # drain the backlog, then exit
    python -m ods_ingest.sink --list     # show topic -> collection routing
"""
from __future__ import annotations

import argparse
import logging
import sys

from ods_ingest import config, state
from ods_ingest.bus.consumer import BatchConsumer
from ods_ingest.sink import mapping, writer


def build_consumer(group_id: str = mapping.SINK_GROUP_ID,
                   topic_names: list[str] | None = None) -> BatchConsumer:
    return BatchConsumer(
        topic_names or mapping.sink_topics(),
        group_id=group_id,
        handler=writer.handle,
        stage="sink",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--group-id", default=mapping.SINK_GROUP_ID)
    parser.add_argument("--idle-timeout", type=float, default=None)
    parser.add_argument("--topics", nargs="+", default=None,
                        help="restrict to these topics (default: every mapped topic). "
                             "Running one sink per feed is how a busy feed is given "
                             "its own consumer group and scaling profile.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=config.LOG_LEVEL, format="%(levelname)s %(name)s: %(message)s")

    if args.list:
        for topic, collection, extractor in mapping.describe():
            print(f"  {topic:34} -> {collection:26} [{extractor}]")
        return 0

    consumer = build_consumer(args.group_id, args.topics)
    if args.once:
        handled = consumer.run_until_idle(args.idle_timeout)
        print(f"landed {handled} record(s); {consumer.records_dead_lettered} dead-lettered")
        consumer.close()
        state.close()
        return 0

    consumer.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
