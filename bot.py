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
# CSVファイル名の設定
# -----------------------------
CSV_FILE_NAME = "output.csv"               # ユーザーUIDリスト(既存)
GUILD_CONFIG_CSV = "guild_config.csv"      # サーバー設定保存先
GRANTED_HISTORY_CSV = "granted_history.csv"# ロール付与履歴
PERMISSIONS_CONFIG_CSV = "permissions_config.csv"  # 権限設定

# -----------------------------
# メモリ上の保持データ
# -----------------------------
valid_uids = set()   # CSVから読み込んだUID
guild_config = {}    # {guild_id(str): {channel_id, role_id, message_id}}
granted_history = {} # {guild_id(str): [ {uid, username, time}, ... ]}
permissions_config = {} # {guild_id(str): { command_name: [role_id, ...], ... }}

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = False
bot = commands.Bot(command_prefix="!", intents=intents)

# =================================================
# 1) CSV読み込み/書き込み ヘルパー
# =================================================

def load_uid_list():
    """output.csv (DCUIDカラム)を読み込む"""
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
        print(f"{CSV_FILE_NAME} not found.")
    valid_uids = new_set
    return len(new_set)

def load_guild_config():
    """guild_config.csv を読み込んで guild_config dictに反映"""
    global guild_config
    guild_config = {}
    if not os.path.exists(GUILD_CONFIG_CSV):
        return
    with open(GUILD_CONFIG_CSV, "r", encoding="utf-8", newline="") as f:
        rdr = csv.DictReader(f)
        for row in rdr:
            g_id = row["guild_id"]
            guild_config[g_id] = {
                "channel_id": int(row["channel_id"]),
                "role_id": int(row["role_id"]),
                "message_id": int(row["message_id"])
            }

