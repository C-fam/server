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
CSV_FILE_NAME = "output.csv"               # UID一覧
GUILD_CONFIG_CSV = "guild_config.csv"      # サーバー設定 + debugチャンネル保存
GRANTED_HISTORY_CSV = "granted_history.csv"# ロール付与履歴
PERMISSIONS_CONFIG_CSV = "permissions_config.csv"  # コマンド権限設定 (省略可)

# -----------------------------
# メモリ上の保持データ
# -----------------------------
valid_uids = set()  # output.csv から読み込んだUID
guild_config = {}   # {guild_id(str): {channel_id, role_id, message_id, debug_channel_id}}
granted_history = {}# {guild_id(str): [ {uid, username, time}, ... ]}
permissions_config = {}  # 今回は最小限のみ使用

# ==========================================
# CSV読み書き系
# ==========================================
def load_uid_list():
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

def load_all():
    load_uid_list()
    load_guild_config()
    load_granted_history()
    # permissions_config関連は割愛 or 実装可

def save_all():
    save_guild_config()
    save_granted_history()

# ==========================================
# Bot Setup
# ==========================================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")
    load_all()
    print(f"Loaded UIDs: {len(valid_uids)}")
    try:
        await bot.tree.sync()
        print("Slash commands synced.")
    except Exception as e:
        print(e)

# ------------------------------------------
# 例外ハンドラ: エラーが起きたら debug channel に報告
# ------------------------------------------
@bot.event
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    print(f"App command error: {error}")
    # "最も多いバグは更新がある場合です/setupしたら直ります" のメッセージ
    # debug_channel_idにエラー報告
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
                        f"Often, re-running `/setup` solves issues if there's a recent update."
                    )
    # 通常のユーザー向けエラー応答
    await interaction.response.send_message(
        "An error occurred. Please try again or ask an admin.\n(You may also try `/setup` if there's an update.)",
        ephemeral=True
    )

# ------------------------------------------
# /setup: debug_channel 追加
# ------------------------------------------
@bot.tree.command(name="setup", description="Set up the role, button, and debug channel.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    channel="Channel for check button",
    role="Role to be granted if eligible",
    debug_channel="Channel for debug logs"
)
async def setup_command(interaction: discord.Interaction,
                        channel: discord.TextChannel,
                        role: discord.Role,
                        debug_channel: discord.TextChannel):
    guild_id_str = str(interaction.guild_id)
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

    embed = discord.Embed(
        title="Check Eligibility",
        description="Click the button to see if you're on the list.",
        color=discord.Color.blue()
    )
    view = CheckEligibilityView()
    msg = await channel.send(embed=embed, view=view)

    guild_config[guild_id_str] = {
        "channel_id": channel.id,
        "role_id": role.id,
        "message_id": msg.id,
        "debug_channel_id": debug_channel.id
    }
    save_guild_config()

    await interaction.response.send_message(
        f"Setup done in {channel.mention} with role {role.mention}.\nDebug channel: {debug_channel.mention}",
        ephemeral=True
    )

# ------------------------------------------
# /relodelist
# ------------------------------------------
@bot.tree.command(name="relodelist", description="Reload the user ID list from CSV.")
@app_commands.default_permissions(administrator=True)
async def relodelist_command(interaction: discord.Interaction):
    count = load_uid_list()
    await interaction.response.send_message(
        f"Reloaded user list. Found {count} IDs.",
        ephemeral=True
    )

# ------------------------------------------
# CheckEligibility (ボタン)
# ------------------------------------------
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

        if user_id_str not in valid_uids:
            return await interaction.response.send_message(
                f"You are not eligible (UID: {user_id_str}).",
                ephemeral=True
            )

        conf = guild_config.get(guild_id_str)
        if not conf:
            return await interaction.response.send_message("No setup found. Please run /setup.", ephemeral=True)

        role_id = conf["role_id"]
        role = interaction.guild.get_role(role_id)
        if not role:
            return await interaction.response.send_message("Configured role not found.", ephemeral=True)

        if role in interaction.user.roles:
            return await interaction.response.send_message("You already have this role.", ephemeral=True)

        # 付与
        try:
            await interaction.user.add_roles(role)
        except discord.Forbidden:
            return await interaction.response.send_message("Insufficient permission to grant role.", ephemeral=True)

        # 履歴追加
        if guild_id_str not in granted_history:
            granted_history[guild_id_str] = []
        granted_history[guild_id_str].append({
            "uid": user_id_str,
            "username": str(interaction.user),
            "time": datetime.utcnow().isoformat()
        })
        save_granted_history()

        # メッセージに付与したロールを表示
        await interaction.response.send_message(
            f"You are **eligible** (UID: {user_id_str}). Role {role.mention} has been granted!",
            ephemeral=True
        )

# ------------------------------------------
# /history
# ------------------------------------------
@bot.tree.command(name="history", description="Show the role-grant history with pagination.")
@app_commands.default_permissions(administrator=True)
async def history_command(interaction: discord.Interaction):
    guild_id_str = str(interaction.guild_id)
    records = granted_history.get(guild_id_str, [])
    if not records:
        return await interaction.response.send_message("No history found for this server.", ephemeral=True)

    view = HistoryPagerView(records)
    await interaction.response.send_message(view.get_page_content(), view=view, ephemeral=True)

class HistoryPagerView(discord.ui.View):
    def __init__(self, records):
        super().__init__(timeout=None)
        self.records = records
        self.page = 0
        self.per_page = 10

        # 10件未満ならPrev/Nextボタンを配置しない
        if len(self.records) > self.per_page:
            self.add_item(PrevButton())
            self.add_item(NextButton())

    def max_page(self):
        return ceil(len(self.records) / self.per_page)

    def get_page_content(self):
        start = self.page*self.per_page
        end = start+self.per_page
        chunk = self.records[start:end]

        lines = []
        for i, r in enumerate(chunk, start=1):
            idx = start + i
            lines.append(f"[{idx}] UID:{r['uid']}, User:{r['username']}, Time:{r['time']}")
        info = f"Page {self.page+1}/{self.max_page()} (Total {len(self.records)})"
        if not lines:
            lines = ["No data on this page."]
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

# ------------------------------------------
# /extractinfo: 管理者のみ, guild_configやロール割当など抽出
# ------------------------------------------
@bot.tree.command(name="extractinfo", description="Extract server info and role assignment logs.")
@app_commands.default_permissions(administrator=True)
async def extractinfo_command(interaction: discord.Interaction):
    guild_id_str = str(interaction.guild_id)
    conf = guild_config.get(guild_id_str)
    if not conf:
        return await interaction.response.send_message("No setup info for this server.", ephemeral=True)

    ch_id = conf["channel_id"]
    role_id = conf["role_id"]
    msg_id = conf["message_id"]
    dbg_id = conf["debug_channel_id"]

    debug_txt = (
        f"**Server Info**\n"
        f"- Channel ID: {ch_id}\n"
        f"- Role ID: {role_id}\n"
        f"- Setup Message ID: {msg_id}\n"
        f"- Debug Channel ID: {dbg_id}\n\n"
        f"**Role Grants** (Total: {len(granted_history.get(guild_id_str, []))})\n"
    )
    # 簡易的に10件だけ表示
    logs = granted_history.get(guild_id_str, [])
    for i, rec in enumerate(logs[-10:], start=1):
        debug_txt += f"{i}. UID={rec['uid']}, Name={rec['username']}, Time={rec['time']}\n"

    await interaction.response.send_message(debug_txt, ephemeral=True)

# ------------------------------------------
# 実行
# ------------------------------------------
if __name__ == "__main__":
    bot.run(TOKEN)