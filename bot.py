import os
import csv
import json
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from datetime import datetime
from math import ceil

# Google Sheets API 用ライブラリ
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# 環境変数の読み込み (.env に BOT_TOKEN と GOOGLE_CREDENTIALS を定義)
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

# CSVファイル (付与対象UID一覧)
CSV_FILE_NAME = "output.csv"

# メモリ上のデータ保持
valid_uids = set()   # output.csvから読み込むUIDリスト
guild_config = {}    # {guild_id: {"server_name", "channel_id", "role_id", "message_id"}}
granted_history = {} # {guild_id: [ {"uid", "username", "time"}, ... ]}

# Google Sheets の認証情報を環境変数から取得（credentials.jsonは使わず.envのみ）
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
google_credentials_str = os.getenv("GOOGLE_CREDENTIALS")
if google_credentials_str is None:
    raise Exception("GOOGLE_CREDENTIALS not found in environment variables.")
try:
    creds_dict = json.loads(google_credentials_str)
except Exception as e:
    raise Exception("Failed to parse GOOGLE_CREDENTIALS: " + str(e))
CREDS = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, SCOPE)
GSPREAD_CLIENT = gspread.authorize(CREDS)

# ※ スプレッドシート名は "keone_list_log"（必要に応じて変更してください）
try:
    SPREADSHEET = GSPREAD_CLIENT.open("keone_list_log")
except Exception as e:
    raise Exception("Failed to open spreadsheet: " + str(e))

# --- Google Sheets 関連の関数 ---
def get_sheet(sheet_name, rows="1000", cols="10"):
    try:
        return SPREADSHEET.worksheet(sheet_name)
    except Exception:
        return SPREADSHEET.add_worksheet(title=sheet_name, rows=rows, cols=cols)

def get_log_sheet():
    return get_sheet("Log")

def append_log_to_sheet(guild_id: str, uid: str, username: str, timestamp: str):
    ws = get_log_sheet()
    try:
        ws.append_row([guild_id, uid, username, timestamp])
    except Exception as e:
        print(f"Failed to append log to sheet: {e}")

def load_guild_config_sheet():
    global guild_config
    guild_config = {}
    try:
        ws = SPREADSHEET.worksheet("guild_config")
        records = ws.get_all_records()
        for row in records:
            guild_id = str(row.get("guild_id")).strip()
            if guild_id:
                guild_config[guild_id] = {
                    "server_name": row.get("server_name", ""),
                    "channel_id": int(row.get("channel_id") or 0),
                    "role_id": int(row.get("role_id") or 0),
                    "message_id": int(row.get("message_id") or 0)
                }
    except Exception as e:
        print(f"Error loading guild_config: {e}")

def save_guild_config_sheet():
    ws = get_sheet("guild_config", rows="100", cols="10")
    headers = ["guild_id", "server_name", "channel_id", "role_id", "message_id"]
    ws.clear()
    data = [headers]
    for gid, conf in guild_config.items():
        row = [
            gid,
            conf.get("server_name", ""),
            conf.get("channel_id", ""),
            conf.get("role_id", ""),
            conf.get("message_id", "")
        ]
        data.append(row)
    ws.update("A1", data)

def load_granted_history_sheet():
    global granted_history
    granted_history = {}
    try:
        ws = SPREADSHEET.worksheet("granted_history")
        records = ws.get_all_records()
        for row in records:
            guild_id = str(row.get("guild_id")).strip()
            if guild_id:
                if guild_id not in granted_history:
                    granted_history[guild_id] = []
                granted_history[guild_id].append({
                    "uid": row.get("uid", ""),
                    "username": row.get("username", ""),
                    "time": row.get("time", "")
                })
    except Exception as e:
        print(f"Error loading granted_history: {e}")

def save_granted_history_sheet():
    ws = get_sheet("granted_history", rows="1000", cols="10")
    headers = ["guild_id", "uid", "username", "time"]
    ws.clear()
    data = [headers]
    for gid, records in granted_history.items():
        for record in records:
            row = [
                gid,
                record.get("uid", ""),
                record.get("username", ""),
                record.get("time", "")
            ]
            data.append(row)
    ws.update("A1", data)

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

