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

miru_client = miru.Client(bot)
miru_client.set_unhandled_component_interaction_hook(unhandled_comp_hook)
model = Model(miru_client)

client = crescent.Client(bot, model)
client.plugins.load_folder("bot.plugins")

# this is locational:
# the code above loads ~/bot/plugins as the folder for plugins
# putting it higher messes with that
# but frankly, any code that can mess with that shouldn't be here
# this is here so that it isn't in both the plugin and model, because it runs here first
# but this is still stupid
# so TODO find a better way
os.chdir(f"{os.getcwd()}/bot/data")

bot.subscribe(hikari.StartingEvent, model.on_start)
bot.subscribe(hikari.StoppedEvent, model.on_stop)

bot.run()
