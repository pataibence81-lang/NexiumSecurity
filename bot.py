
import discord
from discord.ext import commands
from collections import defaultdict, deque
from datetime import timedelta
from flask import Flask, request, redirect
from threading import Thread, RLock
from html import escape
import secrets
import json
import os
import time

# ==========================================
# NEXIUM SECURITY - ALAPBEALLITASOK
# ==========================================

TOKEN = "TOKEN"

CONFIG_FILE = "nexium_config.json"
PREFIX = "!"

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(
    command_prefix=PREFIX,
    intents=intents,
    help_command=None
)

config_lock = RLock()
dashboard_token = secrets.token_urlsafe(32)

joins = defaultdict(deque)
messages = defaultdict(deque)
deletions = defaultdict(deque)
last_messages = {}

# ==========================================
# KONFIGURACIO MENTESE
# ==========================================

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            print("Figyelem: a konfiguráció nem olvasható.")
    return {}


config = load_config()


def save_config():
    with config_lock:
        temp_file = CONFIG_FILE + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        os.replace(temp_file, CONFIG_FILE)


def get_guild_config(guild_id):
    key = str(guild_id)

    with config_lock:
        if key not in config:
            config[key] = {}

        defaults = {
            "log_channel": None,
            "anti_raid": True,
            "anti_spam": True,
            "anti_nuke": True
        }

        for name, value in defaults.items():
            config[key].setdefault(name, value)

        result = dict(config[key])

    return result


async def send_log(guild, message):
    settings = get_guild_config(guild.id)
    channel_id = settings.get("log_channel")

    if not channel_id:
        print(f"[NAPLO] {guild.name}: {message}")
        return

    channel = guild.get_channel(int(channel_id))

    if channel is None:
        print(f"[NAPLO] A naplócsatorna nem található: {message}")
        return

    try:
        await channel.send(
            message,
            allowed_mentions=discord.AllowedMentions.none()
        )
    except discord.HTTPException:
        print("Nem sikerült üzenetet küldeni a naplócsatornába.")


# ==========================================
# WEBES DASHBOARD
# Csak ezen a számítógépen érhető el
# ==========================================

app = Flask("Nexium Security Dashboard")


def local_request():
    return request.remote_addr in ("127.0.0.1", "::1")


@app.before_request
def protect_dashboard():
    if not local_request():
        return "Hozzáférés megtagadva.", 403


@app.route("/", methods=["GET"])
def dashboard():
    cards = []

    for guild in bot.guilds:
        settings = get_guild_config(guild.id)

        channels = []
        for channel in guild.text_channels:
            channels.append(
                f'<option value="{channel.id}" '
                f'{"selected" if settings.get("log_channel") == channel.id else ""}>'
                f'{escape(channel.name)}</option>'
            )

        def checked(name):
            return "checked" if settings.get(name, True) else ""

        cards.append(f"""
        <section class="card">
          <h2>{escape(guild.name)}</h2>
          <p>Szerverazonosító: {guild.id}</p>

          <form method="post" action="/save">
            <input type="hidden" name="csrf"
                   value="{dashboard_token}">
            <input type="hidden" name="guild_id"
                   value="{guild.id}">

            <label>
              <input type="checkbox" name="anti_raid"
                     {checked("anti_raid")}>
              Anti-Raid
            </label>

            <label>
              <input type="checkbox" name="anti_spam"
                     {checked("anti_spam")}>
              Anti-Spam
            </label>

            <label>
              <input type="checkbox" name="anti_nuke"
                     {checked("anti_nuke")}>
              Anti-Nuke
            </label>

            <label for="log_{guild.id}">Naplócsatorna</label>
            <select id="log_{guild.id}" name="log_channel">
              <option value="">Nincs beállítva</option>
              {"".join(channels)}
            </select>

            <button type="submit">Beállítások mentése</button>
          </form>
        </section>
        """)

    status = "Online" if bot.is_ready() else "Kapcsolódás folyamatban"

    return f"""
    <!doctype html>
    <html lang="hu">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Nexium Security Dashboard</title>
      <style>
        body {{
          margin: 0; padding: 24px;
          background: #10131b; color: #f3f4f6;
          font-family: Arial, sans-serif;
        }}
        main {{ max-width: 900px; margin: auto; }}
        h1 {{ color: #8da8ff; }}
        .card {{
          background: #1c2230; border-radius: 12px;
          padding: 20px; margin: 18px 0;
        }}
        label {{ display: block; margin: 14px 0; }}
        input[type=checkbox] {{ margin-right: 8px; }}
        select, button {{
          display: block; width: 100%; box-sizing: border-box;
          padding: 12px; margin-top: 10px; border-radius: 8px;
        }}
        select {{ background: #10131b; color: white; }}
        button {{
          border: 0; background: #526dfe; color: white;
          font-weight: bold; cursor: pointer;
        }}
        .status {{ color: #9be6b0; }}
        .note {{ color: #c5cad5; font-size: 14px; }}
      </style>
    </head>
    <body>
      <main>
        <h1>🛡️ Nexium Security</h1>
        <p class="status">Bot állapota: {status}</p>
        <p>Dashboard – biztonsági beállítások</p>
        {"".join(cards) if cards else
         "<p>A bot még nem csatlakozott egyetlen szerverhez sem.</p>"}
        <p class="note">
          Ez a felület csak ezen a számítógépen érhető el.
          Az Anti-Nuke jelenleg riasztást és naplózást végez,
          nem állítja le automatikusan a támadókat.
        </p>
      </main>
    </body>
    </html>
    """


