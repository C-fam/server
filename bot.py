# bot.py (サンプル)

import os
import csv
import json
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

# ============================================
# ファイル名の設定 (プライベートリポジトリで管理可能)
# ============================================
CSV_FILE_NAME = "output.csv"              # ユーザーIDリスト (もともとのCSV)
CONFIG_FILE_NAME = "guild_config.json"    # サーバーID / チャンネル / ロール等の設定
GRANTED_HISTORY_FILE = "granted_history.json"  # 各サーバーの付与履歴
PERMISSIONS_CONFIG_FILE = "permissions_config.json"  # コマンドごとの使用権限設定

# ============================================
# グローバル変数
# ============================================
valid_uids = set()  # CSVから読み込んだUID
guild_config = {}   # サーバー設定 (CONFIG_FILE_NAME)
granted_history = {}  # 付与履歴 (GRANTED_HISTORY_FILE)
permissions_config = {}  # コマンド権限設定 (PERMISSIONS_CONFIG_FILE)

# ============================================
# JSONのロード/セーブ関数
# ============================================
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

# ============================================
# CSV読み込み
# ============================================
def load_csv():
    """
    CSV_FILE_NAME を読み込み、valid_uids を更新。
    戻り値: 読み込んだUIDの個数
    """
    global valid_uids
    new_set = set()
    try:
        with open(CSV_FILE_NAME, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                uid = row.get("DCUID")
                if uid:
                    new_set.add(uid)
    except FileNotFoundError:
        print(f"List file '{CSV_FILE_NAME}' not found.")
    valid_uids = new_set
    return len(valid_uids)

# ============================================
# Botのセットアップ
# ============================================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # ロール付与には必要
intents.presences = False

bot = commands.Bot(command_prefix="!", intents=intents)

# ============================================
# on_ready: 起動時処理
# ============================================
@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")

    # 各種ファイルをロード
    load_all_configs()

    count = load_csv()
    print(f"List reloaded: {count} UIDs loaded.")

    # Slashコマンド同期
    try:
        await bot.tree.sync()
        print("Slash commands synced.")
    except Exception as e:
        print(e)

# ============================================
# コマンド権限チェック用デコレータ
# ============================================
def check_command_permission(command_name: str):
    """
    ・管理者（Administrator権限）なら常にOK
    ・permissions_config に登録されたロール or ユーザーならOK
    ・それ以外はNG
    """
    def wrapper(func):
        async def inner(interaction: discord.Interaction, *args, **kwargs):
            guild = interaction.guild
            if not guild:
                # DMなどギルド外では許可しない
                return await interaction.response.send_message(
                    "This command is not available in DMs.",
                    ephemeral=True
                )

            # サーバー管理者(Administrator)チェック
            if interaction.user.guild_permissions.administrator:
                return await func(interaction, *args, **kwargs)

            # 追加権限を確認 (permissions_config[guild_id][command_name] に ユーザーorロールが含まれる?)
            guild_id_str = str(guild.id)
            allowed_info = permissions_config.get(guild_id_str, {}).get(command_name, {})
            # allowed_info の例:
            # {
            #   "roles": [1234567890, ...],
            #   "users": [1111111111, ...]
            # }

            # ユーザーIDチェック
            if interaction.user.id in allowed_info.get("users", []):
                return await func(interaction, *args, **kwargs)

            # ロールIDチェック
            user_role_ids = [r.id for r in interaction.user.roles]
            for role_id in user_role_ids:
                if role_id in allowed_info.get("roles", []):
                    return await func(interaction, *args, **kwargs)

            # どれにも該当しない→権限不足
            return await interaction.response.send_message(
                f"You do not have permission to use the command: {command_name}",
                ephemeral=True
            )
        return inner
    return wrapper

# ============================================
# /setup
# ============================================
@bot.tree.command(name="setup", description="Set up the role and button for eligibility checking.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    channel="Select the channel to send the button message",
    role="Select the role to be granted if eligible"
)
async def setup_command(interaction: discord.Interaction, channel: discord.TextChannel, role: discord.Role):
    guild_id_str = str(interaction.guild_id)

    # 既存メッセージ削除
    old_msg_id = guild_config.get(guild_id_str, {}).get("message_id")
    if old_msg_id:
        try:
            old_ch_id = guild_config[guild_id_str]["channel_id"]
            old_ch = interaction.guild.get_channel(old_ch_id)
            if old_ch:
                old_msg = await old_ch.fetch_message(old_msg_id)
                await old_msg.delete()
        except Exception as e:
            print(f"Failed to delete old message: {e}")

    # 新メッセージ送信
    embed = discord.Embed(
        title="Check Eligibility",
        description="Click the button below to see if you are on the list.",
        color=discord.Color.blue()
    )
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
        f"Setup completed in {channel.mention} with role: {role.mention}.",
        ephemeral=True
    )

# ============================================
# /relodelist (リスト再読み込み)
# ============================================
@bot.tree.command(name="relodelist", description="Reload the user list file manually.")
@app_commands.default_permissions(administrator=True)
async def relodelist_command(interaction: discord.Interaction):
    count = load_csv()
    await interaction.response.send_message(
        f"List reloaded successfully. **{count}** UIDs loaded.",
        ephemeral=True
    )

