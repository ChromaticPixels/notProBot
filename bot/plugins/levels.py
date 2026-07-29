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

SETTINGS_DESC = {
    "Calculation": "On/off, cooldown, range, etc.; adjust default XP gain.",
    "Denylist": "Control which channels, roles, and users can gain XP.",
    "Leaderboards": "Adjust timed leaderboards and their start time.",
    "Level Roles": "Set roles to reward for reaching a certain level.",
    "Level Up Messages": "Customize level up message content and location.",
    "Rank Cards": "Customize the card shown when a user checks their rank and XP.",
    "Logging Channels": "Set channels for logging various command activity."
}

SETTINGS_STR_OPTIONS = {
    "XP Bar Color": {
        "Gray", "Red", "Green", "Yellow", "Blue", "Pink", "Cyan", "White"
    }
}

DISABLED_SETTINGS = {
    "Calculation": {"Next Level Formula"},
    "Denylist": {},
    "Leaderboards": {},
    "Level Roles": {},
    "Level Up Messages": {},
    "Rank Cards": {},
    "Logging Channels": {},

}

# inits


plugin = crescent.Plugin[hikari.GatewayBot, Model]()

aiosqlite.register_adapter(hikari.Snowflake, lambda sf: int(sf))

with open("bot/data/settings.json", "r") as f:
    settings: dict = json.load(f)

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
        f"{ESC_CHAR}[{';'.join([ANSI_KEY[style] for style in (styles or ['Normal'])])}m"
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
    non_empty_states = len(style) - 1
    length = 36
    total_divisions = non_empty_states * length
    xp_bar_color_str = f"{settings['Rank Cards']['XP Bar Color']} Text"

    progress = xp_progress / next_lvl_xp
    divisions_left = math.floor(progress * total_divisions)
    full_states_left = divisions_left // non_empty_states
    remainder = divisions_left % non_empty_states
    xp_bar = (
        make_ansi(
            style[non_empty_states] * full_states_left + (style[remainder] if remainder else ""),
            [xp_bar_color_str]
        ) + style[0] * (length - (full_states_left + int(remainder > 0)))
    )
    
    nick = user.nickname or user.display_name
    nick_str = f"{(nick[:25] + '...') if len(nick) > 25 else nick}"

    return "\n".join([
        "```ansi",
        "⠀",
        f"  {make_ansi(nick_str, ['Bold'])}  ",
        f"  {make_ansi('@' + user.username, ['White Text'])}  ",
        "⠀",
        f"  {make_ansi(str(lvl), ['Bold', xp_bar_color_str if progress else 'White Text'])} {xp_bar} {make_ansi(str(lvl + 1), ['Bold', 'White Text'])}  ",
        "⠀",
        f"  {xp} / {xp + next_lvl_xp - xp_progress} XP  ·  RANK #{rank}  ",
        "⠀",
        "```"
    ])


def make_setting_option_screen(category: str, menu: menu.Menu, ctx: miru.ViewContext):
    setting_class = f"{category.removesuffix('s').replace(' ', '')}Screen"
    return globals()[setting_class](menu, ctx)


# settings


def set_setting(category: str, setting: str, value: bool | int | str | list | None) -> None:
    settings[category][setting] = value
    with open("bot/data/settings.json", "w") as f:
        json.dump(settings, f)


def del_settings(category: str, vals: list[str]) -> None:
    for setting in vals:
        del settings[category][setting]
    with open("bot/data/settings.json", "w") as f:
        json.dump(settings, f)


# buttons


class BackButton(menu.ScreenButton):
    def __init__(self) -> None:
        super().__init__(label="Back", style=hikari.ButtonStyle.SECONDARY)

    async def callback(self, ctx: miru.ViewContext) -> None:
        await self.menu.pop()


class BackAllButton(menu.ScreenButton):
    def __init__(self, row=None) -> None:
        super().__init__(label="Back", style=hikari.ButtonStyle.SECONDARY)
        self.row = row
        
    async def callback(self, ctx: miru.ViewContext) -> None:
        await self.menu.pop_until_root()


class ChooseAddButton(menu.ScreenButton):
    def __init__(self) -> None:
        super().__init__(label="Add selected values", style=hikari.ButtonStyle.SUCCESS)
        self.position = 0
        self.row = 4

    async def callback(self, ctx: miru.ViewContext) -> None:
        screen = self.menu.current_screen
        assert isinstance(screen, (ChannelOptionScreen, UserOptionScreen, RoleOptionScreen))
        assert isinstance(screen.value, list)
        old_value = settings[screen.category][screen.setting]
        set_setting(screen.category, screen.setting, old_value + [x for x in screen.value if x not in old_value])
        await self.menu.push(make_setting_option_screen(screen.category, screen.menu, ctx))


