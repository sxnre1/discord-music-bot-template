import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import aiohttp
import time
import random
from yt_dlp import YoutubeDL
import subprocess

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)
queues = {}
loops = {}
volumes = {}
API_KEY = "AIzaSyAAix52yAU7jCgI_JGU8YFSngDUcyU6Z1k"
SPOTIFY_CLIENT_ID= "1e7972517a814a58aec9c41d77d2d364"
SPOTIFY_CLIENT_SECRET= "65f18093dcfd444396a0e6dae4af470c"

def get_queue(guild_id):
    if guild_id not in queues:
        queues[guild_id] = asyncio.Queue()
    return queues[guild_id]

def get_loop(guild_id):
    if guild_id not in loops:
        loops[guild_id] = False
    return loops[guild_id]

def make_progress_bar(current, total, length=15):
    if not total:
        return "길이 알 수 없음"
    progress = int((current / total) * length)
    blue = "[▬](https://youtube.com)"
    gray = "▬"
    return blue * progress + gray * (length - progress)

def format_duration(seconds):
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"

music = app_commands.Group(name="노래", description="음악 관련 명령어")

class SongSelect(discord.ui.View):
    def __init__(self, options, results, vc, interaction):
        super().__init__(timeout=None)
        self.results = results
        self.vc = vc
        self.interaction = interaction
        self.user = interaction.user
        self.message = None
        self.add_item(SongDropdown(options, self))

    async def interaction_check(self, interaction):
        return interaction.user == self.user

class SongDropdown(discord.ui.Select):
    def __init__(self, options, parent_view):
        super().__init__(placeholder="재생할 곡을 선택하세요", options=options)
        self.view_ref = parent_view

    async def callback(self, interaction: discord.Interaction):
        index = int(self.values[0])
        data = self.view_ref.results[index]
        video_id = data['id']['videoId']
        url = f"https://www.youtube.com/watch?v={video_id}"
        title = data['snippet']['title']
        thumbnail = data['snippet']['thumbnails']['high']['url']
        user = interaction.user

        queue = get_queue(interaction.guild.id)
        await queue.put((url, title, thumbnail, user))

        await interaction.response.edit_message(content='재생을 준비 중입니다...', view=None)

        if not self.view_ref.vc.is_playing():
            await play_next(self.view_ref.vc, interaction, self.view_ref.message)
        else:
            embed = discord.Embed(title="대기열에 추가됨", description=f"[{title}]({url})", color=discord.Color.purple())
            embed.set_image(url=thumbnail)
            embed.set_footer(text=f"신청자: {user.display_name}", icon_url=user.display_avatar.url)
            await self.view_ref.message.edit(content=None, embed=embed)

@music.command(name="재생", description="유튜브에서 음악을 검색해 재생합니다.")
async def play(interaction: discord.Interaction, query: str):
    await interaction.response.send_message("🔍 검색 중입니다...")
    msg = await interaction.original_response()

    vc = interaction.guild.voice_client
    if not vc:
        if interaction.user.voice:
            vc = await interaction.user.voice.channel.connect(self_deaf=True)
        else:
            return await msg.edit(embed=discord.Embed(title="❌ 음성 채널에 먼저 들어가 주세요.", color=discord.Color.red()))

    async with aiohttp.ClientSession() as session:
        params = {
            'part': 'snippet',
            'maxResults': 5,
            'q': query,
            'key': API_KEY,
            'type': 'video'
        }
        async with session.get('https://www.googleapis.com/youtube/v3/search', params=params) as resp:
            data = await resp.json()
            if 'items' not in data or not data['items']:
                return await msg.edit(embed=discord.Embed(title="❌ 검색 결과가 없습니다.", color=discord.Color.red()))
            entries = data['items']

    options = []
    for idx, entry in enumerate(entries):
        title = entry['snippet']['title']
        label = (title[:95] + '...') if len(title) > 95 else title
        options.append(discord.SelectOption(label=label, value=str(idx)))

    view = SongSelect(options, entries, vc, interaction)
    await msg.edit(content=None, view=view)
    view.message = msg

