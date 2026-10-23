"""Command line entry point: ``python -m optdesk {feed,pricer,gateway,all}``."""

import argparse
import asyncio
import logging
import signal
import sys
from collections.abc import Sequence

import uvicorn

from optdesk import bus as bus_lib
from optdesk import config, feed, gateway, pricer


async def _run_service(role: str, settings: config.Settings) -> None:
    """Runs a headless service (feed or pricer) until SIGINT/SIGTERM."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    bus = bus_lib.make_bus(settings.bus_url)
    service = (
        feed.FeedService(bus, settings)
        if role == 'feed'
        else pricer.PricerService(bus, settings)
    )
    try:
        await service.run(stop)
    finally:
        await bus.close()


def main(argv: Sequence[str] | None = None) -> int:
    """Parses arguments and runs the requested service role.

    Args:
      argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
      Process exit code.
    """
    parser = argparse.ArgumentParser(prog='optdesk')
    parser.add_argument('role', choices=['feed', 'pricer', 'gateway', 'all'])
    args = parser.parse_args(argv)
    settings = config.Settings.from_env()
    logging.basicConfig(
        level=settings.log_level,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )
    if args.role in ('feed', 'pricer', 'gateway') and (
        settings.bus_url.startswith('memory://')
    ):
        parser.error(
            f'{args.role} needs a shared bus; set OPTDESK_BUS_URL=redis://... '
            "or use the single-process 'all' role."
        )
    if args.role in ('feed', 'pricer'):
        asyncio.run(_run_service(args.role, settings))
        return 0
    app = gateway.create_app(settings, embedded=args.role == 'all')
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        ws_max_size=1 << 16,
        ws_ping_interval=20,
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
