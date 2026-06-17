import random
import os
import asyncio
import json
import typing
import datetime
from collections import Counter

import crescent
import hikari
import miru
import aiosqlite
from crescent.ext import cooldowns

from bot.pprintify import pprintify
from bot.model import Model

plugin = crescent.Plugin[hikari.GatewayBot, Model]()

aiosqlite.register_adapter(hikari.Snowflake, lambda sf: int(sf))

with open("bot/data/temp_settings.json", "r") as f:
    settings = json.load(f)

# ids get added/removed on message to control xp gain per cooldown
on_cooldown = set()

# currently all xp types are in one table
# this will likely change later to one per table
# this array will then refer to table names not column names
all_xp_types = (
    "alltimexp",
    "monthlyxp",
    "weeklyxp",
    "dailyxp"
)

# view with crescent context passed for additional utility
class ContextView(miru.View):
    def __init__(self, ctx: crescent.Context, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.crescent_ctx = ctx


# TeaView is a vestigal class from another bot
# i keep it around in case something inside is useful later
# test_hook is from there too
# ContextView also is but it seems more likely usable here eventually

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


async def print_db(cur: aiosqlite.Cursor) -> None:
    data = await cur.execute("""
        SELECT * FROM levels
    """)
    print(await data.fetchall())


async def init_db() -> None:
    assert plugin.model.db is not None
    async with plugin.model.db.cursor() as cur:
        await cur.execute("""
            DROP TABLE IF EXISTS levels
        """)
        await cur.execute(f"""
            CREATE TABLE levels (
                id INTEGER PRIMARY KEY,
                {' INTEGER,'.join(all_xp_types)} INTEGER
            );
        """)

        await plugin.model.db.commit()
        await print_db(cur)


async def get_xp_db(id: hikari.Snowflake, xp_type: str="alltimexp") -> int | None:
    assert xp_type in all_xp_types
    assert plugin.model.db is not None
    async with plugin.model.db.cursor() as cur:
        data = await (await cur.execute(f"""
            SELECT {xp_type} FROM levels
            WHERE id = ?
        """, (id,))).fetchone()
    return data[0] if data else None


async def set_xp_db(id: hikari.Snowflake, xp: int, xp_type: str="alltimexp") -> None:
    assert xp_type in all_xp_types
    assert plugin.model.db is not None
    async with plugin.model.db.cursor() as cur:
        await cur.execute(f"""
            INSERT INTO levels(id, {', '.join(all_xp_types)}) 
            SELECT ?, {', '.join(['0'] * len(all_xp_types))}
            WHERE NOT EXISTS(SELECT 1 FROM levels WHERE id = ?)
        """, (id, id))
        await cur.execute(f"""
            UPDATE levels
            SET {xp_type} = ?
            WHERE id = ?
        """, (xp, id))

        await plugin.model.db.commit()
        await print_db(cur)


async def reset_xp_db(id: hikari.Snowflake, xp_type: str="alltimexp") -> None:
    assert xp_type in all_xp_types
    assert plugin.model.db is not None
    async with plugin.model.db.cursor() as cur:
        await cur.execute(f"""
            DELETE FROM levels
            WHERE id = ?
        """, (id,))

        await plugin.model.db.commit()
        await print_db(cur)


async def add_xp_db(id: hikari.Snowflake, xp: int, xp_type: str="alltimexp") -> None:
    for xp_type in all_xp_types:
        await set_xp_db(id, (await get_xp_db(id, xp_type) or 0) + xp, xp_type)


async def remove_xp_db(id: hikari.Snowflake, xp: int, xp_type: str="alltimexp") -> None:
    for xp_type in all_xp_types:
        await set_xp_db(id, max((await get_xp_db(id, xp_type) or 0) - xp, 0), xp_type)


async def cooldown_hook(event: hikari.MessageCreateEvent) -> None:
    user = event.message.author
    if user.id in on_cooldown:
        return
    
    on_cooldown.add(user.id)
    await asyncio.sleep(settings["Calculation"]["Cooldown"])
    on_cooldown.remove(user.id)


async def handle_msg_xp_gain(event: hikari.MessageCreateEvent) -> None:
    user = event.message.author
    if user.id in on_cooldown:
        return
    
    xp = random.randint(
        settings["Calculation"]["Minimum XP"],
        settings["Calculation"]["Maximum XP"]
    )
    await add_xp_db(user.id, xp)

    # currently for testing
    # possibly make ephemeral as a prod feature?
    # would require user settings but would be useful for admins
    # but i am the only admin who debugs xp gain so meh
    await event.message.respond("This Pro-flop is Pissing me off...")


async def handle_is_bot_xp(id: hikari.Snowflake, ctx: crescent.Context) -> None:
    if id == ctx.application_id:
        await ctx.respond("~~Someday~~ I mean what?")
        return
    await ctx.respond("We bots don't earn xp...")


@plugin.include
@crescent.hook(cooldown_hook, after=True)
@crescent.event
async def on_message_create(event: hikari.MessageCreateEvent) -> None:
    if not event.message.author.is_bot:
        await handle_msg_xp_gain(event)


# this should move out of levels plugin
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
    name="rank",
    description="check rank & xp of user"
)
class CheckXPCommand:
    user = crescent.option(hikari.User, "user to check rank & xp of", default=None)

    async def callback(self, ctx: crescent.Context) -> None:
        user = self.user or ctx.user
        if user.is_bot:
            await handle_is_bot_xp(user.id, ctx)
            return

        xp = await get_xp_db(user.id)
        if not xp:
            await ctx.respond(f"{user.username}, you don't have any xp yet.")
        else:
            await ctx.respond(f"{user.username}, you have {xp} xp.")