def save_guild_config():
    """guild_config dictを guild_config.csv に書き込み"""
    fieldnames = ["guild_id", "channel_id", "role_id", "message_id"]
    with open(GUILD_CONFIG_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for g_id, val in guild_config.items():
            w.writerow({
                "guild_id": g_id,
                "channel_id": val["channel_id"],
                "role_id": val["role_id"],
                "message_id": val["message_id"]
            })

def load_granted_history():
    """granted_history.csv を読み込み、granted_history dictへ"""
    global granted_history
    granted_history = {}
    if not os.path.exists(GRANTED_HISTORY_CSV):
        return
    with open(GRANTED_HISTORY_CSV, "r", encoding="utf-8", newline="") as f:
        rdr = csv.DictReader(f)
        for row in rdr:
            g_id = row["guild_id"]
            if g_id not in granted_history:
                granted_history[g_id] = []
            granted_history[g_id].append({
                "uid": row["uid"],
                "username": row["username"],
                "time": row["time"]
            })

def save_granted_history():
    """granted_history dictを granted_history.csv に書き込み"""
    fieldnames = ["guild_id", "uid", "username", "time"]
    with open(GRANTED_HISTORY_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for g_id, records in granted_history.items():
            for r in records:
                w.writerow({
                    "guild_id": g_id,
                    "uid": r["uid"],
                    "username": r["username"],
                    "time": r["time"]
                })

def load_permissions_config():
    """
    permissions_config.csv から読み込み
    形式: guild_id, command_name, role_id
    """
    global permissions_config
    permissions_config = {}
    if not os.path.exists(PERMISSIONS_CONFIG_CSV):
        return
    with open(PERMISSIONS_CONFIG_CSV, "r", encoding="utf-8", newline="") as f:
        rdr = csv.DictReader(f)
        for row in rdr:
            g_id = row["guild_id"]
            cmd = row["command_name"]
            role_id = int(row["role_id"])
            if g_id not in permissions_config:
                permissions_config[g_id] = {}
            if cmd not in permissions_config[g_id]:
                permissions_config[g_id][cmd] = []
            permissions_config[g_id][cmd].append(role_id)

def save_permissions_config():
    """
    permissions_config dictを permissions_config.csv に保存
    """
    fieldnames = ["guild_id", "command_name", "role_id"]
    with open(PERMISSIONS_CONFIG_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for g_id, cmd_map in permissions_config.items():
            for cmd, role_list in cmd_map.items():
                for r_id in role_list:
                    w.writerow({
                        "guild_id": g_id,
                        "command_name": cmd,
                        "role_id": r_id
                    })

def load_all_data():
    load_uid_list()
    load_guild_config()
    load_granted_history()
    load_permissions_config()

# ==================================================
# Bot起動時処理
# ==================================================
@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")
    load_all_data()
    print(f"UID list loaded: {len(valid_uids)} entries.")
    try:
        await bot.tree.sync()
        print("Slash commands synced.")
    except Exception as e:
        print(e)

# ==================================================
# /setup
# ==================================================
@bot.tree.command(name="setup", description="Set up the role and button for eligibility.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    channel="Channel for check button",
    role="Role to be granted if eligible"
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

    # 新メッセージ
    embed = discord.Embed(
        title="Check Eligibility",
        description="Click the button below to see if you're on the list.",
        color=discord.Color.blue()
    )
    view = CheckEligibilityView()
    msg = await channel.send(embed=embed, view=view)

    # guild_config 更新
    guild_config[guild_id_str] = {
        "channel_id": channel.id,
        "role_id": role.id,
        "message_id": msg.id
    }
    save_guild_config()

    await interaction.response.send_message(
        f"Setup completed in {channel.mention} with role {role.mention}.",
        ephemeral=True
    )

# ==================================================
# /relodelist (CSV再読み込み)
# ==================================================
@bot.tree.command(name="relodelist", description="Reload the list from CSV.")
@app_commands.default_permissions(administrator=True)
async def relodelist_command(interaction: discord.Interaction):
    count = load_uid_list()
    await interaction.response.send_message(
        f"Reloaded. {count} UIDs found.",
        ephemeral=True
    )

# ==================================================
# ボタンView
# ==================================================
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
            # not eligible
            return await interaction.response.send_message(
                f"You are not eligible (UID: {user_id_str}).",
                ephemeral=True
            )

        # 付与ロール取得
        config = guild_config.get(guild_id_str)
        if not config:
            return await interaction.response.send_message(
                "No setup found. Use /setup first.",
                ephemeral=True
            )
        role_id = config["role_id"]
        role = interaction.guild.get_role(role_id)
        if not role:
            return await interaction.response.send_message("Configured role not found.", ephemeral=True)

        # 既に持っているか確認
        if role in interaction.user.roles:
            # 既に持っている
            return await interaction.response.send_message(
                "You already have this role.",
                ephemeral=True
            )

        # 付与
        try:
            await interaction.user.add_roles(role)
        except discord.Forbidden:
            return await interaction.response.send_message("Cannot grant role. Check bot permissions.", ephemeral=True)

        # 履歴に追加
        if guild_id_str not in granted_history:
            granted_history[guild_id_str] = []
        granted_history[guild_id_str].append({
            "uid": user_id_str,
            "username": str(interaction.user),
            "time": datetime.utcnow().isoformat()
        })
        save_granted_history()

        await interaction.response.send_message(
            f"You are eligible (UID: {user_id_str}). Role granted!",
            ephemeral=True
        )

# ==================================================
# /history: ページングで履歴を見る
# ==================================================
@bot.tree.command(name="history", description="Show role-grant history (in pages of 10).")
async def history_command(interaction: discord.Interaction):
    # 権限チェック(管理者 or permissions_config)
    if interaction.user.guild_permissions.administrator:
        pass
    else:
        guild_id_str = str(interaction.guild_id)
        cmd_perms = permissions_config.get(guild_id_str, {}).get("history", [])
        # cmd_perms はロールIDのリスト
        user_roles = [r.id for r in interaction.user.roles]
        if not set(user_roles) & set(cmd_perms):
            return await interaction.response.send_message(
                "You do not have permission to use /history.",
                ephemeral=True
            )

    # 履歴表示
    guild_id_str = str(interaction.guild_id)
    records = granted_history.get(guild_id_str, [])
    if not records:
        return await interaction.response.send_message("No history for this server.", ephemeral=True)

    view = HistoryPagerView(records)
    # 最初のページ表示
    await interaction.response.send_message(view.get_page_content(), view=view, ephemeral=True)

class HistoryPagerView(discord.ui.View):
    def __init__(self, records):
        super().__init__(timeout=None)
        self.records = records
        self.page = 0
        self.per_page = 10

        # 10件未満なら次/前ボタン不要
        if len(self.records) > self.per_page:
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
            idx = (self.page * self.per_page) + i
            lines.append(f"[{idx}] UID: {r['uid']}, User: {r['username']}, Time: {r['time']}")
        page_info = f"Page {self.page+1}/{self.max_page()}  (Total {len(self.records)})"
        if not lines:
            lines = ["No data here."]
        text = "\n".join(lines)
        content = f"**History**\n```\n{text}\n```\n{page_info}"
        return content

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
        if view.page < view.max_page() - 1:
            view.page += 1
        await interaction.response.defer_update()
        await interaction.edit_original_response(content=view.get_page_content(), view=view)

# ==================================================
# /grantpermission:
#   コマンド名はChoicesから選び、ロールを選択
# ==================================================
class CommandNameChoice(app_commands.Transformer):
    """Botが持つスラッシュコマンドの中から選択肢を提供。"""
    # 手動で列挙してもいいが、ここではBotのTreeから抽出
    async def transform(self, interaction: discord.Interaction, value: str) -> str:
        return value

    @classmethod
    async def autocomplete(cls, interaction: discord.Interaction, current: str):
        # Botが登録しているトップレベルコマンド名を取得
        cmds = [c.name for c in bot.tree.get_commands()]
        # 部分一致で絞る
        return [
            app_commands.Choice(name=cmd, value=cmd)
            for cmd in cmds
            if current.lower() in cmd.lower()
        ][:25]

@bot.tree.command(name="grantpermission", description="Allow a role to use a specific command.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    command_name="Select the Bot command",
    role="Which role to grant permission to"
)
@app_commands.autocomplete(command_name=CommandNameChoice.autocomplete)
async def grantpermission_command(interaction: discord.Interaction, command_name: str, role: discord.Role):
    guild_id_str = str(interaction.guild_id)
    if guild_id_str not in permissions_config:
        permissions_config[guild_id_str] = {}

    if command_name not in permissions_config[guild_id_str]:
        permissions_config[guild_id_str][command_name] = []

    # まだ登録されていなければ追加
    if role.id not in permissions_config[guild_id_str][command_name]:
        permissions_config[guild_id_str][command_name].append(role.id)

    save_permissions_config()
    await interaction.response.send_message(
        f"Granted permission for '{command_name}' to role {role.mention}.",
        ephemeral=True
    )

# ==================================================
# MAIN
# ==================================================
if __name__ == "__main__":
    bot.run(TOKEN)