class ChooseRemoveButton(menu.ScreenButton):
    def __init__(self) -> None:
        super().__init__(label="Remove selected values", style=hikari.ButtonStyle.DANGER)
        self.position = 1
        self.row = 4

    async def callback(self, ctx: miru.ViewContext) -> None:
        screen = self.menu.current_screen
        assert isinstance(screen, (ChannelOptionScreen, UserOptionScreen, RoleOptionScreen))
        assert isinstance(screen.value, list)
        old_value = settings[screen.category][screen.setting]
        set_setting(screen.category, screen.setting, [x for x in old_value if x not in screen.value])
        await self.menu.push(make_setting_option_screen(screen.category, screen.menu, ctx))


class ChooseNoneButton(menu.ScreenButton):
    def __init__(self) -> None:
        super().__init__(label="Choose none / remove all", style=hikari.ButtonStyle.DANGER)
        self.row = 4

    async def callback(self, ctx: miru.ViewContext) -> None:
        screen = self.menu.current_screen
        assert isinstance(screen, (ChannelOptionScreen, UserOptionScreen, RoleOptionScreen))
        set_setting(screen.category, screen.setting, None if screen.singular else [])
        await self.menu.push(make_setting_option_screen(screen.category, screen.menu, ctx))


class AddEditLevelRoleButton(menu.ScreenButton):
    def __init__(self,) -> None:
        super().__init__(label="Add/edit selected roles", style=hikari.ButtonStyle.SUCCESS)
        self.position = 0
        self.row = 4

    async def callback(self, ctx: miru.ViewContext) -> None:
        screen = self.menu.current_screen
        assert isinstance(screen, LevelRoleScreen)
        modal = LevelRoleInputModal("Level Roles")
        await ctx.respond_with_modal(modal)
        await modal.wait()
        if modal.last_context is not None:
            lvl = int(modal.last_context.get_value_by(lambda x: isinstance(x, miru.TextInput)))
            for role in screen.value:
                set_setting("Level Roles", str(role), lvl)
            await modal.last_context.defer()
        else:
            modal.stop()
        await self.menu.push(make_setting_option_screen("Level Roles", screen.menu, ctx))


class RemoveLevelRoleButton(menu.ScreenButton):
    def __init__(self) -> None:
        super().__init__(label="Remove selected roles", style=hikari.ButtonStyle.DANGER)
        self.position = 1
        self.row = 4

    async def callback(self, ctx: miru.ViewContext) -> None:
        screen = self.menu.current_screen
        assert isinstance(screen, LevelRoleScreen)
        current_roles: dict[str, int] = settings["Level Roles"]
        del_settings("Level Roles", [str(role) for role in screen.value if str(role) in current_roles.keys()])
        await self.menu.push(make_setting_option_screen("Level Roles", screen.menu, ctx))


class RemoveAllLevelRoleButton(menu.ScreenButton):
    def __init__(self) -> None:
        super().__init__(label="Choose none / remove all", style=hikari.ButtonStyle.DANGER)
        self.row = 4

    async def callback(self, ctx: miru.ViewContext) -> None:
        screen = self.menu.current_screen
        assert isinstance(screen, LevelRoleScreen)
        del_settings("Level Roles", list(settings["Level Roles"].keys()))
        await self.menu.push(make_setting_option_screen("Level Roles", screen.menu, ctx))


# views


