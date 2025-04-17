# eligibility_bot.py
# Python 3.11+ / discord.py 2.4.1‑post
# 必要環境変数: BOT_TOKEN, GOOGLE_CREDENTIALS(JSON文字列)
# 省略可: SPREADSHEET_NAME (既定 "keone_list_log")

import os, json, asyncio, logging
from datetime import datetime
from math import ceil
from typing import Dict, List

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ───────── ログ ─────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("elig‑bot")

# ───────── 環境変数 / Sheets ─────────
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
GCREDS = os.getenv("GOOGLE_CREDENTIALS")
SHEET_NAME = os.getenv("SPREADSHEET_NAME", "keone_list_log")
if not TOKEN or not GCREDS:
    raise SystemExit("BOT_TOKEN / GOOGLE_CREDENTIALS が未設定")

SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(GCREDS), SCOPE)
gclient = gspread.authorize(creds)
SPREADSHEET = gclient.open(SHEET_NAME)

gs_lock = asyncio.Lock()
EMBED_COLOR = int("836EF9", 16)

# ───────── Utility ─────────
def now_iso() -> str: return datetime.utcnow().isoformat()
def iso2human(s: str) -> str:
    try:  return datetime.fromisoformat(s).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError: return s

# ───────── DataManager ─────────
class DataManager:
    def __init__(self):
        self.uids: set[str] = set()
        self.cfg: Dict[str, Dict[str, int | str]] = {}
        self.hist: Dict[str, List[Dict[str, str]]] = {}

    # ID helpers
    @staticmethod
    def _to_sheet(v: int | str) -> str: s=str(v); return s if s.startswith("'") else f"'{s}"
    @staticmethod
    def _from_sheet(v: str | int) -> int: return int(str(v).lstrip("'"))

    async def _sheet(self, name: str, rows="1000", cols="10"):
        async with gs_lock:
            try:   return SPREADSHEET.worksheet(name)
            except gspread.WorksheetNotFound:
                return SPREADSHEET.add_worksheet(title=name, rows=rows, cols=cols)

    # UID list
    async def load_uids(self) -> int:
        async with gs_lock:
            recs = (await self._sheet("list")).get_all_records()
        self.uids = {str(r.get("DCUID", "")).strip() for r in recs if r.get("DCUID")}
        log.info("UIDs loaded: %d", len(self.uids))
        return len(self.uids)

    # guild_config
    async def load_cfg(self):
        async with gs_lock:
            recs = (await self._sheet("guild_config")).get_all_records()
        self.cfg = {
            str(r["guild_id"]): {
                "server_name": r.get("server_name", ""),
                "channel_id": self._from_sheet(r.get("channel_id", 0)),
                "role_id":    self._from_sheet(r.get("role_id",    0)),
                "message_id": self._from_sheet(r.get("message_id", 0)),
            }
            for r in recs if r.get("guild_id")
        }

    async def save_cfg(self):
        hdr = ["guild_id", "server_name", "channel_id", "role_id", "message_id"]
        rows = [hdr] + [
            [gid, c["server_name"],
             self._to_sheet(c["channel_id"]),
             self._to_sheet(c["role_id"]),
             self._to_sheet(c["message_id"])]
            for gid, c in self.cfg.items()
        ]
        async with gs_lock:
            ws = await self._sheet("guild_config", rows="100", cols="10")
            ws.clear(); ws.update("A1", rows)
        log.info("guild_config saved")

    # history
    async def load_hist(self):
        async with gs_lock:
            recs = (await self._sheet("granted_history")).get_all_records()
        self.hist.clear()
        for r in recs:
            gid = str(r.get("guild_id", "")).strip()
            if gid:
                self.hist.setdefault(gid, []).append(
                    {"uid": r["uid"], "username": r.get("username", ""), "time": r.get("time", "")}
                )

    async def save_hist(self):
        hdr = ["guild_id", "uid", "username", "time"]
        rows = [hdr]
        for gid, lst in self.hist.items():
            for rec in lst:
                rows.append([
                    gid, self._to_sheet(rec["uid"]), rec["username"], iso2human(rec["time"])
                ])
        async with gs_lock:
            ws = await self._sheet("granted_history", rows="1000", cols="10")
            ws.clear(); ws.update("A1", rows)
        log.info("history saved")

    # public
    async def startup(self):
        await self.load_uids(); await self.load_cfg(); await self.load_hist()

    async def add_hist(self, gid: str, uid: str, username: str):
        self.hist.setdefault(gid, []).append({"uid": uid, "username": username, "time": now_iso()})
        await self.save_hist()

