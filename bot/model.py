import hikari
import dataclasses
import miru
import aiosqlite
import os

@dataclasses.dataclass
class Model:
    miru_client: miru.Client
    db: aiosqlite.Connection | None

    def __init__(self, miru_client) -> None:
        self.miru_client = miru_client
        self.db = None

    async def on_start(self, _: hikari.StartedEvent) -> None:

        """
        This function is called when your bot starts. This is a good place to open a
        connection to a database, aiohttp client, or similar.
        """
        ...
        print(f"path: {os.getcwd()}")
        self.db = await aiosqlite.connect("temp_xp.db")
        print(self.db)

    async def on_stop(self, _: hikari.StoppedEvent) -> None:
        """
        This function is called when your bot stops. This is a good place to put
        cleanup functions for the model class.
        """
        ...
        await self.db.close()
