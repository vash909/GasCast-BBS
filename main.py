from __future__ import annotations

import argparse
import asyncio
import logging
import tomllib
from pathlib import Path

from bbs.server import BBSServer


def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GasCast colorful Linux Telnet BBS")
    parser.add_argument("--host", default=None, help="Bind host (default from config or 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None, help="Telnet port (default from config or 2323)")
    parser.add_argument("--data-dir", default=None, help="SQLite data directory")
    parser.add_argument("--config", default="config.toml", help="TOML config file")
    parser.add_argument("--aprs-enable", action="store_true", help="Enable APRS RF bridge")
    parser.add_argument("--aprs-disable", action="store_true", help="Disable APRS RF bridge")
    parser.add_argument("--aprs-callsign", default=None, help="APRS BBS callsign (e.g. IU1BOT-10)")
    parser.add_argument("--aprs-server", default=None, help="APRS-IS server host")
    parser.add_argument("--aprs-port", type=int, default=None, help="APRS-IS server port")
    parser.add_argument("--aprs-filter", default=None, help="APRS javAPRFilter")
    parser.add_argument("--aprs-passcode", type=int, default=None, help="APRS-IS passcode")
    parser.add_argument("--aprs-object-name", default=None, help="APRS object name")
    parser.add_argument(
        "--aprs-lat",
        default=None,
        help="APRS latitude (DDMM.mmN/S) or decimal degrees (e.g. 44.3107N)",
    )
    parser.add_argument(
        "--aprs-lon",
        default=None,
        help="APRS longitude (DDDMM.mmE/W) or decimal degrees (e.g. 9.3320E)",
    )
    parser.add_argument("--aprs-comment", default=None, help="APRS object comment")
    parser.add_argument("--aprs-position-interval", type=int, default=None, help="APRS-IS position packet interval in seconds")
    parser.add_argument("--aprs-object-interval", type=int, default=None, help="APRS object beacon interval in seconds")
    parser.add_argument("--aprs-symbol-table", default=None, help="APRS object symbol table")
    parser.add_argument("--aprs-symbol-code", default=None, help="APRS object symbol code")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logs")
    return parser


async def amain() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = load_config(Path(args.config))
    server_cfg = cfg.get("server", {})
    aprs_cfg = dict(cfg.get("aprs", {}))

    host = args.host if args.host is not None else server_cfg.get("host", "0.0.0.0")
    port = args.port if args.port is not None else int(server_cfg.get("port", 2323))
    data_dir = args.data_dir if args.data_dir is not None else server_cfg.get("data_dir", "./data")

    if args.aprs_enable:
        aprs_cfg["enabled"] = True
    if args.aprs_disable:
        aprs_cfg["enabled"] = False

    overrides = {
        "callsign": args.aprs_callsign,
        "server": args.aprs_server,
        "port": args.aprs_port,
        "filter": args.aprs_filter,
        "passcode": args.aprs_passcode,
        "object_name": args.aprs_object_name,
        "object_lat": args.aprs_lat,
        "object_lon": args.aprs_lon,
        "object_comment": args.aprs_comment,
        "position_interval": args.aprs_position_interval,
        "object_interval": args.aprs_object_interval,
        "object_symbol_table": args.aprs_symbol_table,
        "object_symbol_code": args.aprs_symbol_code,
    }
    for key, value in overrides.items():
        if value is not None:
            aprs_cfg[key] = value

    server = BBSServer(host=host, port=port, data_dir=data_dir, aprs_config=aprs_cfg)
    await server.run()


if __name__ == "__main__":
    asyncio.run(amain())
