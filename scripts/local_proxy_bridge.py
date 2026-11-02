"""把容器可达的 TCP 端口转发到宿主机上的 HTTP 代理。

WSL 里 Windows 代理常只监听 127.0.0.1，Docker 容器访问不到。这个脚本在
WSL 侧监听 0.0.0.0:<listen_port>，把连接转发到 127.0.0.1:<target_port>；
容器通过 host.docker.internal 或 WSL IP 使用它作为 HTTP_PROXY。

用法:
    python3 scripts/local_proxy_bridge.py \
        --listen 0.0.0.0:17892 --target 127.0.0.1:17891
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        with contextlib.suppress(Exception):
            writer.close()


async def _handle(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    target_host: str,
    target_port: int,
) -> None:
    try:
        upstream_reader, upstream_writer = await asyncio.open_connection(
            target_host, target_port
        )
    except OSError:
        client_writer.close()
        return
    await asyncio.gather(
        _pipe(client_reader, upstream_writer),
        _pipe(upstream_reader, client_writer),
    )


def _parse_host_port(value: str) -> tuple[str, int]:
    host, sep, port = value.rpartition(":")
    if not sep or not host:
        raise argparse.ArgumentTypeError(f"expected host:port, got {value!r}")
    return host, int(port)


async def _serve(listen: tuple[str, int], target: tuple[str, int]) -> None:
    server = await asyncio.start_server(
        lambda r, w: _handle(r, w, target[0], target[1]),
        host=listen[0],
        port=listen[1],
    )
    addrs = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    print(f"proxy bridge listening on {addrs}, forwarding to {target[0]}:{target[1]}")
    async with server:
        await server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen", type=_parse_host_port, default=("0.0.0.0", 17892))
    parser.add_argument("--target", type=_parse_host_port, default=("127.0.0.1", 17891))
    args = parser.parse_args(argv)
    try:
        asyncio.run(_serve(args.listen, args.target))
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
