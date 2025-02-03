import os
import csv
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

CSV_FILE_NAME = "output.csv"

# ---------------------------------
# 有効UIDを保持するセット
# ---------------------------------
valid_uids = set()

# ---------------------------------
# ギルド(サーバー)ごとに「チャンネルID」「ロールID」を保持する簡易的な辞書
# 形式: guild_config[guild_id] = {"channel_id": 1234567890, "role_id": 1111111111}
# ---------------------------------
guild_config = {}

# Intentsの設定
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True  # 不要なら False
bot = commands.Bot(command_prefix="!", intents=intents)

# =========================
# CSVの読み込み関数
# =========================
def load_csv():
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
        print(f"CSVファイル {CSV_FILE_NAME} が見つかりませんでした。")
    
    valid_uids = new_valid_uids
    print(f"CSVを読み込みました。有効UID数: {len(valid_uids)}")

# =========================
# Bot起動時処理
# =========================
@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")
    # 起動時にCSV読み込み
    load_csv()

    try:
        await bot.tree.sync()
        print("Slash commands synced.")
    except Exception as e:
        print(e)

    # 定期タスク(毎日1回)を開始する例
    reload_csv_daily.start()

# =========================
# 24時間おきにCSVを再読み込みするタスク
# =========================
@tasks.loop(hours=24)
async def reload_csv_daily():
    load_csv()
    print("定期実行: CSV再読み込み完了")

# =========================
# /setup command
# =========================
@bot.tree.command(name="setup", description="ロール付与ボタン設置用コマンド")
@app_commands.default_permissions(administrator=True)  # 管理者権限がある人だけ使える
@app_commands.describe(
    channel="ボタンを設置するチャンネル",
    role="eligible時に付与するロール"
)
async def setup_command(interaction: discord.Interaction, channel: discord.TextChannel, role: discord.Role):
    """
    例: /setup channel:#ボット用チャンネル role:@付与したいロール
    """
    # サーバー(ギルド)単位で、選択されたチャンネルIDとロールIDを記憶
    guild_id = interaction.guild_id
    guild_config[guild_id] = {
        "channel_id": channel.id,
        "role_id": role.id
    }

    # Embedメッセージ
    embed = discord.Embed(
        title="Botの説明",
        description=(
            "このボタンを押すとCSVのUIDと照合し、該当すればロール付与します。\n"
            "該当しない場合は 'You are not eligible' を表示します。"
        ),
        color=discord.Color.blue()
    )
    # ボタンのViewを作成(ロール情報を渡せるように、ViewのコンストラクタにロールIDを渡す)
    view = CheckEligibilityView(role_id=role.id)

    # 選択されたチャンネルに送信
    await channel.send(embed=embed, view=view)

    await interaction.response.send_message(
        f"{channel.mention} にボタンを設置しました。付与ロール: {role.mention}",
        ephemeral=True
    )

# =========================
# /reloadcsv コマンド (管理者用)
# =========================
@bot.tree.command(name="reloadcsv", description="CSVを手動で再読み込みします")
@app_commands.default_permissions(administrator=True)
async def reload_csv_command(interaction: discord.Interaction):
    load_csv()
    await interaction.response.send_message(
        "CSVを再読み込みしました。",
        ephemeral=True
    )

# =========================
# ボタンが配置されたView
# =========================
class CheckEligibilityView(discord.ui.View):
    """ロールIDなどを保持したままボタンを作るView"""
    def __init__(self, role_id: int):
        super().__init__(timeout=None)  # timeout=Noneで、Bot再起動まで押せるようにする
        self.add_item(CheckEligibilityButton(role_id))

# =========================
# ボタンのクラス
# =========================
class CheckEligibilityButton(discord.ui.Button):
    def __init__(self, role_id: int):
        super().__init__(
            label="Check Eligibility",
            style=discord.ButtonStyle.primary
        )
        self.role_id = role_id  # 付与対象ロールのID

    async def callback(self, interaction: discord.Interaction):
        user_id_str = str(interaction.user.id)
        if user_id_str in valid_uids:
            # eligible の場合
            # ロール付与 (Botに権限がないと失敗するので注意)
            guild = interaction.guild
            role = guild.get_role(self.role_id)
            if role:
                try:
                    await interaction.user.add_roles(role)
                    await interaction.response.send_message(
                        f"You are eligible (UID: {user_id_str}). Role {role.mention} has been added!",
                        ephemeral=True
                    )
                except discord.Forbidden:
                    await interaction.response.send_message(
                        "権限不足でロールを付与できませんでした。",
                        ephemeral=True
                    )
            else:
                # guild.get_role() が None -> ロールが見つからない(削除された?)ケース
                await interaction.response.send_message(
                    "ロールが見つかりませんでした。",
                    ephemeral=True
                )
        else:
            # not eligible の場合
            await interaction.response.send_message(
                f"You are not eligible (UID: {user_id_str}).",
                ephemeral=True
            )

# =========================
# 実行
# =========================
bot.run(TOKEN)
