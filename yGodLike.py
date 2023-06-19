import itertools
import re
import os
import asyncio
from collections import deque
from random import shuffle
import sys
import traceback
from typing import Optional, List

import discord
from discord import app_commands
from discord.ext import commands
import wavelink
from aiohttp import ClientSession
import humanize


URL_REG = re.compile(r'https?://(?:www\.)?.+')

# você pode obter lista de servidores lavalink no link abaixo:
# https://lavalink.darrennathanael.com/SSL/lavalink-with-ssl/
# ou hospede um próprio e preencha os dados conforme o modelo abaixo

lavalink_servers = [

    {
        'host': 'lavalink.devamop.in',
        'port': 443,
        'password': 'DevamOP',
        'identifier': 'devamop',
        'region': 'us_central',
        'secure': True,
    },

    {
        'host': 'purr.aikomechawaii.live',
        'port': 10415,
        'password': 'AnythingAsPassword',
        'identifier': 'aikomechawaii',
        'region': 'us_central',
        'secure': False,
    }

]


def get_button_style(enabled: bool, red=True):
    if enabled:
        if red:
            return discord.ButtonStyle.red
        return discord.ButtonStyle.green
    return discord.ButtonStyle.grey


async def has_perm(ctx):
    try:
        ctx.player = ctx.bot.music.players[ctx.guild.id]
    except KeyError:
        return True

    if ctx.author in ctx.player.dj:
        return True

    if ctx.author.guild_permissions.manage_channels:
        return True

    vc = ctx.bot.get_channel(ctx.player.channel_id)

    if ctx.bot.intents.members and not [m for m in vc.members if
                                        not m.bot and (m.guild_permissions.manage_channels or m in ctx.player.dj)]:
        ctx.player.dj.append(ctx.author)
        await ctx.send(embed=discord.Embed(
            description=f"{ctx.author.mention} foi adicionado à lista de DJ's por não haver um no canal <#{vc.id}>.",
            color=ctx.me.color))
        return True

##################################
######## perms decorators ########
##################################

def has_player():
    def predicate(ctx):

        try:
            ctx.player = ctx.bot.music.players[ctx.guild.id]
        except AttributeError:
            raise NoPlayer()

        return True

    return commands.check(predicate)


def is_dj():
    async def predicate(ctx):

        try:
            ctx.player = ctx.bot.music.players[ctx.guild.id]
        except KeyError:
            raise NoPlayer()

        if ctx.player.restrict_mode and not await has_perm(ctx):
            raise NotDJorStaff()

        return True

    return commands.check(predicate)


def is_requester():
    async def predicate(ctx):

        try:
            ctx.player = ctx.bot.music.players.get(ctx.guild.id)
        except KeyError:
            raise NoPlayer()

        if not ctx.player.current:
            raise NoSource()

        if ctx.player.current.requester == ctx.author:
            return True

        try:
            if await has_perm(ctx):
                return True
        except NotDJorStaff:
            pass

        raise NotRequester()

    return commands.check(predicate)


def check_voice():
    def predicate(ctx):

        try:
            if ctx.author.voice.channel != ctx.me.voice.channel:
                raise DiffVoiceChannel()
        except AttributeError:
            pass

        if not ctx.author.voice:
            raise NoVoice()

        return True

    return commands.check(predicate)


def has_source():
    def predicate(ctx):

        try:
            ctx.player
        except:
            ctx.player = ctx.bot.music.players.get(ctx.guild.id)

        if not ctx.player:
            raise NoPlayer()

        if not ctx.player.current:
            raise NoSource()

        return True

    return commands.check(predicate)


def user_cooldown(rate: int, per: int):
    def custom_cooldown(message):
        if message.author.guild_permissions.administrator:
            return None  # sem cooldown

        return commands.Cooldown(rate, per)

    return custom_cooldown


########################
##### Converters #######
########################

def fix_characters(text: str, limit: int = 0):
    replaces = [
        ('&quot;', '"'),
        ('&amp;', '&'),
        ('(', '\u0028'),
        (')', '\u0029'),
        ('[', '【'),
        (']', '】'),
        ("  ", " "),
        ("*", '"'),
        ("_", ' '),
        ("{", "\u0028"),
        ("}", "\u0029"),
    ]
    for r in replaces:
        text = text.replace(r[0], r[1])

    if limit:
        text = f"{text[:limit]}..." if len(text) > limit else text

    return text


def time_format(milliseconds):
    m, s = divmod(int(milliseconds / 1000), 60)
    h, m = divmod(m, 60)

    strings = f"{m:02d}:{s:02d}"

    if h:
        strings = f"{h}:{strings}"

    return strings


def seek_parser(time):
    try:
        time = str(time).split(':')
        if len(time) > 1:
            return int(time[0]) * 60 + int(time[1])
        else:
            return int(time[0])
    except ValueError:
        return


def get_track_index(ctx, query):
    index = None

    player: CustomPlayer = ctx.player

    for counter, track in enumerate(player.queue):

        if query.lower() in track.title.lower() or \
                all(elem in track.title.lower().split() for elem in query.lower().split()):
            index = counter
            break

    return index

########################
### Classe de testes ###
########################

class TestBot(commands.Bot):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def setup_bot(self):
        await self.wait_until_ready()
        await self.tree.sync()
        print(f'Logado como: {self.user} [{self.user.id}]')

    async def setup_hook(self):
        await self.add_cog(Music(self))
        self.loop.create_task(self.setup_bot())


##################
##### Errors #####
##################

class NoPlayer(commands.CheckFailure):
    pass


class NoVoice(commands.CheckFailure):
    pass


class DiffVoiceChannel(commands.CheckFailure):
    pass


class NoSource(commands.CheckFailure):
    pass


class NotDJorStaff(commands.CheckFailure):
    pass


class NotRequester(commands.CheckFailure):
    pass


##############################
##### Paginator Classes ######
##############################

