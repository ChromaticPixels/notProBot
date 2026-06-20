import random
import os
import asyncio
import json
import math
import typing
import datetime
from collections import Counter
from collections.abc import Iterable, Callable

import crescent
import hikari
import miru
import aiosqlite
from crescent.ext import cooldowns
from miru.ext import menu
from sqlite3 import Row

from bot.pprintify import pprintify
from bot.model import Model

plugin = crescent.Plugin[hikari.GatewayBot, Model]()

aiosqlite.register_adapter(hikari.Snowflake, lambda sf: int(sf))

main_guild_id = int(os.environ["MAIN_GUILD_ID"])
test_guild_id = int(os.environ["TEST_GUILD_ID"])

with open("bot/data/main/temp_settings.json", "r") as f:
    main_settings: dict = json.load(f)

with open("bot/data/test/temp_settings.json", "r") as f:
    test_settings: dict = json.load(f)

# ids get added/removed on message to control xp gain per cooldown
ids_on_cooldoWn = set()

# currently all xp times are in one table
# this will likely change later to one per table
# this array will then refer to table names not column names
all_xp_times = (
    "alltimexp",
    "monthlyxp",
    "weeklyxp",
    "dailyxp"
)

all_xp_times_pretty = (
    "All Time",
    "Monthly",
    "Weekly",
    "Daily"
)

get_db: Callable = lambda id: plugin.model.main_db if id == main_guild_id else plugin.model.test_db

get_settings: Callable[[int], dict] = lambda id: main_settings if id == main_guild_id else test_settings

ceildiv: Callable[[int, int], int] = lambda a, b: -(a // -b)

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

    # ping from teabot as example for future me
    '''
    @plugin.include
    @crescent.command(
        name="ping",
        description="ping pong"
    )
    async def ping(ctx: crescent.Context) -> None:
        view = TeaView(ctx, timeout=3.0)
        await ctx.respond("Pong!", components=view) 
        ctx.client.model.miru_client.start_view(view)
    '''

class PreviousButton(menu.ScreenButton):
    def __init__(self) -> None:
        super().__init__(label="Previous", style=hikari.ButtonStyle.SECONDARY)

    async def callback(self, ctx: miru.ViewContext) -> None:
        await self.menu.pop()

class NextLeaderboardButton(menu.ScreenButton):
    def __init__(self, page=1, xp_time: str="alltimexp") -> None:
        super().__init__(label="Next", style=hikari.ButtonStyle.SECONDARY)
        self.page = page
        self.xp_time = xp_time

    async def callback(self, ctx: miru.ViewContext) -> None:
        await self.menu.push(LeaderboardScreen(self.menu, self.page + 1, self.xp_time))

class LeaderboardScreen(menu.Screen):
    def __init__(self, menu: menu.Menu, page=1, xp_time: str="alltimexp") -> None:
        super().__init__(menu)
        self.page = page
        self.xp_time = xp_time

    async def build_content(self) -> menu.ScreenContent:
        if self.menu.message is None:
            raise hikari.ComponentStateConflictError("Menu is unbound.")
        guild_id = self.menu.message.guild_id
        if guild_id is None:
            raise hikari.ComponentStateConflictError("No guild id found.")
        
        print(self.xp_time)
        info = ""
        for i, (id, xp) in enumerate(await get_xp_db_bulk(guild_id, self.page, self.xp_time)):
            user = await plugin.model.bot.rest.fetch_user(id)
            info += f"{i + 1}. {user.mention} · Level {await get_lvl(xp)} · {xp} XP\n"

        content = hikari.Embed(
            title=f"Leaderboard{': '
                + all_xp_times_pretty[all_xp_times.index(self.xp_time)]
            if self.xp_time != 'alltimexp' else ''}",
            description=info,
            color=hikari.Color(0x000000)
        )
        content.set_footer(f"Page {self.page}/{ceildiv(await get_size_xp_db(guild_id, self.xp_time), 10)}")

        if self.page > 1:
            self.menu.add_item(PreviousButton())
        if self.page < ceildiv(await get_size_xp_db(guild_id, self.xp_time), 10):
            self.menu.add_item(NextLeaderboardButton(page=self.page, xp_time=self.xp_time))
        
        return menu.ScreenContent(embed=content,)


async def print_db(cur: aiosqlite.Cursor) -> None:
    data = await cur.execute("""
        SELECT * FROM levels
    """)
    print(await data.fetchall())


async def init_db(g_id: int) -> None:
    db = get_db(g_id)
    assert db is not None
    async with db.cursor() as cur:
        await cur.execute("""
            DROP TABLE IF EXISTS levels
        """)
        await cur.execute(f"""
            CREATE TABLE levels (
                id INTEGER PRIMARY KEY,
                {' INTEGER,'.join(all_xp_times)} INTEGER
            );
        """)

        await db.commit()
        await print_db(cur)

