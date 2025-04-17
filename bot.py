# -*- coding: utf-8 -*-
import os
import csv
import json
import logging
import asyncio
from datetime import datetime
from math import ceil

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

# Google Sheets 用ライブラリ（同期処理なので asyncio.to_thread() で非同期化）
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from gspread.exceptions import WorksheetNotFound, APIError

# --- ログ設定 ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# --- 環境変数の読み込み ---
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
if TOKEN is None:
    logger.error("BOT_TOKEN not found in environment variables.")
    exit(1)

# Google Sheets 認証情報
GOOGLE_CREDENTIALS_STR = os.getenv("GOOGLE_CREDENTIALS")
if GOOGLE_CREDENTIALS_STR is None:
    logger.error("GOOGLE_CREDENTIALS not found in environment variables.")
    exit(1)
try:
    CREDS_DICT = json.loads(GOOGLE_CREDENTIALS_STR)
except json.JSONDecodeError as e:
    logger.error("Failed to parse GOOGLE_CREDENTIALS: %s", e)
    exit(1)

# Google Sheets スコープとクライアント
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
SPREADSHEET_NAME = "keone_list_log" # 必要に応じて変更してください
LIST_SHEET_NAME = "list"
GUILD_CONFIG_SHEET_NAME = "guild_config"
GRANTED_HISTORY_SHEET_NAME = "granted_history"
LOG_SHEET_NAME = "Log"

try:
    CREDS = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_DICT, SCOPE)
    GSPREAD_CLIENT = gspread.authorize(CREDS)
    SPREADSHEET = GSPREAD_CLIENT.open(SPREADSHEET_NAME)
    logger.info(f"Successfully connected to Google Spreadsheet: {SPREADSHEET_NAME}")
except Exception as e:
    logger.error("Failed to authorize or open Google Sheets: %s", e)
    exit(1)

# --- 補助関数 ---
def format_time(iso_str: str) -> str:
    """ISO8601文字列を 'YYYY-MM-DD HH:MM:SS' 形式に変換"""
    if not iso_str:
        return ""
    try:
        # オフセット情報（例: +00:00）があれば除去
        if '+' in iso_str:
            iso_str = iso_str.split('+')[0]
        # ミリ秒情報（例: .123456）があれば除去
        if '.' in iso_str:
             iso_str = iso_str.split('.')[0]
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        logger.warning(f"Could not parse date: {iso_str}. Returning original string.")
        return iso_str # パースできない場合は元の文字列を返す

# 定数：Embed の色（#836EF9）
EMBED_COLOR = discord.Color(0x836EF9)