class OriginalCrescentCtxView(miru.View):
    original_ctx: crescent.Context

    def __init__(self, ctx: crescent.Context, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.original_ctx = ctx


class ConfirmView(OriginalCrescentCtxView):
    result: crescent.HookResult | None

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.result = None
    
    @miru.button(label="Confirm", style=hikari.ButtonStyle.DANGER)
    async def confirm_button(self, ctx: miru.ViewContext, button: miru.Button) -> None:
        self.result = crescent.HookResult()
        self.stop()
    
    @miru.button(label="Cancel", style=hikari.ButtonStyle.SECONDARY)
    async def cancel_button(self, ctx: miru.ViewContext, button: miru.Button) -> None:
        self.result = crescent.HookResult(exit=True)
        self.stop()

    async def view_check(self, ctx: miru.ViewContext) -> bool:
        return ctx.user.id == self.original_ctx.user.id


# modals


class CheckInputModal(miru.Modal):
    original_ctx: miru.ViewContext
    category: str
    setting: str
    value: str | int

    def __init__(self, ctx: miru.ViewContext, category: str, data: tuple[str, str | int], tied_menu: menu.Menu | None = None, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.original_ctx = ctx
        self.category = category
        self.setting, self.value = data
        self.add_item(miru.TextInput(
            label=self.setting,
            value=str(self.value),
            required=True,
            style = hikari.TextInputStyle.PARAGRAPH if isinstance(self.value, str) else hikari.TextInputStyle.SHORT
        ))

    async def modal_check(self, ctx: miru.ModalContext) -> bool:
        input_str = ctx.values[self.get_item_by(lambda x: isinstance(x, miru.TextInput))]
        match self.value:
            case str():
                return True
            case int():
                return input_str.isdigit()
            case _:
                return True
    
    async def callback(self, ctx: miru.ModalContext) -> None:
        input_str = ctx.values[self.get_item_by(lambda x: isinstance(x, miru.TextInput))]
        match self.value:
            case str():
                set_setting(self.category, self.setting, input_str)
            case int():
                set_setting(self.category, self.setting, int(input_str))
        self.stop()


class LevelRoleInputModal(miru.Modal, title="Choose Level"):
    def __init__(self, category: str, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.category = category
        
    lvl = miru.TextInput(
        label="Level",
        required=True
    )

    async def modal_check(self, ctx: miru.ModalContext) -> bool:
        input_str = ctx.values[self.get_item_by(lambda x: isinstance(x, miru.TextInput))]
        return input_str.isdigit()
    
    async def callback(self, ctx: miru.ModalContext) -> None:
        self.stop()


# screens


class OriginalMiruCtxScreen(menu.Screen):
    original_ctx: miru.ViewContext

    def __init__(self, menu, ctx: miru.ViewContext, *args, **kwargs) -> None:
        super().__init__(menu, *args, **kwargs)
        self.original_ctx = ctx


class StrOptionScreen(menu.Screen):
    category: str
    setting: str
    value: str

    def __init__(self, tied_menu: menu.Menu, category: str, data: tuple[str, str], *args, **kwargs) -> None:
        super().__init__(tied_menu, *args, **kwargs)
        self.category = category
        self.setting, self.value = data
        select = self.get_item_by(lambda x: isinstance(x, menu.ScreenTextSelect))
        assert isinstance(select, menu.ScreenTextSelect)
        select.options = [miru.SelectOption(label=option) for option in SETTINGS_STR_OPTIONS[self.setting]]

    async def build_content(self) -> menu.ScreenContent:
        return menu.ScreenContent(f"**{self.setting}**: {self.value}")

    @menu.text_select(
        placeholder="Select an option...",
        options=[miru.SelectOption(label="Something went wrong (don't select this!)")]
    )
    async def setting_select(self, ctx: miru.ViewContext, select: miru.TextSelect) -> None:
        self.value = select.values[0]
        if self.value != "Something went wrong (don't select this!)":
            set_setting(self.category, self.setting, self.value)
        await self.menu.push(make_setting_option_screen(self.category, self.menu, ctx))

    back = BackAllButton()


class ChannelOptionScreen(menu.Screen):
    category: str
    setting: str
    value: None | int | list[int]
    singular: bool

    def __init__(self, tied_menu: menu.Menu, category: str, data: tuple[str, None | int | list[int]], singular: bool = False, *args, **kwargs) -> None:
        super().__init__(tied_menu, *args, **kwargs)
        self.category = category
        self.setting, self.value = data
        self.singular = singular
        select = self.get_item_by(lambda x: isinstance(x, menu.ScreenChannelSelect))
        assert isinstance(select, menu.ScreenChannelSelect)
        if not self.singular:
            select.max_values = 25
            select.placeholder = "Select channels..."

    async def build_content(self) -> menu.ScreenContent:
        self.menu.add_item(ChooseNoneButton())
        return menu.ScreenContent(f"**{self.setting}**: {
            (f'https://discord.com/channels/{GUILD_ID}/{self.value}' if self.value is not None else None)
            if self.singular else ', '.join([
                f'https://discord.com/channels/{GUILD_ID}/{channel}'
                for channel in self.value # type: ignore
            ])
        }")

    @menu.channel_select(
        placeholder="Select a channel...",
        channel_types=[
            hikari.ChannelType.GUILD_NEWS,
            hikari.ChannelType.GUILD_NEWS_THREAD,
            hikari.ChannelType.GUILD_PRIVATE_THREAD,
            hikari.ChannelType.GUILD_PUBLIC_THREAD,
            hikari.ChannelType.GUILD_STAGE,
            hikari.ChannelType.GUILD_TEXT,
            hikari.ChannelType.GUILD_VOICE
        ],
        min_values=0
    )
    async def setting_select(self, ctx: miru.ViewContext, select: miru.ChannelSelect) -> None:
        self.value = select.values[0].id if self.singular else [channel.id for channel in select.values]
        if self.singular:
            set_setting(self.category, self.setting, self.value)
            await self.menu.push(make_setting_option_screen(self.category, self.menu, ctx))
        else:
            self.menu.remove_item(self.menu.get_item_by(lambda x: isinstance(x, ChooseNoneButton)))
            self.menu.add_item(ChooseAddButton())
            self.menu.add_item(ChooseRemoveButton())
            await self.menu.update_message()

    back = BackAllButton(row=4)
       

class UserOptionScreen(menu.Screen):
    category: str
    setting: str
    value: None | int | list[int]
    singular: bool

    def __init__(self, tied_menu: menu.Menu, category: str, data: tuple[str, None | int | list[int]], singular: bool = False, *args, **kwargs) -> None:
        super().__init__(tied_menu, *args, **kwargs)
        self.category = category
        self.setting, self.value = data
        self.singular = singular
        if not self.singular:
            select = self.get_item_by(lambda x: isinstance(x, menu.ScreenUserSelect))
            assert isinstance(select, menu.ScreenUserSelect)
            select.max_values = 25
            select.placeholder = "Select users..."

    async def build_content(self) -> menu.ScreenContent:
        self.menu.add_item(ChooseNoneButton())
        return menu.ScreenContent(f"**{self.setting}**: {
            (f'<@{self.value}>' if self.value is not None else None)
            if self.singular else ', '.join([f'<@{user}>' for user in self.value]) # type: ignore
        }")

    @menu.user_select(
        placeholder="Select a user...",
        min_values=0
    )
    async def setting_select(self, ctx: miru.ViewContext, select: miru.UserSelect) -> None:
        self.value = select.values[0].id if self.singular else [user.id for user in select.values]
        if self.singular:
            set_setting(self.category, self.setting, self.value)
            await self.menu.push(make_setting_option_screen(self.category, self.menu, ctx))
        else:
            self.menu.remove_item(self.menu.get_item_by(lambda x: isinstance(x, ChooseNoneButton)))
            self.menu.add_item(ChooseAddButton())
            self.menu.add_item(ChooseRemoveButton())
            await self.menu.update_message()

    back = BackAllButton(row=4)


class RoleOptionScreen(menu.Screen):
    category: str
    setting: str
    value: None | int | list[int]
    singular: bool

    def __init__(self, tied_menu: menu.Menu, category: str, data: tuple[str, None | int | list[int]], singular: bool = False, *args, **kwargs) -> None:
        super().__init__(tied_menu, *args, **kwargs)
        self.category = category
        self.setting, self.value = data
        self.singular = singular
        if not self.singular:
            select = self.get_item_by(lambda x: isinstance(x, menu.ScreenRoleSelect))
            assert isinstance(select, menu.ScreenRoleSelect)
            select.max_values = 25
            select.placeholder = "Select roles..."

    async def build_content(self) -> menu.ScreenContent:
        self.menu.add_item(ChooseNoneButton())
        return menu.ScreenContent(f"**{self.setting}**: {
            (f'<@&{self.value}>' if self.value is not None else None)
            if self.singular else ', '.join([f'<@&{role}>' for role in self.value]) # type: ignore
        }")

    @menu.role_select(
        placeholder="Select a role...",
        min_values=0
    )
    async def setting_select(self, ctx: miru.ViewContext, select: miru.RoleSelect) -> None:
        self.value = select.values[0].id if self.singular else [role.id for role in select.values]
        if self.singular:
            set_setting(self.category, self.setting, self.value)
            await self.menu.push(make_setting_option_screen(self.category, self.menu, ctx))
        else:
            self.menu.remove_item(self.menu.get_item_by(lambda x: isinstance(x, ChooseNoneButton)))
            self.menu.add_item(ChooseAddButton())
            self.menu.add_item(ChooseRemoveButton())
            await self.menu.update_message()

    back = BackAllButton(row=4)


class SettingCategoryScreen(OriginalMiruCtxScreen):
    category: str
    id: int = random.randint(0, 999999)

    def __init__(self, tied_menu: menu.Menu, *args, **kwargs) -> None:
        super().__init__(tied_menu, *args, **kwargs)
        self.get_item_by(lambda x: isinstance(x, menu.ScreenTextSelect)).options = [ # type: ignore
            miru.SelectOption(
                label=setting,
                description=(str(value)[:97] + '...') if len(str(value)) > 97 else str(value)
            )
            for setting, value in settings[self.category].items()
            if setting not in DISABLED_SETTINGS[self.category]
        ]

    async def build_content(self) -> menu.ScreenContent:
        return menu.ScreenContent(
            embed=hikari.Embed(
                title=f"{self.category.removesuffix('s')} Settings",
                description="\n".join([
                    f"- **{setting}**: {value}"
                    for setting, value in settings[self.category].items()
                ])
            )
        )

    @menu.text_select(
        placeholder="Select a setting to modify...",
        options=[miru.SelectOption(label="Something went wrong (don't select this!)")]
    )
    async def setting_select(self, ctx: miru.ViewContext, select: miru.TextSelect) -> None:
        setting = select.values[0]
        setting_tuple = (setting, settings[self.category][setting])
        match setting:
            case "Message XP" | "Yearly" | "Monthly" | "Weekly" | "Daily":
                set_setting(self.category, setting, not settings[self.category][setting])
                await self.menu.push(make_setting_option_screen(self.category, self.menu, ctx))
            case "Level Up Channel" | "Manual XP Channel":
                await self.menu.push(ChannelOptionScreen(self.menu, self.category, setting_tuple, singular=True))
            case "Denied Channels":
                await self.menu.push(ChannelOptionScreen(self.menu, self.category, setting_tuple))
            case "Denied Users":
                await self.menu.push(UserOptionScreen(self.menu, self.category, setting_tuple))
            case "Denied Roles":
                await self.menu.push(RoleOptionScreen(self.menu, self.category, setting_tuple))
            case "Rank Cards":
                await self.menu.push(StrOptionScreen(self.menu, self.category, setting_tuple))
            case "Level Up Message" | "Cooldown Seconds" | "Minimum XP" | "Maximum XP":
                modal = CheckInputModal(ctx, self.category, setting_tuple, self.menu, title=f"Modify {setting}")
                await ctx.respond_with_modal(modal)
                await modal.wait()
                if modal.last_context is not None:
                    await modal.last_context.defer()
                else:
                    modal.stop()
                await self.menu.push(make_setting_option_screen(self.category, self.menu, self.original_ctx))

    back = BackAllButton()


class CalculationScreen(SettingCategoryScreen):
    category = "Calculation"


class DenylistScreen(SettingCategoryScreen):
    category = "Denylist"

    async def build_content(self) -> menu.ScreenContent:
        denylist_settings: dict = settings[self.category]
        (deny_channels, deny_roles, deny_users) = list(denylist_settings.values())[-3:]
        
        return menu.ScreenContent(
            embed=hikari.Embed(
                title=f"{self.category} Settings",
                description="\n".join([
                    *[
                        f"- **{setting}**: {value}"
                        for setting, value in dict(list(denylist_settings.items())[:-3])
                    ],
                    f"- Denied Channels: {', '.join([
                        f'https://discord.com/channels/{GUILD_ID}/{channel}'
                        for channel in deny_channels
                    ]) or None}",
                    f"- Denied Roles: {', '.join([f'<@&{role}>' for role in deny_roles]) or None}",
                    f"- Denied Users: {', '.join([f'<@{user}>' for user in deny_users]) or None}"
                ])
            )
        )
    

