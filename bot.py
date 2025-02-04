import os
import csv
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from datetime import datetime
from math import ceil

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

# -----------------------------
# CSVファイル名
# -----------------------------
CSV_FILE_NAME = "output.csv"
GUILD_CONFIG_CSV = "guild_config.csv"
GRANTED_HISTORY_CSV = "granted_history.csv"

# -----------------------------
# メモリ上の保持データ
# -----------------------------
valid_uids = set()  # output.csvから読み込んだUID
guild_config = {}   # {guild_id: {channel_id, role_id, message_id, debug_channel_id}}
granted_history = {}# {guild_id: [ {uid, username, time}, ... ]}

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ====================================
# CSV 読み書き
# ====================================
def load_uid_list():
    global valid_uids
    new_uids = set()
    try:
        with open(CSV_FILE_NAME, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                uid = row.get("DCUID")
                if uid:
                    new_uids.add(uid)
    except FileNotFoundError:
        print(f"{CSV_FILE_NAME} not found.")
    valid_uids = new_uids
    return len(valid_uids)

def load_guild_config():
    global guild_config
    guild_config = {}
    if not os.path.exists(GUILD_CONFIG_CSV):
        return
    with open(GUILD_CONFIG_CSV, "r", encoding="utf-8", newline="") as f:
        rdr = csv.DictReader(f)
        for row in rdr:
            gid = row["guild_id"]
            guild_config[gid] = {
                "channel_id": int(row["channel_id"]),
                "role_id": int(row["role_id"]),
                "message_id": int(row["message_id"]),
                "debug_channel_id": int(row["debug_channel_id"]) if row["debug_channel_id"] else 0
            }

def save_guild_config():
    fieldnames = ["guild_id","channel_id","role_id","message_id","debug_channel_id"]
    with open(GUILD_CONFIG_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for gid, val in guild_config.items():
            w.writerow({
                "guild_id": gid,
                "channel_id": val["channel_id"],
                "role_id": val["role_id"],
                "message_id": val["message_id"],
                "debug_channel_id": val["debug_channel_id"] if val["debug_channel_id"] else ""
            })

def load_granted_history():
    global granted_history
    granted_history = {}
    if not os.path.exists(GRANTED_HISTORY_CSV):
        return
    with open(GRANTED_HISTORY_CSV, "r", encoding="utf-8", newline="") as f:
        rdr = csv.DictReader(f)
        for row in rdr:
            gid = row["guild_id"]
            if gid not in granted_history:
                granted_history[gid] = []
            granted_history[gid].append({
                "uid": row["uid"],
                "username": row["username"],
                "time": row["time"]
            })

def save_granted_history():
    fieldnames = ["guild_id","uid","username","time"]
    with open(GRANTED_HISTORY_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for gid, records in granted_history.items():
            for r in records:
                w.writerow({
                    "guild_id": gid,
                    "uid": r["uid"],
                    "username": r["username"],
                    "time": r["time"]
                })

def load_all_data():
    load_uid_list()
    load_guild_config()
    load_granted_history()

def save_all_data():
    save_guild_config()
    save_granted_history()

# ====================================
# Bot 起動時
# ====================================
@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")
    load_all_data()
    print(f"UID loaded: {len(valid_uids)}")
    try:
        await bot.tree.sync()
        print("Slash commands synced.")
    except Exception as e:
        print(e)

# ------------------------------------
# エラー時: debug channel へ送信
# ------------------------------------
@bot.event
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    print(f"App command error: {error}")
    guild = interaction.guild
    if guild:
        g_id = str(guild.id)
        if g_id in guild_config:
            dbg_id = guild_config[g_id].get("debug_channel_id", 0)
            if dbg_id:
                dbg_ch = guild.get_channel(dbg_id)
                if dbg_ch:
                    await dbg_ch.send(
                        f"[DEBUG] An error occurred: {error}\n"
                        "Often, re-running `/setup` solves issues if there's a recent update."
                    )
    await interaction.response.send_message(
        "An error occurred. Please try again or contact an admin.\n(If updated, `/setup` often fixes it.)",
        ephemeral=True
    )

# ------------------------------------
# /setup: ボタン再利用 (古いメッセージをedit)
# ------------------------------------
@bot.tree.command(name="setup", description="Set up or update the eligibility button, assigned role, and debug channel.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    channel="Channel for the check button",
    role="Role to grant if eligible",
    debug_channel="Channel for debug logs"
)
async def setup_command(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    role: discord.Role,
    debug_channel: discord.TextChannel
):
    guild_id_str = str(interaction.guild_id)
    old_info = guild_config.get(guild_id_str, {})

    old_msg_id = old_info.get("message_id")
    old_ch_id = old_info.get("channel_id", 0)

    if old_msg_id and old_ch_id:
        # 古いメッセージをfetchし、editする
        old_ch = interaction.guild.get_channel(old_ch_id)
        if old_ch:
            try:
                old_msg = await old_ch.fetch_message(old_msg_id)
                # edit
                embed = discord.Embed(
                    title="Check Eligibility",
                    description="(Updated) Click the button below to see if you're on the list.",
                    color=discord.Color.blue()
                )
                view = CheckEligibilityView()
                await old_msg.edit(embed=embed, view=view)

                # guild_config上書き
                guild_config[guild_id_str] = {
                    "channel_id": channel.id,
                    "role_id": role.id,
                    "message_id": old_msg.id,
                    "debug_channel_id": debug_channel.id
                }
                save_guild_config()

                return await interaction.response.send_message(
                    f"**Button message updated** in {old_ch.mention}.\n"
                    f"New role: {role.mention}, debug channel: {debug_channel.mention}",
                    ephemeral=True
                )
            except Exception:
                pass

    # ここに来た場合: 古いメッセージが無い or 取得失敗 → 新規作成
    embed = discord.Embed(
        title="Check Eligibility",
        description="Click the button below to see if you're on the list.",
        color=discord.Color.blue()
    )
    view = CheckEligibilityView()
    new_msg = await channel.send(embed=embed, view=view)

    guild_config[guild_id_str] = {
        "channel_id": channel.id,
        "role_id": role.id,
        "message_id": new_msg.id,
        "debug_channel_id": debug_channel.id
    }
    save_guild_config()

    await interaction.response.send_message(
        f"**Setup complete** in {channel.mention} with role {role.mention}.\nDebug channel: {debug_channel.mention}",
        ephemeral=True
    )

# ------------------------------------
# /reloadList (CSV再読み込み)
# ------------------------------------
@bot.tree.command(name="reloadList", description="Reload the user list from CSV.")
@app_commands.default_permissions(administrator=True)
async def reloadList_command(interaction: discord.Interaction):
    count = load_uid_list()
    await interaction.response.send_message(
        f"Reloaded user list. {count} UIDs found.",
        ephemeral=True
    )

# ------------------------------------
# ボタンView / ボタン本体
# ------------------------------------
class CheckEligibilityView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(CheckEligibilityButton())

class CheckEligibilityButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Check Eligibility", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction):
        guild_id_str = str(interaction.guild_id)
        user_id_str = str(interaction.user.id)

        if user_id_str not in valid_uids:
            return await interaction.response.send_message(
                f"You are not eligible (UID: {user_id_str}).",
                ephemeral=True
            )

        info = guild_config.get(guild_id_str)
        if not info:
            return await interaction.response.send_message(
                "No setup found. Please run /setup.",
                ephemeral=True
            )

        role_id = info["role_id"]
        role = interaction.guild.get_role(role_id)
        if not role:
            return await interaction.response.send_message("Configured role not found.", ephemeral=True)

        if role in interaction.user.roles:
            return await interaction.response.send_message("You already have this role.", ephemeral=True)

        # 付与
        try:
            await interaction.user.add_roles(role)
        except discord.Forbidden:
            return await interaction.response.send_message("Failed to grant role. Check bot permissions.", ephemeral=True)

        # 履歴保存
        if guild_id_str not in granted_history:
            granted_history[guild_id_str] = []
        granted_history[guild_id_str].append({
            "uid": user_id_str,
            "username": str(interaction.user),
            "time": datetime.utcnow().isoformat()
        })
        save_granted_history()

        await interaction.response.send_message(
            f"You are **eligible** (UID: {user_id_str}). Role {role.mention} has been granted!",
            ephemeral=True
        )

# ------------------------------------
# /history: ページング
# ------------------------------------
@bot.tree.command(name="history", description="Show the role-grant history in pages of 10.")
@app_commands.default_permissions(administrator=True)
async def history_command(interaction: discord.Interaction):
    guild_id_str = str(interaction.guild_id)
    records = granted_history.get(guild_id_str, [])
    if not records:
        return await interaction.response.send_message("No history for this server.", ephemeral=True)

    view = HistoryPagerView(records)
    await interaction.response.send_message(view.get_page_content(), view=view, ephemeral=True)

class HistoryPagerView(discord.ui.View):
    def __init__(self, records):
        super().__init__(timeout=None)
        self.records = records
        self.page = 0
        self.per_page = 10
        if len(records) > self.per_page:
            self.add_item(PrevButton())
            self.add_item(NextButton())

    def max_page(self):
        return ceil(len(self.records) / self.per_page)

    def get_page_content(self):
        start = self.page * self.per_page
        end = start + self.per_page
        chunk = self.records[start:end]

        lines = []
        for i, r in enumerate(chunk, start=1):
            idx = start + i
            lines.append(f"[{idx}] UID:{r['uid']}, User:{r['username']}, Time:{r['time']}")
        info = f"Page {self.page+1}/{self.max_page()} (Total {len(self.records)})"
        if not lines:
            lines.append("No data on this page.")
        text = "\n".join(lines)
        return f"**History**\n```\n{text}\n```\n{info}"

class PrevButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="◀ Prev", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        view: HistoryPagerView = self.view  # type: ignore
        if view.page > 0:
            view.page -= 1
        await interaction.response.defer_update()
        await interaction.edit_original_response(content=view.get_page_content(), view=view)

class NextButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Next ▶", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        view: HistoryPagerView = self.view  # type: ignore
        if view.page < view.max_page()-1:
            view.page += 1
        await interaction.response.defer_update()
        await interaction.edit_original_response(content=view.get_page_content(), view=view)


# ------------------------------------
# 任意のサーバー情報抽出例
# ------------------------------------
@bot.tree.command(name="extractinfo", description="Extract server info and recent role assignments.")
@app_commands.default_permissions(administrator=True)
async def extractinfo_command(interaction: discord.Interaction):
    guild_id_str = str(interaction.guild_id)
    info = guild_config.get(guild_id_str)
    if not info:
        return await interaction.response.send_message("No setup info found for this server.", ephemeral=True)

    ch_id = info["channel_id"]
    role_id = info["role_id"]
    msg_id = info["message_id"]
    dbg_id = info["debug_channel_id"]

    lines = []
    lines.append(f"**Server Info**")
    lines.append(f"- Channel ID: {ch_id}")
    lines.append(f"- Role ID: {role_id}")
    lines.append(f"- Setup Message ID: {msg_id}")
    lines.append(f"- Debug Channel ID: {dbg_id}")

    recs = granted_history.get(guild_id_str, [])
    lines.append(f"\n**Recent Role Grants** (total {len(recs)})")
    for i, r in enumerate(recs[-10:], start=1):
        lines.append(f"{i}. UID={r['uid']}, Name={r['username']}, Time={r['time']}")

    report = "\n".join(lines)
    await interaction.response.send_message(report, ephemeral=True)


if __name__ == "__main__":
    bot.run(TOKEN)