# --- DataManager クラス ---
class DataManager:
    def __init__(self):
        self.valid_uids = set()   # listシートから読み込む UID 一覧
        self.guild_config = {}    # {guild_id: {"server_name", "channel_id", "role_id", "message_id"}}
        self.granted_history = {} # {guild_id: [ {"uid", "username", "time"}, ... ]}

    async def _get_or_create_worksheet(self, sheet_name: str, rows: str = "1000", cols: str = "10") -> gspread.Worksheet | None:
        """指定された名前のワークシートを取得、なければ作成する"""
        def _sync_get_or_create():
            try:
                return SPREADSHEET.worksheet(sheet_name)
            except WorksheetNotFound:
                logger.info(f"Worksheet '{sheet_name}' not found, creating new one.")
                try:
                    # rows と cols は gspread v6 以降では add_worksheet に直接渡せない可能性がある
                    # 必要であればシート作成後にリサイズする
                    new_ws = SPREADSHEET.add_worksheet(title=sheet_name, rows=int(rows), cols=int(cols))
                    # ヘッダーが必要なシートもあるので、呼び出し元で適切に処理する
                    return new_ws
                except APIError as e:
                    logger.error(f"Failed to create worksheet '{sheet_name}': {e}")
                    return None
            except APIError as e:
                logger.error(f"API error while getting worksheet '{sheet_name}': {e}")
                return None
            except Exception as e:
                logger.error(f"Unexpected error getting or creating worksheet '{sheet_name}': {e}")
                return None
        return await asyncio.to_thread(_sync_get_or_create)

    async def load_uid_list(self) -> int:
        """
        "list" シートの A列(Discord), B列(DCUID) から UID を読み込み、self.valid_uids を更新する。
        """
        new_uids = set()
        ws = await self._get_or_create_worksheet(LIST_SHEET_NAME, rows="1000", cols="2")
        if not ws:
            logger.error(f"Could not get or create '{LIST_SHEET_NAME}' sheet.")
            return 0

        def _load_from_sheet():
            loaded_count = 0
            try:
                records = ws.get_all_records(head=1) # ヘッダーを1行目と仮定
                for row in records:
                    # get()で取得し、存在しないキーや空の場合に備える
                    discord_name = str(row.get("Discord", "")).strip() # A列
                    uid = str(row.get("DCUID", "")).strip()            # B列
                    if uid: # DCUID が空でなければ追加
                        new_uids.add(uid)
                        loaded_count += 1
                logger.info(f"Loaded {loaded_count} UIDs from '{LIST_SHEET_NAME}' sheet.")
            except APIError as e:
                logger.error(f"API error loading '{LIST_SHEET_NAME}' sheet: {e}")
            except Exception as e:
                logger.warning(f"Error reading '{LIST_SHEET_NAME}' sheet: {e}. Make sure columns 'Discord' and 'DCUID' exist in the first row.")
            return new_uids

        new_uids = await asyncio.to_thread(_load_from_sheet)
        self.valid_uids = new_uids
        return len(self.valid_uids)

    async def load_guild_config_sheet(self):
        """guild_config シートから設定を読み込む"""
        config = {}
        ws = await self._get_or_create_worksheet(GUILD_CONFIG_SHEET_NAME, rows="100", cols="5")
        if not ws:
            logger.error(f"Could not get or create '{GUILD_CONFIG_SHEET_NAME}' sheet.")
            return

        def _load():
            loaded_config = {}
            try:
                records = ws.get_all_records(head=1)
                for row in records:
                    guild_id = str(row.get("guild_id", "")).strip()
                    if guild_id:
                        try:
                            loaded_config[guild_id] = {
                                "server_name": str(row.get("server_name", "")),
                                # ID系は数値として取得しようとするが、文字列で保持した方が安全
                                "channel_id": str(row.get("channel_id", "")).strip(),
                                "role_id": str(row.get("role_id", "")).strip(),
                                "message_id": str(row.get("message_id", "")).strip()
                            }
                        except Exception as e:
                             logger.warning(f"Skipping invalid row in guild_config for guild_id {guild_id}: {e} - Row data: {row}")

            except APIError as e:
                 logger.error(f"API error loading '{GUILD_CONFIG_SHEET_NAME}': {e}")
            except Exception as e:
                logger.error(f"Error reading '{GUILD_CONFIG_SHEET_NAME}' sheet: {e}")
            return loaded_config

        self.guild_config = await asyncio.to_thread(_load)
        logger.info(f"Loaded guild configurations for {len(self.guild_config)} guilds.")

    async def save_guild_config_sheet(self):
        """guild_config シートに設定を保存する"""
        ws = await self._get_or_create_worksheet(GUILD_CONFIG_SHEET_NAME, rows="100", cols="5")
        if not ws:
            logger.error(f"Could not get or create '{GUILD_CONFIG_SHEET_NAME}' sheet for saving.")
            return

        headers = ["guild_id", "server_name", "channel_id", "role_id", "message_id"]
        data_to_write = [headers]
        for gid, conf in self.guild_config.items():
            row = [
                str(gid), # 文字列として保存
                str(conf.get("server_name", "")),
                str(conf.get("channel_id", "")), # 文字列として保存
                str(conf.get("role_id", "")),    # 文字列として保存
                str(conf.get("message_id", ""))  # 文字列として保存
            ]
            data_to_write.append(row)

        def _update():
            try:
                ws.clear()
                # 'A1' からデータを書き込む
                ws.update('A1', data_to_write, value_input_option='USER_ENTERED')
                logger.info(f"Guild config sheet '{GUILD_CONFIG_SHEET_NAME}' saved successfully.")
            except APIError as e:
                logger.error(f"API error saving '{GUILD_CONFIG_SHEET_NAME}' sheet: {e}")
            except Exception as e:
                 logger.error(f"Unexpected error saving '{GUILD_CONFIG_SHEET_NAME}' sheet: {e}")

        await asyncio.to_thread(_update)


    async def load_granted_history_sheet(self):
        """granted_history シートから付与履歴を読み込む"""
        history = {}
        ws = await self._get_or_create_worksheet(GRANTED_HISTORY_SHEET_NAME, rows="1000", cols="4")
        if not ws:
            logger.error(f"Could not get or create '{GRANTED_HISTORY_SHEET_NAME}' sheet.")
            return

        def _load():
            loaded_history = {}
            try:
                records = ws.get_all_records(head=1)
                for row in records:
                    guild_id = str(row.get("guild_id", "")).strip()
                    if guild_id:
                         # UIDはシングルクォート付きで保存されている可能性があるのでそのまま読み込む
                         uid = str(row.get("uid", "")).strip()
                         username = str(row.get("username", "")).strip()
                         time_str = str(row.get("time", "")).strip() # YYYY-MM-DD HH:MM:SS 形式想定

                         loaded_history.setdefault(guild_id, []).append({
                            "uid": uid,
                            "username": username,
                            "time": time_str # 読み込み時はパースせず文字列のまま
                        })
            except APIError as e:
                logger.error(f"API error loading '{GRANTED_HISTORY_SHEET_NAME}': {e}")
            except Exception as e:
                logger.error(f"Error reading '{GRANTED_HISTORY_SHEET_NAME}' sheet: {e}")
            return loaded_history

        self.granted_history = await asyncio.to_thread(_load)
        logger.info(f"Loaded granted history for {len(self.granted_history)} guilds.")


    async def save_granted_history_sheet(self):
        """granted_history シートに付与履歴を保存する"""
        ws = await self._get_or_create_worksheet(GRANTED_HISTORY_SHEET_NAME, rows="1000", cols="4")
        if not ws:
            logger.error(f"Could not get or create '{GRANTED_HISTORY_SHEET_NAME}' sheet for saving.")
            return

        headers = ["guild_id", "uid", "username", "time"]
        data_to_write = [headers]
        for gid, records in self.granted_history.items():
            for record in records:
                raw_uid = str(record.get("uid", ""))
                # UID の先頭にシングルクォートがなければ追加 (Google Sheetsが数値と誤認するのを防ぐため)
                # ただし、 value_input_option='USER_ENTERED' を使えば不要かもしれないが、互換性のため残す
                uid_str = f"'{raw_uid}" if not raw_uid.startswith("'") and raw_uid.isdigit() else raw_uid

                # time は ISO format かもしれないし、フォーマット済みかもしれない
                time_val = record.get("time", "")
                time_str = format_time(time_val) # 保存前に 'YYYY-MM-DD HH:MM:SS' 形式に統一

                row = [
                    str(gid), # 文字列として保存
                    uid_str, # シングルクォート付きまたはそのままの文字列
                    str(record.get("username", "")),
                    time_str
                ]
                data_to_write.append(row)

        def _update():
            try:
                ws.clear()
                ws.update('A1', data_to_write, value_input_option='USER_ENTERED') # USER_ENTEREDで書式を維持しようとする
                logger.info(f"Granted history sheet '{GRANTED_HISTORY_SHEET_NAME}' saved successfully.")
            except APIError as e:
                logger.error(f"API error saving '{GRANTED_HISTORY_SHEET_NAME}' sheet: {e}")
            except Exception as e:
                logger.error(f"Unexpected error saving '{GRANTED_HISTORY_SHEET_NAME}' sheet: {e}")

        await asyncio.to_thread(_update)


    async def append_log_to_sheet(self, guild_id: str, uid: str, username: str, timestamp: str):
        """Log シートに行を追加する"""
        ws = await self._get_or_create_worksheet(LOG_SHEET_NAME, rows="10000", cols="4") # ログは多くなる可能性
        if not ws:
            logger.error(f"Could not get or create '{LOG_SHEET_NAME}' sheet for logging.")
            return

        # UID の先頭にシングルクォートがなければ追加
        uid_str = f"'{uid}" if not uid.startswith("'") and uid.isdigit() else uid
        time_str = format_time(timestamp) # 'YYYY-MM-DD HH:MM:SS' 形式に

        # ヘッダーが存在するか確認し、なければ追加
        def _ensure_header_and_append():
            try:
                header = ws.row_values(1)
                if not header or header != ["guild_id", "uid", "username", "time"]:
                    # ヘッダーがないか、内容が異なる場合は設定
                    ws.insert_row(["guild_id", "uid", "username", "time"], 1)
                    logger.info(f"Header written to '{LOG_SHEET_NAME}' sheet.")
                    # ヘッダー挿入後はデータ行を追記
                    ws.append_row([str(guild_id), uid_str, username, time_str], value_input_option='USER_ENTERED')
                else:
                     # ヘッダーが既に存在する場合は追記のみ
                    ws.append_row([str(guild_id), uid_str, username, time_str], value_input_option='USER_ENTERED')
                # logger.info(f"Appended log to '{LOG_SHEET_NAME}'.") # ログ追記は頻繁なのでINFOレベルでは抑制しても良い
            except APIError as e:
                logger.error(f"API error appending log to '{LOG_SHEET_NAME}': {e}")
            except Exception as e:
                logger.error(f"Failed to append log to sheet '{LOG_SHEET_NAME}': {e}")

        await asyncio.to_thread(_ensure_header_and_append)

    async def load_all_data(self):
        """起動時に全てのデータをシートから読み込む"""
        logger.info("Loading all data from Google Sheets...")
        await self.load_uid_list()
        await self.load_guild_config_sheet()
        await self.load_granted_history_sheet()
        logger.info("Finished loading data.")

    # save_all_data は現状不要そうなのでコメントアウト（必要なら復活させる）
    # async def save_all_data(self):
    #     """全てのデータをシートに保存する（通常は個別保存で十分）"""
    #     logger.info("Saving all data to Google Sheets...")
    #     await self.save_guild_config_sheet()
    #     await self.save_granted_history_sheet()
    #     logger.info("Finished saving data.")