# ============================================
# ボタンView/ボタン
# ============================================
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
            info = guild_config.get(guild_id_str, {})
            role_id = info.get("role_id")
            role = interaction.guild.get_role(role_id) if role_id else None
            if role:
                try:
                    await interaction.user.add_roles(role)
                except discord.Forbidden:
                    return await interaction.response.send_message(
                        "Bot does not have permission to grant this role.",
                        ephemeral=True
                    )
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
                    f"You are **eligible** (UID: {user_id_str}). Role granted!",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "No valid role configured. Please run /setup again.",
                    ephemeral=True
                )
        else:
            await interaction.response.send_message(
                f"You are **not eligible** (UID: {user_id_str}).",
                ephemeral=True
            )

# ============================================
# /history : 履歴をページングで表示
# ============================================
@bot.tree.command(name="history", description="Show role-grant history in pages of 10.")
@check_command_permission("history")  # <- 権限チェック
async def history_command(interaction: discord.Interaction):
    guild_id_str = str(interaction.guild_id)
    records = granted_history.get(guild_id_str, [])

    if not records:
        return await interaction.response.send_message(
            "No history found in this server.",
            ephemeral=True
        )

    # ページング用Viewを作成
    view = HistoryView(records)
    # 初期メッセージ送信 (ページ0で表示)
    await view.update_message(interaction)


class HistoryView(discord.ui.View):
    def __init__(self, records):
        super().__init__(timeout=None)
        self.records = records
        self.page = 0
        self.per_page = 10

        # ボタン追加
        self.add_item(PrevButton())
        self.add_item(NextButton())

    def get_page_entries(self):
        start = self.page * self.per_page
        end = start + self.per_page
        return self.records[start:end]

    def max_page(self):
        # 何ページあるか
        from math import ceil
        return max(1, ceil(len(self.records) / self.per_page))

    async def update_message(self, interaction: discord.Interaction):
        # 現在のページの10件を取得
        entries = self.get_page_entries()
        lines = []
        for i, r in enumerate(entries, start=1):
            lines.append(
                f"[{i + self.page*10}] UID: {r['uid']}, User: {r['name']}, Time: {r['time']}"
            )
        text = "\n".join(lines)
        page_info = f"Page {self.page+1}/{self.max_page()} (Total {len(self.records)} records)"

        # 英語のメッセージ: “You can also open the JSON file in the repository.”
        # など簡潔に追加
        note = (
            f"{page_info}\n\nYou can view the full JSON file in the repository or use these buttons to navigate."
        )

        if text == "":
            text = "(No data on this page)"
        content = f"**History**\n```\n{text}\n```\n{note}"

        # interaction.response の更新か、followupのeditか判断
        if interaction.response.is_done():
            await interaction.edit_original_response(content=content, view=self)
        else:
            await interaction.response.send_message(content=content, view=self, ephemeral=True)


class PrevButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="◀ Prev", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        view = self.view  # type: HistoryView
        if view.page > 0:
            view.page -= 1
        await view.update_message(interaction)


class NextButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Next ▶", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        view = self.view  # type: HistoryView
        if view.page < view.max_page() - 1:
            view.page += 1
        await view.update_message(interaction)

# ============================================
# /grantpermission : ロール or ユーザーにコマンド権限を付与
# ============================================
@bot.tree.command(name="grantpermission", description="Grant permission to a command for a user or role.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    command_name="Which command to grant permission for",
    target_type="Choose 'user' or 'role'",
    target_id="User ID or Role ID (right-click > Copy ID)"
)
async def grantpermission_command(
    interaction: discord.Interaction,
    command_name: str,
    target_type: str,
    target_id: str
):
    """
    例:
      /grantpermission command_name:history target_type:role target_id:123456789012345678
    """
    guild_id_str = str(interaction.guild_id)

    if guild_id_str not in permissions_config:
        permissions_config[guild_id_str] = {}

    if command_name not in permissions_config[guild_id_str]:
        # 初期化
        permissions_config[guild_id_str][command_name] = {
            "roles": [],
            "users": []
        }

    data = permissions_config[guild_id_str][command_name]

    # 文字列→数値変換
    try:
        numeric_id = int(target_id)
    except ValueError:
        return await interaction.response.send_message(
            "Invalid ID. Please enter a numeric ID.",
            ephemeral=True
        )

    if target_type.lower() == "role":
        if numeric_id not in data["roles"]:
            data["roles"].append(numeric_id)
        msg = f"Granted permission for command '{command_name}' to role ID {numeric_id}."
    elif target_type.lower() == "user":
        if numeric_id not in data["users"]:
            data["users"].append(numeric_id)
        msg = f"Granted permission for command '{command_name}' to user ID {numeric_id}."
    else:
        return await interaction.response.send_message(
            "target_type must be 'user' or 'role'.",
            ephemeral=True
        )

    save_permissions_config()
    await interaction.response.send_message(msg, ephemeral=True)

# ============================================
# メイン起動
# ============================================
if __name__ == "__main__":
    bot.run(TOKEN)