xp_group = crescent.Group(name="xp", description="xp management commands")


@plugin.include
@xp_group.child
@crescent.command(
    name="set",
    description="set xp of user"
)
class SetXPCommand:
    user = crescent.option(hikari.User, "user to set xp of")
    xp = crescent.option(int, "xp amount to set")

    async def callback(self, ctx: crescent.Context) -> None:
        if self.user.is_bot:
            await handle_is_bot_xp(self.user.id, ctx)
            return
        await set_xp_db(self.user.id, self.xp)
        await ctx.respond(f"Set xp of {self.user.username} to {self.xp}.")


@plugin.include
@xp_group.child
@crescent.command(
    name="add",
    description="add xp to user"
)
class AddXPCommand:
    user = crescent.option(hikari.User, "user to add xp to")
    xp = crescent.option(int, "xp amount to add")

    async def callback(self, ctx: crescent.Context) -> None:
        if self.user.is_bot:
            await handle_is_bot_xp(self.user.id, ctx)
            return
        await add_xp_db(self.user.id, self.xp)
        await ctx.respond(f"Added {self.xp} xp to {self.user.username}.")


@plugin.include
@xp_group.child
@crescent.command(
    name="remove",
    description="remove xp from user"
)
class RemoveXPCommand:
    user = crescent.option(hikari.User, "user to remove xp from")
    xp = crescent.option(int, "xp amount to remove")

    async def callback(self, ctx: crescent.Context) -> None:
        if self.user.is_bot:
            await handle_is_bot_xp(self.user.id, ctx)
            return
        await remove_xp_db(self.user.id, self.xp)
        await ctx.respond(f"Removed {self.xp} xp from {self.user.username}.")


@plugin.include
@xp_group.child
@crescent.command(
    name="reset",
    description="reset xp of user"
)
class ResetXPCommand:
    user = crescent.option(hikari.User, "user to reset xp of")

    async def callback(self, ctx: crescent.Context) -> None:
        if self.user.is_bot:
            await handle_is_bot_xp(self.user.id, ctx)
            return
        await reset_xp_db(self.user.id)
        await ctx.respond(f"Reset xp of {self.user.username}.")


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