data_manager = DataManager()

# --- Discord Bot の初期化 ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True # メンバー情報の取得に必要
bot = commands.Bot(command_prefix="!", intents=intents)


# --- 永続的な UI コンポーネント: チェックボタンとビュー ---
# 注意: このビューは `timeout=None` かつボタンに `custom_id` があるため永続的です。
# ボットが再起動しても、Discord側がインタラクションをこのボットに転送します。
# `on_ready` で `add_view` するのは、ボット起動時にこのビューをリッスンさせるためです。
class CheckEligibilityView(discord.ui.View):
    def __init__(self):
        # timeout=None で永続ビューにする
        super().__init__(timeout=None)
        # 永続ビュー内のコンポーネントには custom_id が必須
        self.add_item(CheckEligibilityButton(custom_id="check_eligibility_button_v1")) # ID変更は非推奨だが例示

class CheckEligibilityButton(discord.ui.Button):
    # custom_id をコンストラクタで受け取るように変更 (View側で指定)
    def __init__(self, custom_id: str):
        super().__init__(
            custom_id=custom_id,
            label="Check Eligibility",
            style=discord.ButtonStyle.primary
        )

    async def callback(self, interaction: discord.Interaction):
        # インタラクション発生時のギルドとユーザーIDを取得
        if not interaction.guild:
             return await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
        guild_id_str = str(interaction.guild.id)
        user_id_str = str(interaction.user.id)

        # --- UID チェック ---
        if user_id_str not in data_manager.valid_uids:
            logger.info(f"Eligibility check failed for UID: {user_id_str} in guild {guild_id_str}. Not in valid_uids.")
            return await interaction.response.send_message(
                f"Sorry, you are not on the eligibility list (Your UID: {user_id_str}).", ephemeral=True
            )

        # --- ギルド設定チェック ---
        guild_config = data_manager.guild_config.get(guild_id_str)
        if not guild_config:
            logger.warning(f"No setup found for guild {guild_id_str} (Name: {interaction.guild.name}).")
            return await interaction.response.send_message(
                "Bot setup is not complete in this server. Please ask an administrator to run `/setup`.", ephemeral=True
            )

        # --- ロール存在チェック ---
        role_id_str = guild_config.get("role_id")
        if not role_id_str or not role_id_str.isdigit():
            logger.error(f"Invalid or missing role_id in config for guild {guild_id_str}.")
            return await interaction.response.send_message(
                "Configuration error: Role ID is invalid. Please contact an administrator.", ephemeral=True
            )
        role_id = int(role_id_str)
        role = interaction.guild.get_role(role_id)
        if not role:
            logger.warning(f"Configured role (ID: {role_id}) not found in guild {guild_id_str}.")
            return await interaction.response.send_message(
                "Configuration error: The assigned role could not be found in this server. Please contact an administrator.", ephemeral=True
            )

        # --- 既にロールを持っているかチェック ---
        if role in interaction.user.roles:
            return await interaction.response.send_message(
                f"You already have the {role.mention} role.", ephemeral=True
            )

        # --- ロール付与実行 ---
        try:
            await interaction.user.add_roles(role, reason="Eligibility check passed")
            logger.info(f"Granted role '{role.name}' to user {interaction.user} (ID: {user_id_str}) in guild {guild_id_str}.")
        except discord.Forbidden:
            logger.error(f"Failed to grant role '{role.name}' to user {user_id_str} in guild {guild_id_str}. Bot lacks permissions.")
            return await interaction.response.send_message(
                "Error: I couldn't grant the role. Please ensure I have the 'Manage Roles' permission.", ephemeral=True
            )
        except discord.HTTPException as e:
             logger.error(f"Failed to grant role '{role.name}' due to an HTTP error: {e}")
             return await interaction.response.send_message(
                f"An error occurred while trying to grant the role: {e}", ephemeral=True
            )

        # --- 成功メッセージ送信 ---
        response_text = f"You are **eligible** (UID: {user_id_str}). The role {role.mention} has been granted!"
        await interaction.response.send_message(response_text, ephemeral=True)

        # --- バックグラウンドで履歴保存とログ記録 ---
        # この部分は成功した場合のみ実行される
        async def background_tasks():
            timestamp = datetime.utcnow().isoformat() # UTCで記録
            log_entry = {
                "uid": user_id_str,
                "username": str(interaction.user), # username#discriminator
                "time": timestamp
            }
            # メモリ上の履歴に追加
            data_manager.granted_history.setdefault(guild_id_str, []).append(log_entry)
            try:
                # Google Sheets に保存 (非同期)
                await data_manager.save_granted_history_sheet()
                await data_manager.append_log_to_sheet(guild_id_str, user_id_str, str(interaction.user), timestamp)
            except Exception as e:
                # バックグラウンドタスクでのエラーはユーザーには通知せず、ログに残す
                logger.error(f"Error in background tasks (saving history/log) for user {user_id_str} in guild {guild_id_str}: {e}")

        # asyncio.create_task でノンブロッキング実行
        asyncio.create_task(background_tasks())