@app.route("/save", methods=["POST"])
def save_dashboard():
    if request.form.get("csrf") != dashboard_token:
        return "Érvénytelen kérés. Frissítsd az oldalt.", 403

    try:
        guild_id = int(request.form.get("guild_id", ""))
    except ValueError:
        return "Hibás szerverazonosító.", 400

    guild = bot.get_guild(guild_id)
    if guild is None:
        return "A bot nem találja ezt a szervert.", 404

    channel_value = request.form.get("log_channel", "")
    channel_id = None

    if channel_value:
        try:
            channel_id = int(channel_value)
        except ValueError:
            return "Hibás csatornaazonosító.", 400

        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return "Érvénytelen naplócsatorna.", 400

    new_settings = {
        "anti_raid": "anti_raid" in request.form,
        "anti_spam": "anti_spam" in request.form,
        "anti_nuke": "anti_nuke" in request.form,
        "log_channel": channel_id
    }

    with config_lock:
        config[str(guild_id)] = new_settings

    try:
        save_config()
    except OSError:
        return "Nem sikerült elmenteni a beállításokat.", 500

    return redirect("/")


def start_dashboard():
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False,
        use_reloader=False
    )


# ==========================================
# DISCORD BOT ESEMENYEK
# ==========================================

@bot.event
async def on_ready():
    print("=" * 40)
    print(f"Nexium Security elindult: {bot.user}")
    print(f"Szerverek száma: {len(bot.guilds)}")
    print("Dashboard: http://127.0.0.1:5000")
    print("=" * 40)


@bot.event
async def on_member_join(member):
    settings = get_guild_config(member.guild.id)

    if not settings["anti_raid"]:
        return

    now = time.time()
    queue = joins[member.guild.id]
    queue.append(now)

    while queue and now - queue[0] > 10:
        queue.popleft()

    if len(queue) == 5:
        await send_log(
            member.guild,
            "🚨 **ANTI-RAID RIASZTÁS**\n"
            "10 másodperc alatt legalább 5 tag lépett be."
        )


@bot.event
async def on_message(message):
    if message.author.bot or message.guild is None:
        return

    settings = get_guild_config(message.guild.id)

    if settings["anti_spam"]:
        now = time.time()
        key = (message.guild.id, message.author.id)
        queue = messages[key]
        queue.append(now)

        while queue and now - queue[0] > 5:
            queue.popleft()

        previous = last_messages.get(key)
        repeated = (
            bool(message.content)
            and previous == message.content
        )
        last_messages[key] = message.content

        if len(queue) >= 5 or (repeated and len(queue) >= 3):
            try:
                await message.author.timeout(
                    timedelta(seconds=30),
                    reason="Nexium Security: spam"
                )

                try:
                    await message.delete()
                except discord.HTTPException:
                    pass

                await send_log(
                    message.guild,
                    f"💬 Anti-Spam: {message.author} "
                    "30 másodperces időkorlátozást kapott."
                )
                queue.clear()

            except discord.Forbidden:
                await send_log(
                    message.guild,
                    "⚠️ Az Anti-Spam nem tudott időkorlátozást adni. "
                    "Ellenőrizd a jogosultságokat és a bot rangját."
                )
                queue.clear()

            except discord.HTTPException:
                print("Anti-Spam: Discord API-hiba.")

    await bot.process_commands(message)


@bot.event
async def on_audit_log_entry_create(entry):
    guild = entry.guild

    if guild is None:
        return

    settings = get_guild_config(guild.id)
    if not settings["anti_nuke"]:
        return

    watched_actions = {
        discord.AuditLogAction.channel_delete,
        discord.AuditLogAction.role_delete,
        discord.AuditLogAction.ban,
        discord.AuditLogAction.kick,
        discord.AuditLogAction.webhook_create
    }

    if entry.action not in watched_actions:
        return

    now = time.time()
    queue = deletions[guild.id]
    queue.append(now)

    while queue and now - queue[0] > 10:
        queue.popleft()

    await send_log(
        guild,
        "🔒 **ANTI-NUKE RIASZTÁS**\n"
        f"Művelet: `{entry.action}`\n"
        f"Végrehajtó: {entry.user}\n"
        f"10 másodperc alatt észlelt műveletek: {len(queue)}"
    )

    if len(queue) >= 3:
        await send_log(
            guild,
            "🚨 **VESZÉLY:** Több veszélyes művelet történt "
            "rövid idő alatt. Ellenőrizd az auditnaplót!"
        )


# ==========================================
# DISCORD PARANCSOK
# ==========================================

