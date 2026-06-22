import random
import os
import asyncio
import json
import math
from collections.abc import Iterable

import crescent
import hikari
import miru
import aiosqlite
from miru.ext import menu, nav
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


def get_db(id: int) -> aiosqlite.Connection | None:
    return plugin.model.main_db if id == main_guild_id else plugin.model.test_db


def get_settings(id: int) -> dict:
    return main_settings if id == main_guild_id else test_settings


async def get_user_roles(g_id: int, u_id: int, app: hikari.RESTAware) -> list[int]:
    return list(map(int, (await app.rest.fetch_member(g_id, u_id)).role_ids))


def ceildiv(a: int, b: int) -> int:
    return -(a // -b)


def xp_time_is_enabled(guild_id: int, i: int) -> bool:
    return (all_xp_times[i] == "alltimexp"
        or get_settings(guild_id)["Leaderboards"][all_xp_times_pretty[i]])


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

# ids get added/removed on message to control xp gain per cooldown
ids_on_cooldoWn = set()


class PreviousButton(menu.ScreenButton):
    def __init__(self) -> None:
        super().__init__(label="Previous", style=hikari.ButtonStyle.SECONDARY)

    async def callback(self, ctx: miru.ViewContext) -> None:
        await self.menu.pop()


class OriginalCrescentCtxView(miru.View):
    def __init__(self, ctx: crescent.Context, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.original_ctx = ctx


class ConfirmView(OriginalCrescentCtxView):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.result: crescent.HookResult | None = None
    
    @miru.button(label="Confirm", style=hikari.ButtonStyle.SUCCESS)
    async def confirm_button(self, ctx: miru.ViewContext, button: miru.Button) -> None:
        self.result = crescent.HookResult()
        self.stop()
    
    @miru.button(label="Cancel", style=hikari.ButtonStyle.DANGER)
    async def cancel_button(self, ctx: miru.ViewContext, button: miru.Button) -> None:
        self.result = crescent.HookResult(exit=True)
        self.stop()

    async def view_check(self, ctx: miru.ViewContext) -> bool:
        return ctx.user.id == self.original_ctx.user.id


async def print_db(cur: aiosqlite.Cursor) -> None:
    data = await cur.execute("""
        SELECT * FROM levels
    """)
    print(await data.fetchall())


async def init_db(g_id: int) -> None:
    db = get_db(g_id)
    if db is None:
        raise aiosqlite.DatabaseError("No database found.")
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
    if db is None:
        raise aiosqlite.DatabaseError("No database found.")
    async with db.cursor() as cur:
        data = await (await cur.execute(f"""
            SELECT COUNT(*) FROM levels
            WHERE {xp_time} > 0
        """)).fetchone()
    return data[0] if data else 0


async def get_xp_db(g_id: int, u_id: hikari.Snowflake, xp_time: str="alltimexp") -> int:
    assert xp_time in all_xp_times
    db = get_db(g_id)
    if db is None:
        raise aiosqlite.DatabaseError("No database found.")
    async with db.cursor() as cur:
        data = await (await cur.execute(f"""
            SELECT {xp_time} FROM levels
            WHERE id = ?
        """, (u_id,))).fetchone()
    return data[0] if data else 0


async def get_xp_db_bulk(g_id: int, page: int, xp_time: str="alltimexp") -> Iterable[Row]:
    db = get_db(g_id)
    if db is None:
        raise aiosqlite.DatabaseError("No database found.")
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
    if db is None:
        raise aiosqlite.DatabaseError("No database found.")
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
    raise aiosqlite.OperationalError


async def add_xp_db(g_id: int, u_id: hikari.Snowflake, xp: int, xp_time: str="alltimexp") -> None:
    for xp_time in all_xp_times:
        if not xp_time_is_enabled(g_id, all_xp_times.index(xp_time)):
            continue
        old_xp = await get_xp_db(g_id, u_id, xp_time)
        await set_xp_db(g_id, u_id, old_xp + xp, xp_time)


async def remove_xp_db(g_id: int, u_id: hikari.Snowflake, xp: int, xp_time: str="alltimexp") -> None:
    for xp_time in all_xp_times:
        if not xp_time_is_enabled(g_id, all_xp_times.index(xp_time)):
            continue
        old_xp = await get_xp_db(g_id, u_id, xp_time)
        await set_xp_db(g_id, u_id, max(old_xp - xp, 0), xp_time)


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


async def handle_lvl_increase(guild_id: int, user: hikari.User, lvl: int, app: hikari.RESTAware) -> None:
    role_ids = await get_user_roles(guild_id, user.id, app)

    settings = get_settings(guild_id)
    
    for role_id, role_lvl in settings["Level Roles"].items():
        if role_lvl <= lvl and int(role_id) not in role_ids:
            await app.rest.add_role_to_member(guild_id, user, role_id)

    if settings["Level Up Messages"]["Enabled"]:
        await app.rest.create_message(
            settings["Level Up Messages"]["Channel"],
            f"{user.username} just leveled up to level {lvl}!"
        )


async def handle_lvl_decrease(guild_id: int, user: hikari.User, lvl: int, app: hikari.RESTAware) -> None:
    role_ids = await get_user_roles(guild_id, user.id, app)

    settings = get_settings(guild_id)

    for role_id, role_lvl in settings["Level Roles"].items():
        if role_lvl > lvl and int(role_id) in role_ids:
            await app.rest.remove_role_from_member(guild_id, user, role_id)


async def handle_xp_update(guild_id: int, user: hikari.User, xp: int, app: hikari.RESTAware) -> None:
    new_xp = await get_xp_db(guild_id, user.id)
    new_lvl = await get_lvl(new_xp)

    old_lvl = await get_lvl(new_xp - xp)

    if new_lvl > old_lvl:
        await handle_lvl_increase(guild_id, user, new_lvl, app)
    
    if new_lvl < old_lvl:
        await handle_lvl_decrease(guild_id, user, new_lvl, app)

async def user_xp_denied(g_id: int, c_id: int, u_id: int, app: hikari.RESTAware) -> bool:
    settings = get_settings(g_id)
    denylist = settings["Denylist"]

    role_ids = await get_user_roles(g_id, u_id, app)

    return (
        int(c_id) in denylist["Denied Channels"]
        or len(set(role_ids) & set(denylist["Denied Roles"])) > 0
        or int(u_id) in denylist["Denied Users"]
    )


async def handle_msg_xp_gain(event: hikari.MessageCreateEvent) -> None:
    user = event.message.author
    if user.id in ids_on_cooldoWn:
        return
    
    guild_id = event.message.guild_id
    if guild_id is None:
        raise hikari.ComponentStateConflictError("No guild id found.")

    if await user_xp_denied(guild_id, event.message.channel_id, user.id, event.app):
        return

    calculation = get_settings(guild_id)["Calculation"]
    xp = random.randint(
        calculation["Minimum XP"],
        calculation["Maximum XP"]
    )

    await add_xp_db(guild_id, user.id, xp)
    await handle_xp_update(guild_id, user, xp, event.app)

    # currently for testing
    # possibly make ephemeral as a prod feature?
    # would require user settings but would be useful for admins
    # but i am the only admin who debugs xp gain so meh
    await event.message.respond("This Pro-flop is Pissing me off...")


async def is_bot_xp_hook(ctx: crescent.Context) -> crescent.HookResult:
    user = ctx.options.get("user", ctx.user) or ctx.user
    if not user.is_bot:
        return crescent.HookResult()

    if user.id == ctx.application_id:
        await ctx.respond("~~Someday~~ I mean what?")
    else:
        await ctx.respond("We bots don't earn xp...")
    return crescent.HookResult(exit=True)


async def manage_cooldown_hook(event: hikari.MessageCreateEvent) -> None:
    
    guild_id = event.message.guild_id
    if guild_id is None:
        raise hikari.ComponentStateConflictError("No guild id found.")
    
    user = event.message.author
    if user.id in ids_on_cooldoWn or await user_xp_denied(guild_id, event.message.channel_id, user.id, event.app):
        return
    
    ids_on_cooldoWn.add(user.id)
    await asyncio.sleep(get_settings(int(guild_id))["Calculation"]["Cooldown"])
    ids_on_cooldoWn.remove(user.id)


async def confirmation_hook(ctx: crescent.Context) -> crescent.HookResult:
    await ctx.respond("Waiting for confirmation...")
    
    view = ConfirmView(ctx, timeout=15.0)

    confirm = await ctx.respond(
        "Are you sure? **This cannot be undone.**",
        components=view,
        ephemeral=True
    )

    miru_client = ctx.client.model.miru_client
    assert isinstance(miru_client, miru.Client)

    miru_client.start_view(view)
    await view.wait_for_input()

    if confirm is not None:
        await confirm.delete()

    result = view.result or crescent.HookResult(exit=True)
    if result.exit:
        await ctx.delete()
    
    return result


async def is_human_hook(event: hikari.MessageCreateEvent) -> crescent.HookResult:
    return crescent.HookResult(exit=event.message.author.is_bot)


@plugin.include
@crescent.hook(is_human_hook)
@crescent.hook(manage_cooldown_hook, after=True)
@crescent.event
async def on_message_create(event: hikari.MessageCreateEvent) -> None:
    await handle_msg_xp_gain(event)


@plugin.include
@crescent.hook(is_bot_xp_hook)
@crescent.command(
    name="rank",
    description="check rank & xp of user"
)
class CheckXPCommand:
    user = crescent.option(hikari.User, "user to check rank & xp of", default=None)

    async def callback(self, ctx: crescent.Context) -> None:
        user = self.user or ctx.user
        
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
        guild_id = ctx.guild_id
        if guild_id is None:
            raise hikari.ComponentStateConflictError("No guild id found.")
        
        if not xp_time_is_enabled(guild_id, self.time):
            await ctx.respond("This leaderboard is disabled.", ephemeral=True)
            return

        miru_client = ctx.client.model.miru_client
        assert isinstance(miru_client, miru.Client)

        rest = ctx.app.rest

        xp_time = all_xp_times[self.time]
        xp_time_pretty = all_xp_times_pretty[all_xp_times.index(xp_time)]

        max_pages = ceildiv(await get_size_xp_db(guild_id, xp_time), 10)

        # i have not made a list comprehension like this in years okay
        # let me have this
        lb_nav = nav.NavigatorView(pages=[
            hikari.Embed(
                title=f"Leaderboard{': ' + xp_time_pretty
                    if xp_time != 'alltimexp' else ''}",
                description="\n".join([
                    f"{(page - 1) * 10 + i + 1}. {
                        (await rest.fetch_user(id)).mention
                    } · Level {await get_lvl(xp)} · {xp} XP"
                    for i, (id, xp) in enumerate(
                        await get_xp_db_bulk(
                            guild_id, page, xp_time
                        )
                    )
                ])
            )
            for page in range(1, max_pages + 1)
        ])

        builder = await lb_nav.build_response_async(miru_client)
        await ctx.respond_with_builder(builder)
        miru_client.start_view(lb_nav)


