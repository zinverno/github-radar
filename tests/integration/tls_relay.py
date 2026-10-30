"""TLS-transparent relay with explicit barriers, owned by this local test process."""

import asyncio


class Link:
    def __init__(self, client_reader, client_writer, server_reader, server_writer):
        self.client_reader = client_reader
        self.client_writer = client_writer
        self.server_reader = server_reader
        self.server_writer = server_writer
        self.direction = None
        self.held = asyncio.Event()
        self.release = asyncio.Event()
        self.client_eof = asyncio.Event()
        self.retain_server = False
        self.tasks = []

    def arm(self, direction="down", *, retain_server=True):
        assert self.direction is None
        self.direction = direction
        self.retain_server = retain_server

    async def pump(self, reader, writer, direction):
        try:
            while data := await reader.read(65536):
                if (
                    direction == "up"
                    and self.direction == "down"
                    and self.retain_server
                    and self.held.is_set()
                ):
                    # After withholding a reply, also drop later client traffic (including
                    # PQfinish's Terminate packet), while still observing client EOF.
                    continue
                if self.direction == direction:
                    self.held.set()
                    await self.release.wait()
                writer.write(data)
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            if direction == "up":
                self.client_eof.set()
            if not self.retain_server:
                writer.close()

    def start(self):
        self.tasks = [
            asyncio.create_task(self.pump(self.client_reader, self.server_writer, "up")),
            asyncio.create_task(self.pump(self.server_reader, self.client_writer, "down")),
        ]

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        self.client_writer.close()
        self.server_writer.close()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await asyncio.gather(
            self.client_writer.wait_closed(),
            self.server_writer.wait_closed(),
            return_exceptions=True,
        )


class Relay:
    def __init__(self, target_port):
        self.target_port = target_port
        self.links = []

    async def accept(self, reader, writer):
        remote_reader, remote_writer = await asyncio.open_connection("127.0.0.1", self.target_port)
        link = Link(reader, writer, remote_reader, remote_writer)
        self.links.append(link)
        link.start()

    async def __aenter__(self):
        self.server = await asyncio.start_server(self.accept, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *args):
        self.server.close()
        for link in self.links:
            await link.stop()
        await self.server.wait_closed()