@bot.command()
@commands.has_permissions(administrator=True)
async def security(ctx):
    s = get_guild_config(ctx.guild.id)
    await ctx.send(
        "🛡️ **Nexium Security**\n"
        f"Anti-Raid: {'BE' if s['anti_raid'] else 'KI'}\n"
        f"Anti-Spam: {'BE' if s['anti_spam'] else 'KI'}\n"
        f"Anti-Nuke: {'BE' if s['anti_nuke'] else 'KI'}\n"
        f"Naplócsatorna: {s['log_channel'] or 'Nincs beállítva'}"
    )


@bot.command()
@commands.has_permissions(administrator=True)
async def setlog(ctx, channel: discord.TextChannel):
    with config_lock:
        s = get_guild_config(ctx.guild.id)
        config[str(ctx.guild.id)] = {
            **s,
            "log_channel": channel.id
        }

    save_config()
    await ctx.send(f"✅ Naplócsatorna beállítva: {channel.mention}")


@bot.command()
@commands.has_permissions(administrator=True)
async def securitytoggle(ctx, feature: str, state: str):
    feature = feature.lower()
    state = state.lower()

    if feature not in ("anti_raid", "anti_spam", "anti_nuke"):
        await ctx.send(
            "Használat: `!securitytoggle anti_spam be`\n"
            "Funkciók: anti_raid, anti_spam, anti_nuke"
        )
        return

    if state not in ("be", "ki"):
        await ctx.send("Az állapot `be` vagy `ki` lehet.")
        return

    with config_lock:
        s = get_guild_config(ctx.guild.id)
        config[str(ctx.guild.id)] = {
            **s,
            feature: state == "be"
        }

    save_config()
    await ctx.send(f"✅ {feature}: {state.upper()}")


@bot.command()
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason="Nincs megadva"):
    if member == ctx.author or member == bot.user:
        await ctx.send("❌ Ezt a felhasználót nem rúghatod ki.")
        return

    try:
        await member.kick(reason=f"{ctx.author}: {reason}")
        await ctx.send(f"👢 {member} kirúgva. Indok: {reason}")
        await send_log(ctx.guild, f"👢 Kick: {member} | Indok: {reason}")
    except discord.Forbidden:
        await ctx.send("❌ Nincs jogosultságom ehhez.")


@bot.command()
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason="Nincs megadva"):
    if member == ctx.author or member == bot.user:
        await ctx.send("❌ Ezt a felhasználót nem tilthatod ki.")
        return

    try:
        await member.ban(reason=f"{ctx.author}: {reason}")
        await ctx.send(f"🔨 {member} kitiltva. Indok: {reason}")
        await send_log(ctx.guild, f"🔨 Ban: {member} | Indok: {reason}")
    except discord.Forbidden:
        await ctx.send("❌ Nincs jogosultságom ehhez.")


@bot.command()
@commands.has_permissions(ban_members=True)
async def unban(ctx, user_id: int):
    try:
        await ctx.guild.unban(
            discord.Object(id=user_id),
            reason=f"Unban: {ctx.author}"
        )
        await ctx.send(f"✅ Kitiltás feloldva: {user_id}")
    except discord.NotFound:
        await ctx.send("❌ Ez a felhasználó nincs kitiltva.")
    except discord.Forbidden:
        await ctx.send("❌ Nincs jogosultságom ehhez.")


@bot.command()
@commands.has_permissions(manage_messages=True)
async def clear(ctx, amount: int):
    if not 1 <= amount <= 100:
        await ctx.send("❌ 1 és 100 közötti számot adj meg.")
        return

    try:
        deleted = await ctx.channel.purge(limit=amount + 1)
        msg = await ctx.send(
            f"🧹 {max(0, len(deleted) - 1)} üzenet törölve."
        )
        await msg.delete(delay=5)
    except discord.Forbidden:
        await ctx.send("❌ Nincs jogosultságom üzenetek törléséhez.")


@bot.command(name="help")
async def help_command(ctx):
    await ctx.send(
        "🛡️ **Nexium Security – Parancsok**\n\n"
        "`!security` – Állapot\n"
        "`!setlog #csatorna` – Naplócsatorna\n"
        "`!securitytoggle anti_spam be` – Védelem be\n"
        "`!kick @tag indok` – Kirúgás\n"
        "`!ban @tag indok` – Kitiltás\n"
        "`!unban FELHASZNALO_ID` – Kitiltás feloldása\n"
        "`!clear 10` – Üzenetek törlése\n"
        "`!help` – Parancsok"
    )


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Nincs jogosultságod ehhez a parancshoz.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Hiányzó adat. Írd be: `!help`")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ Nem találom ezt a tagot.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ Hibás adatot adtál meg.")
    else:
        print(f"Parancshiba: {error}")


# ==========================================
# INDÍTÁS
# ==========================================

if TOKEN == "IDE_IRD_A_SAJAT_BOT_TOKENED":
    raise RuntimeError("Írd be a saját bot tokenedet a TOKEN sorba!")

Thread(target=start_dashboard, daemon=True).start()
bot.run(TOKEN)
