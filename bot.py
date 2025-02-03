import os
import csv
import json
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

# ============= CONFIGURATIONS =============
LIST_FILE_NAME = "output.csv"           # もともとのCSVファイル名 (内部的にはCSVを読み込む)
CONFIG_FILE_NAME = "guild_config.json"  # JSONにギルド設定を保存
# ==========================================

# 有効UIDを保持 (文字列型に揃える)
valid_uids = set()

# ギルドごとの設定を保持
# 形式:
# {
#   "guild_id文字列": {
#       "channel_id": int,
#       "channel_name": str,
#       "role_id": int,
#       "role_name": str,
#       "message_id": int,   // 新規メッセージのID
#       "spreadsheet_url": str (省略可)
#   },
#   ...
# }
guild_config = {}

# ---------------------------
# JSON 読み込み/保存
# ---------------------------
def load_guild_config():
    global guild_config
    if os.path.exists(CONFIG_FILE_NAME):
        with open(CONFIG_FILE_NAME, "r", encoding="utf-8") as f:
            guild_config = json.load(f)
    else:
        guild_config = {}

def save_guild_config():
    with open(CONFIG_FILE_NAME, "w", encoding="utf-8") as f:
        json.dump(guild_config, f, ensure_ascii=False, indent=2)

# ---------------------------
# リスト(旧CSV)の読み込み
# ---------------------------
def load_list():
    """
    Listファイルを読み込んで valid_uids を更新
    Returns: 読み込まれたUIDの数 (int)
    """
    global valid_uids
    new_valid_uids = set()

    try:
        with open(LIST_FILE_NAME, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                uid = row.get("DCUID")
                if uid:
                    new_valid_uids.add(uid)
    except FileNotFoundError:
        print(f"List file '{LIST_FILE_NAME}' not found.")
    
    valid_uids = new_valid_uids
    return len(valid_uids)


# ---------------------------
# Bot設定
# ---------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # ロール付与にはメンバー情報が必要
intents.presences = False

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------------------
# 起動時処理
# ---------------------------
@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")
    # JSONとリストを読み込み
    load_guild_config()
    num_loaded = load_list()
    print(f"List reloaded successfully. {num_loaded} UIDs loaded.")
    # Slashコマンドを同期
    try:
        await bot.tree.sync()
        print("Slash commands synced.")
    except Exception as e:
        print(e)

# ============================
# /setup
# ============================
@bot.tree.command(name="setup", description="Set up the role, channel, and optional spreadsheet link for eligibility checking.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    channel="Select the channel to send the button message",
    role="Select the role to be granted if eligible",
    spreadsheet="Optional: link to a public spreadsheet"
)
async def setup_command(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    role: discord.Role,
    spreadsheet: str = None
):
    """
    Example:
    /setup channel:#general role:@MyRole spreadsheet:https://docs.google.com/spreadsheets/d/xxxxx
    """
    guild_id_str = str(interaction.guild_id)

    # 古いメッセージがあるなら削除
    old_msg_id = None
    if guild_id_str in guild_config:
        old_msg_id = guild_config[guild_id_str].get("message_id")
    
    if old_msg_id is not None:
        try:
            old_channel_id = guild_config[guild_id_str]["channel_id"]
            old_channel = interaction.guild.get_channel(old_channel_id)
            if old_channel:
                old_msg = await old_channel.fetch_message(old_msg_id)
                await old_msg.delete()
        except Exception as e:
            print(f"Failed to delete old message: {e}")

    # ボタンつきメッセージを新規作成
    embed = discord.Embed(
        title="Check Eligibility",
        description=(
            "Click the button below to check if you are eligible.\n"
            "If you are in the list, the role will be granted."
        ),
        color=discord.Color.blue()
    )
    view = CheckEligibilityView()
    msg = await channel.send(embed=embed, view=view)

    # guild_config更新
    guild_config[guild_id_str] = {
        "channel_id": channel.id,
        "channel_name": channel.name,
        "role_id": role.id,
        "role_name": role.name,
        "message_id": msg.id,
        "spreadsheet_url": spreadsheet or ""
    }
    save_guild_config()

    resp = f"Setup completed in {channel.mention} with role {role.mention}."
    if spreadsheet:
        resp += f"\nSpreadsheet URL saved: {spreadsheet}"

    await interaction.response.send_message(resp, ephemeral=True)

# ============================
# /relodelist (手動でリスト再読み込み)
# ============================
@bot.tree.command(name="relodelist", description="Reload the list file manually.")
@app_commands.default_permissions(administrator=True)
async def relodelist_command(interaction: discord.Interaction):
    count = load_list()
    await interaction.response.send_message(
        f"List reloaded successfully. **{count}** UIDs loaded.",
        ephemeral=True
    )

# ============================
# /showspreadsheet (管理者のみ)
# ============================
@bot.tree.command(name="showspreadsheet", description="Show the spreadsheet link for this server's eligibility.")
@app_commands.default_permissions(administrator=True)
async def show_spreadsheet_command(interaction: discord.Interaction):
    guild_id_str = str(interaction.guild_id)
    config = guild_config.get(guild_id_str)
    if not config:
        await interaction.response.send_message(
            "No setup found for this server. Please run /setup first.",
            ephemeral=True
        )
        return
    spreadsheet_url = config.get("spreadsheet_url")
    if spreadsheet_url:
        await interaction.response.send_message(
            f"Spreadsheet URL: {spreadsheet_url}",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "No spreadsheet URL has been set for this server.",
            ephemeral=True
        )

# ============================
# View と ボタン
# ============================
class CheckEligibilityView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # 再起動するまで有効

        self.add_item(CheckEligibilityButton())

class CheckEligibilityButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Check Eligibility",
            style=discord.ButtonStyle.primary
        )

    async def callback(self, interaction: discord.Interaction):
        user_id_str = str(interaction.user.id)
        guild_id_str = str(interaction.guild_id)

        if user_id_str in valid_uids:
            # eligible -> 付与
            config = guild_config.get(guild_id_str)
            if config:
                role_id = config["role_id"]
                role = interaction.guild.get_role(role_id)
                if role:
                    try:
                        await interaction.user.add_roles(role)
                        await interaction.response.send_message(
                            f"You are **eligible** (UID: {user_id_str}).\nRole {role.mention} has been granted!",
                            ephemeral=True
                        )
                    except discord.Forbidden:
                        await interaction.response.send_message(
                            "Unable to grant the role due to insufficient permissions.",
                            ephemeral=True
                        )
                else:
                    await interaction.response.send_message(
                        "The configured role does not exist anymore. Please run /setup again.",
                        ephemeral=True
                    )
            else:
                await interaction.response.send_message(
                    "This server has not been set up. Please use /setup first.",
                    ephemeral=True
                )
        else:
            await interaction.response.send_message(
                f"You are **not eligible** (UID: {user_id_str}).",
                ephemeral=True
            )

# ============================
# 実行
# ============================
if __name__ == "__main__":
    bot.run(TOKEN)