async def get_size_xp_db(g_id: int, xp_time: str="alltimexp") -> int:
    assert xp_time in all_xp_times
    db = get_db(g_id)
    assert db is not None
    async with db.cursor() as cur:
        data = await (await cur.execute(f"""
            SELECT COUNT(*) FROM levels
            WHERE {xp_time} > 0
        """)).fetchone()
    return data[0] if data else 0


async def get_xp_db(g_id: int, u_id: hikari.Snowflake, xp_time: str="alltimexp") -> int:
    assert xp_time in all_xp_times
    db = get_db(g_id)
    assert db is not None
    async with db.cursor() as cur:
        data = await (await cur.execute(f"""
            SELECT {xp_time} FROM levels
            WHERE id = ?
        """, (u_id,))).fetchone()
    return data[0] if data else 0


async def get_xp_db_bulk(g_id: int, page: int, xp_time: str="alltimexp") -> Iterable[Row]:
    db = get_db(g_id)
    assert db is not None
    async with db.cursor() as cur:
        data = await (await cur.execute(f"""
            SELECT id, {xp_time} FROM levels
            WHERE {xp_time} > 0
            ORDER BY {xp_time} DESC
            LIMIT 10 OFFSET 10 * ?
        """, (page - 1,))).fetchall()
    return data


async def set_xp_db(g_id: int, u_id: hikari.Snowflake, xp: int, xp_time: str="alltimexp") -> None:
    assert xp_time in all_xp_times
    db = get_db(g_id)
    assert db is not None
    async with db.cursor() as cur:
        await cur.execute(f"""
            INSERT INTO levels(id, {', '.join(all_xp_times)}) 
            SELECT ?, {', '.join(['0'] * len(all_xp_times))}
            WHERE NOT EXISTS(SELECT 1 FROM levels WHERE id = ?)
        """, (u_id, u_id))
        await cur.execute(f"""
            UPDATE levels
            SET {xp_time} = ?
            WHERE id = ?
        """, (xp, u_id))

        await db.commit()
        await print_db(cur)


async def reset_xp_db(g_id: int, u_id: hikari.Snowflake, xp_time: str="alltimexp") -> None:
    assert xp_time in all_xp_times
    db = get_db(g_id)
    assert db is not None
    async with db.cursor() as cur:
        await cur.execute(f"""
            DELETE FROM levels
            WHERE id = ?
        """, (u_id,))

        await db.commit()
        await print_db(cur)


async def add_xp_db(g_id: int, u_id: hikari.Snowflake, xp: int, xp_time: str="alltimexp") -> None:
    for xp_time in all_xp_times:
        await set_xp_db(g_id, u_id, (await get_xp_db(g_id, u_id, xp_time)) + xp, xp_time)


async def remove_xp_db(g_id: int, u_id: hikari.Snowflake, xp: int, xp_time: str="alltimexp") -> None:
    for xp_time in all_xp_times:
        await set_xp_db(g_id, u_id, max((await get_xp_db(g_id, u_id, xp_time)) - xp, 0), xp_time)


async def get_next_lvl_xp(lvl: int) -> int:
    # default is `max(floor(208 / 3 * {level} - 104 / 3) + {xp}, 1)`
    # not going to support a lack of {xp}
    # so just `max(floor(208 / 3 * {level} - 104 / 3), 1)` as default
    # and non-default later
    return max(math.floor(208 / 3 * lvl - 104 / 3), 1)


async def get_lvl(xp: int) -> int:
    lvl = 0
    sum = await get_next_lvl_xp(0)
    while sum <= xp:
        lvl += 1
        sum += await get_next_lvl_xp(lvl)
    return lvl


async def manage_cooldown_hook(event: hikari.MessageCreateEvent) -> None:
    user = event.message.author
    if user.id in ids_on_cooldoWn:
        return
    
    guild_id = event.message.guild_id
    if guild_id is None:
        raise hikari.ComponentStateConflictError("No guild id found.")
    
    ids_on_cooldoWn.add(user.id)
    await asyncio.sleep(get_settings(int(guild_id))["Calculation"]["Cooldown"])
    ids_on_cooldoWn.remove(user.id)


async def handle_lvl_increase(guild_id: int, user: hikari.User, lvl: int, app: hikari.RESTAware) -> None:
    if get_settings(guild_id)["Level Up Messages"]["Enabled"]:
        await app.rest.create_message(
            get_settings(guild_id)["Level Up Messages"]["Channel"],
            f"{user.username} just leveled up to level {lvl}!"
        )
    
    role_ids = (await app.rest.fetch_member(guild_id,user.id)).role_ids
    
    for role_id, role_lvl in get_settings(guild_id)["Level Roles"].items():
        if role_lvl <= lvl and role_id not in role_ids:
            await app.rest.add_role_to_member(guild_id, user, role_id)