def load_all_data():
    load_uid_list()
    load_guild_config_sheet()
    load_granted_history_sheet()

def save_all_data():
    save_guild_config_sheet()
    save_granted_history_sheet()

# --- Discord Bot の初期化 ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --- 永続的な UI コンポーネント: チェックボタンとビュー ---
class CheckEligibilityButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            custom_id="check_eligibility_button",
            label="Check Eligibility",
            style=discord.ButtonStyle.primary
        )

    async def callback(self, interaction: discord.Interaction):
        guild_id_str = str(interaction.guild_id)
        user_id_str = str(interaction.user.id)

        if user_id_str not in valid_uids:
            return await interaction.response.send_message(
                f"You are not eligible (UID: {user_id_str}).", ephemeral=True
            )

        info = guild_config.get(guild_id_str)
        if not info:
            return await interaction.response.send_message(
                "No setup found. Please run /setup.", ephemeral=True
            )

        role = interaction.guild.get_role(info["role_id"])
        if not role:
            return await interaction.response.send_message("Configured role not found.", ephemeral=True)

        if role in interaction.user.roles:
            return await interaction.response.send_message("You already have this role.", ephemeral=True)

        try:
            await interaction.user.add_roles(role)
        except discord.Forbidden:
            return await interaction.response.send_message("Failed to grant role. Check bot permissions.", ephemeral=True)

        log_entry = {
            "uid": user_id_str,
            "username": str(interaction.user),
            "time": datetime.utcnow().isoformat()
        }
        granted_history.setdefault(guild_id_str, []).append(log_entry)
        save_granted_history_sheet()
        append_log_to_sheet(guild_id_str, user_id_str, str(interaction.user), datetime.utcnow().isoformat())

        await interaction.response.send_message(
            f"You are **eligible** (UID: {user_id_str}). Role {role.mention} has been granted!",
            ephemeral=True
        )

class CheckEligibilityView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(CheckEligibilityButton())

# --- 履歴表示用のページング UI ---
class HistoryPagerView(discord.ui.View):
    def __init__(self, records):
        super().__init__(timeout=None)
        self.records = records
        self.page = 0
        self.per_page = 10
        self.prev_button = PrevButton()
        self.next_button = NextButton()
        self.add_item(self.prev_button)
        self.add_item(self.next_button)
        self.update_buttons()

    def max_page(self):
        return ceil(len(self.records) / self.per_page) if self.records else 1

    def update_buttons(self):
        self.prev_button.disabled = (self.page <= 0)
        self.next_button.disabled = (self.page >= self.max_page() - 1)

    def get_page_content(self):
        start = self.page * self.per_page
        end = start + self.per_page
        chunk = self.records[start:end]
        lines = []
        for i, record in enumerate(chunk, start=1):
            idx = start + i
            lines.append(f"[{idx}] UID: {record['uid']}, User: {record['username']}, Time: {record['time']}")
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
        view.update_buttons()
        await interaction.response.defer_update()
        await interaction.edit_original_response(content=view.get_page_content(), view=view)

class NextButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Next ▶", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        view: HistoryPagerView = self.view  # type: ignore
        if view.page < view.max_page() - 1:
            view.page += 1
        view.update_buttons()
        await interaction.response.defer_update()
        await interaction.edit_original_response(content=view.get_page_content(), view=view)

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
    bot.add_view(CheckEligibilityView())

@bot.event
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    print(f"App command error: {error}")
    await interaction.response.send_message(
        "An error occurred. Please try again or contact an admin.",
        ephemeral=True
    )