# --- 履歴表示用のページング UI ---
class HistoryPagerView(discord.ui.View):
    def __init__(self, records: list[dict]):
        super().__init__(timeout=180) # 3分でタイムアウト
        self.records = records
        self.current_page = 0
        self.per_page = 10
        self.total_pages = ceil(len(self.records) / self.per_page) if self.records else 1

        self.prev_button = PrevButton(disabled=(self.current_page == 0))
        self.next_button = NextButton(disabled=(self.current_page >= self.total_pages - 1))
        self.add_item(self.prev_button)
        self.add_item(self.next_button)

    def update_buttons(self):
        self.prev_button.disabled = (self.current_page == 0)
        self.next_button.disabled = (self.current_page >= self.total_pages - 1)

    def get_page_embed(self):
        start_index = self.current_page * self.per_page
        end_index = start_index + self.per_page
        page_records = self.records[start_index:end_index]

        description_lines = [
            "This list shows the server's role assignment history.",
            "Below are the recent assignments:\n"
        ]
        if not page_records:
            description_lines.append("No assignments on this page.")
        else:
            for i, record in enumerate(page_records, start=start_index + 1):
                # UIDからシングルクォートを除去してメンションを作成
                uid_clean = record.get("uid", "").lstrip("'")
                username = record.get("username", "N/A")
                time_str = record.get("time", "N/A") # 読み込んだままの形式
                description_lines.append(f"{i}. <@{uid_clean}> ({username}) - {time_str}")

        embed = discord.Embed(
            title="Role Assignment History",
            description="\n".join(description_lines),
            color=EMBED_COLOR
        )
        embed.set_footer(text=f"Page {self.current_page + 1}/{self.total_pages} (Total {len(self.records)} assignments)")
        return embed

    async def update_message(self, interaction: discord.Interaction):
        self.update_buttons()
        embed = self.get_page_embed()
        await interaction.response.edit_message(embed=embed, view=self)

