import os
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

# Intentsの設定 (メンバーやメッセージなど扱う場合)
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# Bot本体の準備
bot = commands.Bot(
    command_prefix="!",  # ここはテキストコマンド用のプリフィックス(使わなくても良い)
    intents=intents
)

# 起動したときに呼ばれる処理
@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")
    try:
        # Tree(sync)でGuildにSlashコマンドを同期
        # (テスト用にGuild IDを指定したい場合はguild=discord.Object(id=ギルドID)を入れる)
        await bot.tree.sync()
        print("Slash commands synced.")
    except Exception as e:
        print(e)

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
    # ボタン付きメッセージを送る
    embed = discord.Embed(
        title="Botの説明",
        description="このボタンを押すと動作確認ができます。",
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
# ボタンのクラス
# =========================
class CheckButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Check Eligibility",  # ボタンのラベル
            style=discord.ButtonStyle.primary
        )

    async def callback(self, interaction: discord.Interaction):
        # とりあえず押したユーザーIDを表示(エフェメラル)
        await interaction.response.send_message(
            f"あなたのUIDは {interaction.user.id} です。",
            ephemeral=True
        )


# 実行
bot.run(TOKEN)