class QueueInteraction(discord.ui.View):

    def __init__(self, player, user: discord.Member, timeout=60):

        self.player = player
        self.user = user
        self.pages = []
        self.current = 0
        self.max_page = len(self.pages) - 1
        super().__init__(timeout=timeout)
        self.embed = discord.Embed(color=user.guild.me.colour)
        self.update_pages()
        self.update_embed()

    def update_pages(self):

        counter = 1

        entries = list(self.player.queue)

        self.pages = [entries[i:i + 8] for i in range(0, len(entries), 8)]

        for n, page in enumerate(self.pages):

            txt = "\n"
            for t in page:
                txt += f"`{counter})` [`{fix_characters(t.title, limit=50)}`]({t.uri})\n" \
                       f"`[{time_format(t.duration) if not t.is_stream else '🔴 Livestream'}]`" + \
                       f" - {t.requester.mention}\n`---------`\n"

                counter += 1

            self.pages[n] = txt

        self.current = 0
        self.max_page = len(self.pages) - 1

    def update_embed(self):
        self.embed.title = f"**Músicas na fila [{self.current + 1} / {self.max_page + 1}]**"
        self.embed.description = self.pages[self.current]

    @discord.ui.button(emoji='⏮️', style=discord.ButtonStyle.grey)
    async def first(self, interaction: discord.Interaction, button):

        self.current = 0
        self.update_embed()
        await interaction.response.edit_message(embed=self.embed)

    @discord.ui.button(emoji='⬅️', style=discord.ButtonStyle.grey)
    async def back(self, interaction: discord.Interaction, button):

        if self.current == 0:
            self.current = self.max_page
        else:
            self.current -= 1
        self.update_embed()
        await interaction.response.edit_message(embed=self.embed)

    @discord.ui.button(emoji='➡️', style=discord.ButtonStyle.grey)
    async def next(self, interaction: discord.Interaction, button):

        if self.current == self.max_page:
            self.current = 0
        else:
            self.current += 1
        self.update_embed()
        await interaction.response.edit_message(embed=self.embed)

    @discord.ui.button(emoji='⏭️', style=discord.ButtonStyle.grey)
    async def last(self, interaction: discord.Interaction, button):

        self.current = self.max_page
        self.update_embed()
        await interaction.response.edit_message(embed=self.embed)

    @discord.ui.button(emoji='⏹️', style=discord.ButtonStyle.grey)
    async def stop_interaction(self, interaction: discord.Interaction, button):

        await interaction.response.edit_message(content="Queue fechada", embed=None, view=None)
        self.stop()

    @discord.ui.button(emoji='🔄', label="Recarregar lista", style=discord.ButtonStyle.grey)
    async def update_q(self, interaction: discord.Interaction, button):

        self.update_pages()
        self.update_embed()
        await interaction.response.edit_message(embed=self.embed)


##########################
##### Music Classes ######
##########################

class PlayerInteractions(discord.ui.View):

    def __init__(self, ctx):
        self.ctx = ctx

        super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction):

        player = self.ctx.bot.music.players.get(interaction.guild.id)

        if player.interaction_cooldown:
            await interaction.response.send_message("O player está em cooldown, tente novamente em instantes.",
                                                    ephemeral=True)
            return

        vc = self.ctx.bot.get_channel(player.channel_id)

        if interaction.user not in vc.members:
            embed = discord.Embed(
                description=f"Você deve estar no canal <#{vc.id}> para usar isto.",
                color=discord.Colour.red()
            )

            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        control = interaction.data.get("custom_id")[12:]

        kwargs = {}

        if control == "help":
            embed = discord.Embed(
                description=f"📘 **IFORMAÇÕES SOBRE OS BOTÕES** 📘\n\n"
                            f"⏯️ `= Pausar/Retomar a música`\n"
                            f"⏮️ `= Voltar para a música tocada anteriormente`\n"
                            f"⏭️ `= Pular para a próxima música`\n"
                            f"⏪ `= Voltar o tempo da música em 20 seg.`\n"
                            f"⏩ `= Avançar o tempo da música em 20 seg.`\n"
                            f"⏹️ `= Parar o player e me desconectar do canal`\n"
                            f"🔀 `= Misturar as músicas da fila`\n"
                            f"🇳 `= Ativar/Desativar o efeito Nightcore`\n"
                            f"🔁 `= Ativar/Desativar repetição da música`",
                color=self.ctx.me.color
            )

            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        elif control == "fastbackward":
            control = "seek"
            kwargs = {"pos": time_format(player.position - 20000)}

        elif control == "fastforward":
            control = "seek"
            kwargs = {"pos": time_format(player.position + 20000)}

        elif control == "playpause":
            control = "pause" if not player.paused else "resume"

        elif control == "loop":
            if player.loop == "current":
                control = "loopqueue"
            elif player.loop == "queue":
                pass
            else:
                player.loop = False

        ctx = await player.bot.get_context(interaction.message)
        cmd: commands.Command = player.bot.get_command(control)

        ctx.command = cmd
        ctx.player = player
        ctx.author = interaction.user
        ctx.interaction = interaction
        ctx.channel = interaction.channel

        try:

            bucket = cmd._buckets.get_bucket(ctx)
            if bucket:
                retry_after = bucket.update_rate_limit()
                if retry_after:
                    raise commands.CommandOnCooldown(cooldown=bucket, retry_after=retry_after, type=cmd._buckets.type)

            await cmd(ctx, **kwargs)

            player.interaction_cooldown = True
            await asyncio.sleep(1)
            player.interaction_cooldown = False

        except Exception as e:
            await ctx.cog.cog_command_error(ctx, e)


class CustomTrack(wavelink.Track):

    def __init__(self, *args, **kwargs):
        self.requester = kwargs.pop('requester')
        args[1]['title'] = fix_characters(args[1]['title'])
        super().__init__(*args, **kwargs)
        if self.ytid:
            self.thumb = f"https://img.youtube.com/vi/{self.ytid}/mqdefault.jpg"
        else:
            self.thumb = self.info.get("artworkUrl", "")


