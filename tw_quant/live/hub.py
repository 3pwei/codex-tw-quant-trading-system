from __future__ import annotations

import asyncio


class BroadcastHub:
    def __init__(self, queue_size: int = 256):
        self.queue_size = queue_size
        self.clients: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self.queue_size)
        self.clients.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self.clients.discard(queue)

    def publish(self, message: dict[str, object]) -> None:
        for queue in tuple(self.clients):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(message)
