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
        self.main_db = None
        self.test_db = None

    async def on_start(self, _: hikari.StartedEvent) -> None:

        """
        This function is called when your bot starts. This is a good place to open a
        connection to a database, aiohttp client, or similar.
        """
        ...
        self.main_db = await aiosqlite.connect("bot/data/main/temp_xp.db")
        self.test_db = await aiosqlite.connect("bot/data/test/temp_xp.db")
        print(self.main_db)
        print(self.test_db)

    async def on_stop(self, _: hikari.StoppedEvent) -> None:
        """
        This function is called when your bot stops. This is a good place to put
        cleanup functions for the model class.
        """
        ...
        if self.main_db is not None:
            await self.main_db.close()
        if self.test_db is not None:
            await self.test_db.close()