xp_group = crescent.Group(
    name="xp",
    description="xp management commands",
    hooks=[is_bot_xp_hook]
)


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
        guild_id = ctx.guild_id
        if guild_id is None:
            raise hikari.ComponentStateConflictError("No guild id found.")
        
        old_xp = await get_xp_db(guild_id, self.user.id)

        try:
            await set_xp_db(guild_id, self.user.id, self.xp)
        except aiosqlite.OperationalError:
            await ctx.respond("Something went wrong updating the data.", ephemeral=True)
        else:
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
        guild_id = ctx.guild_id
        if guild_id is None:
            raise hikari.ComponentStateConflictError("No guild id found.")
        
        try:
            await add_xp_db(guild_id, self.user.id, self.xp)
        except aiosqlite.OperationalError:
            await ctx.respond("Something went wrong updating the data.", ephemeral=True)
        else:
            await handle_xp_update(guild_id, self.user, self.xp, ctx.app)
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
        guild_id = ctx.guild_id
        if guild_id is None:
            raise hikari.ComponentStateConflictError("No guild id found.")
        
        try:
            await remove_xp_db(guild_id, self.user.id, self.xp)
        except aiosqlite.OperationalError:
            await ctx.respond(
                "Something went wrong updating the data.",
                ephemeral=True
            )
        else:
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
        guild_id = ctx.guild_id
        if guild_id is None:
            raise hikari.ComponentStateConflictError("No guild id found.")
        
        old_xp = await get_xp_db(guild_id, self.user.id)
        try:
            await reset_xp_db(guild_id, self.user.id)
        except aiosqlite.OperationalError:
            await ctx.respond("Something went wrong updating the data.", ephemeral=True)
        else:
            await handle_xp_update(guild_id, self.user, -old_xp, ctx.app)
            await ctx.respond(f"Reset xp of {self.user.username}.")


@plugin.include
@crescent.hook(confirmation_hook)
@crescent.command(
    name="init",
    description="removes all level data & creates a new level storage",
    default_member_permissions=hikari.Permissions.ADMINISTRATOR
)
async def init_guild_xp(ctx: crescent.Context) -> None:
    guild_id = ctx.guild_id
    if guild_id is None:
        raise hikari.ComponentStateConflictError("No guild id found.")

    await ctx.edit("Initializing...")
    await init_db(guild_id)

    await asyncio.sleep(1)

    await ctx.edit("Blank level storage created.")