"""Run the web app: python -m webapp [--host 127.0.0.1] [--port 8000]"""

from __future__ import annotations

import argparse
import os

os.environ.setdefault('LOKY_MAX_CPU_COUNT', '1')

import uvicorn  # noqa: E402

from webapp.server import create_app  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description='Hệ thống dự báo thời gian chờ khám - web app')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