class PrevButton(discord.ui.Button):
    def __init__(self, disabled=False):
        super().__init__(label="◀ Prev", style=discord.ButtonStyle.secondary, disabled=disabled)

    async def callback(self, interaction: discord.Interaction):
        view: HistoryPagerView = self.view # type: ignore
        if view.current_page > 0:
            view.current_page -= 1
            await view.update_message(interaction)
        else:
             # ボタンが無効なはずだが、念のため応答
            await interaction.response.defer()


class NextButton(discord.ui.Button):
    def __init__(self, disabled=False):
        super().__init__(label="Next ▶", style=discord.ButtonStyle.secondary, disabled=disabled)

    async def callback(self, interaction: discord.Interaction):
        view: HistoryPagerView = self.view # type: ignore
        if view.current_page < view.total_pages - 1:
            view.current_page += 1
            await view.update_message(interaction)
        else:
            # ボタンが無効なはずだが、念のため応答
            await interaction.response.defer()


# --- Bot イベント ---
@bot.event
async def on_ready():
    """ボット起動時の処理"""
    logger.info(f"Bot logged in as {bot.user.name} ({bot.user.id})")

    # Google Sheetsからデータをロード
    await data_manager.load_all_data()
    logger.info(f"Initial UID list loaded: {len(data_manager.valid_uids)} UIDs found.")

    # 永続ビューをリスナーに追加（ボット再起動後もインタラクションを受け付けるため）
    # 既に存在する場合は上書きされるだけなので問題ない
    bot.add_view(CheckEligibilityView())
    logger.info("Persistent CheckEligibilityView added.")

    # スラッシュコマンドを同期
    try:
        # 個別ギルドではなくグローバルに同期する場合
        synced = await bot.tree.sync()
        # 特定のギルドのみでテストする場合:
        # guild = discord.Object(id=YOUR_GUILD_ID) # テストサーバーIDに置き換える
        # synced = await bot.tree.sync(guild=guild)
        logger.info(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        logger.error(f"Error syncing slash commands: {e}")

@bot.event
async def on_guild_join(guild: discord.Guild):
    """ボットが新しいサーバーに参加したときのログ"""
    logger.info(f"Joined new guild: {guild.name} (ID: {guild.id})")

@bot.event
async def on_guild_remove(guild: discord.Guild):
    """ボットがサーバーから退出したときのログ"""
    logger.info(f"Removed from guild: {guild.name} (ID: {guild.id})")
    # 必要であれば、このギルドの設定や履歴を削除する処理を追加
    guild_id_str = str(guild.id)
    if guild_id_str in data_manager.guild_config:
        del data_manager.guild_config[guild_id_str]
        logger.info(f"Removed configuration for guild {guild_id_str}.")
        # シートからも削除する場合は save_guild_config_sheet を呼び出す
        # await data_manager.save_guild_config_sheet() # 即時保存する場合
    if guild_id_str in data_manager.granted_history:
        del data_manager.granted_history[guild_id_str]
        logger.info(f"Removed history for guild {guild_id_str}.")
        # シートからも削除する場合は save_granted_history_sheet を呼び出す
        # await data_manager.save_granted_history_sheet() # 即時保存する場合


@bot.event
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """スラッシュコマンドのエラーハンドリング"""
    logger.error(f"App command error in command '{interaction.command.name if interaction.command else 'Unknown'}': {error}", exc_info=True)

    error_message = "An unexpected error occurred. Please try again later."
    if isinstance(error, app_commands.CommandNotFound):
        error_message = "Sorry, I don't recognize that command."
    elif isinstance(error, app_commands.MissingPermissions):
        error_message = f"You don't have the required permissions to run this command: {', '.join(error.missing_permissions)}"
    elif isinstance(error, app_commands.BotMissingPermissions):
         error_message = f"I don't have the required permissions to perform this action: {', '.join(error.missing_permissions)}"
    elif isinstance(error, app_commands.CheckFailure):
         error_message = "You do not meet the requirements to use this command."
    # 必要に応じて他のエラータイプもハンドル

    try:
        if interaction.response.is_done():
            await interaction.followup.send(error_message, ephemeral=True)
        else:
            await interaction.response.send_message(error_message, ephemeral=True)
    except discord.NotFound:
         logger.warning("Interaction was not found when trying to send error message.")
    except discord.HTTPException as e:
        logger.error(f"Failed to send error message to interaction: {e}")

# --- スラッシュコマンド ---

@bot.tree.command(name="setup", description="Set up or update the eligibility button and assigned role.")
@app_commands.default_permissions(administrator=True) # 管理者権限を持つユーザーのみ実行可能
@app_commands.describe(
    channel="The channel where the eligibility check button will be posted.",
    role="The role to grant to eligible users."
)
async def setup_command(interaction: discord.Interaction, channel: discord.TextChannel, role: discord.Role):
    """セットアップコマンド: ボタンを投稿し、設定を保存する"""
    if not interaction.guild:
        return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
    guild_id_str = str(interaction.guild.id)

    # 権限チェック（ボットがロールを管理できるか）
    if not interaction.app_permissions.manage_roles:
         return await interaction.response.send_message(
            "I need the 'Manage Roles' permission to assign roles.", ephemeral=True
        )
    # ボットの最高位ロールが付与対象ロールより低い場合、付与できない
    if interaction.guild.me.top_role <= role:
         return await interaction.response.send_message(
            f"My highest role ('{interaction.guild.me.top_role.name}') is not high enough to manage the role '{role.name}'. Please adjust my role position in the server settings.",
            ephemeral=True
        )
    # ボットがチャンネルにメッセージを送信/編集できるか
    if not channel.permissions_for(interaction.guild.me).send_messages or \
       not channel.permissions_for(interaction.guild.me).embed_links:
         return await interaction.response.send_message(
            f"I don't have permission to send messages or embeds in {channel.mention}.", ephemeral=True
        )


    await interaction.response.defer(ephemeral=True) # 時間がかかる可能性があるのでdefer

    embed = discord.Embed(
        title="Check Eligibility",
        description="Click the button below to see if you're eligible for the special role!",
        color=EMBED_COLOR
    )
    view = CheckEligibilityView()

    message_id_to_save = None
    message_link = "Not available"

    # 既存の設定があれば、古いメッセージを更新しようと試みる
    old_config = data_manager.guild_config.get(guild_id_str)
    if old_config and old_config.get("message_id") and old_config.get("channel_id"):
        old_msg_id_str = old_config["message_id"]
        old_ch_id_str = old_config["channel_id"]
        if old_msg_id_str.isdigit() and old_ch_id_str.isdigit():
            old_msg_id = int(old_msg_id_str)
            old_ch_id = int(old_ch_id_str)
            # 指定された新しいチャンネルIDと古いチャンネルIDが同じか確認
            if channel.id == old_ch_id:
                try:
                    old_msg = await channel.fetch_message(old_msg_id)
                    await old_msg.edit(embed=embed, view=view)
                    message_id_to_save = old_msg.id
                    message_link = old_msg.jump_url
                    logger.info(f"Updated existing eligibility message in guild {guild_id_str}, channel {channel.id}.")
                except discord.NotFound:
                    logger.warning(f"Old message (ID: {old_msg_id}) not found in channel {channel.id}. A new message will be created.")
                except discord.Forbidden:
                    logger.error(f"Failed to edit old message (ID: {old_msg_id}) in channel {channel.id}. Insufficient permissions.")
                    # 編集権限がない場合はフォローアップで通知し、新規作成は行わない
                    return await interaction.followup.send(f"I don't have permission to edit the existing message in {channel.mention}. Please check my permissions or delete the old message manually.", ephemeral=True)
                except discord.HTTPException as e:
                    logger.error(f"Failed to edit old message (ID: {old_msg_id}) due to HTTP error: {e}")
                    # 編集に失敗した場合もフォローアップで通知し、新規作成は行わない
                    return await interaction.followup.send(f"An error occurred while trying to update the message: {e}", ephemeral=True)

    # 古いメッセージを更新できなかった場合、または設定がなかった場合は新しいメッセージを送信
    if message_id_to_save is None:
        try:
            new_msg = await channel.send(embed=embed, view=view)
            message_id_to_save = new_msg.id
            message_link = new_msg.jump_url
            logger.info(f"Sent new eligibility message to guild {guild_id_str}, channel {channel.id}.")
        except discord.Forbidden:
            logger.error(f"Failed to send message to channel {channel.id}. Insufficient permissions.")
            return await interaction.followup.send(f"I don't have permission to send messages in {channel.mention}.", ephemeral=True)
        except discord.HTTPException as e:
            logger.error(f"Failed to send message to channel {channel.id} due to HTTP error: {e}")
            return await interaction.followup.send(f"An error occurred while trying to send the message: {e}", ephemeral=True)


    # 設定を保存
    if message_id_to_save:
        data_manager.guild_config[guild_id_str] = {
            "server_name": interaction.guild.name,
            "channel_id": str(channel.id), # 文字列で保存
            "role_id": str(role.id),       # 文字列で保存
            "message_id": str(message_id_to_save) # 文字列で保存
        }
        await data_manager.save_guild_config_sheet()
        await interaction.followup.send(
            f"Setup complete! The eligibility check button is now active in {channel.mention} (Message: <{message_link}>).\nEligible users will receive the {role.mention} role.",
            ephemeral=True
        )
    else:
        # メッセージIDが取得できなかった（＝メッセージ送信/編集に失敗した）場合
        await interaction.followup.send(
            "Setup failed. Could not post or update the eligibility message.",
            ephemeral=True
        )


@bot.tree.command(name="reloadlist", description="Reload the eligible user list from Google Sheets.")
@app_commands.default_permissions(administrator=True)
async def reloadlist_command(interaction: discord.Interaction):
    """リロードコマンド: list シートからUIDリストを再読み込みする"""
    await interaction.response.defer(ephemeral=True)
    count = await data_manager.load_uid_list()
    await interaction.followup.send(
        f"Successfully reloaded the eligibility list from the '{LIST_SHEET_NAME}' sheet. Found {count} eligible UIDs.",
        ephemeral=True
    )


@bot.tree.command(name="history", description="Show the role assignment history for this server.")
@app_commands.default_permissions(administrator=True)
async def history_command(interaction: discord.Interaction):
    """履歴表示コマンド: このサーバーのロール付与履歴を表示する"""
    if not interaction.guild:
        return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
    guild_id_str = str(interaction.guild.id)

    await interaction.response.defer(ephemeral=True)

    # 常に最新の履歴をシートから読み込む
    await data_manager.load_granted_history_sheet()
    records = data_manager.granted_history.get(guild_id_str, [])

    if not records:
        return await interaction.followup.send("No role assignment history found for this server.", ephemeral=True)

    # 履歴は新しいものが後ろに追加される想定なので、表示のために逆順にする
    records_display_order = sorted(records, key=lambda x: x.get('time', ''), reverse=True)


    view = HistoryPagerView(records_display_order)
    embed = view.get_page_embed()
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


@bot.tree.command(name="extractinfo", description="Show current setup info and recent role assignments.")
@app_commands.default_permissions(administrator=True)
async def extractinfo_command(interaction: discord.Interaction):
    """情報抽出コマンド: 現在の設定と最近のロール付与履歴を表示する"""
    if not interaction.guild:
        return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
    guild_id_str = str(interaction.guild.id)

    await interaction.response.defer(ephemeral=True)

    # 最新の設定と履歴を読み込む
    await data_manager.load_guild_config_sheet()
    await data_manager.load_granted_history_sheet()

    config = data_manager.guild_config.get(guild_id_str)
    history = data_manager.granted_history.get(guild_id_str, [])

    if not config:
        return await interaction.followup.send("No setup information found for this server. Run `/setup` first.", ephemeral=True)

    ch_id = config.get("channel_id", "N/A")
    role_id = config.get("role_id", "N/A")
    msg_id = config.get("message_id", "N/A")

    channel_mention = f"<#{ch_id}>" if ch_id.isdigit() else "Invalid/Not set"
    role_mention = f"<@&{role_id}>" if role_id.isdigit() else "Invalid/Not set"
    msg_link = "N/A"
    if ch_id.isdigit() and msg_id.isdigit():
        msg_link = f"https://discord.com/channels/{guild_id_str}/{ch_id}/{msg_id}"


    report_lines = [
        f"**Server Configuration for {interaction.guild.name}**",
        f"- Server Name: {config.get('server_name', 'N/A')}",
        f"- Target Channel: {channel_mention} (ID: {ch_id})",
        f"- Assigned Role: {role_mention} (ID: {role_id})",
        f"- Button Message: {msg_link} (ID: {msg_id})",
        f"\n**Recent Role Grants (last 10)** (Total: {len(history)})"
    ]

    # 履歴は新しい順に最大10件表示
    recent_history = sorted(history, key=lambda x: x.get('time', ''), reverse=True)[:10]

    if not recent_history:
        report_lines.append("- No recent assignments.")
    else:
        for i, record in enumerate(recent_history, start=1):
            uid_clean = record.get('uid', '').lstrip("'")
            username = record.get('username', 'N/A')
            time_str = record.get('time', 'N/A')
            report_lines.append(f"{i}. <@{uid_clean}> ({username}) - {time_str}")

    report = "\n".join(report_lines)
    # メッセージが長すぎる場合を考慮（Discordの制限は2000文字）
    if len(report) > 2000:
        report = report[:1997] + "..."

    await interaction.followup.send(report, ephemeral=True)


@bot.tree.command(name="reset_history", description="⚠️ Reset the role assignment history for this server (Admin only).")
@app_commands.default_permissions(administrator=True)
async def reset_history_command(interaction: discord.Interaction):
    """履歴リセットコマンド: このサーバーのロール付与履歴を消去する"""
    if not interaction.guild:
        return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
    guild_id_str = str(interaction.guild.id)

    await interaction.response.defer(ephemeral=True)

    # メモリ上の履歴をクリア
    if guild_id_str in data_manager.granted_history:
        data_manager.granted_history[guild_id_str] = []
    else:
        # 履歴が存在しない場合もメッセージは出す
        pass

    # Google Sheets 上の履歴もクリア（シート自体は残し、中身をヘッダーのみにする）
    ws = await data_manager._get_or_create_worksheet(GRANTED_HISTORY_SHEET_NAME)
    if ws:
        def _clear_and_write_header():
            try:
                # 該当ギルドの行のみ削除するのはgspreadでは面倒なため、全クリアしてヘッダー再書き込み
                # ただし、他のギルドの履歴も消えてしまうため、要件に応じて実装変更が必要
                # ここでは、コマンド実行ギルドのメモリ上のデータのみクリアし、シートは全クリアする実装とする
                # ws.clear() # 全クリアする場合
                # ws.update('A1', [["guild_id", "uid", "username", "time"]], value_input_option='USER_ENTERED')

                # より安全な方法：該当ギルドのデータのみ削除（少し遅い）
                all_records = ws.get_all_records(head=1)
                rows_to_keep = [ws.row_values(1)] # ヘッダーは保持
                for row_num, record in enumerate(all_records, start=2): # 2行目からデータ
                    if str(record.get("guild_id", "")).strip() != guild_id_str:
                        rows_to_keep.append(ws.row_values(row_num))

                ws.clear()
                ws.update('A1', rows_to_keep, value_input_option='USER_ENTERED')

                logger.info(f"Cleared history for guild {guild_id_str} in sheet '{GRANTED_HISTORY_SHEET_NAME}'.")
            except APIError as e:
                 logger.error(f"API error clearing history for guild {guild_id_str} in sheet: {e}")
            except Exception as e:
                 logger.error(f"Unexpected error clearing history for guild {guild_id_str} in sheet: {e}")

        await asyncio.to_thread(_clear_and_write_header)
        await interaction.followup.send(f"Role assignment history for **{interaction.guild.name}** has been reset.", ephemeral=True)
    else:
        await interaction.followup.send(f"Could not access the history sheet. History reset failed.", ephemeral=True)


# --- Bot 実行 ---
if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except Exception as e:
        logger.critical(f"Fatal error running the bot: {e}", exc_info=True)
