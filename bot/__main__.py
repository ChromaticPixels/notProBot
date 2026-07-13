import os
import crescent
import dotenv
import hikari
import asyncio
import uvloop
import miru

from bot.model import Model

uvloop.install()
asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

dotenv.load_dotenv()

bot = hikari.GatewayBot(os.environ["TOKEN"])

async def unhandled_comp_hook(inter: hikari.ComponentInteraction) -> None:
    await inter.create_initial_response(
        hikari.ResponseType.MESSAGE_CREATE,
        "Something went wrong, or the interaction expired.",
        flags=hikari.MessageFlag.EPHEMERAL
    )

async def is_guild_command_hook(ctx: crescent.Context):
    if ctx.interaction.context != hikari.ApplicationContextType.GUILD:
        await ctx.respond("DMs? I don't know... that's scary...", ephemeral=True)
        return crescent.HookResult(exit=True)
    return crescent.HookResult()

miru_client = miru.Client(bot)
miru_client.set_unhandled_component_interaction_hook(unhandled_comp_hook)
model = Model(bot, miru_client)

client = crescent.Client(bot, model, command_hooks=[is_guild_command_hook])
client.plugins.load_folder("bot.plugins")

bot.subscribe(hikari.StartingEvent, model.on_start)
bot.subscribe(hikari.StoppedEvent, model.on_stop)

bot.run()