async def handle_lvl_decrease(guild_id: int, user: hikari.User, lvl: int, app: hikari.RESTAware) -> None:
    role_ids = (await app.rest.fetch_member(guild_id,user.id)).role_ids
    
    for role_id, role_lvl in get_settings(guild_id)["Level Roles"].items():
        if role_lvl > lvl and role_id in role_ids:
            await app.rest.remove_role_from_member(guild_id, user, role_id)

async def handle_xp_update(guild_id: int, user: hikari.User, xp: int, app: hikari.RESTAware) -> None:
    new_xp = await get_xp_db(guild_id, user.id)
    new_lvl = await get_lvl(new_xp)

    old_lvl = await get_lvl(new_xp - xp)

    if new_lvl > old_lvl:
        await handle_lvl_increase(guild_id, user, new_lvl, app)
    
    if new_lvl < old_lvl:
        await handle_lvl_decrease(guild_id, user, new_lvl, app)


async def handle_msg_xp_gain(event: hikari.MessageCreateEvent) -> None:
    user = event.message.author
    if user.id in ids_on_cooldoWn:
        return
    
    guild_id = event.message.guild_id
    if guild_id is None:
        raise hikari.ComponentStateConflictError("No guild id found.")
    
    xp = random.randint(
        get_settings(guild_id)["Calculation"]["Minimum XP"],
        get_settings(guild_id)["Calculation"]["Maximum XP"]
    )

    await add_xp_db(guild_id, user.id, xp)
    await handle_xp_update(guild_id, user, xp, event.app)

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
@crescent.hook(manage_cooldown_hook, after=True)
@crescent.event
async def on_message_create(event: hikari.MessageCreateEvent) -> None:
    if not event.message.author.is_bot:
        await handle_msg_xp_gain(event)


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
        
        guild_id = ctx.guild_id
        if guild_id is None:
            raise hikari.ComponentStateConflictError("No guild id found.")

        xp = await get_xp_db(guild_id, user.id)
        if xp == 0:
            await ctx.respond(f"{user.username}, you don't have any xp yet.")
        else:
            lvl = await get_lvl(xp)
            await ctx.respond(f"{user.username}, you have {xp} xp and are level {lvl}.")


@plugin.include
@crescent.command(
    name="leaderboard",
    description="view top 10 users by xp"
)
class LeaderboardCommand:
    time = crescent.option(
        int, "time period to view xp for",
        default=0,
        choices=[(xp_time, i) for i, xp_time in enumerate(all_xp_times_pretty)]
    )

    async def callback(self, ctx: crescent.Context) -> None:
        miru_client = ctx.client.model.miru_client
        lb_menu = menu.Menu()

        builder = await lb_menu.build_response_async(miru_client, LeaderboardScreen(lb_menu, xp_time=all_xp_times[self.time]))
        await ctx.respond_with_builder(builder)

        miru_client.start_view(lb_menu)


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
        
        guild_id = ctx.guild_id
        if guild_id is None:
            raise hikari.ComponentStateConflictError("No guild id found.")
        
        old_xp = await get_xp_db(guild_id, self.user.id)
        await set_xp_db(guild_id, self.user.id, self.xp)
        await handle_xp_update(guild_id, self.user, self.xp - old_xp, ctx.app)

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
        
        guild_id = ctx.guild_id
        if guild_id is None:
            raise hikari.ComponentStateConflictError("No guild id found.")
        
        old_xp = await get_xp_db(guild_id, self.user.id)
        await add_xp_db(guild_id, self.user.id, self.xp)
        await handle_xp_update(guild_id, self.user, -old_xp, ctx.app)

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
        
        guild_id = ctx.guild_id
        if guild_id is None:
            raise hikari.ComponentStateConflictError("No guild id found.")
        
        await remove_xp_db(guild_id, self.user.id, self.xp)
        await handle_xp_update(guild_id, self.user, -self.xp, ctx.app)

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
        
        guild_id = ctx.guild_id
        if guild_id is None:
            raise hikari.ComponentStateConflictError("No guild id found.")
        
        await reset_xp_db(guild_id, self.user.id)
        
        await ctx.respond(f"Reset xp of {self.user.username}.")


# (admin) reset guild xp (ADD CONFIRMATION!!!!!11!!1!)
@plugin.include
@crescent.command(
    name="init",
    description="removes all level data & creates a new level storage",
    default_member_permissions=hikari.Permissions.ADMINISTRATOR
)
async def init_guild_xp(ctx: crescent.Context) -> None:
    guild_id = ctx.guild_id
    if guild_id is None:
        return
    
    await init_db(guild_id)
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