import random
import os
import asyncio
import json
import typing
from datetime import datetime, timezone
from collections import Counter

import crescent
import hikari
import miru
import aiosqlite
from crescent.ext import cooldowns
from datetime import datetime, timezone

from bot.pprintify import pprintify
from bot.model import Model

plugin = crescent.Plugin[hikari.GatewayBot, Model]()

@plugin.include
@crescent.command(
    name="ping",
    description="ping pong"
)
async def ping(ctx: crescent.Context) -> None:
    bot = ctx.client.model.bot
    assert isinstance(bot, hikari.GatewayBot)
    await ctx.respond(f"Pong!\n-# Latency: {int(bot.heartbeat_latency * 1000)}ms")