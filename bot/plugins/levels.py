import random
import os
import asyncio
from collections import Counter
import json

import crescent
import hikari
import miru
import aiosqlite

from bot.pprintify import pprintify
from bot.model import Model


plugin = crescent.Plugin[hikari.GatewayBot, Model]()

# currently all xp types are in one table
# this will likely change later to one per table
# this array will then refer to table names not column names

xp_types = [
    "alltimexp",
    "monthlyxp",
    "weeklyxp",
    "dailyxp"
]
aiosqlite.register_adapter(hikari.Snowflake, lambda sf: int(sf))

# view with crescent context passed for additional utility
class ContextView(miru.View):
    def __init__(self, ctx: crescent.Context, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.crescent_ctx = ctx


# TeaView is a vestigal class from another bot
# i keep it around in case something inside is useful later
# test_hook is from there too

# view for tea games (teabot)
class TeaView(ContextView):
    def __init__(self, ctx: crescent.Context, *args, **kwargs) -> None:
        super().__init__(ctx, *args, **kwargs)
        self.host = self.crescent_ctx.interaction.user.id
        self.players = set()

    # define a new TextSelect menu with two options (vestigal template that might be useful)
    '''@miru.text_select(
        placeholder="Select me!",
        options=[
            miru.SelectOption(label="Option 1"),
            miru.SelectOption(label="Option 2"),
        ],
    )
    async def basic_select(self, ctx: miru.ViewContext, select: miru.TextSelect) -> None:
        await ctx.respond(f"You've chosen {select.values[0]}!")'''

    # join tea
    @miru.button(
        custom_id=f"join_{os.urandom(16).hex()}",
        label="Players: 0",
        style=hikari.ButtonStyle.PRIMARY
    )
    async def join_button(self, ctx: miru.ViewContext, button: miru.Button) -> None:
        was_in_players = ctx.interaction.user.id in self.players
        if was_in_players:
            self.players.remove(ctx.interaction.user.id)
        else:
            self.players.add(ctx.interaction.user.id)
        button.label = f"Players: {len(self.players)}"
        await ctx.edit_response(components=self)
        await ctx.respond("Left!" if was_in_players else "Joined!", flags=hikari.MessageFlag.EPHEMERAL)

    # cancel tea
    @miru.button(
        custom_id=f"stop_{os.urandom(16).hex()}",
        label="Abort! (Host)",
        style=hikari.ButtonStyle.DANGER
    )
    async def stop_button(self, ctx: miru.ViewContext, button: miru.Button) -> None:
        await ctx.respond("The host has aborted.")
        self.stop()

    async def view_check(self, ctx: miru.ViewContext) -> bool:
        if ctx.interaction.custom_id.startswith("stop") and ctx.interaction.user.id != self.host:
            return False
        return True

    # TODO: create followup to begin tea
    # pass self.players and maybe self.crescent_ctx
    # select from threes, listen for messages from ids in self.players
    # expire after 10s (default for now)
    # recurse if score threshold is not reached

    # reminder: multiple games in one channel, but not multiple games for one user
    # so, disable multiple games in one channel for now, because you can't fully scope to ctx
    # (you have to check if user is already a player in an ongoing game in channel)
    async def on_timeout(self) -> None:
        # if no interactions, no ctx available to respond with...
        if self.message is not None and len(self.players) > 0:
            await self.message.respond(f"It seems {len(self.players)} player(s) were interested.")
            return None
        # ...thus, moderate scuff
        await self.crescent_ctx.respond("Nobody joined? How drab...")


async def init_db() -> None:
    async with plugin.model.db.cursor() as cur:
        await cur.execute("""
            DROP TABLE IF EXISTS levels
        """)
        await cur.execute(f"""
            CREATE TABLE levels (
                id INTEGER PRIMARY KEY,
                {' INTEGER,'.join(xp_types)} INTEGER
            );
        """)

        await plugin.model.db.commit()
        print(await cur.fetchall())


async def get_xp_db(id: hikari.Snowflake, xp: int, xp_type: str="alltimexp") -> None:
    assert xp_type in xp_types
    async with plugin.model.db.cursor() as cur:
        return await cur.execute(f"""
            SELECT {xp_type} FROM levels WHERE id = ?)
        """, (id,)).fetchone()


async def set_xp_db(id: hikari.Snowflake, xp: int, xp_type: str="alltimexp") -> None:
    assert xp_type in xp_types
    async with plugin.model.db.cursor() as cur:
        await cur.execute(f"""
            INSERT INTO levels(id, {', '.join(xp_types)}) 
            SELECT ?, {', '.join(['0'] * len(xp_types))}
            WHERE NOT EXISTS(SELECT 1 FROM levels WHERE id = ?)
        """, (id, id))
        await cur.execute(f"""
            UPDATE levels
            SET {xp_type} = ?
            WHERE id = ?
        """, (xp, id))

        await plugin.model.db.commit()
        data = await cur.execute("""
            SELECT * FROM levels
        """)
        print(await data.fetchall())


async def add_xp_db(id: hikari.Snowflake, xp: int, xp_type: str="alltimexp") -> None:
    assert xp_type in xp_types
    async with plugin.model.db.cursor() as cur:
        await cur.execute(f"""
            INSERT INTO levels(id, {', '.join(xp_types)}) 
            SELECT ?, {', '.join(['0'] * len(xp_types))}
            WHERE NOT EXISTS(SELECT 1 FROM levels WHERE id = ?)
        """, (id, id))
        await cur.execute(f"""
            UPDATE levels
            SET {xp_type} = {xp_type} + ?
            WHERE id = ?
        """, (xp, id))

        await plugin.model.db.commit()
        data = await cur.execute("""
            SELECT * FROM levels
        """)
        print(await data.fetchall())


# on_msg
@plugin.include
@crescent.event
async def on_message_create(event: hikari.MessageCreateEvent) -> None:
    user = event.message.author
    if user.is_bot:
        return
    
    # the xp amount should be handled in its own function that is then called here
    xp = random.randint(2, 42)
    for xp_type in xp_types:
        await add_xp_db(user.id, xp, xp_type)

    await event.message.respond("This Pro-flop is Pissing me off...")


# ping
@plugin.include
@crescent.command(
    name="ping",
    description="ping pong"
)
async def ping(ctx: crescent.Context) -> None:
    view = TeaView(ctx, timeout=3.0)
    await ctx.respond("Pong!", components=view) 
    ctx.client.model.miru_client.start_view(view)


@plugin.include
@crescent.command(
    name="xp",
    description="check xp of user"
)
async def check_xp(ctx: crescent.Context, user: hikari.User | None=None) -> None:
    user = user or ctx.user
    xp = await get_xp_db(user.id)
    print(xp)

    if not xp:
        await ctx.respond(f"{user.username}, you don't have any xp yet.")
    else:
        await ctx.respond(f"{user.username}, you have {xp} xp.")


@plugin.include
@crescent.command(
    name="setxp",
    description="set xp of user"
)
async def set_xp(ctx: crescent.Context, user: hikari.User, xp: int) -> None:
    await set_xp_db(user.id, xp)
    await ctx.respond(f"Set xp of {user.username} to {xp}.")


# (admin) reset guild xp (ADD CONFIRMATION!!!!!11!!1!)
@plugin.include
@crescent.command(
    name="init",
    description="removes all level data & creates a new level storage",
    default_member_permissions=hikari.Permissions.ADMINISTRATOR
)
async def init_guild_xp(ctx: crescent.Context) -> None:
    await init_db()
    await ctx.respond("Blank level storage created.")


# hook test (teabot)
async def test_hook(ctx: crescent.Context) -> None:
    star_msg = await ctx.respond("Star        walker")
    await asyncio.sleep(5)


@plugin.include
@crescent.hook(test_hook)
@crescent.command
async def tea(ctx: crescent.Context) -> None:
    await ctx.respond("piss tea")