class CustomPlayer(wavelink.Player):

    def __init__(self, *args, **kwargs):
        self.ctx: commands.Context = kwargs.pop('ctx')
        super().__init__(*args, **kwargs)
        self.text_channel: discord.TextChannel = self.ctx.channel
        self.message: Optional[discord.Message] = None
        self.queue = deque()
        self.played = deque(maxlen=20)
        self.nightcore = False
        self.dj = [] if self.ctx.author.guild_permissions.manage_channels else [self.ctx.author]
        self.loop = False
        self.last_track: Optional[CustomTrack] = None
        self.locked = False
        self.idle = None
        self.idle_timeout = 180  # aguardar 3 minutos para adicionar novas músicas
        self.is_previows_music = False
        self.updating_message = None
        self.command_log = ""
        self.last_embed = None
        self.interaction_cooldown = False
        self.votes = set()
        self.force_edit_message = False
        self.restrict_mode = False

    async def destroy(self, *, force: bool = False):
        try:
            self.idle.cancel()
        except:
            pass

        await self.destroy_message()

        await super().destroy()

    async def idling_mode(self):

        await self.destroy_message()

        self.view = PlayerInteractions(self.ctx)

        self.view.add_item(discord.ui.Button(emoji="⏮️", custom_id=f"musicplayer_back", label="Voltar"))
        self.view.add_item(discord.ui.Button(emoji="⏹️", custom_id=f"musicplayer_stop", label="Parar"))

        embed = discord.Embed(
            description=f"**Não há músicas na fila. Adicione uma música ou use um dos botões abaixo\n\n"
                        f"[O Player será desligado em: {time_format(self.idle_timeout * 1000)}]**",
            color=self.ctx.me.colour
        )
        self.message = await self.text_channel.send(embed=embed, view=self.view)

        await asyncio.sleep(self.idle_timeout)
        embed = discord.Embed(description="**O player foi desligado por inatividade...**",
                              color=discord.Colour.dark_gold())
        self.bot.loop.create_task(self.text_channel.send(embed=embed))
        self.bot.loop.create_task(self.destroy())
        return

    async def process_next(self):

        if self.locked:
            return

        try:
            track = self.queue.popleft()
        except:
            self.idle = self.bot.loop.create_task(self.idling_mode())
            return

        try:
            self.idle.cancel()
        except:
            pass

        self.locked = True

        self.last_track = track

        await self.play(track)

        self.locked = False

    async def invoke_np(self, force=False, interaction=None):

        if not self.current:
            return

        embed = discord.Embed(color=self.ctx.me.color)
        embed_top = discord.Embed(
            color=self.ctx.me.color,
            description=f"> [**{self.current.title}**]({self.current.uri})"
        )

        if not self.paused:
            embed_top.set_author(
                name="Tocando Agora:",
                icon_url="https://cdn.discordapp.com/attachments/480195401543188483/895862881105616947/music_equalizer.gif"
            )
        else:
            embed_top.set_author(
                name="Em Pausa:",
                icon_url="https://cdn.discordapp.com/attachments/480195401543188483/896013933197013002/pause.png"
            )
        embed_top.set_thumbnail(url=self.current.thumb)

        if self.current.is_stream:
            duration = "🔴 **⠂Livestream**"
        else:
            duration = f"⏰ **⠂Duração:** `{time_format(self.current.duration)}`"

        txt = f"> {duration}\n" \
              f"> 💠 **⠂Uploader**: `{self.current.author}`\n" \
              f"> 🎧 **⠂Pedido por:** {self.current.requester.mention}\n" \
              f"> 🔊 **⠂Volume:** `{self.volume}%`"

        txt += "\n"

        if self.restrict_mode:
            txt += "> 🔒 **⠂Modo restrito: `ativado`\n"

        if self.command_log:
            txt += f"> 📈 **⠂Última Interação:** {self.command_log}\n"

        if len(self.queue):

            txt += "```ansi\n[34;1mPróximas Músicas:[0m```"
            txt += "\n".join(
                f"`{n + 1}) [{time_format(t.duration) if t.duration else '🔴 Livestream'}]` [`{fix_characters(t.title, 31)}`]({t.uri})"
                for n, t
                in enumerate(itertools.islice(self.queue, 3))
            )

            if (qsize := len(self.queue)) > 3:
                txt += f"\n`╚══════ E mais {qsize - 3} música(s) ══════╝`"

        embed.description = txt

        embed.set_image(
            url="https://cdn.discordapp.com/attachments/554468640942981147/937918500784197632/rainbow_bar.gif"
        )

        embed_top.set_image(
            url="https://cdn.discordapp.com/attachments/554468640942981147/937918500784197632/rainbow_bar.gif"
        )

        try:
            self.view.stop()
        except:
            pass

        self.view = PlayerInteractions(self.ctx)

        self.view.add_item(
            discord.ui.Button(emoji="⏯️", custom_id=f"musicplayer_playpause", style=get_button_style(self.paused)))
        self.view.add_item(discord.ui.Button(emoji="⏮️", custom_id=f"musicplayer_back"))
        self.view.add_item(discord.ui.Button(emoji="⏭️", custom_id=f"musicplayer_skip", disabled=not self.queue))
        self.view.add_item(discord.ui.Button(emoji="🇳", custom_id=f"musicplayer_nightcore",
                                             style=get_button_style(self.nightcore, red=False)))
        self.view.add_item(discord.ui.Button(
            emoji=("🔂" if self.loop == "current" else "🔁"),
            custom_id=f"musicplayer_loop", style=discord.ButtonStyle.grey
            if not self.loop else discord.ButtonStyle.blurple
            if self.loop == "current"
            else discord.ButtonStyle.green)
        )
        self.view.add_item(discord.ui.Button(emoji="⏹️", custom_id=f"musicplayer_stop"))
        self.view.add_item(discord.ui.Button(emoji="⏪", custom_id=f"musicplayer_fastbackward"))
        self.view.add_item(discord.ui.Button(emoji="⏩", custom_id=f"musicplayer_fastforward"))
        self.view.add_item(discord.ui.Button(emoji="🔀", custom_id=f"musicplayer_shuffle"))
        self.view.add_item(discord.ui.Button(emoji="ℹ️", custom_id=f"musicplayer_help"))

        if not force and self.message:

            self.force_edit_message = False

            try:
                if interaction:
                    await interaction.response.edit_message(embeds=[embed_top, embed], view=self.view)
                else:
                    await self.message.edit(embeds=[embed_top, embed], view=self.view)
                return
            except:
                traceback.print_exc()
                pass

        await self.destroy_message(destroy_view=False)

        self.last_embed = embed

        self.ctx.player = self

        self.message = await self.text_channel.send(embeds=[embed_top, embed], view=self.view)

    async def destroy_message(self, destroy_view=True):

        if destroy_view:

            try:
                self.view.stop()
            except:
                pass

            self.view = None

        try:
            await self.message.delete()
        except:
            pass

        self.last_embed = None

        self.message = None

    def is_last_message(self):

        try:
            return self.text_channel.last_message_id == self.message.id
        except AttributeError:
            return

    async def update_message_task(self, interaction=None):

        if not interaction:
            await asyncio.sleep(5)

        try:
            await self.invoke_np(interaction=interaction)
        except:
            traceback.print_exc()

        self.updating_message = None

    def update_message(self, interaction=None):

        if self.updating_message:
            return

        self.updating_message = self.bot.loop.create_task(self.update_message_task(interaction=interaction))


