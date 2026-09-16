"""Run with python -m src.services.autochat --config FILE --data-dir DIRECTORY."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
import yaml

from src.llm_core import Gateway, ModelSpec
from .engine import Engine
from .store import Store
from .transport import RpcGateway, RpcPlatform
from .types import Clock, ManualClock
from .types import PROTOCOL_VERSION
from .config import ConfigFile


async def run(args):
    configuration = ConfigFile(args.config)
    settings, rpc, log_level = configuration.read()
    logging.basicConfig(level=log_level, stream=sys.stderr)
    root = Path(args.data_dir).resolve()
    production = (Path(__file__).resolve().parents[3] / "data" / "chat" / "autochat").resolve()
    if args.control_stdio and (root == production or production in root.parents):
        raise ValueError(
            "Controlled runs require an isolated directory outside production autochat data"
        )
    clock = ManualClock(args.start_time) if args.control_stdio else Clock()
    store = Store(root, exclusive=True)
    platform = RpcPlatform(rpc['url'], rpc['token'], rpc['consumer_id'])
    models = (
        yaml.safe_load(Path(args.models_file).read_text(encoding='utf-8'))['models']
        if args.models_file
        else None
    )
    gateway = (
        Gateway({key: ModelSpec(**value) for key, value in models.items()})
        if models
        else RpcGateway(platform)
    )
    if isinstance(gateway, RpcGateway):
        await gateway.describe(
            [
                settings.model,
                settings.summary_model,
                settings.embedding_model,
                settings.vision_model,
                *settings.fallback_models,
            ]
        )
    engine = Engine(store, gateway, platform, settings, clock)
    platform.management_handler = engine.manage_memory
    cursor = store.get("platform_cursor", 0)

    async def step(now=None):
        nonlocal cursor
        try:
            changed = configuration.read()
            if changed:
                next_settings, next_rpc, level = changed
                if next_rpc != rpc:
                    raise ValueError('rpc: transport changes require restart')
                if isinstance(gateway, RpcGateway):
                    await gateway.describe(
                        [
                            next_settings.model,
                            next_settings.summary_model,
                            next_settings.embedding_model,
                            next_settings.vision_model,
                            *next_settings.fallback_models,
                        ]
                    )
                else:
                    for name in (
                        next_settings.model,
                        next_settings.summary_model,
                        next_settings.embedding_model,
                        next_settings.vision_model,
                        *next_settings.fallback_models,
                    ):
                        if name and name not in gateway.models:
                            raise ValueError('chat.llm: unknown model alias ' + name)
                engine.update_settings(next_settings)
                logging.getLogger().setLevel(level)
                print(json.dumps({'type': 'config_updated'}), file=sys.stderr, flush=True)
        except Exception as exc:
            print(
                json.dumps({'type': 'config_error', 'error': str(exc)}, ensure_ascii=False),
                file=sys.stderr,
                flush=True,
            )
        if now is not None:
            clock.advance(now)
        page = await platform.poll(cursor)
        if page.get("protocol_version") != PROTOCOL_VERSION:
            raise ValueError("Incompatible platform protocol")
        engine.enabled = page.get("enabled", {})
        for value in page.get("events", []):
            await engine.ingest(value)
            cursor = max(cursor, int(value["seq"]))
            store.set("platform_cursor", cursor)
        await platform.ack(cursor)
        await engine.tick()
        await asyncio.sleep(0.02)

    try:
        if args.control_stdio:
            print(json.dumps({"type": "ready", "protocol_version": PROTOCOL_VERSION}), flush=True)
            while True:
                line = await asyncio.to_thread(sys.stdin.readline)
                if not line:
                    break
                request = json.loads(line)
                try:
                    method = request.get("method")
                    if method == "advance":
                        await step(request["time"])
                        value = engine.snapshot()
                    elif method == "snapshot":
                        value = engine.snapshot()
                    elif method == "trace":
                        value = store.traces(request.get("after", 0))
                    elif method == "stop":
                        print(
                            json.dumps({"id": request.get("id"), "result": {"stopped": True}}),
                            flush=True,
                        )
                        break
                    else:
                        raise ValueError("Unknown control method")
                    print(
                        json.dumps({"id": request.get("id"), "result": value}, ensure_ascii=False),
                        flush=True,
                    )
                except Exception as exc:
                    print(
                        json.dumps(
                            {"id": request.get("id"), "error": str(exc)}, ensure_ascii=False
                        ),
                        flush=True,
                    )
        else:
            while True:
                try:
                    await step()
                except Exception as exc:
                    print(
                        json.dumps({"type": "platform_error", "error": type(exc).__name__}),
                        file=sys.stderr,
                        flush=True,
                    )
                await clock.sleep(1)
    finally:
        await engine.close()
        await platform.close()
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default='config/chat/autochat.yaml')
    parser.add_argument("--data-dir", default='data/chat/autochat/engine')
    parser.add_argument("--control-stdio", action="store_true")
    parser.add_argument(
        '--models-file', help='Standalone model descriptors; absent means authenticated Luna RPC'
    )
    parser.add_argument(
        '--start-time',
        type=float,
        default=0,
        help='Initial logical time for external controlled runs',
    )
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
