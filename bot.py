import os
import csv
import json
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from datetime import datetime
from math import ceil

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

# ファイル名
CSV_FILE_NAME = "output.csv"
CONFIG_FILE_NAME = "guild_config.json"
GRANTED_HISTORY_FILE = "granted_history.json"
PERMISSIONS_CONFIG_FILE = "permissions_config.json"

valid_uids = set()      # CSVから読み取るUIDリスト
guild_config = {}       # サーバー設定
granted_history = {}    # 付与履歴
permissions_config = {} # コマンド権限設定

# ------------------------------
# JSONファイルのロード/セーブ
# ------------------------------
def load_json(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_all_configs():
    global guild_config, granted_history, permissions_config
    guild_config = load_json(CONFIG_FILE_NAME)
    granted_history = load_json(GRANTED_HISTORY_FILE)
    permissions_config = load_json(PERMISSIONS_CONFIG_FILE)

def save_guild_config():
    save_json(CONFIG_FILE_NAME, guild_config)

def save_granted_history():
    save_json(GRANTED_HISTORY_FILE, granted_history)

def save_permissions_config():
    save_json(PERMISSIONS_CONFIG_FILE, permissions_config)

# ------------------------------
# CSVの読み込み
# ------------------------------
def load_csv():
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

# ------------------------------
# Botセットアップ
# ------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = False

bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------------------
# 起動時
# ------------------------------
@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")

    load_all_configs()
    count = load_csv()
    print(f"Loaded {count} UIDs from CSV.")

    try:
        await bot.tree.sync()
        print("Slash commands synced.")
    except Exception as e:
        print(e)

# ------------------------------
# /setup
# ------------------------------
@bot.tree.command(name="setup", description="Set up the role and button for eligibility checking.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    channel="Select the channel for the check button",
    role="Select the role to be granted if eligible"
)
async def setup_command(interaction: discord.Interaction, channel: discord.TextChannel, role: discord.Role):
    guild_id_str = str(interaction.guild_id)

    old_msg_id = guild_config.get(guild_id_str, {}).get("message_id")
    if old_msg_id:
        # 古いメッセージを削除
        try:
            old_ch_id = guild_config[guild_id_str]["channel_id"]
            old_ch = interaction.guild.get_channel(old_ch_id)
            if old_ch:
                old_msg = await old_ch.fetch_message(old_msg_id)
                await old_msg.delete()
        except Exception as e:
            print(f"Failed to delete old message: {e}")

    # 新規メッセージ
    embed = discord.Embed(title="Check Eligibility",
                          description="Click the button to see if you're on the list.",
                          color=discord.Color.blue())
    view = CheckEligibilityView()
    msg = await channel.send(embed=embed, view=view)

    # guild_configに保存
    guild_config[guild_id_str] = {
        "channel_id": channel.id,
        "channel_name": channel.name,
        "role_id": role.id,
        "role_name": role.name,
        "message_id": msg.id
    }
    save_guild_config()

    await interaction.response.send_message(
        f"Setup completed in {channel.mention} with role {role.mention}.",
        ephemeral=True
    )

# ------------------------------
# /relodelist
# ------------------------------
@bot.tree.command(name="relodelist", description="Reload the user list from CSV.")
@app_commands.default_permissions(administrator=True)
async def relodelist_command(interaction: discord.Interaction):
    count = load_csv()
    await interaction.response.send_message(
        f"List reloaded. {count} UIDs found.",
        ephemeral=True
    )

# ------------------------------
# ボタン用View
# ------------------------------
class CheckEligibilityView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(CheckEligibilityButton())

class CheckEligibilityButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Check Eligibility", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction):
        user_id_str = str(interaction.user.id)
        guild_id_str = str(interaction.guild_id)

        if user_id_str in valid_uids:
            # eligible
            config = guild_config.get(guild_id_str, {})
            role_id = config.get("role_id")
            role = interaction.guild.get_role(role_id) if role_id else None
            if role:
                try:
                    await interaction.user.add_roles(role)
                except discord.Forbidden:
                    await interaction.response.send_message("Insufficient permission to grant role.", ephemeral=True)
                    return

                # 履歴に追加
                if guild_id_str not in granted_history:
                    granted_history[guild_id_str] = []
                granted_history[guild_id_str].append({
                    "uid": user_id_str,
                    "name": str(interaction.user),
                    "time": datetime.utcnow().isoformat()
                })
                save_granted_history()

                await interaction.response.send_message(
                    f"You are eligible (UID: {user_id_str}). Role granted!",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message("No valid role configured. Please /setup again.", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"You are not eligible (UID: {user_id_str}).",
                ephemeral=True
            )

# ------------------------------
# /history: 履歴をページング表示
# ------------------------------
@bot.tree.command(name="history", description="Show role-grant history in pages of 10.")
@app_commands.describe()
async def history_command(interaction: discord.Interaction):
    # ---- ここで権限をインラインチェック ----
    # 1) サーバー管理者ならOK
    if interaction.user.guild_permissions.administrator:
        pass
    else:
        # 2) permissions_configで許可されたユーザー/ロールか？
        guild_id_str = str(interaction.guild_id)
        allowed_info = permissions_config.get(guild_id_str, {}).get("history", {})
        # allowed_info形式: {"roles": [123, ...], "users": [456, ...]}
        user_id = interaction.user.id
        user_role_ids = [r.id for r in interaction.user.roles]

        # ユーザーID含まれている？
        if user_id not in allowed_info.get("users", []):
            # ロールID含まれている？
            intersect = set(user_role_ids) & set(allowed_info.get("roles", []))
            if not intersect:
                return await interaction.response.send_message(
                    "You do not have permission to use /history.",
                    ephemeral=True
                )

    # ここまで来たら権限OK → 履歴表示
    guild_id_str = str(interaction.guild_id)
    records = granted_history.get(guild_id_str, [])
    if not records:
        await interaction.response.send_message("No history found here.", ephemeral=True)
        return

    view = HistoryView(records)
    await view.update_message(interaction)  # 最初のページを送信

class HistoryView(discord.ui.View):
    def __init__(self, records):
        super().__init__(timeout=None)
        self.records = records
        self.page = 0
        self.per_page = 10

        self.add_item(PrevButton())
        self.add_item(NextButton())

    def max_page(self):
        return max(1, ceil(len(self.records) / self.per_page))

    def get_page_entries(self):
        start = self.page * self.per_page
        end = start + self.per_page
        return self.records[start:end]

    async def update_message(self, interaction: discord.Interaction):
        entries = self.get_page_entries()
        lines = []
        for i, r in enumerate(entries, start=1):
            index = i + self.page * self.per_page
            lines.append(f"[{index}] UID:{r['uid']}, User:{r['name']}, Time:{r['time']}")
        page_info = f"Page {self.page+1}/{self.max_page()} (Total {len(self.records)} records)"
        note = "You can view the full JSON file for all data."

        content = f"**History**\n```\n" + "\n".join(lines) + "\n```\n" + page_info + "\n" + note

        if interaction.response.is_done():
            await interaction.edit_original_response(content=content, view=self)
        else:
            await interaction.response.send_message(content=content, view=self, ephemeral=True)

class PrevButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="◀ Prev", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        view: HistoryView = self.view  # type: ignore
        if view.page > 0:
            view.page -= 1
        await view.update_message(interaction)

class NextButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Next ▶", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        view: HistoryView = self.view  # type: ignore
        if view.page < view.max_page() - 1:
            view.page += 1
        await view.update_message(interaction)

# ------------------------------
# /grantpermission: 任意コマンドに許可を与える
# ------------------------------
@bot.tree.command(name="grantpermission", description="Grant permission for a command to a user or role.")
@app_commands.describe(
    command_name="Target command name",
    target_type="Set to 'user' or 'role'",
    target_id="Numeric ID of user or role"
)
async def grantpermission_command(interaction: discord.Interaction, command_name: str, target_type: str, target_id: str):
    # シンプルに「管理者権限のみOK」とする
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message(
            "Only server admins can use /grantpermission.",
            ephemeral=True
        )

    guild_id_str = str(interaction.guild_id)
    if guild_id_str not in permissions_config:
        permissions_config[guild_id_str] = {}

    if command_name not in permissions_config[guild_id_str]:
        permissions_config[guild_id_str][command_name] = {"roles": [], "users": []}

    data = permissions_config[guild_id_str][command_name]
    try:
        num_id = int(target_id)
    except ValueError:
        return await interaction.response.send_message("Please provide a numeric ID.", ephemeral=True)

    if target_type.lower() == "user":
        if num_id not in data["users"]:
            data["users"].append(num_id)
        msg = f"Granted permission for '{command_name}' to user ID {num_id}."
    elif target_type.lower() == "role":
        if num_id not in data["roles"]:
            data["roles"].append(num_id)
        msg = f"Granted permission for '{command_name}' to role ID {num_id}."
    else:
        return await interaction.response.send_message("Use 'user' or 'role' only.", ephemeral=True)

    save_permissions_config()
    await interaction.response.send_message(msg, ephemeral=True)

# ------------------------------
# 実行
# ------------------------------
if __name__ == "__main__":
    bot.run(TOKEN)
