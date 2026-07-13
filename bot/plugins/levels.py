import random
import os
import asyncio
import json
import math
from datetime import datetime, timezone, timedelta
from collections.abc import Iterable
from sqlite3 import Row

import crescent
import hikari
import miru
import aiosqlite
from crescent.ext import tasks
from miru.ext import menu, nav

from bot.pprintify import pprintify
from bot.model import Model


# consts

GUILD_ID = int(os.environ["GUILD_ID"])

ALL_XP_TIMES = (
    "alltimexp",
    "yearlyxp",
    "monthlyxp",
    "weeklyxp",
    "dailyxp"
)

ALL_XP_TIMES_PRETTY = (
    "All Time",
    "Yearly",
    "Monthly",
    "Weekly",
    "Daily"
)

ANSI_KEY = {
    "Normal": "0",
    "Bold": "1",
    "Underline": "4",
    "Gray Text": "30",
    "Red Text": "31",
    "Green Text": "32",
    "Yellow Text": "33",
    "Blue Text": "34",
    "Pink Text": "35",
    "Cyan Text": "36",
    "White Text": "37",
    "Black BG": "40",
    "Red BG": "41",
    "Green BG": "42",
    "Yellow BG": "43",
    "Blue BG": "44",
    "Pink BG": "45",
    "Cyan BG": "46",
    "White BG": "47"
}

ESC_CHAR = ""


# inits


plugin = crescent.Plugin[hikari.GatewayBot, Model]()

aiosqlite.register_adapter(hikari.Snowflake, lambda sf: int(sf))

with open("bot/data/temp_settings.json", "r") as f:
    settings: dict = json.load(f)

# ids get added/removed on message to control xp gain per cooldown
ids_on_cooldoWn = set()


# side effect free functions