dm = DataManager()

# ───────── Discord Bot ─────────
intents = discord.Intents.default()
intents.members = True; intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# Buttons & Views
class CheckButton(discord.ui.Button):
    def __init__(self):
        super().__init__(custom_id="check_eligibility_button",
                         label="Check Eligibility", style=discord.ButtonStyle.primary)

    async def callback(self, itx: discord.Interaction):
        gid, uid = str(itx.guild_id), str(itx.user.id)
        if uid not in dm.uids:
            return await itx.response.send_message(f"You are not eligible (UID: {uid}).", ephemeral=True)
        cfg = dm.cfg.get(gid)
        if not cfg:
            return await itx.response.send_message("No setup found. Please run /setup.", ephemeral=True)

        role = itx.guild.get_role(cfg["role_id"])
        if not role:
            return await itx.response.send_message("Configured role not found.", ephemeral=True)
        if role in itx.user.roles:
            return await itx.response.send_message(f"You already have {role.mention}.", ephemeral=True)

        try:
            await itx.user.add_roles(role, reason="Eligibility check")
        except discord.Forbidden:
            return await itx.response.send_message("Failed to grant role. Check bot permissions.", ephemeral=True)

        await itx.response.send_message(
            f"You are **eligible** (UID: {uid}). Role {role.mention} has been granted!",
            ephemeral=True,
        )
        asyncio.create_task(dm.add_hist(gid, uid, str(itx.user)))

class CheckView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None); self.add_item(CheckButton())

class Pager(discord.ui.View):
    def __init__(self, recs: List[Dict[str, str]]):
        super().__init__(timeout=None)
        self.recs, self.page, self.per = recs, 0, 10
        self.prev = discord.ui.Button(label="◀ Prev", style=discord.ButtonStyle.secondary)
        self.next = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.secondary)
        self.prev.callback = self.prev_page; self.next.callback = self.next_page
        self.add_item(self.prev); self.add_item(self.next); self._update()

    def _max(self): return max(1, ceil(len(self.recs)/self.per))
    def _embed(self):
        s = self.page*self.per; chunk = self.recs[s:s+self.per]
        lines = [f"{s+i+1}. <@{r['uid'].lstrip(\"'\")}>"
                 for i, r in enumerate(chunk)]
        desc = "This list shows the server's role assignment history.\n\n" + "\n".join(lines) \
               if lines else "No assignments on this page."
        em = discord.Embed(title="Role Assignment History", description=desc, color=EMBED_COLOR)
        em.set_footer(text=f"Page {self.page+1}/{self._max()} (Total {len(self.recs)})")
        return em
    def _update(self):
        self.prev.disabled = self.page == 0
        self.next.disabled = self.page >= self._max()-1
    async def prev_page(self,itx:discord.Interaction):
        if self.page: self.page -= 1; self._update()
        await itx.response.edit_message(embed=self._embed(), view=self)
    async def next_page(self,itx:discord.Interaction):
        if self.page < self._max()-1: self.page += 1; self._update()
        await itx.response.edit_message(embed=self._embed(), view=self)

# Events
@bot.event
async def on_ready():
    log.info("Logged in as %s (%s)", bot.user, bot.user.id)
    await dm.startup()
    bot.add_view(CheckView())
    await tree.sync()
    log.info("Slash‑commands synced")