class LeaderboardScreen(SettingCategoryScreen):
    category = "Leaderboards"


class LevelRoleScreen(OriginalMiruCtxScreen):
    value: list[int]

    def __init__(self, tied_menu: menu.Menu, *args, **kwargs) -> None:
        super().__init__(tied_menu, *args, **kwargs)

    async def build_content(self) -> menu.ScreenContent:
        self.menu.add_item(RemoveAllLevelRoleButton())
        return menu.ScreenContent(
            embed=hikari.Embed(
                title=f"Level Role Settings",
                description="\n".join([
                    f"- <@&{role}>: Level {lvl}"
                    for role, lvl in settings["Level Roles"].items()
                ]) or "No active level roles."
            )
        )

    @menu.role_select(
        placeholder="Select roles...",
        min_values=0,
        max_values=25
    )
    async def setting_select(self, ctx: miru.ViewContext, select: miru.RoleSelect) -> None:
        self.value = [role.id for role in select.values]
        self.menu.remove_item(self.menu.get_item_by(lambda x: isinstance(x, RemoveAllLevelRoleButton)))
        self.menu.add_item(AddEditLevelRoleButton())
        self.menu.add_item(RemoveLevelRoleButton())
        await self.menu.update_message()

    back = BackAllButton(row=4)
    

