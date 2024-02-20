import asyncio
from asyncio import timeout

import discord
from discord.ext import commands
import youtube_dl
import logging

logging.basicConfig(level=logging.INFO)

intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.voice_states = True
intents.message_content = True  # 메시지 내용에 접근하기 위해 필요합니다.

# 봇 설정
bot = commands.Bot(command_prefix='!', intents=intents)


youtube_dl.utils.bug_reports_message = lambda: ''


@bot.event
async def on_ready():
    print(f'{bot.user.name} has connected to Discord!')

# 봇 실행


youtube_dl.utils.bug_reports_message = lambda: ''


ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn -bufsize 6144k -b:a 320k -q:a 0 -ar 48000',
}


music_players = {}


async def ensure_music_player(ctx):
    guild_id = ctx.guild.id
    if guild_id not in music_players:
        music_players[guild_id] = MusicPlayer(ctx.guild, ctx.channel, bot)
    return music_players[guild_id]


class MusicPlayer:
    # ctx 대신 필요한 속성만 명시적으로 전달
    def __init__(self, guild, channel, bot):
        self._guild = guild
        self._channel = channel
        self.bot = bot
        self.queue = asyncio.Queue()
        self.next = asyncio.Event()
        self.np = None  # Now playing message
        self.volume = 0.5
        self.current = None

        self.bot.loop.create_task(self.player_loop())

    async def play_next(self):
        if not self._guild.voice_client.is_playing():  # 이미 재생 중인지 확인
            try:
                # 큐에서 다음 트랙을 가져옵니다. 큐가 비어있으면 예외 발생
                source = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # 큐가 비어있을 때 처리
                await clear_now_playing()  # 봇의 상태를 "아무것도 듣지 않음"으로 설정
                return  # 더 이상 재생할 트랙이 없으므로 함수 종료

            self.current = source

            # 새 트랙을 재생합니다.
            self._guild.voice_client.play(source, after=lambda _: self.bot.loop.call_soon_threadsafe(self.next.set))
            self.np = await self._channel.send(f'**재생:** {source.title}')

            await update_now_playing(source.title)
        else:
            # 이미 재생 중인 경우, 이 부분을 통해 로그를 남길 수 있습니다.
            print("Already playing audio. Waiting for the current track to finish.")

    def destroy(self, guild):
        return self.bot.loop.create_task(self._cog.cleanup(guild))

    async def player_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            self.next.clear()
            await self.next.wait()  # 다음 트랙을 재생할 준비가 될 때까지 기다립니다.

            try:
                async with timeout(300):  # 5분 동안 재생할 노래가 없으면 루프 종료
                    await self.play_next()
            except asyncio.TimeoutError:
                return self.destroy(self._guild)


class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))

        if 'entries' in data:
            # 재생목록 처리
            data = data['entries'][0]

        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)


ytdl_format_options = {
    'format': 'bestaudio/best',
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '320',
    }],
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)


@bot.event
async def on_command_error(ctx, error):
    logging.error(f"An error occurred: {error}")
    await ctx.send(f"An error occurred: {error}")


@bot.event
async def on_voice_state_update(member, before, after):
    # 봇이 이미 연결된 음성 채널이 있는지 확인
    voice_client = discord.utils.get(bot.voice_clients, guild=member.guild)

    # 사용자가 새로운 음성 채널에 들어갔는지 확인 (after.channel) 및 봇이 이미 어떤 채널에도 연결되어 있지 않는지 확인
    if after.channel is not None and voice_client is None:
        # 사용자가 들어간 채널에 봇을 연결
        await after.channel.connect()

    # 사용자가 음성 채널에서 나갔고, 봇만 남았다면 봇도 채널을 떠나게 함
    elif before.channel is not None and voice_client is not None:
        # 해당 채널에 있는 멤버 수를 확인
        if len(before.channel.members) == 1:  # 봇만 남았을 경우
            await voice_client.disconnect()


@bot.command(name='test', help='봇 명령어 작동 확인 테스트')
async def test(ctx):
    await ctx.send("Test successful!")


async def clear_now_playing():
    """봇의 상태를 '아무것도 듣지 않음'으로 업데이트합니다."""
    await bot.change_presence(activity=None)


async def update_now_playing(title):
    """현재 재생 중인 노래 제목을 바탕으로 봇의 상태를 업데이트합니다."""
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=title))


@bot.command(name='play', help='노래를 재생합니다. !play 노래제목')
async def play(ctx, *, query):
    music_player = await ensure_music_player(ctx)

    # 음성 채널 연결
    try:
        voice_channel = ctx.author.voice.channel
    except AttributeError:
        await ctx.send("음성 채널에 연결되어 있지 않습니다.")
        return

    # 음성 채널에 봇이 연결되어 있지 않은 경우 연결을 시도합니다.
    if ctx.voice_client is None:
        await voice_channel.connect()
    elif ctx.voice_client.channel != voice_channel:
        await ctx.voice_client.move_to(voice_channel)

    async with ctx.typing():
        player = await YTDLSource.from_url(query, loop=bot.loop, stream=True)
        await music_player.queue.put(player)

    if not ctx.voice_client.is_playing():
        await music_player.play_next()
    else:
        await ctx.send(f'**큐에 추가됨:** {player.title}')  # 큐에 추가됨 메시지


@bot.command(name='pause', help='노래를 일시 정지합니다.')
async def pause(ctx):
    voice_client = ctx.guild.voice_client
    if voice_client.is_playing():
        voice_client.pause()
        await ctx.send("노래를 일시 정지했습니다.")
    else:
        await ctx.send("현재 재생 중인 노래가 없습니다.")


@bot.command(name='resume', help='일시 정지된 노래를 다시 재생합니다.')
async def resume(ctx):
    voice_client = ctx.guild.voice_client
    if voice_client.is_paused():
        voice_client.resume()
        await ctx.send("노래를 다시 재생합니다.")
    else:
        await ctx.send("일시 정지된 노래가 없습니다.")


@bot.command(name='stop', help='노래 재생을 중지합니다.')
async def stop(ctx):
    voice_client = ctx.guild.voice_client
    if voice_client:
        await voice_client.disconnect()
        await ctx.send("노래 재생을 중지하고, 음성 채널에서 나갔습니다.")
    else:
        await ctx.send("봇이 음성 채널에 없습니다.")


@bot.command(name='skip', help='현재 노래를 건너뛰고 다음 노래를 재생합니다.')
async def skip(ctx):
    guild_id = ctx.guild.id
    music_player = music_players.get(guild_id)
    if music_player:
        ctx.guild.voice_client.stop()  # 현재 재생 중인 트랙을 중지합니다.
        music_player.next.set()  # 다음 트랙을 재생하기 위해 이벤트를 설정합니다.
    else:
        await ctx.send("재생 중인 노래가 없습니다.")


bot.run('봇 토큰')