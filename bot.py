import os
import csv
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

# CSVファイル名 (毎日更新される想定)
CSV_FILE_NAME = "output.csv"

# ---------------------------------
# グローバルで保持する有効UIDリスト
# ---------------------------------
valid_uids = set()  # 文字列としてIDを格納する

# Intentsの設定
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True  # 必要なければFalseにしてください

bot = commands.Bot(command_prefix="!", intents=intents)

# =========================
# CSVの読み込み関数
# =========================
def load_csv():
    """output.csv を読み込み、valid_uidsセットを更新する"""
    global valid_uids
    new_valid_uids = set()
    
    # CSVの場所がBotの実行ファイルと同じフォルダにある想定
    # Railway等で使う場合は適宜配置パスを合わせてください
    try:
        with open(CSV_FILE_NAME, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # CSV列名 "DCUID" を参照してUIDを取得
                uid = row.get("DCUID")
                if uid:
                    # 数値として扱うか、文字列にするか揃えましょう
                    # (interaction.user.idは int型ですが、比較のときはstrにして比較)
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
    # まず起動時にCSVを読み込む
    load_csv()

    # スラッシュコマンドを同期
    try:
        await bot.tree.sync()
        print("Slash commands synced.")
    except Exception as e:
        print(e)
    
    # 定期タスク開始 (毎日1回 CSV再読み込みしたい場合)
    reload_csv_daily.start()

# =========================
# 24時間おきにCSVを再読み込みするタスク
# =========================
@tasks.loop(hours=24)
async def reload_csv_daily():
    load_csv()
    print("定期実行: CSV再読み込み完了")

# =========================
# /setup command (admin only)
# =========================
@bot.tree.command(name="setup", description="ボタン設置用コマンド")
@app_commands.default_permissions(administrator=True)  # 管理者権限がある人だけ使える
@app_commands.describe(channel="ボタンを設置するチャンネル")
async def setup_command(interaction: discord.Interaction, channel: discord.TextChannel):
    """
    /setup channel: #指定チャンネル
    """
    embed = discord.Embed(
        title="Botの説明",
        description=(
            "このボタンを押すと、CSVで設定されたUIDとの照合を行います。\n"
            "合致すれば 'You are eligible'、なければ 'You are not eligible' を表示します。"
        ),
        color=discord.Color.blue()
    )
    view = discord.ui.View()
    view.add_item(CheckButton())
    
    await channel.send(embed=embed, view=view)
    
    # Slashコマンド実行者に通知(エフェメラルでも可)
    await interaction.response.send_message(
        f"{channel.mention} にボタンを設置しました。",
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
# ボタンのクラス
# =========================
class CheckButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Check Eligibility",
            style=discord.ButtonStyle.primary
        )

    async def callback(self, interaction: discord.Interaction):
        # ボタンを押したユーザーのUID (int) を strに変換
        user_id_str = str(interaction.user.id)
        
        if user_id_str in valid_uids:
            # eligible
            # ここでロール付与したい場合は下記の例を使用
            """
            guild = interaction.guild
            if guild:
                role = guild.get_role(ROLE_ID)  # ROLE_IDを置き換えてください
                if role:
                    await interaction.user.add_roles(role)
            """
            await interaction.response.send_message(
                f"You are eligible (UID: {user_id_str}).",
                ephemeral=True
            )
        else:
            # not eligible
            await interaction.response.send_message(
                f"You are not eligible (UID: {user_id_str}).",
                ephemeral=True
            )

# =========================
# 実行
# =========================
bot.run(TOKEN)