class LevelUpMessageScreen(SettingCategoryScreen):
    category = "Level Up Messages"

    async def build_content(self) -> menu.ScreenContent:
        level_up_settings: dict = settings[self.category]
        (channel, message) = list(level_up_settings.values())[-2:]

        user = self.original_ctx.user
        level = random.randint(1, 100)
        return menu.ScreenContent(
            embed=hikari.Embed(
                title=f"{self.category} Settings",
                description="\n".join([
                    *[
                        f"- **{setting}**: {value}"
                        for setting, value in dict(list(level_up_settings.items())[:-2])
                    ],
                    f"- **Channel**: {f'https://discord.com/channels/{GUILD_ID}/{channel}' or None}",
                    f"- **Message**: {
                        f'\n{message.format_map(locals())}'
                        if message is not None else 'Default'
                    }"
                ])
            )
        )
    

class RankCardScreen(SettingCategoryScreen):
    category = "Rank Cards"

    async def build_content(self) -> menu.ScreenContent:
        rank_card_settings: dict = settings[self.category]
        (xp_bar_color,) = list(rank_card_settings.values())[-1:]
        
        return menu.ScreenContent(
            embed=hikari.Embed(
                title=f"{self.category} Settings",
                description="\n".join([
                    *[
                        f"- **{setting}**: {value}"
                        for setting, value in dict(list(rank_card_settings.items())[:-1])
                    ],
                    f"**XP Bar Color**: ```ansi\n{make_ansi(
                        xp_bar_color, [f'{xp_bar_color} Text', 'Bold']
                    )}```"
                ])
            )
        )

class LoggingChannelScreen(SettingCategoryScreen):
    category = "Logging Channels"

    async def build_content(self) -> menu.ScreenContent:
        return menu.ScreenContent(
            embed=hikari.Embed(
                title=f"{self.category} Settings",
                description="\n".join([
                    f"- **{setting}**: {f'https://discord.com/channels/{GUILD_ID}/{channel}' if channel else channel}"
                    for setting, channel in settings[self.category].items()
                ])
            )
        )


class SettingsScreen(menu.Screen):
    async def build_content(self) -> menu.ScreenContent:
        return menu.ScreenContent(
            embed=hikari.Embed(
                title="Settings",
                description="\n".join([
                    f"- **{category}**: {desc}"
                    for category, desc in SETTINGS_DESC.items()
                ])
            )
        )
    
    @menu.text_select(
        placeholder="Select a category to view...",
        options=[
            miru.SelectOption(label=category, description=desc)
            for category, desc in SETTINGS_DESC.items()
        ]
    )
    async def setting_select(self, ctx: miru.ViewContext, select: miru.TextSelect) -> None:
        await self.menu.push(make_setting_option_screen(select.values[0], self.menu, ctx))


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

    channel = settings["Level Up Messages"]["Level Up Channel"]
    message = (
        settings["Level Up Messages"]["Level Up Message"]
        or "{user} is now level {level}!"
    ).replace("{level}", "{lvl}").format_map(locals())
    if channel is not None:
        await app.rest.create_message(channel, embed=hikari.Embed(description=message))


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


# logging


async def log_manual_xp(ctx: crescent.Context, xp=None) -> None:
    channel_id = settings["Logging Channels"]["Manual XP Channel"]
    if channel_id is None:
        return
    
    cmd_user = ctx.user
    arg_user: hikari.User = ctx.options.get("user", ctx.user)

    message = {
        "set": f"{cmd_user.mention} set {arg_user.mention}'s XP to {xp}",
        "add": f"{cmd_user.mention} added {xp} XP to {arg_user.mention}",
        "remove": f"{cmd_user.mention} removed {xp} XP from {arg_user.mention}",
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


async def is_human_hook(event: hikari.MessageCreateEvent) -> crescent.HookResult:
    return crescent.HookResult(exit=event.message.author.is_bot)


async def is_correct_guild_msg_create_hook(event: hikari.MessageCreateEvent):
    return crescent.HookResult(exit=event.message.guild_id != GUILD_ID)


async def is_bot_xp_hook(ctx: crescent.Context) -> crescent.HookResult:
    user = ctx.options.get("user", ctx.user)
    if not user.is_bot:
        return crescent.HookResult()

    if user.id == ctx.application_id:
        await ctx.respond("~~Someday~~ I mean what?")
    else:
        await ctx.respond("We bots don't earn xp...")
    return crescent.HookResult(exit=True)


async def is_xp_or_lvl_hook(ctx: crescent.Context) -> crescent.HookResult:
    if ctx.options.get("xp") is None == ctx.options.get("lvl") is None:
        await ctx.respond(
            "You must specify either an xp amount or a level amount.",
            ephemeral=True
        )
        return crescent.HookResult(exit=True)
    return crescent.HookResult()


async def manage_cooldown_hook(event: hikari.MessageCreateEvent) -> None:
    user = event.message.author
    if user.id in ids_on_cooldoWn or await user_xp_denied(event.message.channel_id, user.id, event.app):
        return
    
    ids_on_cooldoWn.add(user.id)
    await asyncio.sleep(settings["Calculation"]["Cooldown Seconds"])
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


# events


@plugin.include
@crescent.hook(is_correct_guild_msg_create_hook)
@crescent.hook(is_human_hook)
@crescent.hook(manage_cooldown_hook, after=True)
@crescent.event
async def on_message_create(event: hikari.MessageCreateEvent) -> None:
    await handle_msg_xp_gain(event)


# scheduled tasks


@plugin.include
@tasks.cronjob("*/30 * * * *", on_startup=True)
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
        if not xp_time_is_enabled(self.time):
            await ctx.respond("This leaderboard is disabled.", ephemeral=True)
            return

        xp_time = ALL_XP_TIMES[self.time]
        xp_time_pretty = ALL_XP_TIMES_PRETTY[ALL_XP_TIMES.index(xp_time)]
        timestamp = make_timestamp(datetime.now(timezone.utc))

        max_pages = ceildiv(await get_size_xp_db(xp_time), 10)
        if max_pages == 0:
            await ctx.respond(embed=hikari.Embed(
                title=f"Leaderboard{': ' + xp_time_pretty if xp_time != 'alltimexp' else ''}",
                description="No data for this leaderboard yet; limbillions must chat."
            ).set_footer(timestamp))
            return

        # i have not made a list comprehension like this in years okay
        # let me have this
        lb_nav = nav.NavigatorView(pages=[
            hikari.Embed(
                title=f"Leaderboard{': ' + xp_time_pretty if xp_time != 'alltimexp' else ''}",
                description="\n".join([
                    f"{(page - 1) * 10 + i + 1}. <@{id}> · Level {get_lvl(xp)} · {xp} XP"
                    for i, (id, xp) in enumerate(await get_xp_db_bulk(page, xp_time))
                ])
            ).set_footer(timestamp)
            for page in range(1, max_pages + 1)
        ])

        miru_client = ctx.client.model.miru_client
        assert isinstance(miru_client, miru.Client)
        builder = await lb_nav.build_response_async(miru_client)
        await ctx.respond_with_builder(builder)
        miru_client.start_view(lb_nav)


xp_group = crescent.Group(
    name="xp",
    description="xp management commands",
    hooks=[is_bot_xp_hook],
    default_member_permissions=hikari.Permissions.MANAGE_GUILD
)


@plugin.include
@xp_group.child
@crescent.hook(is_xp_or_lvl_hook)
@crescent.command(
    name="set",
    description="set xp of user"
)
class SetXPCommand:
    user = crescent.option(hikari.User, "user to set xp of")
    xp = crescent.option(int, "xp amount to set", default=None)
    lvl = crescent.option(int, "level amount to set", default=None)

    async def callback(self, ctx: crescent.Context) -> None:
        xp = self.xp if self.xp is not None else get_xp_for_lvl(ctx.options.get("lvl", 0))
        old_xp = await get_xp_db(self.user.id)

        try:
            await set_xp_db(self.user.id, xp)
        except aiosqlite.OperationalError:
            await ctx.respond("Something went wrong updating the data.", ephemeral=True)
        else:
            await handle_xp_update(self.user, xp - old_xp, ctx.app)
            await ctx.respond(f"Set xp of {self.user.username} to {xp}.")
            await log_manual_xp(ctx, xp)


@plugin.include
@xp_group.child
@crescent.hook(is_xp_or_lvl_hook)
@crescent.command(
    name="add",
    description="add xp to user"
)
class AddXPCommand:
    user = crescent.option(hikari.User, "user to add xp to")
    xp = crescent.option(int, "xp amount to add", default=None)
    lvl = crescent.option(int, "level amount to add", default=None)

    async def callback(self, ctx: crescent.Context) -> None:
        xp = self.xp
        if xp is None:
            current_lvl = get_lvl(await get_xp_db(self.user.id))
            xp = get_xp_for_lvl(current_lvl + ctx.options.get("lvl", 0)) - get_xp_for_lvl(current_lvl)
        
        try:
            await add_xp_db(self.user.id, xp)
        except aiosqlite.OperationalError:
            await ctx.respond("Something went wrong updating the data.", ephemeral=True)
        else:
            await handle_xp_update(self.user, xp, ctx.app)
            await ctx.respond(f"Added {xp} xp to {self.user.username}.")
            await log_manual_xp(ctx, xp)


@plugin.include
@xp_group.child
@crescent.hook(is_xp_or_lvl_hook)
@crescent.command(
    name="remove",
    description="remove xp from user"
)
class RemoveXPCommand:
    user = crescent.option(hikari.User, "user to remove xp from")
    xp = crescent.option(int, "xp amount to remove", default=None)
    lvl = crescent.option(int, "level amount to remove", default=None)

    async def callback(self, ctx: crescent.Context) -> None:
        xp = self.xp
        if xp is None:
            current_lvl = get_lvl(await get_xp_db(self.user.id))
            xp = get_xp_for_lvl(current_lvl) - get_xp_for_lvl(current_lvl - ctx.options.get("lvl", 0))
        
        try:
            await remove_xp_db(self.user.id, xp)
        except aiosqlite.OperationalError:
            await ctx.respond(
                "Something went wrong updating the data.",
                ephemeral=True
            )
        else:
            await handle_xp_update(self.user, -xp, ctx.app)
            await ctx.respond(f"Removed {xp} xp from {self.user.username}.")
            await log_manual_xp(ctx, xp)


@plugin.include
@xp_group.child
@crescent.command(
    name="reset",
    description="reset xp of user"
)
class ResetXPCommand:
    user = crescent.option(hikari.User, "user to reset xp of")

    async def callback(self, ctx: crescent.Context) -> None:
        old_xp = await get_xp_db(self.user.id)
        try:
            await reset_xp_db(self.user.id)
        except aiosqlite.OperationalError:
            await ctx.respond("Something went wrong updating the data.", ephemeral=True)
        else:
            await handle_xp_update(self.user, -old_xp, ctx.app)
            await ctx.respond(f"Reset xp of {self.user.username}.")
            await log_manual_xp(ctx)


allxp_group = crescent.Group(
    name="allxp",
    description="all xp management commands",
    hooks=[is_bot_xp_hook],
    default_member_permissions=hikari.Permissions.MANAGE_GUILD
)


@plugin.include
@allxp_group.child
@crescent.hook(confirmation_hook)
@crescent.command(
    name="import",
    description="replaces all xp data with imported data",
)
class ImportXPCommand:
    file = crescent.option(hikari.Attachment, "db file to import")

    async def callback(self, ctx: crescent.Context) -> None:
        if not self.file.filename.endswith(".db"):
            await ctx.respond("File must be an SQLite database.", ephemeral=True)
            return

        await ctx.edit("Importing...")
        with open("bot/data/xp.db", "wb") as f:
            f.write(await self.file.read())
        await ctx.edit("Import complete.")


@plugin.include
@allxp_group.child
@crescent.command(
    name="export",
    description="exports all xp data to a file",
)
async def export_xp(ctx: crescent.Context) -> None:
    await ctx.respond("Exporting...")
    date = datetime.now(timezone.utc).strftime('%Y%m%d')
    with (
        open("bot/data/xp.db", "rb") as f,
        open(f"bot/data/exports/xp_{date}.db", "wb") as export
    ):
        export.write(f.read())
    await ctx.edit("Export complete.", attachment=f"bot/data/exports/xp_{date}.db")
    os.remove(f"bot/data/exports/xp_{date}.db")


@plugin.include
@allxp_group.child
@crescent.hook(confirmation_hook)
@crescent.command(
    name="reset",
    description="removes all xp data & creates a new xp storage",
)
async def reset_guild_xp(ctx: crescent.Context) -> None:
    await ctx.edit("Resetting...")
    for xp_time in ALL_XP_TIMES:
        await init_xp_table_db(xp_time)
    await ctx.edit("Blank XP storage created.")


@plugin.include
@crescent.command(
    name="settings",
    description="view & edit bot settings"
)
async def view_settings(ctx: crescent.Context) -> None:
    miru_client = ctx.client.model.miru_client
    settings_menu = menu.Menu()
    builder = await settings_menu.build_response_async(miru_client, SettingsScreen(settings_menu))
    await ctx.respond_with_builder(builder)
    miru_client.start_view(settings_menu)