##################################
##### Music Commands/Events ######
##################################

class Music(commands.Cog, wavelink.WavelinkMixin):

    def __init__(self, bot: commands.Bot):

        if not hasattr(bot, 'music'):
            bot.music = wavelink.Client(bot=bot)

        self.bot = bot

        self.bot.loop.create_task(self.process_nodes())

    async def process_nodes(self):

        await self.bot.wait_until_ready()

        if not hasattr(self.bot, 'session') or not self.bot.session:
            self.bot.session = ClientSession()

        for node in lavalink_servers:
            self.bot.loop.create_task(self.connect_node(node))

    async def connect_node(self, data: dict):

        data['rest_uri'] = ("https" if data.get('secure') else "http") + f"://{data['host']}:{data['port']}"

        retries = 1
        backoff = 7
        while not self.bot.is_closed():
            if retries >= 25:
                print(f"Todas as tentativas de conectar ao servidor [{data['identifier']}] falharam.")
                return
            else:
                try:
                    async with self.bot.session.get(data['rest_uri'], timeout=10) as r:
                        break
                except Exception:
                    await asyncio.sleep(backoff)
                    print(f'Falha ao conectar no servidor [{data["identifier"]}], tentativa: {retries}/25')
                    retries += 1
                    backoff += 2
                    continue

        await self.bot.music.initiate_node(**data)

    @wavelink.WavelinkMixin.listener("on_websocket_closed")
    async def node_ws_voice_closed(self, node, payload: wavelink.events.WebsocketClosed):

        if payload.code == 1000:
            return

        player: CustomPlayer = payload.player

        if payload.code == 4014:

            if player.ctx.me.voice:
                return
            vc = player.bot.get_channel(player.channel_id)
            if vc:
                vcname = f" **{vc.name}**"
            else:
                vcname = ""
            channel = player.text_channel
            embed = discord.Embed(color=player.ctx.me.color)
            embed.description = f"Conexão perdida com o canal de voz{vcname}..."
            embed.description += "\nO player será finalizado..."
            player.bot.loop.create_task(channel.send(embed=embed))
            await player.destroy()
            return

        # fix para dpy 2x (erro ocasionado ao mudar o bot de canal)
        if payload.code == 4006:
            await player.connect(player.channel_id)
            return

        print(f"Erro no canal de voz! server: {player.ctx.guild.name} reason: {payload.reason} | code: {payload.code}")

    @wavelink.WavelinkMixin.listener('on_track_exception')
    async def wavelink_track_error(self, node, payload: wavelink.TrackException):
        player: CustomPlayer = payload.player
        track = player.last_track
        embed = discord.Embed(
            description=f"**Falha ao reproduzir música:\n[{track.title}]({track.uri})** ```java\n{payload.error}\n```",
            color=discord.Colour.red())
        await player.text_channel.send(embed=embed)

        if player.locked:
            return

        player.current = None
        if payload.error == "This IP address has been blocked by YouTube (429)":
            player.queue.appendleft(player.last_track)
        else:
            player.played.append(player.last_track)

        player.locked = True
        await asyncio.sleep(6)
        player.locked = False
        await player.process_next()

    @wavelink.WavelinkMixin.listener()
    async def on_node_ready(self, node: wavelink.Node):
        print(f'Servidor de música: [{node.identifier}] está pronto para uso!')

    @wavelink.WavelinkMixin.listener()
    async def on_track_end(self, node: wavelink.Node, payload: wavelink.TrackEnd):

        player: CustomPlayer = payload.player

        if player.locked:
            return

        if payload.reason == "FINISHED":
            player.command_log = ""
        elif payload.reason == "STOPPED":
            player.force_edit_message = True
        else:
            return

        player.votes.clear()

        player.locked = True

        await asyncio.sleep(0.5)

        if player.last_track:

            if player.loop == "queue":
                if player.is_previows_music:
                    player.queue.insert(1, player.last_track)
                    player.is_previows_music = False
                else:
                    player.queue.append(player.last_track)
            elif player.loop == "current":
                player.queue.appendleft(player.last_track)
            elif player.is_previows_music:
                player.queue.insert(1, player.last_track)
                player.is_previows_music = False
            else:
                player.played.append(player.last_track)

        elif player.is_previows_music:
            player.is_previows_music = False

        player.locked = False

        await player.process_next()

    async def interaction_message(self, ctx, txt):
        if ctx.interaction:
            txt = f"{ctx.author.mention} {txt}"
            ctx.player.command_log = txt
            ctx.player.update_message(interaction=ctx.interaction)
        else:
            txt = f"{ctx.author.mention} **{txt}**"
            embed = discord.Embed(color=discord.Colour.green(), description=txt)
            ctx.player.update_message()
            await ctx.send(embed=embed)

    async def send_message(self, ctx, text=None, *, embed: discord.Embed = None, ephemeral=False):

        if ctx.interaction:
            await ctx.send(text, embed=embed)
        else:
            try:
                await ctx.respond(text, embed=embed, ephemeral=ephemeral)
            except AttributeError:
                await ctx.reply(text, embed=embed, mention_author=False)

    @wavelink.WavelinkMixin.listener('on_track_start')
    async def track_start(self, node, payload: wavelink.TrackStart):

        player: CustomPlayer = payload.player
        await player.invoke_np(force=not player.is_last_message())
        player.command_log = ""

    @check_voice()
    @commands.dynamic_cooldown(user_cooldown(2, 5), commands.BucketType.member)
    @app_commands.describe(
        query="Nome ou link da música (youtube/soundcloud/spotify)",
        position="Posição da fila para adicionar"
    )
    @commands.hybrid_command(aliases=["p", "tocar"], description="Tocar música em um canal de voz.")
    async def play(self, ctx, *, query: str, position=None):

        query = query.strip("<>")

        if not URL_REG.match(query):
            query = f"ytsearch:{query}"

        embed = discord.Embed(color=discord.Colour.red())

        node = self.bot.music.get_best_node()
        if not node:
            embed.description = "Não há servidores de música disponível."
            await ctx.send(embed=embed)
            return

        await ctx.defer()

        tracks = await self.bot.music.get_tracks(query)

        if not tracks:
            embed.description = "Não houve resultados para sua busca."
            await ctx.send(embed=embed)
            return

        player: CustomPlayer = self.bot.music.get_player(guild_id=ctx.guild.id, cls=CustomPlayer, ctx=ctx,
                                                         node_id=node.identifier)

        pos_txt = ""

        embed.colour = ctx.me.color

        if isinstance(tracks, list):

            track = CustomTrack(tracks[0].id, tracks[0].info, requester=ctx.author)

            if position is None:
                player.queue.append(track)
            else:
                player.queue.insert(position, track)
                pos_txt = f" na posição {position + 1} da fila"

            embed.description = f"**Música adicionada{pos_txt}:\n[`{track.title}`]({track.uri})**\n\n`{track.author} | " \
                                f"{time_format(track.duration) if not track.is_stream else '🔴 Livestream'}`"
            embed.set_thumbnail(url=track.thumb)

        else:

            tracks.tracks = [CustomTrack(t.id, t.info, requester=ctx.author) for t in tracks.tracks]

            info = tracks.data['playlistInfo']
            if (selected := info['selectedTrack']) > 0:
                tracks.tracks = tracks.tracks[selected:] + tracks.tracks[:selected]

            if position is None or len(tracks.tracks) < 2:
                for track in tracks.tracks:
                    player.queue.append(track)
            else:
                tracks.tracks.reverse()
                for track in tracks.tracks:
                    player.queue.insert(position, track)

                pos_txt = f" na posição {position + 1} da fila"

            embed.description = f"**Playlist adicionada{pos_txt}:**\n[`{info['name']}`]({query})\n\n`[{len(tracks.tracks)}] Música(s)`"
            embed.set_thumbnail(url=tracks.tracks[0].thumb)

        await ctx.send(embed=embed)

        if not player.is_connected:
            await player.connect(ctx.author.voice.channel.id)

        if not player.current:
            await player.process_next()
        else:
            player.update_message()

    @check_voice()
    @has_source()
    @is_requester()
    @commands.dynamic_cooldown(user_cooldown(2, 8), commands.BucketType.guild)
    @commands.hybrid_command(description="Pula a música atual que está tocando.", aliases=['pular', 's'])
    async def skip(self, ctx):

        player: CustomPlayer = ctx.player

        if not len(player.queue):
            await self.send_message(ctx, embed=discord.Embed(description="Não há músicas na fila...",
                                                             color=discord.Colour.red()))
            return

        if not ctx.interaction:
            await ctx.message.add_reaction('👍')
        elif ctx.interaction.type == discord.InteractionType.component:
            await ctx.interaction.response.defer()
            player.command_log = f"{ctx.author.mention} pulou a música."
        elif ctx.interaction.type == discord.InteractionType.application_command:
            await ctx.send(
                embed=discord.Embed(
                    description=f"{ctx.author.mention} pulou a música.",
                    color=ctx.me.color
                )
            )

        if player.loop == "current":
            player.loop = False

        await player.stop()

    @check_voice()
    @has_player()
    @is_requester()
    @commands.dynamic_cooldown(user_cooldown(2, 8), commands.BucketType.guild)
    @commands.hybrid_command(
        description="Voltar para a música anterior (ou para o início da música caso não tenha músicas tocadas/na fila).",
        aliases=['voltar', 'b']
    )
    async def back(self, ctx: commands.Context):

        player: CustomPlayer = ctx.player

        if not len(player.played) and not len(player.queue):
            await player.seek(0)
            await self.interaction_message(ctx, "voltou para o início da música.")
            return

        try:
            track = player.played.pop()
        except:
            track = player.queue.pop()
            player.last_track = None
            player.queue.appendleft(player.current)
        player.queue.appendleft(track)

        if not ctx.interaction:
            await ctx.message.add_reaction('👍')
        elif ctx.interaction.type == discord.InteractionType.component:
            await ctx.interaction.response.defer()
            player.command_log = f"{ctx.author.mention} voltou para a música atual."
        elif ctx.interaction.type == discord.InteractionType.application_command:
            await ctx.send(
                embed=discord.Embed(
                    description=f"{ctx.author.mention} voltou para a música atual.",
                    color=ctx.me.color
                )
            )

        if player.loop == "current":
            player.loop = False
        player.is_previows_music = True
        if not player.current:
            await player.process_next()
        else:
            await player.stop()

    @check_voice()
    @has_player()
    @is_dj()
    @commands.hybrid_command(
        description="Parar o player e me desconectar do canal de voz.",
        aliases=["parar", "sair", "leave", "l"]
    )
    async def stop(self, ctx):

        player: CustomPlayer = ctx.player

        await player.destroy()

        embed = discord.Embed(
            color=discord.Colour.red(),
            description=f"{ctx.author.mention} **parou o player!**"
        )

        await ctx.send(embed=embed)

    @check_voice()
    @has_source()
    @is_dj()
    @commands.dynamic_cooldown(user_cooldown(1, 5), commands.BucketType.member)
    @app_commands.describe(vol="porcentagem")
    @commands.hybrid_command(aliases=['v', 'vol'], description="Ajustar volume da música.")
    async def volume(self, ctx, *, vol: commands.Range[int, 5, 100]):

        player: CustomPlayer = ctx.player

        embed = discord.Embed(color=discord.Colour.red())

        await player.set_volume(vol)

        player.update_message()

        embed.colour = discord.Colour.green()
        embed.description = f"{ctx.author.mention} ajustou o volume para **{vol}%**"
        await ctx.send(embed=embed)

    @check_voice()
    @has_source()
    @is_dj()
    @commands.dynamic_cooldown(user_cooldown(2, 10), commands.BucketType.member)
    @commands.hybrid_command(description="Pausar a música.", aliases=["pausar"])
    async def pause(self, ctx):

        player: CustomPlayer = ctx.player

        embed = discord.Embed(color=discord.Colour.red())

        if player.paused:
            await self.send_message(ctx, embed=embed)
            return

        await player.set_pause(True)

        txt = f"pausou a música."

        await self.interaction_message(ctx, txt)

    @check_voice()
    @has_source()
    @is_dj()
    @commands.dynamic_cooldown(user_cooldown(2, 10), commands.BucketType.member)
    @commands.hybrid_command(description="Retomar/Despausar a música.", aliases=["retomar"])
    async def resume(self, ctx):

        player: CustomPlayer = ctx.player

        embed = discord.Embed(color=discord.Colour.red())

        if not player.paused:
            embed.description = "A música não está pausada."
            await self.send_message(ctx, embed=embed)
            return

        await player.set_pause(False)

        txt = f"retomou a música."
        await self.interaction_message(ctx, txt)

    @check_voice()
    @has_source()
    @is_dj()
    @commands.dynamic_cooldown(user_cooldown(2, 5), commands.BucketType.member)
    @app_commands.describe(pos="posição, ex: 1:10 / 30 / 0:25")
    @commands.hybrid_command(description="Avançar/Retomar a música para um tempo específico.")
    async def seek(self, ctx, pos="0"):

        player: CustomPlayer = ctx.player

        embed = discord.Embed(color=discord.Colour.red())

        if player.current.is_stream:
            embed.description = "Você não pode usar este comando em uma livestream."
            await self.send_message(ctx, embed=embed)
            return

        seconds = seek_parser(pos)

        if seconds is None:
            embed.description = "Você usou um tempo inválido! Use segundos (1 ou 2 digitos) ou no formato (minutos):(segundos)"
            return await self.send_message(ctx, embed=embed)

        milliseconds = seconds * 1000

        if milliseconds < 0:
            milliseconds = 0

        try:
            await player.seek(milliseconds)
        except Exception as e:
            embed.description = f"Ocorreu um erro no comando\n```py\n{repr(e)}```."
            await self.send_message(ctx, embed=embed)
            return

        txt = f"{'avançou' if milliseconds > player.position else 'voltou'} a música para: {time_format(milliseconds)}"
        await self.interaction_message(ctx, txt)

    @check_voice()
    @has_player()
    @is_dj()
    @commands.dynamic_cooldown(user_cooldown(3, 5), commands.BucketType.member)
    @commands.hybrid_command(name="shuffle", aliases=["misturar"], description="Misturar as músicas da fila")
    async def shuffle_(self, ctx):

        player = ctx.player

        embed = discord.Embed(color=discord.Colour.red())

        if len(player.queue) < 3:
            embed.description = "A fila tem que ter no mínimo 3 músicas para ser misturada."
            await self.send_message(ctx, embed=embed)
            return

        shuffle(player.queue)

        txt = f"misturou as músicas da fila."

        await self.interaction_message(ctx, txt)

    @check_voice()
    @has_source()
    @is_dj()
    @commands.dynamic_cooldown(user_cooldown(3, 5), commands.BucketType.member)
    @commands.hybrid_command(aliases=["repeat", "repetir"], description="Ativar/Desativar a repetição da música atual")
    async def loop(self, ctx):

        player = ctx.player

        player.loop = "current" if player.loop is False else False

        txt = f"{'ativou' if player.loop else 'desativou'} a repetição da música."

        await self.interaction_message(ctx, txt)

    @check_voice()
    @has_source()
    @is_dj()
    @commands.dynamic_cooldown(user_cooldown(3, 5), commands.BucketType.member)
    @commands.hybrid_command(aliases=["repetirfila", "lq"], description="ativar repetição da fila")
    async def loopqueue(self, ctx):

        player: CustomPlayer = ctx.player

        if player.loop == "queue":
            embed = discord.Embed(color=discord.Colour.red())
            embed.description = "a repetição da fila já está ativada."
            await self.send_message(ctx, embed=embed)
            return

        player.loop = "queue"

        await self.interaction_message(ctx, "ativou a repetição da fila.")

    @check_voice()
    @has_player()
    @is_dj()
    @app_commands.describe(item="música")
    @commands.hybrid_command(description="Remover uma música específica da fila.", aliases=["delete", "remover", "del"])
    async def remove(self, ctx, item: str):

        embed = discord.Embed(color=discord.Colour.red())

        if not item.isdigit():
            embed.description = f"Você usou uma posição inválida: {item}"
            await ctx.send(embed=embed)
            return

        player: CustomPlayer = ctx.player

        try:
            track = player.queue[int(item) - 1]
        except IndexError:
            embed.description = f"Você usou a posição de uma música inexistente na fila: {item}\n(Tamanho da fila atual: {len(player.queue)})"
            await ctx.send(embed=embed)
            return

        player.queue.remove(track)

        embed = discord.Embed(
            description=f"{ctx.author.mention} removeu a música [`{fix_characters(track.title, limit=25)}`]({track.uri}) da fila.",
            color=discord.Colour.green()
        )

        await ctx.send(embed=embed)

    @check_voice()
    @has_player()
    @is_dj()
    @commands.dynamic_cooldown(user_cooldown(2, 10), commands.BucketType.guild)
    @commands.hybrid_command(description="Readicionar as músicas tocadas na fila.")
    async def readd(self, ctx):

        player: CustomPlayer = ctx.player

        embed = discord.Embed(color=discord.Colour.red())

        if not player.played:
            embed.description = f"{ctx.author.mention} **não há músicas tocadas.**"
            await ctx.send(embed=embed)
            return

        embed.colour = discord.Colour.green()
        embed.description = f"{ctx.author.mention} **readicionou [{len(player.played)}] música(s) tocada(s) na fila.**"

        player.played.reverse()
        player.queue.extend(player.played)
        player.played.clear()

        await ctx.send(embed=embed)
        if not player.current:
            await player.process_next()
        else:
            player.update_message()

    @check_voice()
    @has_source()
    @is_dj()
    @commands.dynamic_cooldown(user_cooldown(2, 8), commands.BucketType.guild)
    @app_commands.rename(query="música")
    @commands.hybrid_command(description="Pula para a música especificada.")
    async def skipto(self, ctx, *, query: str):

        embed = discord.Embed(color=discord.Colour.red())

        index = get_track_index(ctx, query)

        if index is None:
            embed.description = f"{ctx.author.mention} **não há músicas na fila com o nome: {query}**"
            await ctx.reply(embed=embed, mention_author=False)
            return

        player: CustomPlayer = ctx.player

        track = player.queue[index]

        player.queue.append(player.last_track)
        player.last_track = None

        if player.loop == "current":
            player.loop = False

        if index > 0:
            player.queue.rotate(0 - (index))

        await player.stop()

        embed.description = f"{ctx.author.mention} **pulou para a música:** [`{track.title}`]({track.uri})"
        embed.colour = discord.Colour.green()
        await ctx.send(embed=embed)

    @check_voice()
    @has_source()
    @is_dj()
    @app_commands.rename(pos="posição", query="música")
    @commands.hybrid_command(description="Move uma música para a posição especificada da fila.")
    async def move(self, ctx: commands.Context, *, query: str, position: int = None):

        embed = discord.Embed(colour=discord.Colour.red())

        if position is None:
            if ctx.interaction:
                position = 1
            else:
                try:
                    song = query.split(" ")
                    position = int(query[0])
                    query = " ".join(song[1:])
                except:
                    position = 1

        index = get_track_index(ctx, query)

        if index is None:
            embed.description = f"{ctx.author.mention} **não há músicas na fila com o nome: {query}**"
            await ctx.reply(embed=embed, mention_author=False)
            return

        if position < 0:
            embed.description = f"{ctx.author.mention} **você não pode usar número negativo.**"
            await ctx.reply(embed=embed, mention_author=False)
            return

        player: CustomPlayer = ctx.player

        track = player.queue[index]

        player.queue.remove(track)

        player.queue.insert(position - 1, track)

        embed = discord.Embed(
            description=f"{ctx.author.mention} moveu a música [`{fix_characters(track.title, limit=25)}`]({track.uri}) "
                        f"para a posição **[{position}]** da fila.",
            color=discord.Colour.green()
        )

        await ctx.send(embed=embed)

        player.update_message()

    @check_voice()
    @has_source()
    @is_dj()
    @commands.dynamic_cooldown(user_cooldown(2, 10), commands.BucketType.guild)
    @app_commands.rename(query="música")
    @commands.hybrid_command(aliases=["rt"], description="Rotaciona a fila para a música especificada.")
    async def rotate(self, ctx, query: str):

        embed = discord.Embed(colour=discord.Colour.red())

        index = get_track_index(ctx, query)

        if index is None:
            embed.description = f"{ctx.author.mention} **não há músicas na fila com o nome: {query}**"
            await ctx.reply(embed=embed, mention_author=False)
            return

        player: CustomPlayer = ctx.player

        track = player.queue[index]

        if index <= 0:
            embed.description = f"{ctx.author.mention} **a música **[`{track.title}`]({track.uri}) já é a próxima da fila."
            await ctx.reply(embed=embed, mention_author=False)
            return

        player.queue.rotate(0 - (index))

        embed = discord.Embed(
            description=f"{ctx.author.mention} rotacionou a fila para a música [`{fix_characters(track.title, limit=25)}`]({track.uri}).",
            color=discord.Colour.green()
        )

        await ctx.send(embed=embed)

        player.update_message()

    @skipto.autocomplete("query")
    @move.autocomplete("query")
    @rotate.autocomplete("query")
    async def queue_autocomplete(
            self,
            interaction: discord.Interaction,
            current: str,
    ) -> List[app_commands.Choice[str]]:

        try:
            player = self.bot.music.players[interaction.guild.id]
            return [app_commands.Choice(name=t.title[:50], value=t.title) for t in player.queue if not current or current.lower() in t.title.lower()][:20]
        except KeyError:
            return []

    @check_voice()
    @has_source()
    @is_dj()
    @commands.cooldown(1, 5, commands.BucketType.guild)
    @commands.hybrid_command(description="Ativar/Desativar o efeito nightcore (Música acelerada com tom mais agudo).",
                             aliases=['nc'])
    async def nightcore(self, ctx):

        player: CustomPlayer = ctx.player

        op = {"op": "filters", "guildId": str(ctx.guild.id)}

        player.nightcore = not player.nightcore

        if player.nightcore:
            op["timescale"] = {"pitch": 1.2, "speed": 1.1, "rate": 1.0}
            txt = "ativou"
        else:
            txt = "desativou"

        await player.node._send(**op)

        txt = f"{txt} o efeito nightcore."

        await self.interaction_message(ctx, txt)

    @has_player()
    @commands.hybrid_command(name="queue", description="Mostra as músicas que estão da fila.", aliases=['q', 'fila'])
    @commands.max_concurrency(1, commands.BucketType.member)
    async def q(self, ctx):

        player: CustomPlayer = ctx.player

        if not player.queue:
            embedvc = discord.Embed(
                colour=1646116,
                description='Não existe músicas na fila no momento.'
            )
            await self.send_message(ctx, embed=embedvc)
            return

        view = QueueInteraction(player, ctx.author)
        embed = view.embed

        await ctx.send(embed=embed, view=view, ephemeral=True)

        await view.wait()

    @check_voice()
    @has_source()
    @is_dj()
    @commands.dynamic_cooldown(user_cooldown(1, 10), commands.BucketType.guild)
    @commands.hybrid_command(aliases=["limparfila", "qc"], description="Limpar a fila de música")
    async def clearqueue(self, ctx):

        player: CustomPlayer = ctx.player
        embed = discord.Embed(color=discord.Colour.red())
        if len(player.queue) < 1:
            embed.description = f"{ctx.author.mention} **não há músicas na fila.**"
            await ctx.send(embed=embed)
            return

        player.queue.clear()
        player.update_message()

        embed.colour = discord.Colour.green()
        embed.description = f"{ctx.author.mention} **limpou a fila de música.**"
        await ctx.send(embed=embed)

    @has_player()
    @is_dj()
    async def restrict(self, ctx):

        player: CustomPlayer = ctx.player
        player.restrict_mode = not player.restrict_mode

        embed = discord.Embed(
            description=f"{ctx.author.mention} **{'' if player.restrict_mode else 'des'}ativou o modo "
                        f"restrito do player para dj's e staffs**", colour=ctx.guild.me.color)

        await ctx.send(embed=embed)

    @has_source()
    @commands.hybrid_command(description="Reenvia a mensagem do player com a música atual.")
    async def player(self, ctx):

        player: CustomPlayer = ctx.player
        await player.destroy_message()
        await player.invoke_np()

    @has_player()
    @is_dj()
    @commands.hybrid_command(description="Adicionar um membro à lista de DJ's [menção/id/nome]", aliases=["dj"])
    async def adddj(self, ctx, *, member: discord.Member):

        embed = discord.Embed(color=discord.Colour.red())

        if member.guild_permissions.manage_channels:
            text = f"você não pode adicionar o membro {member.mention} na lista de DJ's (ele(a) possui permissão de **gerenciar canais**)."
        elif member == ctx.author:
            text = "Você não pode adicionar a si mesmo na lista de DJ's."
        elif member in ctx.player.dj:
            text = f"O membro {member.mention} já está na lista de DJ's"
        else:
            ctx.player.dj.append(member)
            embed.colour = discord.Colour.green()
            text = f"O membro {member.mention} foi adicionado à lista de DJ's por {ctx.author.mention}."

        embed.description = text
        await ctx.send(embed=embed)

    @commands.hybrid_command(aliases=["nodeinfo"], description="Ver informações dos servidores de música.")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def nodestats(self, ctx):

        em = discord.Embed(color=ctx.me.color, title="Servidores de música:")

        if not self.bot.music.nodes:
            em.description = "**Não há servidores.**"
            await ctx.send(embed=em)
            return

        for identifier, node in self.bot.music.nodes.items():
            if not node.available: continue

            txt = f"Região: `{node.region.title()}`\n"

            current_player = True if node.players.get(ctx.guild.id) else False

            if node.stats:
                used = humanize.naturalsize(node.stats.memory_used)
                total = humanize.naturalsize(node.stats.memory_allocated)
                free = humanize.naturalsize(node.stats.memory_free)
                cpu_cores = node.stats.cpu_cores
                cpu_usage = f"{node.stats.lavalink_load * 100:.2f}"
                started = node.stats.players

                ram_txt = f'RAM: `{used}/{free} ({total})`'

                txt += f'{ram_txt}\n' \
                       f'CPU Cores: `{cpu_cores}`\n' \
                       f'Uso de CPU: `{cpu_usage}%`\n' \
                       f'Uptime: `{time_format(node.stats.uptime)}`'

                if started:
                    txt += "\nPlayers: "
                    players = node.stats.playing_players
                    idle = started - players
                    if players:
                        txt += f'`[▶️{players}]`' + (" " if idle else "")
                    if idle:
                        txt += f'`[💤{idle}]`'

            if current_player:
                status = "🌟"
            else:
                status = "✅" if node.is_available else '❌'

            em.add_field(name=f'**{identifier}** `{status}`', value=txt)

        await ctx.reply(embed=em, mention_author=False)

    async def cog_before_invoke(self, ctx):

        try:
            ctx.player
        except AttributeError:
            ctx.player = ctx.bot.music.players.get(ctx.guild.id)

    async def cog_command_error(self, ctx, error):

        embed = discord.Embed(color=discord.Colour.red())

        if isinstance(error, commands.RangeError):
            embed.description = f"**Você deve usar um valor entre {error.minimum} e {error.maximum}.**"
            return await self.send_message(ctx, embed=embed)

        if isinstance(error, NotDJorStaff):
            embed.description = "**Você deve estar na lista de DJ ou ter a permissão de **Gerenciar canais** para usar este comando.**"
            return await self.send_message(ctx, embed=embed)

        if isinstance(error, NotRequester):
            embed.description = "**Você deve ser dono da música atual ou estar na lista de DJ ou ter a permissão de **Gerenciar canais** para pular músicas.**"
            return await self.send_message(ctx, embed=embed)

        if isinstance(error, DiffVoiceChannel):
            embed.description = "**Você deve estar no meu canal de voz atual para usar este comando.**"
            return await self.send_message(ctx, embed=embed)

        if isinstance(error, NoSource):
            embed.description = "**Não há músicas no player atualmente.**"
            return await self.send_message(ctx, embed=embed)

        if isinstance(error, NoVoice):
            embed.description = "**Você deve estar em um canal de voz para usar este comando.**"
            return await self.send_message(ctx, embed=embed)

        if isinstance(error, NoPlayer):
            embed.description = "**Não há player inicializado no servidor.**"
            return await self.send_message(ctx, embed=embed)

        error = getattr(error, 'original', error)

        if isinstance(error, commands.CommandOnCooldown):
            remaing = int(error.retry_after)
            if remaing < 1:
                remaing = 1
            embed.description = "**Você deve aguardar {} para usar este comando.**".format(
                time_format(int(remaing) * 1000))
            return await self.send_message(ctx, embed=embed)

        if isinstance(error, commands.MaxConcurrencyReached):
            bucket = commands.BucketType
            txt = f"{error.number} vezes " if error.number > 1 else ''
            txt = {
                bucket.member: f"você já usou esse comando {txt}neste servidor",
                bucket.guild: f"esse comando já foi usado {txt}neste servidor",
                bucket.user: f"você já usou esse comando {txt}",
                bucket.channel: f"esse comando já foi usado {txt}neste atual",
                bucket.category: f"esse comando já foi usado {txt}na categoria do canal atual",
                bucket.role: f"esse comando já foi usado {txt}por um membro que possui o cargo permitido",
                bucket.default: f"esse comando já foi usado {txt}por alguém"
            }
            txt = txt.get(error.per)
            txt = f"{ctx.author.mention} **{txt} e ainda não teve seu{'s' if error.number > 1 else ''} uso{'s' if error.number > 1 else ''} finalizado{'s' if error.number > 1 else ''}!**"
            embed.description = txt
            return await self.send_message(ctx, embed=embed)

        traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)

        if isinstance(error, commands.CommandNotFound):
            return

        embed.description = f"**Ocorreu um erro no comando:** `{ctx.invoked_with}`\n```py\n{str(repr(error))[:2020].replace(ctx.bot.http.token, 'mytoken')}```"
        await self.send_message(ctx, embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))


if __name__ == "__main__":

    
    try:
        token = os.environ["TOKEN"]
    except KeyError:
        token = "TOKEN_DISCORD" # Adcionar o token no discord aqui https://discord.com/developers/applications

    bot = TestBot(command_prefix=commands.when_mentioned_or("+"), intents=discord.Intents.all())

    bot.run(token)