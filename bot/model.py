import hikari
import dataclasses
import miru
import aiosqlite
import os

@dataclasses.dataclass
class Model:
    bot: hikari.GatewayBot
    miru_client: miru.Client
    main_db: aiosqlite.Connection | None
    test_db: aiosqlite.Connection | None

    def __init__(self, bot, miru_client) -> None:
        self.bot = bot
        self.miru_client = miru_client
        self.db = None

    async def on_start(self, _: hikari.StartedEvent) -> None:

        """
        This function is called when your bot starts. This is a good place to open a
        connection to a database, aiohttp client, or similar.
        """
        ...
        self.db = await aiosqlite.connect("bot/data/xp.db")
        print(self.db)

    async def on_stop(self, _: hikari.StoppedEvent) -> None:
        """
        This function is called when your bot stops. This is a good place to put
        cleanup functions for the model class.
        """
        ...
        if self.db is not None:
            await self.db.close()