async def play_next(vc, interaction, msg):
    queue = get_queue(interaction.guild.id)
    if queue.empty():
        return

    url, title, thumbnail, user = await queue.get()
    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'cookiefile': 'cookies.txt'
    }

    loop = asyncio.get_event_loop()
    info = await loop.run_in_executor(None, lambda: YoutubeDL(ydl_opts).extract_info(url, download=False))
    duration = info.get('duration')
    stream_url = info['url']

    ffmpeg_opts = {
        'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin -loglevel quiet',
        'options': '-vn',
        'stderr': subprocess.DEVNULL
    }

    source = discord.FFmpegPCMAudio(stream_url, **ffmpeg_opts)
    vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(vc, interaction, msg), bot.loop))
    vc.start_time = time.time()

    embed = discord.Embed(
        title="🎶 재생 중",
        description=f"[{title}]({url})\n{format_duration(duration)} {make_progress_bar(0, duration)} 0:00",
        color=discord.Color.green()
    )
    embed.set_image(url=thumbnail)
    embed.set_footer(text=f"신청자: {user.display_name}", icon_url=user.display_avatar.url)
    await msg.edit(embed=embed)

    bot.loop.create_task(update_progress_bar(vc, msg, title, url, duration, thumbnail, user))

async def update_progress_bar(vc, msg, title, url, duration, thumbnail, user):
    while vc.is_playing():
        current_time = time.time() - vc.start_time
        bar = make_progress_bar(current_time, duration)
        embed = discord.Embed(
            title="🎶 재생 중",
            description=f"[{title}]({url})\n{format_duration(duration)} {bar} {format_duration(current_time)}",
            color=discord.Color.green()
        )
        embed.set_image(url=thumbnail)
        embed.set_footer(text=f"신청자: {user.display_name}", icon_url=user.display_avatar.url)

        try:
            await msg.edit(embed=embed)
        except:
            pass

        await asyncio.sleep(2)

    try:
        await msg.edit(embed=discord.Embed(title="✅ 재생 완료", description="노래가 끝났어요!", color=discord.Color.green()))
    except:
        pass

# 반복, 스킵, 정지 등 기타 명령어
@music.command(name="반복", description="노래 반복 모드 전환")
async def toggle_loop(interaction: discord.Interaction):
    gid = interaction.guild.id
    loops[gid] = not get_loop(gid)
    state = "활성화됨" if loops[gid] else "비활성화됨"
    await interaction.response.send_message(f"🔁 반복 모드: **{state}**")

@music.command(name="일시정지", description="노래 일시정지")
async def pause(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await interaction.response.send_message("⏸️ 일시정지했어요.")
    else:
        await interaction.response.send_message("현재 재생 중인 노래가 없어요.")

@music.command(name="다시시작", description="노래 재시작")
async def resume(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message("▶️ 다시 재생했어요.")
    else:
        await interaction.response.send_message("재생할 노래가 없어요.")

@music.command(name="스킵", description="노래 스킵")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        loops[interaction.guild.id] = False
        vc.stop()
        await interaction.response.send_message("⏭️ 노래를 스킵했어요.")
    else:
        await interaction.response.send_message("스킵할 노래가 없어요.")

@music.command(name="멈춰", description="재생 중지 및 나가기")
async def stop(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc:
        await vc.disconnect()
        await interaction.response.send_message("🛑 재생을 멈추고 나갔어요.")
    else:
        await interaction.response.send_message("봇이 음성 채널에 없어요.")

@music.command(name="대기열", description="대기열 보기")
async def show_queue(interaction: discord.Interaction):
    queue = get_queue(interaction.guild.id)
    if queue.empty():
        return await interaction.response.send_message("대기열이 비어 있어요.")
    items = list(queue._queue)
    desc = "\n".join([f"{i+1}. {title}" for i, (_, title, _, _) in enumerate(items)])
    embed = discord.Embed(title="📜 현재 대기열", description=desc, color=discord.Color.blurple())
    await interaction.response.send_message(embed=embed)

@music.command(name="셔플", description="대기열 셔플")
async def shuffle(interaction: discord.Interaction):
    queue = get_queue(interaction.guild.id)
    items = []
    while not queue.empty():
        items.append(await queue.get())
    random.shuffle(items)
    for item in items:
        await queue.put(item)
    await interaction.response.send_message("🔀 대기열이 셔플되었어요!")

@music.command(name="대기열초기화", description="대기열 비우기")
async def clear(interaction: discord.Interaction):
    queue = get_queue(interaction.guild.id)
    while not queue.empty():
        await queue.get()
    await interaction.response.send_message("🧹 대기열이 초기화되었어요!")

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ 로그인됨: {bot.user}")

bot.tree.add_command(music)
bot.run("MTM5NzQ4NDUwMzY3MjQyNjQ5Ng.G66N4B.Z6osnGT-MSh1hWBxKPQ5eynHeyQxkIwjf48Sag")
