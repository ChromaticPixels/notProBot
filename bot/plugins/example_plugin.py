import crescent
import hikari
import random
import os
import json
import asyncio
import miru
from bot.pprintify import pprintify
from collections import Counter

from bot.model import MyModel

plugin = crescent.Plugin[hikari.GatewayBot, MyModel]()

os.chdir(f"{os.getcwd()}/bot/plugins")

# view with crescent context passed for additional utility
class ContextView(miru.View):
    def __init__(self, ctx: crescent.Context, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.crescent_ctx = ctx

# view for tea games
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

@plugin.include
@crescent.command
async def ping(ctx: crescent.Context) -> None:
    view = TeaView(ctx, timeout=3.0)
    await ctx.respond("Pong!", components=view)
    ctx.client.model.miru.start_view(view)

@plugin.include
@crescent.command
async def word(ctx: crescent.Context) -> None:
    with open("scrabble_words_24.txt", "r") as wordsFile:
        randWord = random.choice(wordsFile.readlines())
    await ctx.respond(randWord)

@plugin.include
@crescent.command
async def three(ctx: crescent.Context) -> None:
    with open("scrabble_threes_24.json", "r") as threesFile:
        randThree = ", ".join(str(i) for i in random.choice(Counter(json.load(threesFile)).most_common(1400)))
    await ctx.respond(randThree)

async def tea_hook(ctx: crescent.Context) -> None:
    star_msg = await ctx.respond("Star        walker")
    await asyncio.sleep(5)


@plugin.include
@crescent.hook(tea_hook)
@crescent.command
async def tea(ctx: crescent.Context) -> None:
    await ctx.respond("piss tea")