def ceildiv(a: int, b: int) -> int:
    return -(a // -b)

async def get_user_roles(u_id: int, app: hikari.RESTAware) -> list[int]:
    return list(map(int, (await app.rest.fetch_member(GUILD_ID, u_id)).role_ids))

def get_next_lvl_xp(lvl: int) -> int:
    # default is `floor(208 / 3 * {level} - 104 / 3) + {xp}`
    # not going to support a lack of {xp}
    # so just `floor(208 / 3 * lvl + 104 / 3)` as default
    # and non-default later
    return math.floor(208 / 3 * lvl + 104 / 3)

def get_xp_for_lvl(lvl: int) -> int:
    return sum([get_next_lvl_xp(i) for i in range(0, lvl)])

def get_lvl(xp: int) -> int:
    lvl = 0
    sum = get_next_lvl_xp(0)
    while sum <= xp:
        lvl += 1
        sum += get_next_lvl_xp(lvl)
    return lvl

def xp_time_is_enabled(i: int) -> bool:
    return (ALL_XP_TIMES[i] == "alltimexp"
        or settings["Leaderboards"][ALL_XP_TIMES_PRETTY[i]])

async def user_xp_denied(c_id: int, u_id: int, app: hikari.RESTAware) -> bool:
    denylist = settings["Denylist"]
    role_ids = await get_user_roles(u_id, app)
    return (
        int(c_id) in denylist["Denied Channels"]
        or len(set(role_ids) & set(denylist["Denied Roles"])) > 0
        or int(u_id) in denylist["Denied Users"]
    )

def make_ansi(txt: str, styles: list[str] = []) -> str:
    return (
        f"{ESC_CHAR}[{';'.join([ANSI_KEY[style] for style in (styles or ["Normal"])])}m"
        + txt + f"{ESC_CHAR}[0m"
    )

def make_timestamp(dt: datetime) -> str:
    return dt.strftime("%Y/%m/%d %I:%M %p %Z%:z")

async def make_rank_card(u_id, xp: int, lvl: int, app: hikari.RESTAware) -> str:
    user = await app.rest.fetch_member(GUILD_ID, u_id)
    rank = await get_rank(u_id)
    next_lvl_xp = get_next_lvl_xp(lvl)
    xp_progress = xp - get_xp_for_lvl(lvl)

    # consider making these external constants
    style = ("░", "▒", "▓", "█")
    num_states = len(style)
    length = 36
    total_divisions = (num_states - 1) * length

    progress = xp_progress / next_lvl_xp
    divisions_left = math.floor(progress * total_divisions)
    full_states_left = divisions_left // num_states
    remainder = divisions_left % num_states
    xp_bar = (
        make_ansi(
            style[num_states - 1] * full_states_left + (style[remainder] if remainder else ""),
            ["Blue Text"]
        ) + style[0] * (length - (full_states_left + int(remainder > 0)))
    )
    
    nick = user.nickname or user.display_name
    nick_str = f"{(nick[:25] + '...') if len(nick) > 25 else nick}"

    return "\n".join([
        "```ansi",
        "⠀",
        f"  {make_ansi(nick_str, ["Bold"])}  ",
        f"  {make_ansi('@' + user.username, ["White Text"])}  ",
        "⠀",
        f"  {make_ansi(str(lvl), ["Bold", "Blue Text"])} {xp_bar} {make_ansi(str(lvl + 1), ["Bold", "White Text"])}  ",
        "⠀",
        f"  {xp} / {xp + next_lvl_xp - xp_progress} XP  ·  RANK #{rank}  ",
        "⠀",
        "```"
    ])


# classes


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


# database functions


async def print_db(cur: aiosqlite.Cursor) -> None:
    for xp_time in ALL_XP_TIMES:
        data = await cur.execute(f"""
            SELECT * FROM {xp_time}
        """)
        print(f"{xp_time} data:\n{await data.fetchall()}")


async def init_xp_table_db(xp_time: str) -> None:
    db = plugin.model.db
    if db is None:
        raise aiosqlite.DatabaseError("No database found.")
    async with db.cursor() as cur:
        await cur.execute(f"""
            DROP TABLE IF EXISTS {xp_time}
        """)
        await cur.execute(f"""
            CREATE TABLE {xp_time} (
                id INTEGER PRIMARY KEY,
                xp INTEGER
            );
        """)
        with open("bot/data/last_table_reset.txt", "w") as f:
            f.write(str(datetime.timestamp(datetime.now(timezone.utc))))

        await db.commit()
        await print_db(cur)


async def get_size_xp_db(xp_time: str) -> int:
    assert xp_time in ALL_XP_TIMES
    db = plugin.model.db
    if db is None:
        raise aiosqlite.DatabaseError("No database found.")
    async with db.cursor() as cur:
        data = await (await cur.execute(f"""
            SELECT COUNT(*) FROM {xp_time}
            WHERE xp > 0
        """)).fetchone()
    return data[0] if data else 0


async def get_xp_db(u_id: hikari.Snowflake, xp_time: str = "alltimexp") -> int:
    assert xp_time in ALL_XP_TIMES
    db = plugin.model.db
    if db is None:
        raise aiosqlite.DatabaseError("No database found.")
    async with db.cursor() as cur:
        data = await (await cur.execute(f"""
            SELECT xp FROM {xp_time}
            WHERE id = ?
        """, (u_id,))).fetchone()
    return data[0] if data else 0


async def get_xp_db_bulk(page: int, xp_time: str) -> Iterable[Row]:
    db = plugin.model.db
    if db is None:
        raise aiosqlite.DatabaseError("No database found.")
    async with db.cursor() as cur:
        data = await (await cur.execute(f"""
            SELECT id, xp FROM {xp_time}
            WHERE xp > 0
            ORDER BY xp DESC
            LIMIT 10 OFFSET 10 * ?
        """, (page - 1,))).fetchall()
    return data


async def get_rank(u_id: int) -> int:
    db = plugin.model.db
    if db is None:
        raise aiosqlite.DatabaseError("No database found.")
    async with db.cursor() as cur:
        data = await (await cur.execute(f"""
            SELECT rn FROM (
                SELECT *, ROW_NUMBER()
                OVER (ORDER BY xp DESC) rn
                FROM alltimexp
            ) WHERE id = ?
        """, (u_id,))).fetchone()
    return data[0] if data else 0


async def set_xp_db(u_id: hikari.Snowflake, xp: int, xp_time: str = "alltimexp") -> None:
    assert xp_time in ALL_XP_TIMES
    db = plugin.model.db
    if db is None:
        raise aiosqlite.DatabaseError("No database found.")
    async with db.cursor() as cur:
        await cur.execute(f"""
            INSERT INTO {xp_time}(id, xp) 
            SELECT ?, 0
            WHERE NOT EXISTS(SELECT 1 FROM {xp_time} WHERE id = ?)
        """, (u_id, u_id))
        await cur.execute(f"""
            UPDATE {xp_time}
            SET xp = ?
            WHERE id = ?
        """, (xp, u_id))

        await db.commit()
        await print_db(cur)


async def reset_xp_db(u_id: hikari.Snowflake, xp_time: str = "alltimexp") -> None:
    assert xp_time in ALL_XP_TIMES
    db = plugin.model.db
    assert db is not None
    async with db.cursor() as cur:
        await cur.execute(f"""
            DELETE FROM {xp_time}
            WHERE id = ?
        """, (u_id,))

        await db.commit()
        await print_db(cur)


async def add_xp_db(u_id: hikari.Snowflake, xp: int, xp_time: str = "alltimexp") -> None:
    for xp_time in ALL_XP_TIMES:
        if not xp_time_is_enabled(ALL_XP_TIMES.index(xp_time)):
            continue
        old_xp = await get_xp_db(u_id, xp_time)
        await set_xp_db(u_id, old_xp + xp, xp_time)


async def remove_xp_db(u_id: hikari.Snowflake, xp: int, xp_time: str = "alltimexp") -> None:
    for xp_time in ALL_XP_TIMES:
        if not xp_time_is_enabled(ALL_XP_TIMES.index(xp_time)):
            continue
        old_xp = await get_xp_db(u_id, xp_time)
        await set_xp_db(u_id, max(old_xp - xp, 0), xp_time)


# handlers


async def handle_lvl_increase(user: hikari.User, lvl: int, app: hikari.RESTAware) -> None:
    role_ids = await get_user_roles(user.id, app)
    
    for role_id, role_lvl in settings["Level Roles"].items():
        if role_lvl <= lvl and int(role_id) not in role_ids:
            await app.rest.add_role_to_member(
                GUILD_ID, user, role_id,
                reason=f"Level up to {lvl}\n (≥ Level {role_lvl})"
            )

    channel = settings["Level Up Messages"]["Channel"]
    if channel is not None:
        # hardcoded for now
        await app.rest.create_message(channel, embed=hikari.Embed(
            description="\n".join((
                f"### {user.username} climbed to level {lvl}",
                "Keep it up and you *might* make it to a Nest (real)",
                "\n:tada: :tada: :tada: :tada:"
            ))
        ))


async def handle_lvl_decrease(user: hikari.User, lvl: int, app: hikari.RESTAware) -> None:
    role_ids = await get_user_roles(user.id, app)

    for role_id, role_lvl in settings["Level Roles"].items():
        if role_lvl > lvl and int(role_id) in role_ids:
            await app.rest.remove_role_from_member(
                GUILD_ID, user, role_id,
                reason=f"Level down to {lvl}\n (< Reward Level {role_lvl})"
            )


async def handle_xp_update(user: hikari.User, xp: int, app: hikari.RESTAware) -> None:
    new_xp = await get_xp_db(user.id)
    new_lvl = get_lvl(new_xp)
    old_lvl = get_lvl(new_xp - xp)

    if new_lvl > old_lvl:
        await handle_lvl_increase(user, new_lvl, app)
    if new_lvl < old_lvl:
        await handle_lvl_decrease(user, new_lvl, app)


async def handle_msg_xp_gain(event: hikari.MessageCreateEvent) -> None:
    if event.message.guild_id is None:
        raise hikari.ComponentStateConflictError("No guild id found.")

    user = event.message.author
    if (
        user.id in ids_on_cooldoWn
        or await user_xp_denied(event.message.channel_id, user.id, event.app)
    ):
        return

    calculation = settings["Calculation"]
    xp = random.randint(
        calculation["Minimum XP"],
        calculation["Maximum XP"]
    )

    await add_xp_db(user.id, xp)
    await handle_xp_update(user, xp, event.app)

    # currently for testing
    # possibly make ephemeral as a prod feature?
    # would require user settings but would be useful for admins
    # but i am the only admin who debugs xp gain so meh
    await event.message.respond("This Pro-flop is Pissing me off...")


# logging


async def log_manual_xp(ctx: crescent.Context) -> None:
    channel_id = settings["Logging Channels"]["Manual XP"]
    if channel_id is None:
        return
    
    cmd_user = ctx.user
    arg_user: hikari.User = ctx.options.get("user", ctx.user)

    message = {
        "set": f"{cmd_user.mention} set {arg_user.mention}'s XP to {ctx.options.get('xp')}",
        "add": f"{cmd_user.mention} added {ctx.options.get('xp')} XP to {arg_user.mention}",
        "remove": f"{cmd_user.mention} removed {ctx.options.get('xp')} XP from {arg_user.mention}",
        "reset": f"{cmd_user.mention} reset {arg_user.mention}'s XP"
    }[ctx.command]

    await ctx.app.rest.create_message(
        channel_id,
        embed=hikari.Embed(
            title="Manual XP",
            description=message
        ).set_footer(make_timestamp(datetime.now(timezone.utc)))
    )


# hooks


async def is_bot_xp_hook(ctx: crescent.Context) -> crescent.HookResult:
    user = ctx.options.get("user", ctx.user)
    if not user.is_bot:
        return crescent.HookResult()

    if user.id == ctx.application_id:
        await ctx.respond("~~Someday~~ I mean what?")
    else:
        await ctx.respond("We bots don't earn xp...")
    return crescent.HookResult(exit=True)


async def manage_cooldown_hook(event: hikari.MessageCreateEvent) -> None:
    if event.message.guild_id is None:
        raise hikari.ComponentStateConflictError("No guild id found.")
    
    user = event.message.author
    if user.id in ids_on_cooldoWn or await user_xp_denied(event.message.channel_id, user.id, event.app):
        return
    
    ids_on_cooldoWn.add(user.id)
    await asyncio.sleep(settings["Calculation"]["Cooldown"])
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


# events


@plugin.include
@crescent.hook(is_human_hook)
@crescent.hook(manage_cooldown_hook, after=True)
@crescent.event
async def on_message_create(event: hikari.MessageCreateEvent) -> None:
    await handle_msg_xp_gain(event)


# scheduled tasks


@plugin.include
@tasks.cronjob("* * * * *", on_startup=True)
async def reset_xp_task() -> None:
    with open("bot/data/last_table_reset.txt", "r") as f:
        ts = f.read()
        last_reset = datetime.fromtimestamp(float(ts), timezone.utc) if ts else None
    now = datetime.now(timezone.utc)
    monday_week = int(settings["Leaderboards"]["Start Week On Monday"])
    
    if last_reset is None:
        # ALL_XP_TIMES[1:] is all but "alltimexp"
        [await init_xp_table_db(xp_time) for xp_time in ALL_XP_TIMES[1:]]
    else:
        if now.date() > last_reset.date():
            await init_xp_table_db("dailyxp")
        if now.date() > last_reset.date() - timedelta(
            days = (last_reset.isoweekday() - monday_week) % 7 - (now.isoweekday() - monday_week) % 7
        ):
            await init_xp_table_db("weeklyxp")
        if now.date().replace(day=1) > last_reset.date().replace(day=1):
            await init_xp_table_db("monthlyxp")
        if now.date().year > last_reset.date().year:
            await init_xp_table_db("yearlyxp")


# commands


@plugin.include
@crescent.hook(is_bot_xp_hook)
@crescent.command(
    name="rank",
    description="check rank & xp of user"
)
class CheckXPCommand:
    user = crescent.option(hikari.User, "user to check rank & xp of", default=None)

    async def callback(self, ctx: crescent.Context) -> None:
        if ctx.guild_id is None:
            raise hikari.ComponentStateConflictError("No guild id found.")
        
        user = self.user or ctx.user
        xp = await get_xp_db(user.id)
        lvl = get_lvl(xp)

        await ctx.respond(hikari.Embed(description=await make_rank_card(user.id, xp, lvl, ctx.app)))
        return


@plugin.include
@crescent.command(
    name="leaderboard",
    description="view top 10 users by xp"
)
class LeaderboardCommand:
    time = crescent.option(
        int, "time period to view xp for",
        default=0,
        choices=[(xp_time, i) for i, xp_time in enumerate(ALL_XP_TIMES_PRETTY)]
    )

    async def callback(self, ctx: crescent.Context) -> None:
        if ctx.guild_id is None:
            raise hikari.ComponentStateConflictError("No guild id found.")
        
        if not xp_time_is_enabled(self.time):
            await ctx.respond("This leaderboard is disabled.", ephemeral=True)
            return

        miru_client = ctx.client.model.miru_client
        assert isinstance(miru_client, miru.Client)

        rest = ctx.app.rest
        xp_time = ALL_XP_TIMES[self.time]
        xp_time_pretty = ALL_XP_TIMES_PRETTY[ALL_XP_TIMES.index(xp_time)]
        timestamp = make_timestamp(datetime.now(timezone.utc))

        max_pages = ceildiv(await get_size_xp_db(xp_time), 10)
        if max_pages == 0:
            await ctx.respond(embed=hikari.Embed(
                title=f"Leaderboard{': ' + xp_time_pretty
                    if xp_time != 'alltimexp' else ''}",
                description="No data for this leaderboard yet; limbillions must chat."
            ).set_footer(timestamp))
            return

        # i have not made a list comprehension like this in years okay
        # let me have this
        lb_nav = nav.NavigatorView(pages=[
            hikari.Embed(
                title=f"Leaderboard{': ' + xp_time_pretty
                    if xp_time != 'alltimexp' else ''}",
                description="\n".join([
                    f"{(page - 1) * 10 + i + 1}. {
                        (await rest.fetch_user(id)).mention
                    } · Level {get_lvl(xp)} · {xp} XP"
                    for i, (id, xp) in enumerate(
                        await get_xp_db_bulk(
                            page, xp_time
                        )
                    )
                ])
            ).set_footer(timestamp)
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
        assert ctx.guild_id is not None
        old_xp = await get_xp_db(self.user.id)

        try:
            await set_xp_db(self.user.id, self.xp)
        except aiosqlite.OperationalError:
            await ctx.respond("Something went wrong updating the data.", ephemeral=True)
        else:
            await handle_xp_update(self.user, self.xp - old_xp, ctx.app)
            await ctx.respond(f"Set xp of {self.user.username} to {self.xp}.")
            await log_manual_xp(ctx)


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
        assert ctx.guild_id is not None
        try:
            await add_xp_db(self.user.id, self.xp)
        except aiosqlite.OperationalError:
            await ctx.respond("Something went wrong updating the data.", ephemeral=True)
        else:
            await handle_xp_update(self.user, self.xp, ctx.app)
            await ctx.respond(f"Added {self.xp} xp to {self.user.username}.")
            await log_manual_xp(ctx)


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
        assert ctx.guild_id is not None
        try:
            await remove_xp_db(self.user.id, self.xp)
        except aiosqlite.OperationalError:
            await ctx.respond(
                "Something went wrong updating the data.",
                ephemeral=True
            )
        else:
            await handle_xp_update(self.user, -self.xp, ctx.app)
            await ctx.respond(f"Removed {self.xp} xp from {self.user.username}.")
            await log_manual_xp(ctx)