# --- /setup コマンド (投稿チャンネルと付与するロールのみ) ---
@bot.tree.command(name="setup", description="Set up or update the eligibility button and assigned role.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    channel="Channel for the check button",
    role="Role to grant if eligible"
)
async def setup_command(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    role: discord.Role
):
    guild_id_str = str(interaction.guild_id)
    old_info = guild_config.get(guild_id_str, {})
    old_msg_id = old_info.get("message_id")
    old_ch_id = old_info.get("channel_id", 0)

    embed_text = "Click the button below to see if you're on the list."
    if old_msg_id and old_ch_id:
        old_ch = interaction.guild.get_channel(old_ch_id)
        if old_ch:
            try:
                old_msg = await old_ch.fetch_message(old_msg_id)
                embed = discord.Embed(
                    title="Check Eligibility",
                    description=embed_text,
                    color=discord.Color.blue()
                )
                view = CheckEligibilityView()
                await old_msg.edit(embed=embed, view=view)
                guild_config[guild_id_str] = {
                    "server_name": interaction.guild.name,
                    "channel_id": channel.id,
                    "role_id": role.id,
                    "message_id": old_msg.id
                }
                save_guild_config_sheet()
                return await interaction.response.send_message(
                    f"Button message updated in {old_ch.mention}. Role set to {role.mention}.",
                    ephemeral=True
                )
            except Exception as e:
                print(f"Error editing old message: {e}")

    embed = discord.Embed(
        title="Check Eligibility",
        description=embed_text,
        color=discord.Color.blue()
    )
    view = CheckEligibilityView()
    new_msg = await channel.send(embed=embed, view=view)
    guild_config[guild_id_str] = {
        "server_name": interaction.guild.name,
        "channel_id": channel.id,
        "role_id": role.id,
        "message_id": new_msg.id
    }
    save_guild_config_sheet()
    await interaction.response.send_message(
        f"Setup complete in {channel.mention} with role {role.mention}.",
        ephemeral=True
    )

# --- /reloadlist コマンド ---
@bot.tree.command(name="reloadlist", description="Reload the user list from CSV.")
@app_commands.default_permissions(administrator=True)
async def reloadlist_command(interaction: discord.Interaction):
    count = load_uid_list()
    await interaction.response.send_message(
        f"Reloaded user list. {count} UIDs found.",
        ephemeral=True
    )

# --- /history コマンド ---
@bot.tree.command(name="history", description="Show the role-grant history in pages of 10.")
@app_commands.default_permissions(administrator=True)
async def history_command(interaction: discord.Interaction):
    load_granted_history_sheet()  # 最新データを再読み込み
    guild_id_str = str(interaction.guild_id)
    records = granted_history.get(guild_id_str, [])
    if not records:
        return await interaction.response.send_message("No history for this server.", ephemeral=True)

    view = HistoryPagerView(records)
    await interaction.response.send_message(view.get_page_content(), view=view, ephemeral=True)

# --- /extractinfo コマンド ---
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

    lines = []
    lines.append("**Server Info**")
    lines.append(f"- Server Name: {info.get('server_name', '')}")
    lines.append(f"- Channel ID: {ch_id}")
    lines.append(f"- Role ID: {role_id}")
    lines.append(f"- Setup Message ID: {msg_id}")

    recs = granted_history.get(guild_id_str, [])
    lines.append(f"\n**Recent Role Grants** (total {len(recs)})")
    for i, record in enumerate(recs[-10:], start=1):
        lines.append(f"{i}. UID: {record['uid']}, Name: {record['username']}, Time: {record['time']}")

    report = "\n".join(lines)
    await interaction.response.send_message(report, ephemeral=True)

# --- /reset_history コマンド (履歴のリセット) ---
@bot.tree.command(name="reset_history", description="Reset the role-grant history (admin only).")
@app_commands.default_permissions(administrator=True)
async def reset_history_command(interaction: discord.Interaction):
    guild_id_str = str(interaction.guild_id)
    granted_history[guild_id_str] = []
    save_granted_history_sheet()
    await interaction.response.send_message("History has been reset for this server.", ephemeral=True)

if __name__ == "__main__":
    bot.run(TOKEN)
