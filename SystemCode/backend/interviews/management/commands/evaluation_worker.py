"""Run one bounded scoring worker independently of ASGI/browser lifetime."""

import asyncio

from django.core.management.base import BaseCommand

from interviews.evaluation_worker import process_one


class Command(BaseCommand):
    help = "Process durable evaluation jobs; use a separate terminal/service from runserver."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--concurrency", type=int, choices=range(1, 5), default=2)

    def handle(self, *args, **options):
        async def serve():
            while True:
                worked = await process_one()
                if options["once"]:
                    return
                if not worked:
                    await asyncio.sleep(2)

        try:

            async def main():
                await asyncio.gather(
                    *(serve() for _ in range(1 if options["once"] else options["concurrency"]))
                )

            asyncio.run(main())
        except KeyboardInterrupt:
            pass