@plugin.include
@xp_group.child
@crescent.command(
    name="reset",
    description="reset xp of user"
)
class ResetXPCommand:
    user = crescent.option(hikari.User, "user to reset xp of")

    async def callback(self, ctx: crescent.Context) -> None:
        assert ctx.guild_id is not None
        old_xp = await get_xp_db(self.user.id)
        try:
            await reset_xp_db(self.user.id)
        except aiosqlite.OperationalError:
            await ctx.respond("Something went wrong updating the data.", ephemeral=True)
        else:
            await handle_xp_update(self.user, -old_xp, ctx.app)
            await ctx.respond(f"Reset xp of {self.user.username}.")
            await log_manual_xp(ctx)


@plugin.include
@crescent.hook(confirmation_hook)
@crescent.command(
    name="init",
    description="removes all level data & creates a new level storage",
    context_types=[hikari.ApplicationContextType.GUILD],
    default_member_permissions=hikari.Permissions.ADMINISTRATOR
)
async def init_guild_xp(ctx: crescent.Context) -> None:
    assert ctx.guild_id is not None
    await ctx.edit("Initializing...")
    for xp_time in ALL_XP_TIMES:
        await init_xp_table_db(xp_time)

    await asyncio.sleep(1)
    await ctx.edit("Blank level storage created.")