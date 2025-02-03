import os
import csv
import json
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

# =============== SETTINGS ===============
CSV_FILE_NAME = "output.csv"             # CSVファイル名
CONFIG_FILE_NAME = "guild_config.json"    # JSON保存ファイル
# ========================================

# 有効UID (文字列型で保持)
valid_uids = set()

# ギルドごとの設定を保持する辞書
# 形式: {
#   guild_id (str): {
#       "channel_id": int,
#       "channel_name": str,
#       "role_id": int,
#       "role_name": str,
#       "message_id": int
#   },
#   ...
# }
guild_config = {}

# ---------------------------
# JSON 読み込み/書き込み
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
# CSV読み込み
# ---------------------------
def load_csv():
    """
    Returns: 読み込んだUIDの数
    """
    global valid_uids
    new_valid_uids = set()
    
    try:
        with open(CSV_FILE_NAME, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                uid = row.get("DCUID")
                if uid:
                    new_valid_uids.add(uid)
    except FileNotFoundError:
        print(f"CSV file '{CSV_FILE_NAME}' not found. Creating an empty set.")
    
    valid_uids = new_valid_uids
    return len(valid_uids)

# ---------------------------
# Discord Bot Setup
# ---------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # Required for add_roles
intents.presences = False  # Usually not needed unless you specifically need presence info

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------------------
# 起動時
# ---------------------------
@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")

    # JSON & CSV のロード
    load_guild_config()
    loaded_count = load_csv()
    print(f"CSV reloaded. {loaded_count} UIDs loaded.")

    # スラッシュコマンド同期
    try:
        await bot.tree.sync()
        print("Slash commands synced.")
    except Exception as e:
        print(e)

# ============================
# /setup command
# ============================
@bot.tree.command(name="setup", description="Set up the role and button for eligibility checking.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    channel="Select the channel to send the button message",
    role="Select the role to be granted if eligible"
)
async def setup_command(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    role: discord.Role
):
    """
    Usage Example:
    /setup channel:#general role:@MyRole
    """
    guild_id_str = str(interaction.guild_id)
    
    # 既存のメッセージがあれば削除 or 編集
    old_message_id = None
    if guild_id_str in guild_config:
        old_message_id = guild_config[guild_id_str].get("message_id")

    # もし古いメッセージがある場合、削除して新しいチャンネルに投稿する(編集で同じチャンネルに貼り直すのも可)
    if old_message_id is not None:
        # 古いメッセージを削除(存在する＆Botが削除権限を持っている場合)
        try:
            old_channel_id = guild_config[guild_id_str]["channel_id"]
            old_channel = interaction.guild.get_channel(old_channel_id)
            if old_channel:
                old_message = await old_channel.fetch_message(old_message_id)
                await old_message.delete()
        except Exception as e:
            print(f"Failed to delete old message: {e}")

    # 新しいメッセージを作成
    embed = discord.Embed(
        title="Check Eligibility",
        description=(
            "Click the button below to check if you are eligible.\n"
            "If eligible, you will be granted the specified role."
        ),
        color=discord.Color.blue()
    )
    view = CheckEligibilityView()

    # 送信
    msg = await channel.send(embed=embed, view=view)

    # ギルド設定を保存
    guild_config[guild_id_str] = {
        "channel_id": channel.id,
        "channel_name": channel.name,
        "role_id": role.id,
        "role_name": role.name,
        "message_id": msg.id
    }
    save_guild_config()

    await interaction.response.send_message(
        f"Setup completed in {channel.mention} with role: {role.mention}.",
        ephemeral=True
    )

# ============================
# /relodelist command
# ============================
@bot.tree.command(name="relodelist", description="Reload the CSV list manually.")
@app_commands.default_permissions(administrator=True)
async def relodelist_command(interaction: discord.Interaction):
    """
    Reloads the CSV file and tells how many UIDs were loaded.
    """
    loaded_count = load_csv()
    await interaction.response.send_message(
        f"CSV reloaded successfully. **{loaded_count}** UIDs loaded.",
        ephemeral=True
    )

# ============================
# ボタンView
# ============================
class CheckEligibilityView(discord.ui.View):
    def __init__(self):
        # timeout=Noneで再起動まで押せる (ただし再起動すると無効になる)
        super().__init__(timeout=None)
        self.add_item(CheckEligibilityButton())

# ============================
# ボタン本体
# ============================
class CheckEligibilityButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Check Eligibility",
            style=discord.ButtonStyle.primary
        )

    async def callback(self, interaction: discord.Interaction):
        user_id_str = str(interaction.user.id)
        guild_id_str = str(interaction.guild_id)

        # Check if user is in CSV list
        if user_id_str in valid_uids:
            # Eligible -> Grant role
            if guild_id_str in guild_config:
                # 取得
                role_id = guild_config[guild_id_str]["role_id"]
                role = interaction.guild.get_role(role_id)
                if role:
                    try:
                        await interaction.user.add_roles(role)
                        await interaction.response.send_message(
                            f"You are **eligible** (UID: {user_id_str}). Role {role.mention} has been granted!",
                            ephemeral=True
                        )
                    except discord.Forbidden:
                        # Botがロール付与できる権限を持っていないケース
                        await interaction.response.send_message(
                            "Bot does not have permission to add that role.",
                            ephemeral=True
                        )
                else:
                    await interaction.response.send_message(
                        "The configured role no longer exists on this server.",
                        ephemeral=True
                    )
            else:
                await interaction.response.send_message(
                    "No configuration found for this server. Please use /setup first.",
                    ephemeral=True
                )
        else:
            # Not eligible
            await interaction.response.send_message(
                f"You are **not eligible** (UID: {user_id_str}).",
                ephemeral=True
            )


# ============================
# Main
# ============================
if __name__ == "__main__":
    bot.run(TOKEN)