# Commands
@tree.command(name="setup", description="Set up / update eligibility button & role")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(channel="Channel for the button", role="Role to grant")
async def setup(itx:discord.Interaction, channel:discord.TextChannel, role:discord.Role):
    gid = str(itx.guild_id); cfg = dm.cfg.get(gid, {})
    embed_txt = "Click the button below to see if you're on the list."

    # try editing old message
    if cfg.get("message_id") and cfg.get("channel_id"):
        ch = itx.guild.get_channel(cfg["channel_id"])
        if isinstance(ch, discord.TextChannel):
            try:
                msg = await ch.fetch_message(cfg["message_id"])
                await msg.edit(embed=discord.Embed(title="Check Eligibility", description=embed_txt,
                                                   color=EMBED_COLOR),
                               view=CheckView())
                cfg.update({"server_name": itx.guild.name, "channel_id": channel.id,
                            "role_id": role.id, "message_id": msg.id})
                dm.cfg[gid] = cfg; await dm.save_cfg()
                return await itx.response.send_message(
                    f"Button updated in {ch.mention}. Role set to {role.mention}.", ephemeral=True)
            except discord.NotFound:
                pass

    # new message
    msg = await channel.send(embed=discord.Embed(title="Check Eligibility",
                                                 description=embed_txt, color=EMBED_COLOR),
                             view=CheckView())
    dm.cfg[gid] = {"server_name": itx.guild.name, "channel_id": channel.id,
                   "role_id": role.id, "message_id": msg.id}
    await dm.save_cfg()
    await itx.response.send_message(
        f"Setup complete in {channel.mention} with role {role.mention}.", ephemeral=True)

@tree.command(name="reloadlist", description="Reload UID list from Sheets")
@app_commands.default_permissions(administrator=True)
async def reloadlist(itx:discord.Interaction):
    n = await dm.load_uids()
    await itx.response.send_message(f"UID list reloaded: **{n}** entries.", ephemeral=True)

@tree.command(name="history", description="Show role‑grant history")
@app_commands.default_permissions(administrator=True)
async def history(itx:discord.Interaction):
    await dm.load_hist(); gid = str(itx.guild_id)
    recs = dm.hist.get(gid, [])
    if not recs:
        return await itx.response.send_message("No history for this server.", ephemeral=True)
    await itx.response.send_message(embed=Pager(recs)._embed(), view=Pager(recs), ephemeral=True)

@tree.command(name="extractinfo", description="Extract server info & last 10 grants")
@app_commands.default_permissions(administrator=True)
async def extractinfo(itx:discord.Interaction):
    gid = str(itx.guild_id); cfg = dm.cfg.get(gid)
    if not cfg:
        return await itx.response.send_message("No setup info found.", ephemeral=True)
    await dm.load_hist(); recs = dm.hist.get(gid, [])
    lines = [
        "**Server Info**",
        f"- Server Name: {cfg['server_name']}",
        f"- Channel ID: {cfg['channel_id']}",
        f"- Role ID: {cfg['role_id']}",
        f"- Setup Message ID: {cfg['message_id']}",
        "",
        f"**Recent Role Grants** (total {len(recs)})",
    ] + [f"{i}. <@{r['uid'].lstrip(\"'\")}>"
         for i, r in enumerate(recs[-10:], 1)]
    await itx.response.send_message("\n".join(lines), ephemeral=True)

@tree.command(name="reset_history", description="Reset role‑grant history")
@app_commands.default_permissions(administrator=True)
async def reset_history(itx:discord.Interaction):
    gid = str(itx.guild_id); dm.hist[gid] = []; await dm.save_hist()
    await itx.response.send_message("History reset for this server.", ephemeral=True)

# Auto backup every 30 min
@tasks.loop(minutes=30)
async def auto_backup():
    await dm.save_cfg(); await dm.save_hist()
    log.info("Auto‑backup done")

if __name__ == "__main__":
    auto_backup.start()
    bot.run(TOKEN)
