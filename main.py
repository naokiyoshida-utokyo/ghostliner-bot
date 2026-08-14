import discord
from discord.ext import commands, tasks
from discord import app_commands
import random
import time
import asyncio
import os
import json
import glob
import sqlite3
import logging
import traceback
import unicodedata
from dotenv import load_dotenv

# AIプレイヤー関連。
#   ai_player : Discordのメンバーのふりをする偽物クラス（AIの「体」）
#   engine    : AIの推理エンジン。ここでは対応人数の確認にだけ使う
import ai_player
import ai_brain
import engine

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📝 ロギング（監視カメラ）の設定
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),  # ファイルに保存
        logging.StreamHandler()                            # 黒い画面（ターミナル）にも表示
    ]
)
logger = logging.getLogger("ghostliner")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🤖 Botの初期設定（分身の術: AutoShardedBot）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
intents = discord.Intents.default()
intents.message_content = True
# 数千サーバーに対応するため AutoShardedBot を使用
bot = commands.AutoShardedBot(command_prefix="!", intents=intents)

games = {}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🌍 多言語対応 (i18n) マネージャー
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LOCALES = {}
DB_FILE = "ghostliner.db"
USER_LANGS = {}

SETTING_LABELS = {
    "ja": {
        "navigator": "航海士同士を知る", "charon": "カロン同士を知る", "hades": "ハデス同士を知る",
        "h_knows_c": "ハデスがカロンを知る", "c_knows_h": "カロンがハデスを知る", "allow_spectate": "観戦者への全役職公開",
        "vic_o": "〇勝利条件", "vic_x": "✕勝利条件"
    },
    "en-US": {
        "navigator": "Navigator Knows", "charon": "Charon Knows", "hades": "Hades Knows",
        "h_knows_c": "Hades Knows Charon", "c_knows_h": "Charon Knows Hades", "allow_spectate": "Spectator Reveal",
        "vic_o": "O pt target", "vic_x": "X pt target"
    },
    "zh-TW": {
        "navigator": "領航員互相確認", "charon": "卡戎互相確認", "hades": "黑帝斯互相確認",
        "h_knows_c": "黑帝斯知道卡戎", "c_knows_h": "卡戎知道黑帝斯", "allow_spectate": "對觀戰者公開職業",
        "vic_o": "〇勝利條件", "vic_x": "✕勝利條件"
    },
    "zh-CN": {
        "navigator": "领航员互相确认", "charon": "卡戎互相确认", "hades": "哈迪斯互相确认",
        "h_knows_c": "哈迪斯知道卡戎", "c_knows_h": "卡戎知道哈迪斯", "allow_spectate": "对观战者公开职业",
        "vic_o": "〇胜利条件", "vic_x": "✕胜利条件"
    },
    "ko": {
        "navigator": "항해사끼리 앎", "charon": "카론끼리 앎", "hades": "하데스끼리 앎",
        "h_knows_c": "하데스가 카론을 앎", "c_knows_h": "카론이 하데스를 앎", "allow_spectate": "관전자에게 직업 공개",
        "vic_o": "〇 승리 조건", "vic_x": "✕ 승리 조건"
    }
}

def get_setting_label(lang, key):
    return SETTING_LABELS.get(lang, SETTING_LABELS["en-US"]).get(key, key)

def load_locales():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    locales_dir = os.path.join(base_dir, "locales")
    json_files = glob.glob(os.path.join(locales_dir, "*.json"))
    
    for filepath in json_files:
        lang = os.path.splitext(os.path.basename(filepath))[0]
        with open(filepath, "r", encoding="utf-8") as f:
            LOCALES[lang] = json.load(f)
            logger.info(f"✅ 言語ファイルの読み込みに成功: {lang}.json")

def setup_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS user_langs
                 (user_id INTEGER PRIMARY KEY, lang TEXT)''')
    conn.commit()
    conn.close()

def load_user_langs():
    global USER_LANGS
    USER_LANGS = {}
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id, lang FROM user_langs")
    for row in c.fetchall():
        USER_LANGS[row[0]] = row[1]
    conn.close()

def save_user_lang_to_db(user_id, lang):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("REPLACE INTO user_langs (user_id, lang) VALUES (?, ?)", (user_id, lang))
    conn.commit()
    conn.close()

def t(lang, *keys, **kwargs):
    def get_val(l):
        d = LOCALES.get(l, {})
        for k in keys:
            if isinstance(d, dict):
                d = d.get(k)
            else:
                return None
        return d

    val = get_val(lang)
    if val is None: val = get_val("en-US")
    if val is None: val = get_val("ja")
    if val is None: return "TEXT_NOT_FOUND"
    
    if isinstance(val, str) and kwargs:
        try: return val.format(**kwargs)
        except KeyError: return val
    return val

def get_image_file(image_filename, lang):
    if not image_filename:
        return None
    if not os.path.exists(image_filename):
        fallback_name = image_filename.replace(f"_{lang}.jpg", "_en.jpg")
        if os.path.exists(fallback_name):
            return fallback_name
    if os.path.exists(image_filename):
        return image_filename
    return None

load_locales()
setup_db()
load_user_langs()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 基礎関数群
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ★ハデスとセイレーンについて
#   ハデスは廃止し、上位互換のセイレーンに置き換える方針。
#   ただしハデス自体は動くまま残してあるので、役職設定画面で＋を押せば
#   今まで通り遊べる。ここで変えているのは「初期値」だけ。
#
#   8人以上の初期値を hades → siren にしたのは、AIプレイヤーの推理エンジン
#   （engine.py の COMPOSITIONS）がセイレーン構成しか持っていないため。
#   ここが食い違っていると、AIを入れたゲームで毎回ホストが手動で
#   ハデス－／セイレーン＋を押す羽目になる。
#
#   5人・6人はまだハデスのままにしてある（engine.py が7〜9人しか
#   対応していないので、どのみちAIは参加できない人数のため）。
def get_default_role_counts(n):
    if n <= 4: return {"navigator": 0, "passenger": max(0, n-1), "charon": 1, "hades": 0, "siren": 0}
    # ★5人・6人の初期値は、以前はハデス1だった。ハデスは廃止してセイレーンに
    #   置き換えた役職なうえ、AI（engine.py）はハデスを知らないので、
    #   初期値のままではAI入りで始められなかった。8人・9人と同じ方針に揃える。
    #   （ハデス自体はボタンで足せば今まで通り遊べる）
    if n == 5: return {"navigator": 1, "passenger": 2, "charon": 1, "hades": 0, "siren": 1}
    if n == 6: return {"navigator": 1, "passenger": 3, "charon": 1, "hades": 0, "siren": 1}
    if n == 7: return {"navigator": 2, "passenger": 3, "charon": 2, "hades": 0, "siren": 0}
    if n == 8: return {"navigator": 2, "passenger": 3, "charon": 2, "hades": 0, "siren": 1}
    return {"navigator": 2, "passenger": max(4, n - 5), "charon": 2, "hades": 0, "siren": 1}

def has_siren(game):
    return any(r == "siren" for r in game.get("roles", {}).values())

def make_progress_bar(current, target):
    filled = min(current, target)
    empty = target - filled
    return "■" * filled + "□" * empty

def to_emoji_num(n):
    emojis = ["0️⃣", "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]
    if 0 <= n <= 9: return emojis[n]
    return str(n).translate(str.maketrans('0123456789', '０１２３４５６７８９'))

def get_player_number_emoji(idx, total_players):
    num = idx + 1
    emojis = ["0️⃣", "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]
    if total_players >= 10:
        return f"{emojis[num // 10]}{emojis[num % 10]}"
    else:
        return emojis[num]

def pad_display_name(name, width=6):
    """
    昼の行動結果の表で、名前の幅をそろえる。

    ★「文字数」ではなく「表示幅」で数えるのが要点。
      ひらがな・漢字・全角記号は半角2つぶんの幅を持つので、
      len() で数えると見た目の幅と合わない。

      例：「つばき（AI）」
        文字数は7だが、（ は全角・A と I は半角なので
        表示幅は 2+2+2 + 2+1+1+2 = 12（＝全角6文字ぶん）でちょうどよい。
        ところが文字数で6に切り詰めると最後の ） が落ちて幅10になり、
        表がずれる。これがAI名だけずれて見えた原因。

      例：「Takumi」
        文字数は6なので余白が足されないが、表示幅は6しかない。
        つまりアルファベットの名前は以前からずれていた。

    width : 全角何文字ぶんの幅にそろえるか
    """
    target = width * 2          # 半角いくつぶんか
    out = []
    used = 0
    for ch in name:
        # East Asian Width が F(全角)/W(広)/A(曖昧) の文字は半角2つぶん
        ch_w = 2 if unicodedata.east_asian_width(ch) in ("F", "W", "A") else 1
        if used + ch_w > target:
            break                # これ以上入れると溢れるので、ここで打ち切る
        out.append(ch)
        used += ch_w
    rest = target - used
    # 全角スペースで埋め、1つぶん余ったら半角スペースで調整する
    return "".join(out) + "　" * (rest // 2) + (" " if rest % 2 else "")


def get_mentions(game):
    return " ".join([p.mention for p in game["players"]])

def update_last_active(channel_id):
    if channel_id in games:
        games[channel_id]["last_active"] = time.time()

def get_game_lang(channel_id):
    if channel_id in games and "lang" in games[channel_id]:
        return games[channel_id]["lang"]
    return "ja"

def get_user_lang(interaction: discord.Interaction):
    if interaction.user.id in USER_LANGS:
        return USER_LANGS[interaction.user.id]
    return interaction.locale.value

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ルール表示用のViewと関数
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def update_rule_message(interaction: discord.Interaction, key: str):
    await interaction.response.defer()
    lang = get_user_lang(interaction)
    
    data = t(lang, "rules", key)
    if not data:
        return
        
    embed = discord.Embed(title=data["title"], description=data["desc"], color=0x808080)
    
    raw_image_name = data.get("image")
    valid_image = get_image_file(raw_image_name, lang)
    
    if valid_image:
        file = discord.File(valid_image, filename=valid_image)
        embed.set_image(url=f"attachment://{valid_image}")
        await interaction.edit_original_response(embed=embed, attachments=[file])
    else:
        await interaction.edit_original_response(embed=embed, attachments=[])

class RulesView(discord.ui.View):
    def __init__(self, lang):
        super().__init__(timeout=None)
        
        role_options = [
            discord.SelectOption(label=t(lang, "rules", "navigator", "title"), value="navigator", emoji="🧭"),
            discord.SelectOption(label=t(lang, "rules", "passenger", "title"), value="passenger", emoji="🧑‍💼"),
            discord.SelectOption(label=t(lang, "rules", "charon", "title"), value="charon", emoji="💀"),
            discord.SelectOption(label=t(lang, "rules", "hades", "title"), value="hades", emoji="👑"),
            discord.SelectOption(label=t(lang, "rules", "siren", "title"), value="siren", emoji="🧜"),
            discord.SelectOption(label=t(lang, "rules", "ghost", "title"), value="ghost", emoji="👻")
        ]
        self.role_select = discord.ui.Select(placeholder=t(lang, "ui", "select_role_rule"), options=role_options, custom_id="rule_role")
        self.role_select.callback = self.select_callback
        
        dest_options = [
            discord.SelectOption(label=t(lang, "rules", "bridge", "title"), value="bridge", emoji="⚓"),
            discord.SelectOption(label=t(lang, "rules", "library", "title"), value="library", emoji="📖"),
            discord.SelectOption(label=t(lang, "rules", "lounge", "title"), value="lounge", emoji="🛋️")
        ]
        self.dest_select = discord.ui.Select(placeholder=t(lang, "ui", "select_dest_rule"), options=dest_options, custom_id="rule_dest")
        self.dest_select.callback = self.select_callback
        
        other_options = [
            discord.SelectOption(label=t(lang, "rules", "breakdown", "title"), value="breakdown", emoji="📊"),
            discord.SelectOption(label=t(lang, "rules", "faq", "title"), value="faq", emoji="❓"),
            discord.SelectOption(label=t(lang, "rules", "concept", "title"), value="concept", emoji="💡")
        ]
        self.other_select = discord.ui.Select(placeholder=t(lang, "ui", "select_other_rule"), options=other_options, custom_id="rule_other")
        self.other_select.callback = self.select_callback

        self.add_item(self.role_select)
        self.add_item(self.dest_select)
        self.add_item(self.other_select)
        
        back_btn = discord.ui.Button(label=t(lang, "ui", "back_to_summary"), style=discord.ButtonStyle.primary, row=3)
        async def back_callback(interaction: discord.Interaction):
            await update_rule_message(interaction, "summary")
        back_btn.callback = back_callback
        self.add_item(back_btn)

    async def select_callback(self, interaction: discord.Interaction):
        value = interaction.data["values"][0]
        await update_rule_message(interaction, value)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 各種Viewクラス
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class LibrarySelectView(discord.ui.View):
    def __init__(self, channel_id, user_lang):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.user_lang = user_lang
        game = games[channel_id]
        options = [discord.SelectOption(label=p.display_name, value=str(p.id)) for p in game["players"]]
        self.target_select = discord.ui.Select(placeholder=t(user_lang, "ui", "select_target_library"), options=options)
        self.target_select.callback = self.submit_callback
        self.add_item(self.target_select)

    async def submit_callback(self, interaction: discord.Interaction):
        update_last_active(self.channel_id)
        game = games.get(self.channel_id)
        if game.get("library_used_today"):
            await interaction.response.edit_message(content=t(self.user_lang, "msg", "err_lib_used"), view=None)
            return
        game["library_used_today"] = True
        target_id = int(self.target_select.values[0])
        target_user = discord.utils.get(game["players"], id=target_id)
        target_role_key = game["roles"][target_user]
        target_role_name = t(self.user_lang, "roles", target_role_key)
        
        msg = t(self.user_lang, "msg", "lib_result", target=target_user.display_name, role=target_role_name)
        await interaction.response.edit_message(content=msg, view=None)


class LibraryAndNextView(discord.ui.View):
    def __init__(self, channel_id, host, valid_library_user=None):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.host = host
        self.valid_library_user = valid_library_user
        
        g_lang = get_game_lang(channel_id)
        
        if not valid_library_user:
            self.trigger_button.disabled = True
            self.trigger_button.style = discord.ButtonStyle.secondary
            game = games.get(channel_id, {})
            day_results = game.get("day_results", {})
            lib_count = sum(1 for p, d in day_results.items() if d.get("dest") == "library")
            if lib_count >= 2:
                self.trigger_button.label = t(g_lang, "ui", "btn_use_library_used")
            else:
                self.trigger_button.label = t(g_lang, "ui", "btn_use_library_none")
        else:
            self.trigger_button.label = t(g_lang, "ui", "btn_use_library")

        self.next_phase_button.label = t(g_lang, "ui", "btn_next_night")

    @discord.ui.button(style=discord.ButtonStyle.success, custom_id="btn_lib")
    async def trigger_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        update_last_active(self.channel_id)
        game = games.get(self.channel_id)
        if not game or interaction.user not in game["players"]: return
        
        u_lang = get_user_lang(interaction)
        
        if interaction.user != self.valid_library_user:
            await interaction.response.send_message(t(u_lang, "msg", "err_lib_not_target"), ephemeral=True)
            return
        if game.get("library_used_today"):
            await interaction.response.send_message(t(u_lang, "msg", "err_lib_used"), ephemeral=True)
            return
            
        view = LibrarySelectView(self.channel_id, u_lang)
        await interaction.response.send_message(t(u_lang, "ui", "select_target_library"), view=view, ephemeral=True)

    @discord.ui.button(style=discord.ButtonStyle.primary, custom_id="btn_next_night")
    async def next_phase_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        update_last_active(self.channel_id)
        u_lang = get_user_lang(interaction)
        if interaction.user != self.host:
            await interaction.response.send_message(t(u_lang, "msg", "err_host_only"), ephemeral=True)
            return
        await interaction.response.defer()
        await interaction.message.edit(content="", view=None)
        await transition_to_night_phase(interaction.channel, games[self.channel_id])

class ResultRevealView(discord.ui.View):
    def __init__(self, channel_id, host):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.host = host
        g_lang = get_game_lang(channel_id)
        self.reveal_button.label = t(g_lang, "ui", "btn_reveal_day")

    @discord.ui.button(style=discord.ButtonStyle.primary, custom_id="btn_reveal_day")
    async def reveal_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        update_last_active(self.channel_id)
        u_lang = get_user_lang(interaction)
        g_lang = get_game_lang(self.channel_id)
        
        if interaction.user != self.host:
            await interaction.response.send_message(t(u_lang, "msg", "err_host_only"), ephemeral=True)
            return
        await interaction.response.defer()
        try: await interaction.message.delete()
        except: pass
        
        game = games[self.channel_id]
        day_num = game.get("day", 1)
        if "dead" not in game: game["dead"] = []
        if "pt" not in game: game["pt"] = {"c": 0, "x": 0}
        if "history" not in game: game["history"] = {}

        results = {}
        for p in game["players"]:
            inp = game["inputs"].get(p, {})
            if p in game["dead"]: results[p] = {"dest": "ghost", "card": "-", "ghost_target": inp.get("target", "none")}
            else: results[p] = {"dest": inp.get("dest", "lounge"), "card": inp.get("card", "c"), "ghost_target": "none"}

        for p, data in results.items():
            if p in game["dead"] and data["ghost_target"] != "none":
                target_id = int(data["ghost_target"])
                target_user = discord.utils.get(game["players"], id=target_id)
                if target_user and target_user not in game["dead"]:
                    results[target_user]["dest"] = "lounge_overwrite"

        # ── セイレーンの呼び寄せ（亡霊の上書き後・display付与前・攻撃判定前）──
        game.setdefault("sirened", [])  # 過去に呼び寄せ成功した相手のid（対象1人につき1回）
        sirened_today = set()
        for siren_p, m_target in game.get("mermaids", {}).items():
            if m_target == "none": continue
            if game["roles"].get(siren_p) != "siren": continue
            if siren_p in game["dead"]: continue
            # セイレーン自身の「最終的な行先」が操舵室のときのみ発動
            # （亡霊に談話室へ上書きされていれば results は lounge_overwrite になり発動しない）
            if results[siren_p]["dest"] != "bridge": continue
            target_user = discord.utils.get(game["players"], id=int(m_target))
            if not target_user or target_user in game["dead"]: continue
            if target_user == siren_p: continue                     # 自分自身は不可
            if game["roles"].get(target_user) == "siren": continue  # セイレーン同士は呼び寄せ不可
            if target_user.id in game["sirened"]: continue          # 対象1人1回
            if target_user in sirened_today: continue               # 同ターン重複不可
            # 亡霊の上書きが優先：その対象が亡霊に上書きされていたら発動せず、回数も消費しない
            if results[target_user]["dest"] == "lounge_overwrite": continue
            # 発動：操舵室＋〇に上書き
            results[target_user]["dest"] = "bridge"
            results[target_user]["card"] = "c"
            sirened_today.add(target_user)
            game["sirened"].append(target_user.id)

        for p, data in results.items():
            if p in game["dead"]:
                if data["ghost_target"] == "none":
                    if game.get("rules", {}).get("ghost", True):
                        data["display"] = t(g_lang, "display", "ghost_no_block")
                    else:
                        data["display"] = t(g_lang, "display", "ghost_no_exist")
                    data["history_emoji"] = "➖"
                else:
                    tgt_user = discord.utils.get(game["players"], id=int(data["ghost_target"]))
                    tgt_name = tgt_user.display_name[:4] if tgt_user else "Unknown"
                    data["display"] = t(g_lang, "display", "ghost_blocked", target=tgt_name)
                    data["history_emoji"] = "👻"
            else:
                d_val = data["dest"]
                if d_val == "bridge":
                    data["display"] = t(g_lang, "display", "dest_bridge")
                    data["history_emoji"] = "🟦"
                elif d_val == "library":
                    data["display"] = t(g_lang, "display", "dest_library")
                    data["history_emoji"] = "🟩"
                    if p.id not in game.setdefault("used_library", []):
                        game["used_library"].append(p.id)
                elif d_val == "lounge":
                    data["display"] = t(g_lang, "display", "dest_lounge")
                    data["history_emoji"] = "🟥"
                elif d_val == "lounge_overwrite":
                    data["display"] = t(g_lang, "display", "dest_lounge_ow")
                    data["history_emoji"] = "⛔"

        # セイレーンに呼び寄せられた人は🧜で明示（攻撃で死亡すれば後段の💀で上書きされる）
        for target_user in sirened_today:
            results[target_user]["display"] = t(g_lang, "display", "siren_dragged")
            results[target_user]["history_emoji"] = "🧜"

        new_dead = []
        attack_targets = []   # AIの推理用：その日、攻撃の標的にされた人（公開情報）
        for p_charon, target_id in game.get("attacks", {}).items():
            if target_id != "none" and game["roles"][p_charon] == "charon" and p_charon not in game["dead"] and p_charon not in new_dead:
                # 攻撃可否は「最終的な行先」で判定する。
                # ・セイレーンに操舵室へ呼び寄せられたカロンは談話室にいないので攻撃は発生しない（#3）
                # ・亡霊に談話室へ上書きされたカロンは談話室にいる扱いになり攻撃できる（#5, lounge_overwrite）
                charon_final_dest = results[p_charon]["dest"]
                if charon_final_dest in ("lounge", "lounge_overwrite"):
                    t_user = discord.utils.get(game["players"], id=int(target_id))
                    if t_user and t_user not in game["dead"] and t_user not in new_dead:
                        attack_targets.append(t_user)
                        t_dest = results[t_user]["dest"]
                        if t_dest in ["lounge", "lounge_overwrite"]:
                            tgt_name = t_user.display_name[:2]
                            results[p_charon]["display"] = t(g_lang, "display", "attack_self_destruct", target=tgt_name)
                            results[p_charon]["history_emoji"] = "💀"
                            new_dead.append(p_charon)
                        else:
                            base_dest = t(g_lang, "dests", t_dest)
                            if t_user in sirened_today:
                                results[t_user]["display"] = t(g_lang, "display", "attack_killed_siren", dest=base_dest)
                            else:
                                results[t_user]["display"] = t(g_lang, "display", "attack_killed", dest=base_dest)
                            results[t_user]["history_emoji"] = "💀"
                            new_dead.append(t_user)

        game["dead"].extend(new_dead)
        game["dead"] = list(set(game["dead"]))
        game["day_results"] = results 

        lib_users = [p for p, data in results.items() if data["dest"] == "library" and p not in new_dead]
        valid_library_user = lib_users[0] if len(lib_users) == 1 else None
        game["library_used_today"] = False

        souda_members = [p for p, d in results.items() if d["dest"] == "bridge" and p not in new_dead and p not in game.get("dead_before_today", [])]
        
        c_count = sum(1 for p in souda_members if results[p]["card"] == "c")
        x_count = sum(1 for p in souda_members if results[p]["card"] == "x")

        need = game["settings"]["need"]
        add_c = add_x = 0
        
        shortage = False
        if len(souda_members) == 0 or len(souda_members) < need:
            shortage = True
            add_x = 1
            pt_text = "✕1pt"
            c_count_text = t(g_lang, "display", "shortage")
        else:
            if x_count > 0:
                add_x = x_count
                pt_text = f"✕{x_count}pt"
            else:
                add_c = c_count
                pt_text = f"〇{c_count}pt"
            c_count_text = f"〇{c_count}：✕{x_count}"

        game["pt"]["c"] += add_c
        game["pt"]["x"] += add_x
        game["dead_before_today"] = list(game["dead"])

        game["history"][day_num] = {
            "players": {p: results[p]["history_emoji"] for p in game["players"]},
            "c": c_count,
            "x": x_count,
            "shortage": shortage
        }

        # ---- AIの信念状態を、今日の公開情報で更新する ----
        # ★これがAIの「学習」。ここが無いとAIは初日のまま何も学ばない。
        #   AIが1人もいなければ record_day_results は何もしない。
        if game.get("ai_state"):
            try:
                ai_brain.record_day_results(
                    game, results, c_count, x_count,
                    charmed=sirened_today, newly_dead=new_dead,
                    attack_targets=attack_targets,
                )
            except Exception:
                logger.error(f"❌ AIの信念状態の更新に失敗: {traceback.format_exc()}")

        embed = discord.Embed(title=t(g_lang, "msg", "day_result_title", day=day_num), color=0xE6C229)
        embed.description = t(g_lang, "msg", "day_result_desc")
        
        total_players = len(game["players"])
        
        detail_text = ""
        for idx, p in enumerate(game["players"]):
            emoji = get_player_number_emoji(idx, total_players)
            disp_name = p.display_name
            if g_lang == "ja":
                disp_name = pad_display_name(disp_name, 6)
            detail_text += f"{emoji} {disp_name}│{results[p]['display']}\n"
        detail_text += f"〇✕│{c_count_text}\n"
        detail_text += f" PT│{pt_text}\n"
        embed.add_field(name=t(g_lang, "msg", "field_action_detail"), value=detail_text, inline=False)

        line_history = "━━━━━━━━━━━━━━━\n"
        history_text = f"```text\n{line_history}"
        for idx, p in enumerate(game["players"]):
            emoji = get_player_number_emoji(idx, total_players)
            history_text += f"{emoji} │"
            for d in range(1, day_num + 1):
                history_text += game["history"][d]["players"][p]
            history_text += "\n"
        history_text += line_history
        
        label_c = "⭕"
        label_x = "❌"
            
        history_text += f"{label_c} │"
        for d in range(1, day_num + 1):
            if game["history"][d]["shortage"]: history_text += "➖"
            else: history_text += to_emoji_num(game["history"][d]["c"])
        history_text += "\n"
        
        history_text += f"{label_x} │"
        for d in range(1, day_num + 1):
            if game["history"][d]["shortage"]: history_text += "➖"
            else: history_text += to_emoji_num(game["history"][d]["x"])
        history_text += f"\n{line_history}```\n"
        embed.add_field(name=t(g_lang, "msg", "field_history"), value=history_text, inline=False)

        c_bar = make_progress_bar(game['pt']['c'], game['settings']['win_c'])
        x_bar = make_progress_bar(game['pt']['x'], game['settings']['win_x'])
        pt_text_str = f"〇: {c_bar} ({game['pt']['c']} / {game['settings']['win_c']} pt)\n"
        pt_text_str += f"✕: {x_bar} ({game['pt']['x']} / {game['settings']['win_x']} pt)\n"
        embed.add_field(name=t(g_lang, "msg", "field_points"), value=pt_text_str, inline=False)

        if game.get("attack_msg"): await game["attack_msg"].edit(view=None)
        lib_view = LibraryAndNextView(self.channel_id, self.host, valid_library_user)
        await interaction.channel.send(content=get_mentions(game), embed=embed, view=lib_view)

        # ---- 図書室に入れたのがAIなら、その場で役職を確認させる ----
        # ★人間の場合はボタンを押して自分だけに結果が表示される。
        #   AIは押せないので、ここで代わりに済ませる。
        #   結果は「確認した本人の視点」にだけ入る私的情報。
        #   何を見たかを他人に話すのは、ひとり言（ステップ5）の担当。
        if valid_library_user is not None and ai_player.is_ai(valid_library_user) \
                and game.get("ai_state") and not game.get("library_used_today"):
            try:
                tgt = ai_brain.pick_library_target(game, valid_library_user)
                if tgt is not None:
                    game["library_used_today"] = True
                    ai_brain.record_library_result(
                        game, valid_library_user, tgt, game["roles"][tgt])
                    # 何と報告するか（嘘をつくかも含めて）はここで決まる。
                    # 実際に喋るのは夜の議論の冒頭なので、取っておく。
                    speech = ai_brain.library_report(
                        game, valid_library_user, tgt, game["roles"][tgt])
                    game["ai_library_report"] = (
                        (valid_library_user, speech) if speech else None)
                    logger.info(
                        f"📖 AI {valid_library_user.display_name} が "
                        f"{tgt.display_name} の役職を確認した")
            except Exception:
                logger.error(f"❌ AIの図書室確認に失敗: {traceback.format_exc()}")

async def transition_to_night_phase(channel, game):
    game["votes"] = {}
    game["phase_voteresult_started"] = False
    game["vote_start"] = time.time()
    await update_vote_message(channel, game, is_first=True)

    # ---- 夜の議論と、AIの投票 ----
    if game.get("ai_state"):
        # 図書室に入ったAIの報告が口火を切り、そのあと各自がコメントする
        report = game.pop("ai_library_report", None)
        game["ai_talk_task"] = asyncio.create_task(
            run_ai_night_talk(channel, game, report=report))
        game["ai_task"] = asyncio.create_task(run_ai_votes(channel, game))

async def update_vote_message(channel, game, is_first=False):
    g_lang = get_game_lang(channel.id)
    alive_players = [p for p in game["players"] if p not in game.get("dead", [])]
    submitted_votes = sorted(game["votes"].items(), key=lambda x: x[1].get("rank_num", 999))
    
    lines = []
    for p, data in submitted_votes:
        target_id = data["target"]
        if target_id == "none": target_name = t(g_lang, "ui", "opt_abstain")
        else:
            tgt_user = discord.utils.get(game["players"], id=int(target_id))
            target_name = tgt_user.display_name if tgt_user else "Unknown"
        lines.append(f"{data['rank']} {p.display_name} → {target_name} ({data['time']})")
        
    embed = discord.Embed(color=0x191970)
    
    if "vote_end_time" in game:
        elapsed_sec = int(game["vote_end_time"] - game["vote_start"])
        time_str = f"{elapsed_sec // 60}m{elapsed_sec % 60}s"
        embed.title = t(g_lang, "msg", "vote_night_desc_end", time=time_str)
        desc = "\n".join(lines) if lines else ""
        embed.description = desc
        content_str = ""  
    else:
        embed.title = t(g_lang, "msg", "vote_night_title")
        lines_str = "\n".join(lines) if lines else "(None)"
        desc = t(g_lang, "msg", "vote_night_desc_active", start_time=int(game['vote_start']), current=len(game['votes']), total=len(alive_players), lines=lines_str)
        embed.description = desc
        content_str = get_mentions(game) 
    
    if is_first:
        view = VoteTriggerView(channel.id, game["host"])
        game["vote_msg"] = await channel.send(content=content_str, embed=embed, view=view)
    else:
        if game.get("vote_msg"):
            try: await game["vote_msg"].edit(content=content_str, embed=embed)
            except: pass

class VoteTriggerView(discord.ui.View):
    def __init__(self, channel_id, host):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.host = host
        g_lang = get_game_lang(channel_id)
        self.trigger_button.label = t(g_lang, "ui", "btn_vote_exile")
        self.force_next_button.label = t(g_lang, "ui", "btn_force_vote")

    @discord.ui.button(style=discord.ButtonStyle.success, custom_id="btn_vote_trig")
    async def trigger_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        update_last_active(self.channel_id)
        game = games.get(self.channel_id)
        u_lang = get_user_lang(interaction)
        if not game or interaction.user not in game["players"]: return
        if interaction.user in game.get("dead", []):
            await interaction.response.send_message(t(u_lang, "msg", "err_ghost_no_vote"), ephemeral=True)
            return
            
        if "votes" in game and interaction.user in game["votes"]: return
        view = VoteInputView(self.channel_id, u_lang)
        await interaction.response.send_message(t(u_lang, "ui", "select_target_vote"), view=view, ephemeral=True)

    @discord.ui.button(style=discord.ButtonStyle.primary, custom_id="btn_vote_force")
    async def force_next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        update_last_active(self.channel_id)
        game = games.get(self.channel_id)
        u_lang = get_user_lang(interaction)
        if interaction.user != self.host:
            await interaction.response.send_message(t(u_lang, "msg", "err_host_only"), ephemeral=True)
            return
            
        if not game:
            await interaction.response.send_message(t(u_lang, "msg", "err_no_game"), ephemeral=True)
            return
        if "votes" not in game: game["votes"] = {}
        
        alive_players = [p for p in game["players"] if p not in game.get("dead", [])]
        for p in alive_players:
            if p not in game["votes"]:
                game["votes"][p] = {"target": "none", "rank": "Forced", "rank_num": 999, "time": "-"}
                
        game["vote_end_time"] = time.time()
        await interaction.response.defer()
        await update_vote_message(interaction.channel, game)
        if game.get("vote_msg"): await game["vote_msg"].edit(view=None)
        
        if not game.get("phase_voteresult_started"):
            game["phase_voteresult_started"] = True
            g_lang = get_game_lang(self.channel_id)
            view = VoteResultRevealView(self.channel_id, game["host"])
            await interaction.channel.send(content=t(g_lang, "msg", "vote_forced"), view=view)

class VoteInputView(discord.ui.View):
    def __init__(self, channel_id, user_lang):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.user_lang = user_lang
        game = games[channel_id]
        
        options = [discord.SelectOption(label=t(user_lang, "ui", "opt_abstain"), value="none")]
        for p in game["players"]:
            if p not in game.get("dead", []):
                options.append(discord.SelectOption(label=p.display_name, value=str(p.id)))
                
        self.target_select = discord.ui.Select(placeholder=t(user_lang, "ui", "select_target_vote"), options=options)
        self.target_select.callback = self.dummy_callback
        self.add_item(self.target_select)
        self.submit_button.label = t(user_lang, "ui", "btn_submit_vote")

    async def dummy_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

    @discord.ui.button(style=discord.ButtonStyle.success, row=1, custom_id="btn_submit_vote")
    async def submit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        update_last_active(self.channel_id)
        game = games.get(self.channel_id)
        if not game or interaction.user not in game["players"]: return
        if "votes" not in game: game["votes"] = {}
        if interaction.user in game["votes"]: return
        if not self.target_select.values: return

        target_id = self.target_select.values[0]
        
        elapsed_sec = int(time.time() - game["vote_start"])
        time_str = f"{elapsed_sec // 60}m{elapsed_sec % 60}s"
        rank = len(game["votes"]) + 1
        rank_str = f"{rank}st" if rank == 1 else f"{rank}nd" if rank == 2 else f"{rank}rd" if rank == 3 else f"{rank}th"

        game["votes"][interaction.user] = {"target": target_id, "rank": rank_str, "rank_num": rank, "time": time_str}

        if target_id == "none": 
            await interaction.response.edit_message(content=t(self.user_lang, "msg", "vote_submit_abstain"), view=None)
        else:
            tgt_user = discord.utils.get(game["players"], id=int(target_id))
            tgt_name = tgt_user.display_name if tgt_user else 'Unknown'
            await interaction.response.edit_message(content=t(self.user_lang, "msg", "vote_submit_target", target=tgt_name), view=None)

        alive_players = [p for p in game["players"] if p not in game.get("dead", [])]
        if len(game["votes"]) == len(alive_players) and not game.get("phase_voteresult_started"):
            game["phase_voteresult_started"] = True
            game["vote_end_time"] = time.time()
            await update_vote_message(interaction.channel, game)
            if game.get("vote_msg"): await game["vote_msg"].edit(view=None)
            
            g_lang = get_game_lang(self.channel_id)
            view = VoteResultRevealView(self.channel_id, game["host"])
            await interaction.channel.send(content=t(g_lang, "msg", "vote_all_done"), view=view)
        else:
            await update_vote_message(interaction.channel, game)

class VoteResultRevealView(discord.ui.View):
    def __init__(self, channel_id, host):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.host = host
        g_lang = get_game_lang(channel_id)
        self.reveal_button.label = t(g_lang, "ui", "btn_reveal_vote")

    @discord.ui.button(style=discord.ButtonStyle.danger, custom_id="btn_reveal_vote_res")
    async def reveal_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        update_last_active(self.channel_id)
        u_lang = get_user_lang(interaction)
        g_lang = get_game_lang(self.channel_id)
        
        if interaction.user != self.host:
            await interaction.response.send_message(t(u_lang, "msg", "err_host_only"), ephemeral=True)
            return
        await interaction.response.defer()
        try: await interaction.message.delete()
        except: pass
        
        game = games[self.channel_id]
        alive_players = [p for p in game["players"] if p not in game.get("dead", [])]
        
        vote_counts = {}
        for p, data in game["votes"].items():
            t_id = data["target"]
            if t_id != "none":
                vote_counts[t_id] = vote_counts.get(t_id, 0) + 1

        exiled_user = None
        if vote_counts:
            max_votes = max(vote_counts.values())
            candidates = [t_id for t_id, v in vote_counts.items() if v == max_votes]
            required_votes = (len(alive_players) + 1) // 2 
            
            if len(candidates) == 1 and max_votes >= required_votes:
                exiled_user = discord.utils.get(game["players"], id=int(candidates[0]))

        exile_embed = discord.Embed(color=0x000000)
        if exiled_user:
            exile_embed.description = t(g_lang, "msg", "exile_result", target=exiled_user.display_name)
            if exiled_user not in game["dead"]:
                game["dead"].append(exiled_user)
        else:
            exile_embed.description = t(g_lang, "msg", "exile_none")

        # ---- 誰が誰に入れたか・誰が追放されたかを、翌朝の話題用に記録 ----
        if game.get("ai_state"):
            try:
                ai_brain.record_vote_results(game, exiled_user)
            except Exception:
                logger.error(f"❌ 投票結果の記録に失敗: {traceback.format_exc()}")

        await interaction.channel.send(embed=exile_embed)
        await asyncio.sleep(1)

        # 勝敗を先に確定（送信副作用なし。勝利画像の出し分けに使う）
        new_alive_players = [p for p in game["players"] if p not in game["dead"]]
        alive_charons = sum(1 for p in new_alive_players if game["roles"][p] == "charon")

        win_msg = ""
        reason_msg = ""
        game_over = False
        embed_color = 0x808080
        winner = None  # "human" / "charon"

        if alive_charons == 0:
            win_msg = t(g_lang, "msg", "win_human")
            reason_msg = t(g_lang, "msg", "win_reason_charon_dead")
            game_over = True
            embed_color = 0x00BFFF
            winner = "human"
        elif alive_charons * 2 >= len(new_alive_players):
            win_msg = t(g_lang, "msg", "win_charon")
            reason_msg = t(g_lang, "msg", "win_reason_charon_half")
            game_over = True
            embed_color = 0xDC143C
            winner = "charon"
        else:
            if game["pt"]["c"] >= game["settings"]["win_c"]:
                win_msg = t(g_lang, "msg", "win_human")
                reason_msg = t(g_lang, "msg", "win_reason_c_pt", pt=game['settings']['win_c'])
                game_over = True
                embed_color = 0x00BFFF
                winner = "human"
            elif game["pt"]["x"] >= game["settings"]["win_x"]:
                win_msg = t(g_lang, "msg", "win_charon")
                reason_msg = t(g_lang, "msg", "win_reason_x_pt", pt=game['settings']['win_x'])
                game_over = True
                embed_color = 0xDC143C
                winner = "charon"
            else:
                win_msg = t(g_lang, "msg", "win_not_yet")

        # 演出：「幽霊客船の運命は……／行きつく先は、生か死か……？」
        tension_msg = await interaction.channel.send(t(g_lang, "msg", "tension_1"))
        await asyncio.sleep(2)
        await tension_msg.edit(content=t(g_lang, "msg", "tension_2"))
        await asyncio.sleep(2)
        await tension_msg.delete()

        # ゲーム終了時は、結果embedの前に勝利画像をバーンと表示
        if game_over and winner:
            victory_img = f"victory_{winner}.jpg"
            if os.path.exists(victory_img):
                try:
                    await interaction.channel.send(file=discord.File(victory_img, filename=victory_img))
                    await asyncio.sleep(1)
                except Exception:
                    pass


        c_bar = make_progress_bar(game['pt']['c'], game['settings']['win_c'])
        x_bar = make_progress_bar(game['pt']['x'], game['settings']['win_x'])
        
        if game_over:
            embed_title = f"✨🎉 {win_msg} 🎉✨"
        else:
            embed_title = f"🚢 {win_msg}"

        desc = ""
        if reason_msg: desc += f"**[{reason_msg}]**\n\n"
        desc += f"O: {c_bar} ({game['pt']['c']} / {game['settings']['win_c']} pt)\n"
        desc += f"X: {x_bar} ({game['pt']['x']} / {game['settings']['win_x']} pt)\n"
        
        if game_over:
            total_players = len(game["players"])
            reveal_lines = []
            for idx, p in enumerate(game["players"]):
                emoji = get_player_number_emoji(idx, total_players)
                role_name = t(g_lang, "roles", game["roles"][p])
                skull = " 💀" if p in game["dead"] else ""
                reveal_lines.append(t(g_lang, "msg", "role_reveal_line", emoji=emoji, name=p.display_name, role=role_name, skull=skull))
            desc += "\n" + "\n".join(reveal_lines) + "\n"
        
        embed = discord.Embed(title=embed_title, description=desc, color=embed_color)

        if game_over:
            res_msg = await interaction.channel.send(content=get_mentions(game), embed=embed)
            await res_msg.add_reaction("🎉")
            await res_msg.add_reaction("🎊")
            await res_msg.add_reaction("✨")
            await interaction.channel.send(t(g_lang, "msg", "game_over"))
            shop_url = "https://booth.pm/ja/items/8438970"
            shop_msg = t(g_lang, "msg", "shop_ad", shop_url=shop_url)
            shop_embed = discord.Embed(description=shop_msg, color=0xFF7F50)
            await interaction.channel.send(embed=shop_embed)
            if self.channel_id in games:
                del games[self.channel_id]
        else:
            view = NextDayView(self.channel_id, self.host)
            await interaction.channel.send(content=get_mentions(game), embed=embed, view=view)

class NextDayView(discord.ui.View):
    def __init__(self, channel_id, host):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.host = host
        g_lang = get_game_lang(channel_id)
        self.next_day_button.label = t(g_lang, "ui", "btn_next_day")

    @discord.ui.button(style=discord.ButtonStyle.primary, custom_id="btn_next_day")
    async def next_day_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        update_last_active(self.channel_id)
        u_lang = get_user_lang(interaction)
        g_lang = get_game_lang(self.channel_id)
        if interaction.user != self.host:
            await interaction.response.send_message(t(u_lang, "msg", "err_host_only"), ephemeral=True)
            return
        await interaction.response.defer()
        await interaction.message.edit(content="", view=None)
        
        game = games[self.channel_id]
        game["day"] = game.get("day", 1) + 1
        game["day_start"] = time.time()
        game.pop("day_end_time", None)
        game.pop("vote_end_time", None)
        game["inputs"] = {}
        game["attacks"] = {}
        game["mermaids"] = {}
        game["votes"] = {}
        game["phase_charon_started"] = False
        game["phase_result_started"] = False
        game["phase_voteresult_started"] = False
        
        game["blocked_yesterday"] = []
        for data in game.get("day_results", {}).values():
            if data["dest"] == "ghost" and data["ghost_target"] != "none":
                game["blocked_yesterday"].append(int(data["ghost_target"]))

        if not game.get("rules", {}).get("ghost", True):
            for p in game.get("dead", []):
                game["inputs"][p] = {"type": "ghost", "target": "none", "rank": "Dead", "rank_num": 999, "time": "-"}

        day_num = game["day"]
        embed = discord.Embed(title=t(g_lang, "msg", "day_start_title", day=day_num), color=0x4682B4)
        
        alive_players = [p for p in game["players"] if p not in game.get("dead", [])]
        active_count = len(game['players']) if game.get("rules", {}).get("ghost", True) else len(alive_players)
        
        desc = t(g_lang, "msg", "day_start_desc", start_time=int(game['day_start']), current=0, total=active_count, lines="(None)")
        embed.description = desc
        
        view = TriggerInputView(self.channel_id, game["host"])
        game["main_msg"] = await interaction.channel.send(content=get_mentions(game), embed=embed, view=view)

        # ---- 新しい日の行動をAIに始めさせる ----
        if game.get("ai_state"):
            game["ai_task"] = asyncio.create_task(run_ai_actions(interaction.channel, game))

async def update_attack_status_message(channel, game):
    g_lang = get_game_lang(channel.id)
    alive_players = [p for p in game["players"] if p not in game.get("dead", [])]
    is_finished = len(game.get("attacks", {})) == len(alive_players)
    embed = discord.Embed(color=0x8B0000)
    siren = has_siren(game)
    
    if is_finished:
        embed.title = t(g_lang, "msg", "attack_end_title_siren" if siren else "attack_end_title")
        embed.description = ""
        content_str = ""  
    else:
        embed.title = t(g_lang, "msg", "attack_status_title_siren" if siren else "attack_status_title")
        embed.description = t(g_lang, "msg", "attack_status_desc_siren" if siren else "attack_status_desc", current=len(game.get('attacks', {})), total=len(alive_players))
        content_str = " ".join([p.mention for p in alive_players])
    
    if game.get("attack_status_msg"):
        try: await game["attack_status_msg"].edit(content=content_str, embed=embed)
        except: pass

class AttackInputView(discord.ui.View):
    def __init__(self, channel_id, user, user_lang):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.user_lang = user_lang
        game = games[channel_id]
        siren = has_siren(game)
        no_label = t(user_lang, "ui", "opt_no_attack_siren" if siren else "opt_no_attack")
        options = [discord.SelectOption(label=no_label, value="none")]
        
        for p in game["players"]:
            if p not in game.get("dead", []):
                if game["roles"].get(user) == "charon" and game["roles"].get(p) == "charon":
                    continue
                if game["roles"].get(user) == "siren" and game["roles"].get(p) == "siren":
                    continue  # セイレーンはセイレーン(自分含む)を妨害対象にできない
                options.append(discord.SelectOption(label=p.display_name, value=str(p.id)))
                
        if len(options) == 1:
            options = [discord.SelectOption(label=no_label, value="none")]

        sel_ph = t(user_lang, "ui", "select_target_attack_siren" if siren else "select_target_attack")
        self.target_select = discord.ui.Select(placeholder=sel_ph, options=options)
        self.target_select.callback = self.dummy_callback
        self.add_item(self.target_select)
        self.submit_button.label = t(user_lang, "ui", "btn_submit_attack")

    async def dummy_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

    @discord.ui.button(style=discord.ButtonStyle.success, row=1, custom_id="btn_sub_atk")
    async def submit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        update_last_active(self.channel_id)
        game = games.get(self.channel_id)
        if not game or interaction.user not in game["players"]: return
        if "attacks" not in game: game["attacks"] = {}
        if "mermaids" not in game: game["mermaids"] = {}
        if interaction.user in game["attacks"]: return
        if not self.target_select.values: return

        target_id = self.target_select.values[0]
        role = game["roles"][interaction.user]
        is_alive = interaction.user not in game.get("dead", [])
        # その日の自分の行先（カロンは談話室、セイレーンは操舵室でないと効力が出ない）。
        # 選べてしまう仕様はそのままに、提出メッセージで「効かない」ことだけ伝える。
        # （カロンは亡霊に談話室へ上書きされれば成立するので、断定せず条件だけを伝える）
        own_dest = game.get("inputs", {}).get(interaction.user, {}).get("dest")

        if role == "charon" and is_alive:
            game["attacks"][interaction.user] = target_id
            if target_id == "none":
                await interaction.response.edit_message(content=t(self.user_lang, "msg", "submit_attack_none"), view=None)
            else:
                t_user = discord.utils.get(game["players"], id=int(target_id))
                tgt_name = t_user.display_name if t_user else 'Unknown'
                content = t(self.user_lang, "msg", "submit_attack_target", target=tgt_name)
                if own_dest != "lounge":
                    content += t(self.user_lang, "msg", "submit_attack_cond_note")
                await interaction.response.edit_message(content=content, view=None)
        elif role == "siren" and is_alive:
            game["attacks"][interaction.user] = "none"   # 入力完了数のカウント用
            game["mermaids"][interaction.user] = target_id
            if target_id == "none":
                await interaction.response.edit_message(content=t(self.user_lang, "msg", "submit_mermaid_none"), view=None)
            else:
                t_user = discord.utils.get(game["players"], id=int(target_id))
                tgt_name = t_user.display_name if t_user else 'Unknown'
                content = t(self.user_lang, "msg", "submit_mermaid_target", target=tgt_name)
                if own_dest != "bridge":
                    content += t(self.user_lang, "msg", "submit_no_effect_note")
                await interaction.response.edit_message(content=content, view=None)
        else:
            game["attacks"][interaction.user] = "none"
            await interaction.response.edit_message(content=t(self.user_lang, "msg", "submit_attack_none"), view=None)

        await update_attack_status_message(interaction.channel, game)

        alive_players = [p for p in game["players"] if p not in game.get("dead", [])]
        if len(game["attacks"]) == len(alive_players) and not game.get("phase_result_started"):
            game["phase_result_started"] = True
            if game.get("attack_msg"): await game["attack_msg"].edit(view=None)
            view = ResultRevealView(self.channel_id, game["host"])
            await interaction.channel.send(view=view)

class TriggerAttackView(discord.ui.View):
    def __init__(self, channel_id, host):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.host = host
        g_lang = get_game_lang(channel_id)
        siren = has_siren(games.get(channel_id, {}))
        self.trigger_button.label = t(g_lang, "ui", "btn_input_attack_siren" if siren else "btn_input_attack")
        self.force_next_button.label = t(g_lang, "ui", "btn_force_attack_siren" if siren else "btn_force_attack")

    @discord.ui.button(style=discord.ButtonStyle.success, custom_id="btn_trig_atk")
    async def trigger_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        update_last_active(self.channel_id)
        game = games.get(self.channel_id)
        u_lang = get_user_lang(interaction)
        
        if not game or interaction.user not in game["players"]: return
        if "attacks" in game and interaction.user in game["attacks"]: return
        
        if interaction.user in game.get("dead", []):
            await interaction.response.send_message(t(u_lang, "msg", "err_ghost_no_attack"), ephemeral=True)
            return
            
        view = AttackInputView(self.channel_id, interaction.user, u_lang)
        msg = t(u_lang, "msg", "attack_prompt_siren" if has_siren(game) else "attack_prompt")
        await interaction.response.send_message(msg, view=view, ephemeral=True)

    @discord.ui.button(style=discord.ButtonStyle.primary, custom_id="btn_force_atk")
    async def force_next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        update_last_active(self.channel_id)
        u_lang = get_user_lang(interaction)
        if interaction.user != self.host:
            await interaction.response.send_message(t(u_lang, "msg", "err_host_only"), ephemeral=True)
            return
        game = games.get(self.channel_id)
        if not game:
            await interaction.response.send_message(t(u_lang, "msg", "err_no_game"), ephemeral=True)
            return
        if "attacks" not in game: game["attacks"] = {}
        
        alive_players = [p for p in game["players"] if p not in game.get("dead", [])]
        for p in alive_players:
            if p not in game["attacks"]: game["attacks"][p] = "none"
            
        await interaction.response.defer()
        await update_attack_status_message(interaction.channel, game)
        
        if game.get("attack_msg"): await game["attack_msg"].edit(view=None)
        if not game.get("phase_result_started"):
            game["phase_result_started"] = True
            view = ResultRevealView(self.channel_id, game["host"])
            await interaction.channel.send(view=view)

class CharonTimerActiveView(discord.ui.View):
    def __init__(self, channel_id, host):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.host = host
        g_lang = get_game_lang(channel_id)
        self.stop_button.label = t(g_lang, "ui", "btn_stop_charon")

    @discord.ui.button(style=discord.ButtonStyle.primary, custom_id="btn_stop_chr")
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        update_last_active(self.channel_id)
        u_lang = get_user_lang(interaction)
        if interaction.user != self.host:
            await interaction.response.send_message(t(u_lang, "msg", "err_host_only"), ephemeral=True)
            return
        await interaction.response.defer()
        game = games.get(self.channel_id)
        if game:
            game["charon_timer_active"] = False

class CharonTimerSetupView(discord.ui.View):
    def __init__(self, channel_id, host):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.host = host
        self.duration = 30 
        g_lang = get_game_lang(channel_id)
        
        options = [discord.SelectOption(label=f"{i}s", value=str(i)) for i in range(15, 181, 15)]
        self.time_select = discord.ui.Select(placeholder=t(g_lang, "ui", "select_charon_time", sec=30), options=options)
        self.time_select.callback = self.time_callback
        self.add_item(self.time_select)
        self.start_button.label = t(g_lang, "ui", "btn_start_charon")

    async def time_callback(self, interaction: discord.Interaction):
        update_last_active(interaction.channel_id)
        u_lang = get_user_lang(interaction)
        if interaction.user != self.host:
            await interaction.response.send_message(t(u_lang, "msg", "err_host_only"), ephemeral=True)
            return
        self.duration = int(interaction.data["values"][0])
        await interaction.response.defer()

    @discord.ui.button(style=discord.ButtonStyle.primary, row=1, custom_id="btn_start_chr")
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        update_last_active(interaction.channel_id)
        u_lang = get_user_lang(interaction)
        g_lang = get_game_lang(self.channel_id)
        if interaction.user != self.host:
            await interaction.response.send_message(t(u_lang, "msg", "err_host_only"), ephemeral=True)
            return
        
        try: await interaction.message.delete()
        except: pass

        game = games.get(self.channel_id)
        if not game:
            return
        game["phase_result_started"] = False
        game["charon_timer_active"] = True

        end_time = int(time.time()) + self.duration
        
        embed = discord.Embed(title=t(g_lang, "msg", "charon_timer_start_title"), color=0x4B0082)
        embed.description = t(g_lang, "msg", "charon_timer_start_desc", end_time=end_time)
        
        view = CharonTimerActiveView(self.channel_id, self.host)
        timer_msg = await interaction.channel.send(embed=embed, view=view)

        # ---- AIカロンの予定を、人間のカロンにDMで知らせる ----
        # 密談の頭に届かないと合わせようがないので、タイマーを待たずに送る。
        if game.get("ai_state"):
            await send_ai_charon_dm(interaction.channel, game)

        for _ in range(self.duration):
            if not game.get("charon_timer_active", False):
                break
            await asyncio.sleep(1)
            
        game["charon_timer_active"] = False

        embed.title = t(g_lang, "msg", "charon_timer_end_title", sec=self.duration)
        embed.description = t(g_lang, "msg", "charon_timer_end_desc")
        try: await timer_msg.edit(embed=embed, view=None)
        except: pass
        
        alive_players = [p for p in game["players"] if p not in game.get("dead", [])]
        
        siren = has_siren(game)
        attack_embed = discord.Embed(title=t(g_lang, "msg", "attack_status_title_siren" if siren else "attack_status_title"), color=0x8B0000)
        attack_embed.description = t(g_lang, "msg", "attack_status_desc_siren" if siren else "attack_status_desc", current=0, total=len(alive_players))
        
        attack_view = TriggerAttackView(self.channel_id, self.host)
        
        mentions = " ".join([p.mention for p in alive_players])
        game["attack_msg"] = await interaction.channel.send(content=mentions, embed=attack_embed, view=attack_view)
        game["attack_status_msg"] = game["attack_msg"]

        # ---- AIの襲撃／呼び寄せを提出させる ----
        if game.get("ai_state"):
            game["ai_task"] = asyncio.create_task(
                run_ai_attacks(interaction.channel, game))

async def transition_to_charon_phase(channel, game):
    if game.get("main_msg"): await game["main_msg"].edit(content="", view=None)
    g_lang = get_game_lang(channel.id)
    view = CharonTimerSetupView(channel.id, game["host"])
    embed = discord.Embed(title=t(g_lang, "msg", "charon_phase_title"), color=0x4B0082)
    embed.description = t(g_lang, "msg", "charon_phase_desc")
    await channel.send(content=get_mentions(game), embed=embed, view=view)

class GhostInputView(discord.ui.View):
    def __init__(self, channel_id, user_lang):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.user_lang = user_lang
        game = games[channel_id]
        options = [discord.SelectOption(label=t(user_lang, "ui", "opt_no_block"), value="none")]
        blocked = game.get("blocked_yesterday", [])
        
        for p in game["players"]:
            if p not in game.get("dead", []):
                if p.id not in blocked:
                    options.append(discord.SelectOption(label=p.display_name, value=str(p.id)))
                    
        self.target_select = discord.ui.Select(placeholder=t(user_lang, "ui", "select_target_ghost"), options=options)
        self.target_select.callback = self.dummy_callback
        self.add_item(self.target_select)
        self.submit_button.label = t(user_lang, "ui", "btn_submit_attack")

    async def dummy_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

    @discord.ui.button(style=discord.ButtonStyle.success, row=1, custom_id="btn_sub_gst")
    async def submit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        update_last_active(self.channel_id)
        game = games.get(self.channel_id)
        if not game or interaction.user not in game["players"]: return
        if interaction.user in game["inputs"]: return
        if not self.target_select.values: return
        
        target_id = self.target_select.values[0]
        elapsed_sec = int(time.time() - game["day_start"])
        time_str = f"{elapsed_sec // 60}m{elapsed_sec % 60}s"
        rank = len(game["inputs"]) + 1
        rank_str = f"{rank}st" if rank == 1 else f"{rank}nd" if rank == 2 else f"{rank}rd" if rank == 3 else f"{rank}th"

        game["inputs"][interaction.user] = {"type": "ghost", "target": target_id, "rank": rank_str, "rank_num": rank, "time": time_str}

        if target_id == "none": 
            await interaction.response.edit_message(content=t(self.user_lang, "msg", "submit_ghost_none"), view=None)
        else:
            t_user = discord.utils.get(game["players"], id=int(target_id))
            tgt_name = t_user.display_name if t_user else 'Unknown'
            await interaction.response.edit_message(content=t(self.user_lang, "msg", "submit_ghost_target", target=tgt_name), view=None)
            
        if len(game["inputs"]) == len(game["players"]) and not game.get("phase_charon_started"):
            game["phase_charon_started"] = True
            game["day_end_time"] = time.time()
            await update_main_message(interaction.channel, game)
            await transition_to_charon_phase(interaction.channel, game)
        else:
            await update_main_message(interaction.channel, game)

class ActionInputView(discord.ui.View):
    def __init__(self, channel_id, user_role_key, user_id, user_lang):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.user_role_key = user_role_key
        self.user_lang = user_lang
        game = games[channel_id]
        
        dest_options = []
        
        if user_role_key == "charon":
            dest_options.append(discord.SelectOption(label=t(user_lang, "ui", "opt_bridge_c"), value="bridge_c"))
            dest_options.append(discord.SelectOption(label=t(user_lang, "ui", "opt_bridge_x"), value="bridge_x"))
            if game.get("rules", {}).get("library", True) and user_id not in game.get("used_library", []):
                dest_options.append(discord.SelectOption(label=t(user_lang, "ui", "opt_library"), value="library"))
            dest_options.append(discord.SelectOption(label=t(user_lang, "ui", "opt_lounge_attack"), value="lounge"))
        elif user_role_key == "navigator":
            dest_options.append(discord.SelectOption(label=t(user_lang, "ui", "opt_bridge_c"), value="bridge_c"))
        elif user_role_key == "siren":
            dest_options.append(discord.SelectOption(label=t(user_lang, "ui", "opt_bridge_c_siren"), value="bridge_c"))
            if game.get("rules", {}).get("library", True) and user_id not in game.get("used_library", []):
                dest_options.append(discord.SelectOption(label=t(user_lang, "ui", "opt_library"), value="library"))
            dest_options.append(discord.SelectOption(label=t(user_lang, "ui", "opt_lounge"), value="lounge"))
        else:
            dest_options.append(discord.SelectOption(label=t(user_lang, "ui", "opt_bridge_c"), value="bridge_c"))
            if game.get("rules", {}).get("library", True) and user_id not in game.get("used_library", []):
                dest_options.append(discord.SelectOption(label=t(user_lang, "ui", "opt_library"), value="library"))
            dest_options.append(discord.SelectOption(label=t(user_lang, "ui", "opt_lounge"), value="lounge"))
            
        self.dest_select = discord.ui.Select(placeholder=t(user_lang, "ui", "select_dest_action"), options=dest_options)
        self.dest_select.callback = self.dummy_callback
        self.add_item(self.dest_select)
        self.submit_button.label = t(user_lang, "ui", "btn_submit_attack")

    async def dummy_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

    @discord.ui.button(style=discord.ButtonStyle.success, row=1, custom_id="btn_sub_act")
    async def submit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        update_last_active(self.channel_id)
        game = games.get(self.channel_id)
        if not game or interaction.user not in game["players"]: return
        if interaction.user in game["inputs"]: return
        if not self.dest_select.values: return

        val = self.dest_select.values[0]
        dest = ""
        card = "c"
        
        if val == "bridge_c":
            dest = "bridge"
            card = "c"
        elif val == "bridge_x":
            dest = "bridge"
            card = "x"
        elif val == "library":
            dest = "library"
        elif val == "lounge":
            dest = "lounge"

        elapsed_sec = int(time.time() - game["day_start"])
        time_str = f"{elapsed_sec // 60}m{elapsed_sec % 60}s"
        rank = len(game["inputs"]) + 1
        rank_str = f"{rank}st" if rank == 1 else f"{rank}nd" if rank == 2 else f"{rank}rd" if rank == 3 else f"{rank}th"

        game["inputs"][interaction.user] = {"type": "alive", "dest": dest, "card": card, "rank": rank_str, "rank_num": rank, "time": time_str}

        selected_label = self.dest_select.options[[opt.value for opt in self.dest_select.options].index(val)].label
        await interaction.response.edit_message(content=t(self.user_lang, "msg", "submit_action", dest=selected_label), view=None)
        
        if len(game["inputs"]) == len(game["players"]) and not game.get("phase_charon_started"):
            game["phase_charon_started"] = True
            game["day_end_time"] = time.time()
            await update_main_message(interaction.channel, game)
            await transition_to_charon_phase(interaction.channel, game)
        else:
            await update_main_message(interaction.channel, game)

async def update_main_message(channel, game):
    g_lang = get_game_lang(channel.id)
    day_num = game.get("day", 1)
    submitted_players = [(p, data) for p, data in game["inputs"].items() if data.get("rank") != "Dead"]
    submitted_players = sorted(submitted_players, key=lambda x: x[1].get("rank_num", 999))
    lines = []
    for p, data in submitted_players:
        if data.get("type") == "ghost":
            tgt = data.get("target", "none")
            if tgt == "none":
                lines.append(t(g_lang, "msg", "line_ghost_none", rank=data['rank'], name=p.display_name, time=data['time']))
            else:
                tgt_user = discord.utils.get(game["players"], id=int(tgt))
                tgt_name = tgt_user.display_name if tgt_user else "Unknown"
                lines.append(t(g_lang, "msg", "line_ghost_block", rank=data['rank'], name=p.display_name, time=data['time'], target=tgt_name))
        else:
            lines.append(f"{data['rank']} {p.display_name} ({data['time']})")
    
    embed = discord.Embed(color=0x4682B4)
    
    if "day_end_time" in game:
        elapsed_sec = int(game["day_end_time"] - game["day_start"])
        time_str = f"{elapsed_sec // 60}m{elapsed_sec % 60}s"
        embed.title = t(g_lang, "msg", "day_end_title", day=day_num, time=time_str)
        desc = "\n".join(lines) if lines else ""
        embed.description = desc
        content_str = ""  
    else:
        embed.title = t(g_lang, "msg", "day_start_title", day=day_num)
        alive_players = [p for p in game["players"] if p not in game.get("dead", [])]
        active_count = len(game['players']) if game.get("rules", {}).get("ghost", True) else len(alive_players)
        
        lines_str = "\n".join(lines) if lines else "(None)"
        desc = t(g_lang, "msg", "day_start_desc", start_time=int(game['day_start']), current=len(submitted_players), total=active_count, lines=lines_str)
        embed.description = desc
        content_str = get_mentions(game) 
    
    if game.get("main_msg"):
        try: await game["main_msg"].edit(content=content_str, embed=embed)
        except: pass

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🤖 AIプレイヤーの代打（ボタンを押す代わりに入力を埋める）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _rank_str(rank):
    """1st / 2nd / 3rd / 4th … の文字列（ActionInputView と同じ規則）"""
    if rank == 1: return "1st"
    if rank == 2: return "2nd"
    if rank == 3: return "3rd"
    return f"{rank}th"


async def run_ai_actions(channel, game):
    """
    その日の行先を、AIの代わりに提出する。

    ★なぜ一気に提出せず、時間差を付けるのか
      このゲームは「誰が何番目に、何分何秒で出したか」が全員に見える。
      人間はそこも読んで遊んでいる。AIが即答すると毎回上位を独占して
      しまい、順位という情報そのものが壊れる。
      なので ai_brain.AI_ACTION_DELAY_MIN〜MAX の範囲でばらけさせる。

    ★途中でゲームが終わったりリセットされたりしても大丈夫なように、
      1人提出するたびに「まだ同じゲームの同じ日か」を確認している。
    """
    day = game.get("day", 1)
    try:
        actions = ai_brain.decide_day_actions(game)
    except Exception:
        logger.error(f"❌ AIの行動決定に失敗: {traceback.format_exc()}")
        return

    # ★朝の議論は、行先が決まってからでないと作れない
    #   （「今日は図書室に行くつもり」などを話すため）。
    #   なので別タスクにせず、ここから始める。
    game["ai_talk_task"] = asyncio.create_task(run_ai_morning_talk(channel, game))

    targets = []
    for p in game["players"]:
        if not ai_player.is_ai(p):
            continue
        if p in game.get("inputs", {}):
            continue
        if p in game.get("dead", []):
            # 死んだAI（亡霊）は、誰かの行先を談話室へ引きずり出せる。
            try:
                blocked = ai_brain.decide_ghost_block(game, p)
            except Exception:
                logger.error(f"❌ 亡霊の判断に失敗: {traceback.format_exc()}")
                blocked = None
            targets.append((p, {"ghost_target": str(blocked.id) if blocked else "none"}))
        elif p in actions:
            targets.append((p, actions[p]))

    if not targets:
        return

    delays = sorted(random.uniform(ai_brain.AI_ACTION_DELAY_MIN,
                                   ai_brain.AI_ACTION_DELAY_MAX)
                    for _ in targets)
    random.shuffle(targets)

    waited = 0.0
    for (p, act), delay in zip(targets, delays):
        await asyncio.sleep(max(0.0, delay - waited))
        waited = delay

        g = games.get(channel.id)
        # ゲームが消えた・別のゲームになった・日が変わった・
        # ホストが「入力を終了」を押した → もう手を出さない
        if g is not game or g.get("day") != day or g.get("phase_charon_started"):
            return
        if p in g["inputs"]:
            continue

        elapsed = int(time.time() - g.get("day_start", time.time()))
        rank = len(g["inputs"]) + 1
        entry = {"rank": _rank_str(rank), "rank_num": rank,
                 "time": f"{elapsed // 60}m{elapsed % 60}s"}
        if "ghost_target" in act:
            entry.update({"type": "ghost", "target": act["ghost_target"]})
        else:
            entry.update({"type": "alive", "dest": act["dest"], "card": act["card"]})
        g["inputs"][p] = entry

        try:
            # 全員そろったら次のフェーズへ。
            # ★ActionInputView の submit_button と同じ判定。
            #   既存のボタンには手を入れず、同じことをここでも行う。
            if len(g["inputs"]) == len(g["players"]) and not g.get("phase_charon_started"):
                g["phase_charon_started"] = True
                g["day_end_time"] = time.time()
                await update_main_message(channel, g)
                await transition_to_charon_phase(channel, g)
                return
            await update_main_message(channel, g)
        except Exception:
            logger.error(f"❌ AIの入力反映に失敗: {traceback.format_exc()}")
            return


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🎭 AIの発言をwebhookで投稿する（見た目を人間のチャットに近づける）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# ★なぜwebhookを使うのか
#   普通の channel.send だと9人ぶんの発言が全部「botの1人の発言」として
#   まとまってしまい、名前は本文の中の太字にしかならない。
#   webhookは1メッセージごとに名前とアイコンを指定できる唯一の方法なので、
#   AI9人がそれぞれ別の人として喋っているように見える。
#
# ★権限が無いサーバーでも絶対に壊さないこと
#   webhookを作るには「ウェブフックの管理」権限が要るが、
#   すでに導入済みのサーバーには後から自動で権限が付かない。
#   そこで「使えるなら使う、駄目なら今まで通り channel.send」に倒す。
#   これで既存サーバーは何もしなくても従来どおり動く。

# チャンネルに作るwebhookの名前。すでに同じ名前のものがあれば作り直さない
# （webhookはチャンネルあたり15個までなので、毎ゲーム作ると枯渇する）。
AI_WEBHOOK_NAME = "GhostLiner AI"

# 人格ごとのアイコン画像URL。
#
# ★キーは ai_player.AI_NAME_POOL と同じ11個。
#   ai_brain の人格割り当てが「表示名＝人格」なので（ai_brain.py の
#   init_game 参照）、名前で引けばその人の人格のアイコンになる。
#   つまり「探偵AIはいつもこの顔」が毎ゲーム保たれる。
#
# ★空文字のままでも動く（Discordのデフォルトアイコンになるだけ）。
#   画像はどこかに置いた公開URLを貼る。手軽なのは、適当なチャンネルに
#   11枚アップロードして、その画像のURLをコピーする方法。
# ★画像を差し替えるときは、同じファイル名で上書きしないこと。
#   Discordはアイコンを画像URL単位でキャッシュするので、中身だけ変えても
#   古い絵のまま出続けることがある。差し替える時はファイル名ごと変える。
_AVATAR_BASE = ("https://raw.githubusercontent.com/"
                "naokiyoshida-utokyo/ghostliner-bot/main/avatars/")

AI_AVATAR_URLS = {
    "探偵AI":     _AVATAR_BASE + "tantei.png",
    "芸能人AI":   _AVATAR_BASE + "geinoujin.png",
    "美容師AI":   _AVATAR_BASE + "biyoushi.png",
    "商人AI":     _AVATAR_BASE + "syounin.png",
    "武道家AI":   _AVATAR_BASE + "butouka.png",
    "猟師AI":     _AVATAR_BASE + "ryoushi.png",
    "コックAI":   _AVATAR_BASE + "cook.png",
    "小説家AI":   _AVATAR_BASE + "syousetsuka.png",
    "大学生AI":   _AVATAR_BASE + "daigakusei.png",
    "ゲーマーAI": _AVATAR_BASE + "gamer.png",
    "遊び人AI":   _AVATAR_BASE + "asobinin.png",
}

# チャンネルID → webhook。False は「このチャンネルでは使えない（権限が無い）」。
# 毎回さがしに行くとAPIを無駄に叩くので、結果を覚えておく。
_ai_webhooks = {}


def ai_avatar_url(player):
    """
    そのAIのアイコン画像URLを返す（無ければ None）。

    ★名前プールを使い切ると「探偵AI2」のような名前になる。
      その時は末尾の数字を落として引く。ai_brain の人格の引き方と同じ規則に
      そろえてあるので、喋り方とアイコンがずれない。
    """
    name = getattr(player, "display_name", "")
    url = AI_AVATAR_URLS.get(name)
    if not url:
        url = AI_AVATAR_URLS.get(name.rstrip("0123456789"))
    return url or None


async def get_ai_webhook(channel):
    """
    そのチャンネルでAIの発言に使うwebhookを返す。使えなければ None。

    ★None が返るのは異常ではない。「ウェブフックの管理」権限が無いだけで、
      呼び出し側は今まで通り channel.send に落ちればよい。
    """
    cached = _ai_webhooks.get(channel.id)
    if cached is not None:
        return cached or None       # False（使えない）なら None を返す

    # DMなどwebhookを作れない場所。ゲームは通常のチャンネルで進むので
    # ここに来ることはまず無いが、念のため。
    if not hasattr(channel, "webhooks"):
        _ai_webhooks[channel.id] = False
        return None

    try:
        # --- すでに作ってあるものを探す ---
        # ★token が無いものは送信に使えないので除外する。
        #   （他のアプリが作ったwebhookはtokenが見えない）
        for w in await channel.webhooks():
            if w.name == AI_WEBHOOK_NAME and w.token:
                _ai_webhooks[channel.id] = w
                return w

        # --- 無ければ作る ---
        w = await channel.create_webhook(name=AI_WEBHOOK_NAME)
        _ai_webhooks[channel.id] = w
        return w

    except discord.Forbidden:
        # 権限が無い。これは想定内なので、エラーではなく情報として残す。
        logger.info(
            f"ℹ️ webhookを使えないチャンネル（ウェブフックの管理 権限なし）: "
            f"{channel.id} → 従来の表示で続行します"
        )
        _ai_webhooks[channel.id] = False
        return None
    except Exception:
        logger.error(f"❌ webhookの準備に失敗: {traceback.format_exc()}")
        _ai_webhooks[channel.id] = False
        return None


async def send_ai_line(channel, player, text, mark):
    """
    AIの発言を1つ投稿する。

    webhookが使えれば「その人の名前とアイコン」で投稿し、
    使えなければ従来どおり「マーク **名前**: 本文」で投稿する。

    ★mark は空文字でもよい（朝は空にしてある）。
      アイコンで発言者が分かるようになった今、全行の頭に付く💬は情報量が
      ゼロで、ただの繰り返し記号だった。夜の🌙と図書室報告の📖は
      「いつもと違う場面」の目印として効くので残している。
    """
    webhook = await get_ai_webhook(channel)

    if webhook is not None:
        try:
            await webhook.send(
                content=f"{mark} {text}".strip(),
                username=player.display_name,
                avatar_url=ai_avatar_url(player),
            )
            return True
        except discord.NotFound:
            # 誰かがwebhookを手で消した。覚えていた分を捨てて作り直させる。
            _ai_webhooks.pop(channel.id, None)
        except discord.Forbidden:
            # 途中で権限を外された。以後は従来の表示に落とす。
            _ai_webhooks[channel.id] = False
        except Exception:
            logger.error(f"❌ webhookでの発言送信に失敗: {traceback.format_exc()}")
            # ここでは諦めずに下の従来方式で送る（発言が消えるより良い）

    try:
        await channel.send(f"{mark} **{player.display_name}**: {text}".strip())
        return True
    except Exception:
        logger.error(f"❌ AIの発言の送信に失敗: {traceback.format_exc()}")
        return False


async def post_ai_speeches(channel, game, lines, gap_min, gap_max, mark):
    """
    AIの発言を、少しずつ間を空けてチャットに流す。

    ★一度にまとめて投稿すると、9人ぶんの発言が一瞬で流れて
      「議論」に見えない。人が読める速さで、順に出す。
    """
    day = game.get("day", 1)
    for p, line in lines:
        await asyncio.sleep(random.uniform(gap_min, gap_max))
        g = games.get(channel.id)
        if g is not game or g.get("day") != day:
            return          # ゲームが終わった/日が変わった → 途中でやめる
        if not await send_ai_line(channel, p, line, mark):
            return          # 送信に失敗した（送れない状態）→ 打ち切る


async def run_ai_morning_talk(channel, game):
    """朝の議論。AIが順にひとり言を投稿する。"""
    try:
        lines = ai_brain.morning_speeches(game)
    except Exception:
        logger.error(f"❌ 朝の発言の生成に失敗: {traceback.format_exc()}")
        return
    # ★朝はマーク無し。アイコンで誰の発言か分かるので、全行の頭に付く💬は
    #   繰り返し記号にしかならない（夜🌙・図書室報告📖は場面の目印として残す）。
    await post_ai_speeches(channel, game, lines,
                           ai_brain.TALK_GAP_MIN, ai_brain.TALK_GAP_MAX, "")


async def run_ai_night_talk(channel, game, report=None):
    """
    夜の議論。図書室の報告が口火を切り、そのあと各自がコメントする
    （ルールブック通りの順番）。
    """
    if report is not None:
        reporter, speech = report
        await send_ai_line(channel, reporter, speech, "📖")
    try:
        lines = ai_brain.night_speeches(game)
    except Exception:
        logger.error(f"❌ 夜の発言の生成に失敗: {traceback.format_exc()}")
        return
    # ★夜もマーク無し（朝と同じ理由。アイコンで発言者が分かるので🌙は
    #   繰り返し記号にしかならない）。図書室の報告📖だけは、その日1人しか
    #   喋らない特別な発言なので目印を残してある。
    await post_ai_speeches(channel, game, lines,
                           ai_brain.TALK_GAP_MIN, ai_brain.TALK_GAP_MAX, "")


async def send_ai_charon_dm(channel, game):
    """
    カロンの密談が始まったとき、人間のカロンにAIカロンの予定をDMで送る。

    ★なぜ必要か
      AIは人間の言葉を聞き取れない（LLMを使っていないため）。
      密談は本来「息を合わせる場」なのに、AIが相手だと一方通行になる。
      そこで、AI側の予定だけでも先に全部見せて、人間が合わせられるようにする。

    ★情報漏洩について
      送り先は「生きている人間のカロン」だけ。しかもカロン同士が
      互いを知っているルール（rules["charon"]）のときに限る。
      互いを知らない設定なら、これを送ると仲間が誰か分かってしまう。
    """
    if not game.get("ai_state"):
        return
    if not game.get("rules", {}).get("charon", True):
        return

    dead = game.get("dead", [])
    humans = [p for p in game["players"]
              if game["roles"].get(p) == "charon"
              and not ai_player.is_ai(p) and p not in dead]
    if not humans:
        return

    try:
        plans = ai_brain.charon_talk_plans(game)
    except Exception:
        logger.error(f"❌ AIカロンの予定の取得に失敗: {traceback.format_exc()}")
        return
    if not plans:
        return

    g_lang = get_game_lang(channel.id)
    day = game.get("day", 1)

    for human in humans:
        p_lang = USER_LANGS.get(human.id, g_lang)
        lines = [t(p_lang, "msg", "dm_ai_charon_header", day=day)]
        for ai, plan in plans.items():
            name = ai.display_name
            if plan["dest"] == "bridge":
                key = "dm_ai_charon_bridge_x" if plan["card"] == "x" else "dm_ai_charon_bridge_c"
                lines.append(t(p_lang, "msg", key, name=name))
            elif plan["dest"] == "library":
                lines.append(t(p_lang, "msg", "dm_ai_charon_library", name=name))
            elif plan["attack"] is not None:
                lines.append(t(p_lang, "msg", "dm_ai_charon_attack",
                               name=name, target=plan["attack"].display_name))
            else:
                lines.append(t(p_lang, "msg", "dm_ai_charon_no_attack", name=name))
        lines.append(t(p_lang, "msg", "dm_ai_charon_footer"))

        try:
            await human.send(content="\n".join(lines))
        except discord.Forbidden:
            logger.warning(f"⚠️ {human.display_name} にカロンの密談DMを送れませんでした")
        except Exception:
            logger.error(f"❌ カロンの密談DMの送信に失敗: {traceback.format_exc()}")


async def run_ai_attacks(channel, game):
    """
    カロンの襲撃・セイレーンの呼び寄せを、AIの代わりに提出する。

    ★このフェーズは「生きている人全員が入力し終わる」まで進まない。
      カロンでもセイレーンでもない人も「なし」を出す必要があるので、
      生きているAI全員ぶんを埋める。
    """
    day = game.get("day", 1)
    try:
        plans = ai_brain.decide_attacks(game)
    except Exception:
        logger.error(f"❌ AIの襲撃判断に失敗: {traceback.format_exc()}")
        return
    if not plans:
        return

    items = list(plans.items())
    random.shuffle(items)
    delays = sorted(random.uniform(ai_brain.AI_ATTACK_DELAY_MIN,
                                   ai_brain.AI_ATTACK_DELAY_MAX) for _ in items)

    waited = 0.0
    for (p, choice), delay in zip(items, delays):
        await asyncio.sleep(max(0.0, delay - waited))
        waited = delay

        g = games.get(channel.id)
        if g is not game or g.get("day") != day or g.get("phase_result_started"):
            return
        if p in g.get("attacks", {}) or p in g.get("dead", []):
            continue

        role = g["roles"].get(p)
        g.setdefault("attacks", {})
        g.setdefault("mermaids", {})
        if role == "charon" and choice["attack"] is not None:
            g["attacks"][p] = str(choice["attack"].id)
        elif role == "siren":
            # セイレーンは attacks に "none" を入れて入力済みとして数え、
            # 実際の呼び寄せ先は mermaids に入れる（人間の入力と同じ形）
            g["attacks"][p] = "none"
            g["mermaids"][p] = str(choice["charm"].id) if choice["charm"] else "none"
        else:
            g["attacks"][p] = "none"

        try:
            await update_attack_status_message(channel, g)
            alive_players = [q for q in g["players"] if q not in g.get("dead", [])]
            if len(g["attacks"]) == len(alive_players) and not g.get("phase_result_started"):
                g["phase_result_started"] = True
                if g.get("attack_msg"):
                    await g["attack_msg"].edit(view=None)
                await channel.send(view=ResultRevealView(channel.id, g["host"]))
                return
        except Exception:
            logger.error(f"❌ AIの襲撃入力の反映に失敗: {traceback.format_exc()}")
            return


async def run_ai_votes(channel, game):
    """夜の投票を、AIの代わりに提出する。"""
    day = game.get("day", 1)
    try:
        votes = ai_brain.decide_votes(game)
    except Exception:
        logger.error(f"❌ AIの投票判断に失敗: {traceback.format_exc()}")
        return
    if not votes:
        return

    items = list(votes.items())
    random.shuffle(items)
    delays = sorted(random.uniform(ai_brain.AI_VOTE_DELAY_MIN,
                                   ai_brain.AI_VOTE_DELAY_MAX) for _ in items)

    waited = 0.0
    for (p, target), delay in zip(items, delays):
        await asyncio.sleep(max(0.0, delay - waited))
        waited = delay

        g = games.get(channel.id)
        if g is not game or g.get("day") != day or g.get("phase_voteresult_started"):
            return
        if p in g.get("votes", {}) or p in g.get("dead", []):
            continue

        elapsed = int(time.time() - g.get("vote_start", time.time()))
        rank = len(g["votes"]) + 1
        g["votes"][p] = {"target": str(target.id) if target is not None else "none",
                         "rank": _rank_str(rank), "rank_num": rank,
                         "time": f"{elapsed // 60}m{elapsed % 60}s"}

        try:
            alive_players = [q for q in g["players"] if q not in g.get("dead", [])]
            if len(g["votes"]) == len(alive_players) and not g.get("phase_voteresult_started"):
                g["phase_voteresult_started"] = True
                g["vote_end_time"] = time.time()
                await update_vote_message(channel, g)
                if g.get("vote_msg"):
                    await g["vote_msg"].edit(view=None)
                g_lang = get_game_lang(channel.id)
                await channel.send(content=t(g_lang, "msg", "vote_all_done"),
                                   view=VoteResultRevealView(channel.id, g["host"]))
                return
            await update_vote_message(channel, g)
        except Exception:
            logger.error(f"❌ AIの投票の反映に失敗: {traceback.format_exc()}")
            return


class TriggerInputView(discord.ui.View):
    def __init__(self, channel_id, host):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.host = host
        g_lang = get_game_lang(channel_id)
        self.trigger_button.label = t(g_lang, "ui", "btn_input_action")
        self.force_next_button.label = t(g_lang, "ui", "btn_force_action")

    @discord.ui.button(style=discord.ButtonStyle.success, custom_id="btn_trig_act")
    async def trigger_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        update_last_active(self.channel_id)
        game = games.get(self.channel_id)
        u_lang = get_user_lang(interaction)
        
        if not game or interaction.user not in game["players"]: return
        if interaction.user in game["inputs"] and game["inputs"][interaction.user].get("rank") == "Dead":
            await interaction.response.send_message(t(u_lang, "msg", "err_ghost_off_dead"), ephemeral=True)
            return
        if interaction.user in game["inputs"]: return

        is_dead = interaction.user in game.get("dead", [])
        if is_dead:
            if game.get("rules", {}).get("ghost", True):
                view = GhostInputView(self.channel_id, u_lang)
                msg = t(u_lang, "msg", "ghost_prompt")
                await interaction.response.send_message(msg, view=view, ephemeral=True)
            else:
                await interaction.response.send_message(t(u_lang, "msg", "err_ghost_off_dead"), ephemeral=True)
        else:
            user_role_key = game["roles"][interaction.user]
            view = ActionInputView(self.channel_id, user_role_key, interaction.user.id, u_lang)
            
            c_pt = game.get("pt", {}).get("c", 0)
            x_pt = game.get("pt", {}).get("x", 0)
            win_c = game.get("settings", {}).get("win_c", 6)
            win_x = game.get("settings", {}).get("win_x", 2)
            c_bar = make_progress_bar(c_pt, win_c)
            x_bar = make_progress_bar(x_pt, win_x)
            
            role_name = t(u_lang, "roles", user_role_key)
            need = game.get("settings", {}).get("need", 2)
            msg = t(u_lang, "msg", "action_prompt_common", role=role_name, need=need, c_bar=c_bar, c_pt=c_pt, win_c=win_c, x_bar=x_bar, x_pt=x_pt, win_x=win_x)
                
            await interaction.response.send_message(msg, view=view, ephemeral=True)

    @discord.ui.button(style=discord.ButtonStyle.primary, custom_id="btn_force_act")
    async def force_next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        update_last_active(self.channel_id)
        u_lang = get_user_lang(interaction)
        if interaction.user != self.host:
            await interaction.response.send_message(t(u_lang, "msg", "err_host_only"), ephemeral=True)
            return
        game = games.get(self.channel_id)
        if not game:
            await interaction.response.send_message(t(u_lang, "msg", "err_no_game"), ephemeral=True)
            return
        for p in game["players"]:
            if p not in game["inputs"]:
                if p in game.get("dead", []): game["inputs"][p] = {"type": "ghost", "target": "none", "rank": "Forced", "rank_num": 999, "time": "-"}
                else: game["inputs"][p] = {"type": "alive", "dest": "lounge", "card": "c", "rank": "Forced", "rank_num": 999, "time": "-"}
        game["day_end_time"] = time.time()
        await interaction.response.defer()
        await update_main_message(interaction.channel, game)
        if not game.get("phase_charon_started"):
            game["phase_charon_started"] = True
            await transition_to_charon_phase(interaction.channel, game)

class GameSetupView(discord.ui.View):
    def __init__(self, host, players, counts, rules, lang, apply_prefs=False):
        super().__init__(timeout=None)
        self.host = host
        self.players = players
        self.counts = counts
        self.rules = rules
        self.lang = lang
        self.apply_prefs = apply_prefs
        
        n = len(players)
        if n <= 5: self.settings = {"need": 2, "win_c": 6, "win_x": 2}
        elif n == 6: self.settings = {"need": 2, "win_c": 8, "win_x": 2}
        elif n == 7 or n == 8: self.settings = {"need": 3, "win_c": 10, "win_x": 5}
        else: self.settings = {"need": 3, "win_c": 12, "win_x": 5}
        
        opt_need = [discord.SelectOption(label=f"{i}", value=str(i)) for i in range(1, 11)]
        opt_c = [discord.SelectOption(label=f"{i}pt", value=str(i)) for i in range(1, 26)]
        opt_x = [discord.SelectOption(label=f"{i}pt", value=str(i)) for i in range(1, 26)]

        self.sel_need = discord.ui.Select(placeholder=t(lang, "msg", "setup_need", n=self.settings['need']), options=opt_need, row=0)
        self.sel_c = discord.ui.Select(placeholder=t(lang, "msg", "setup_win_c", n=self.settings['win_c']), options=opt_c, row=1)
        self.sel_x = discord.ui.Select(placeholder=t(lang, "msg", "setup_win_x", n=self.settings['win_x']), options=opt_x, row=2)

        self.sel_need.callback = self.make_callback("need", "setup_need")
        self.sel_c.callback = self.make_callback("win_c", "setup_win_c")
        self.sel_x.callback = self.make_callback("win_x", "setup_win_x")

        self.add_item(self.sel_need)
        self.add_item(self.sel_c)
        self.add_item(self.sel_x)
        self.start_button.label = t(lang, "ui", "btn_start_game")

    def make_callback(self, key, placeholder_key):
        async def callback(interaction: discord.Interaction):
            update_last_active(interaction.channel_id)
            u_lang = get_user_lang(interaction)
            if interaction.user != self.host:
                await interaction.response.send_message(t(u_lang, "msg", "err_host_only"), ephemeral=True)
                return
            self.settings[key] = int(interaction.data["values"][0])
            
            if key == "need": self.sel_need.placeholder = t(self.lang, "msg", placeholder_key, n=self.settings[key])
            elif key == "win_c": self.sel_c.placeholder = t(self.lang, "msg", placeholder_key, n=self.settings[key])
            elif key == "win_x": self.sel_x.placeholder = t(self.lang, "msg", placeholder_key, n=self.settings[key])
            
            await interaction.response.edit_message(view=self)
        return callback

    @discord.ui.button(style=discord.ButtonStyle.primary, row=3, custom_id="btn_start_fin")
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        update_last_active(interaction.channel_id)
        u_lang = get_user_lang(interaction)
        if interaction.user != self.host:
            await interaction.response.send_message(t(u_lang, "msg", "err_host_only"), ephemeral=True)
            return
        await interaction.message.delete()
        await distribute_roles(interaction.channel, self.players, self.counts, self.rules, self.settings, self.apply_prefs)

class RuleSetupView(discord.ui.View):
    def __init__(self, host, players, counts, lang, apply_prefs=False):
        super().__init__(timeout=None)
        self.host = host
        self.players = players
        self.counts = counts
        self.lang = lang
        self.apply_prefs = apply_prefs
        n = len(players)
        use_lib = (n >= 8)
        use_ghost = (n >= 7)
        sailor_knows = (n >= 7)
        self.rules = {
            "navigator": sailor_knows, "charon": True, "hades": True, "h_knows_c": True, "c_knows_h": False,
            "siren_knows": True, "s_knows_c": True, "c_knows_s": False, "h_knows_s": False, "s_knows_h": False,
            "library": use_lib, "ghost": use_ghost, "allow_spectate": True
        }
        self.update_buttons()

    def _visible(self, key):
        c = self.counts
        nav = c.get("navigator", 0); chr_ = c.get("charon", 0)
        hds = c.get("hades", 0); sir = c.get("siren", 0)
        cond = {
            "navigator": nav >= 2,
            "charon": chr_ >= 2,
            "hades": hds >= 2,
            "h_knows_c": hds >= 1 and chr_ >= 1,
            "c_knows_h": hds >= 1 and chr_ >= 1,
            "siren_knows": sir >= 2,
            "s_knows_c": sir >= 1 and chr_ >= 1,
            "c_knows_s": sir >= 1 and chr_ >= 1,
            "h_knows_s": sir >= 1 and hds >= 1,
            "s_knows_h": sir >= 1 and hds >= 1,
            "library": True, "ghost": True, "allow_spectate": True,
        }
        return cond.get(key, True)

    def update_buttons(self):
        self.clear_items()
        
        def make_toggle(key):
            is_on = self.rules[key]
            style = discord.ButtonStyle.success if is_on else discord.ButtonStyle.secondary
            
            label = t(self.lang, "settings", f"rule_{key}")
            if label == "TEXT_NOT_FOUND":
                if key == "h_knows_c": label = t(self.lang, "settings", "rule_hds_chr")
                elif key == "c_knows_h": label = t(self.lang, "settings", "rule_chr_hds")
                elif key == "navigator": label = t(self.lang, "settings", "rule_nav_knows")
                elif key == "charon": label = t(self.lang, "settings", "rule_chr_knows")
                elif key == "hades": label = t(self.lang, "settings", "rule_hds_knows")
                elif key == "library": label = t(self.lang, "dests", "library")
                elif key == "ghost": label = t(self.lang, "roles", "ghost")
                elif key == "allow_spectate": label = t(self.lang, "settings", "rule_spec")
            
            on_off = "ON" if is_on else "OFF"
            btn = discord.ui.Button(label=f"{label}: {on_off}", style=style)
            
            async def callback(interaction: discord.Interaction):
                update_last_active(interaction.channel_id)
                u_lang = get_user_lang(interaction)
                if interaction.user != self.host:
                    await interaction.response.send_message(t(u_lang, "msg", "err_host_only"), ephemeral=True)
                    return
                self.rules[key] = not self.rules[key]
                self.update_buttons()
                await interaction.response.edit_message(view=self)
            btn.callback = callback
            return btn
            
        for k in self.rules.keys():
            if self._visible(k):
                self.add_item(make_toggle(k))
        
        next_btn = discord.ui.Button(label=t(self.lang, "ui", "btn_to_win_setting"), style=discord.ButtonStyle.primary, row=4)
        async def next_callback(interaction: discord.Interaction):
            update_last_active(interaction.channel_id)
            u_lang = get_user_lang(interaction)
            if interaction.user != self.host:
                await interaction.response.send_message(t(u_lang, "msg", "err_host_only"), ephemeral=True)
                return
            await interaction.message.delete()
            view = GameSetupView(self.host, self.players, self.counts, self.rules, self.lang, self.apply_prefs)
            embed = discord.Embed(title=t(self.lang, "msg", "setup_detail_title"), color=0x808080)
            embed.description = t(self.lang, "msg", "setup_detail_desc")
            await interaction.channel.send(embed=embed, view=view)
        next_btn.callback = next_callback
        self.add_item(next_btn)

class RoleSetupView(discord.ui.View):
    # 陣営ごとの役職と＋/－ボタンの行配置（Discordは1行最大5ボタン・最大5行）
    LAYOUT = [
        (0, ["navigator", "passenger"]),  # 人間陣営: 航海士＋－ 乗客＋－
        (1, ["charon", "hades"]),          # カロン陣営: カロン＋－ ハデス＋－
        (2, ["siren"]),                    # カロン陣営: セイレーン＋－
    ]
    ALL_ROLES = ["navigator", "passenger", "charon", "hades", "siren"]

    def __init__(self, host, players, lang):
        super().__init__(timeout=None)
        self.host = host
        self.players = players
        self.lang = lang
        self.n = len(players)
        self.counts = get_default_role_counts(self.n)
        # 役職希望（なりたい／なりたくない）を反映するか。
        # ★デフォルトON。募集画面で希望を出した人がいるのに、
        #   ホストがボタンを押し忘れて無視されるのを防ぐため。
        #   誰も希望を出していなければ、ONでも完全なランダム配役になる
        #   （assign_roles_with_prefs は希望が空なら普通に randomize する）。
        self.apply_prefs = True
        self._build_items()

    def _build_items(self):
        self.clear_items()
        for row, roles in self.LAYOUT:
            for role in roles:
                self.add_item(self._adj_button(role, +1, row))
                self.add_item(self._adj_button(role, -1, row))
        # 決定ボタン(row3)
        confirm = discord.ui.Button(label=t(self.lang, "ui", "btn_to_detail_setting"),
                                    style=discord.ButtonStyle.primary, row=3)
        confirm.callback = self._confirm
        self.add_item(confirm)
        # 役職希望トグル(row4)
        # ★ON/OFFのどちらでも赤(danger)にしてある。緑＋グレーだと他のボタンに
        #   埋もれてホストが気づかず、希望が無視されたまま開始される事故が
        #   あったため、目立つ色で固定する。ON/OFFはラベルの文字で分かる。
        on_off = "ON" if self.apply_prefs else "OFF"
        self.prefs_btn = discord.ui.Button(
            label=f"{t(self.lang, 'settings', 'rule_prefs')}: {on_off}",
            style=discord.ButtonStyle.danger,
            row=4)
        self.prefs_btn.callback = self._toggle_prefs
        self.add_item(self.prefs_btn)

    def _adj_button(self, role, delta, row):
        sym = "＋" if delta > 0 else "－"
        r_name = t(self.lang, "roles", role)
        style = discord.ButtonStyle.success if delta > 0 else discord.ButtonStyle.secondary
        btn = discord.ui.Button(label=f"{r_name} {sym}", style=style, row=row)
        async def cb(interaction: discord.Interaction):
            update_last_active(interaction.channel_id)
            u_lang = get_user_lang(interaction)
            if interaction.user != self.host:
                await interaction.response.send_message(t(u_lang, "msg", "err_host_only"), ephemeral=True)
                return
            new = max(0, min(15, self.counts.get(role, 0) + delta))
            self.counts[role] = new
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        btn.callback = cb
        return btn

    def _total(self):
        return sum(self.counts.get(r, 0) for r in self.ALL_ROLES)

    def build_embed(self):
        lines = [t(self.lang, "msg", "faction_human")]
        for r in ["navigator", "passenger"]:
            lines.append(f"　{t(self.lang, 'roles', r)}: {self.counts.get(r, 0)}")
        lines.append("")
        lines.append(t(self.lang, "msg", "faction_charon"))
        for r in ["charon", "hades", "siren"]:
            lines.append(f"　{t(self.lang, 'roles', r)}: {self.counts.get(r, 0)}")
        lines.append("")
        total = self._total()
        lines.append(t(self.lang, "msg", "setup_role_total", n=self.n, total=total))
        if total != self.n:
            lines.append(t(self.lang, "msg", "setup_role_total_warn"))
        else:
            lines.append(t(self.lang, "msg", "setup_role_total_ok"))
        embed = discord.Embed(title=t(self.lang, "msg", "setup_role_title"),
                              description="\n".join(lines), color=0x808080)
        return embed

    async def _toggle_prefs(self, interaction: discord.Interaction):
        update_last_active(interaction.channel_id)
        u_lang = get_user_lang(interaction)
        if interaction.user != self.host:
            await interaction.response.send_message(t(u_lang, "msg", "err_host_only"), ephemeral=True)
            return
        self.apply_prefs = not self.apply_prefs
        self._build_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _confirm(self, interaction: discord.Interaction):
        update_last_active(interaction.channel_id)
        u_lang = get_user_lang(interaction)
        if interaction.user != self.host:
            await interaction.response.send_message(t(u_lang, "msg", "err_host_only"), ephemeral=True)
            return
        if self._total() != self.n:
            await interaction.response.send_message(t(u_lang, "msg", "err_role_count"), ephemeral=True)
            return

        # ─────────────────────────────────────────────
        # AIが参加している場合の歯止め
        # ─────────────────────────────────────────────
        # ホストは役職の数を自由にいじれるが、AIの推理エンジンは
        # 決まった構成しか知らない。対応外の構成のまま始めると、
        # AIが「ありうる配役の一覧」を作れず何も判断できなくなる。
        # 始まってから壊れるより、ここで止めた方がまし。
        #
        # ★AIが1人もいなければ、このチェックは丸ごと素通りする。
        #   人間だけで遊ぶ時は今まで通り、どんな構成でも自由。
        n_ai = sum(1 for p in self.players if ai_player.is_ai(p))
        if n_ai:
            ok, why, info = ai_player.check_composition(self.n, self.counts, n_ai=n_ai)
            if not ok:
                if why == "max_players":
                    msg = t(u_lang, "msg", "err_ai_max_players", max=info["max"], n=info["n"])
                elif why == "role":
                    # AIが知らない役職（現状はハデスだけ）。役職名は各国語に訳す。
                    names = "/".join(t(u_lang, "roles", key) for key in info["roles"])
                    msg = t(u_lang, "msg", "err_ai_role_unsupported",
                            roles=names, siren=t(u_lang, "roles", "siren"))
                else:
                    # 組み合わせが多すぎる（人数ではなく内訳の問題）
                    msg = t(u_lang, "msg", "err_ai_too_complex",
                            load=f"{info['load']:,}", limit=f"{info['limit']:,}")
                await interaction.response.send_message(msg, ephemeral=True)
                return

        await interaction.message.delete()
        rule_view = RuleSetupView(self.host, self.players, self.counts, self.lang, self.apply_prefs)
        embed = discord.Embed(title=t(self.lang, "msg", "setup_rule_title"), color=0x808080)
        embed.description = t(self.lang, "msg", "setup_rule_desc")
        await interaction.channel.send(embed=embed, view=rule_view)

class PrefRoleView(discord.ui.View):
    """役職希望の選択画面（なりたい/なりたくない 各最大1つ ＋ 提出ボタン）"""
    def __init__(self, recruit_view, user):
        super().__init__(timeout=300)
        self.rv = recruit_view
        self.user = user
        lang = USER_LANGS.get(user.id, recruit_view.lang)
        self.lang = lang

        cur_want = self.rv.preferences.get(user, "any")
        cur_reject = self.rv.anti_preferences.get(user, "none")
        role_vals = ["navigator", "passenger", "charon", "hades", "siren"]

        # 未選択（any/none）のときは default を付けず、プレースホルダー（項目名）を表示させる
        want_opts = [discord.SelectOption(label=t(lang, "roles", r), value=r, default=(r == cur_want)) for r in role_vals]
        want_opts.append(discord.SelectOption(label=t(lang, "ui", "opt_pref_any"), value="any", default=False))
        self.want_select = discord.ui.Select(placeholder=t(lang, "ui", "select_pref_want"), options=want_opts, row=0)
        self.want_select.callback = self.on_want
        self.add_item(self.want_select)

        reject_opts = [discord.SelectOption(label=t(lang, "roles", r), value=r, default=(r == cur_reject)) for r in role_vals]
        reject_opts.append(discord.SelectOption(label=t(lang, "ui", "opt_pref_none"), value="none", default=False))
        self.reject_select = discord.ui.Select(placeholder=t(lang, "ui", "select_pref_reject"), options=reject_opts, row=1)
        self.reject_select.callback = self.on_reject
        self.add_item(self.reject_select)

        submit = discord.ui.Button(label=t(lang, "ui", "btn_pref_submit"), style=discord.ButtonStyle.success, row=2)
        submit.callback = self.on_submit
        self.add_item(submit)

    async def on_want(self, interaction: discord.Interaction):
        val = interaction.data["values"][0]
        if val == "any":
            self.rv.preferences.pop(self.user, None)
        else:
            self.rv.preferences[self.user] = val
            # 後勝ち: なりたくないに同じ役職があれば解除
            if self.rv.anti_preferences.get(self.user) == val:
                self.rv.anti_preferences.pop(self.user, None)
        await interaction.response.edit_message(view=PrefRoleView(self.rv, self.user))

    async def on_reject(self, interaction: discord.Interaction):
        val = interaction.data["values"][0]
        if val == "none":
            self.rv.anti_preferences.pop(self.user, None)
        else:
            self.rv.anti_preferences[self.user] = val
            # 後勝ち: なりたいに同じ役職があれば解除
            if self.rv.preferences.get(self.user) == val:
                self.rv.preferences.pop(self.user, None)
        await interaction.response.edit_message(view=PrefRoleView(self.rv, self.user))

    async def on_submit(self, interaction: discord.Interaction):
        want = self.rv.preferences.get(self.user)
        reject = self.rv.anti_preferences.get(self.user)
        want_label = t(self.lang, "roles", want) if want else t(self.lang, "ui", "opt_pref_any")
        reject_label = t(self.lang, "roles", reject) if reject else t(self.lang, "ui", "opt_pref_none")
        content = t(self.lang, "msg", "pref_status", want=want_label, reject=reject_label)
        await interaction.response.edit_message(content=content, view=PrefConfirmView(self.rv, self.user))


class PrefConfirmView(discord.ui.View):
    """希望提出後の確認画面（再提出で選択画面に戻る）"""
    def __init__(self, recruit_view, user):
        super().__init__(timeout=300)
        self.rv = recruit_view
        self.user = user
        lang = USER_LANGS.get(user.id, recruit_view.lang)
        self.lang = lang
        btn = discord.ui.Button(label=t(lang, "ui", "btn_pref_resubmit"), style=discord.ButtonStyle.secondary)
        btn.callback = self.on_resubmit
        self.add_item(btn)

    async def on_resubmit(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            content=t(self.lang, "msg", "pref_prompt"),
            view=PrefRoleView(self.rv, self.user))


class RecruitView(discord.ui.View):
    def __init__(self, host, lang):
        super().__init__(timeout=None)
        self.host = host
        self.lang = lang
        self.players = set()
        self.spectators = set()
        self.preferences = {}  # Member -> role_key（なりたい：具体的な希望のみ）
        self.anti_preferences = {}  # Member -> role_key（なりたくない：1つのみ）

        # AIプレイヤー。人間とは別のリストで持つ。
        # ★人間は set（順不同の集合）だが、AIは list（順序つき）にしている。
        #   「AIを外す」で最後に足した1体を外したいので、順序が要るため。
        #   また、人間の参加/退出処理（self.players を触る部分）に
        #   一切手を入れずに済むという利点もある。
        self.ai_players = []

        self.join_button.label = t(lang, "ui", "btn_join")
        self.join_pref_button.label = t(lang, "ui", "btn_join_pref")
        self.spectate_button.label = t(lang, "ui", "btn_spectate")
        self.leave_button.label = t(lang, "ui", "btn_leave")
        self.add_ai_button.label = t(lang, "ui", "btn_add_ai")
        self.remove_ai_button.label = t(lang, "ui", "btn_remove_ai")
        self.start_button.label = t(lang, "ui", "btn_close_recruit")

    def all_players(self):
        """人間とAIを合わせた参加者一覧。ゲーム開始時にはこれを使う。"""
        return list(self.players) + self.ai_players

    @discord.ui.button(style=discord.ButtonStyle.success, row=0, custom_id="btn_join")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        update_last_active(interaction.channel_id)
        await interaction.response.defer()
        self.players.add(interaction.user)
        self.spectators.discard(interaction.user)
        self.preferences.pop(interaction.user, None)
        self.anti_preferences.pop(interaction.user, None)
        await self.update_message(interaction)

    @discord.ui.button(style=discord.ButtonStyle.success, row=0, custom_id="btn_join_pref")
    async def join_pref_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        update_last_active(interaction.channel_id)
        self.players.add(interaction.user)
        self.spectators.discard(interaction.user)
        p_lang = USER_LANGS.get(interaction.user.id, self.lang)
        await interaction.response.send_message(content=t(p_lang, "msg", "pref_prompt"), view=PrefRoleView(self, interaction.user), ephemeral=True)
        await self.refresh_main(interaction)

    @discord.ui.button(style=discord.ButtonStyle.secondary, row=0, custom_id="btn_spec")
    async def spectate_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        update_last_active(interaction.channel_id)
        await interaction.response.defer()
        self.spectators.add(interaction.user)
        self.players.discard(interaction.user)
        self.preferences.pop(interaction.user, None)
        self.anti_preferences.pop(interaction.user, None)
        await self.update_message(interaction)

    @discord.ui.button(style=discord.ButtonStyle.secondary, row=0, custom_id="btn_leave")
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        update_last_active(interaction.channel_id)
        await interaction.response.defer()
        self.players.discard(interaction.user)
        self.spectators.discard(interaction.user)
        self.preferences.pop(interaction.user, None)
        self.anti_preferences.pop(interaction.user, None)
        await self.update_message(interaction)

    # ─────────────────────────────────────────────
    # AIプレイヤーの追加／削除（ホスト専用）
    # ─────────────────────────────────────────────
    # ★ホスト専用にしている理由：
    #   参加/観戦/退出は「自分のこと」なので誰でも押せるが、
    #   AIを足すのは卓の人数構成を変える行為なので、締め切りボタンと
    #   同じくホストの判断に任せる。

    @discord.ui.button(style=discord.ButtonStyle.secondary, row=1, custom_id="btn_add_ai")
    async def add_ai_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        update_last_active(interaction.channel_id)
        u_lang = get_user_lang(interaction)
        if interaction.user != self.host:
            await interaction.response.send_message(t(u_lang, "msg", "err_host_only"), ephemeral=True)
            return

        # AIが推理できる人数には上限がある。
        # 上限に達していたら、ここで足すのをやめる。
        # ★ここで止めておかないと、役職設定の画面まで進んでから
        #   「その人数では無理です」と言われることになり、
        #   募集からやり直しになってしまう。
        limit = ai_player.max_players_with_ai()
        total = len(self.players) + len(self.ai_players)
        if total >= limit:
            await interaction.response.send_message(
                t(u_lang, "msg", "err_ai_limit", max=limit, total=total), ephemeral=True)
            return

        # 人数だけでなく「推理の重さ」でも止める。
        # ★役職の内訳はこの画面では未確定なので、初期値の構成で見積もる。
        #   ホストが後から役職をいじって重くした場合は、役職設定の画面で
        #   改めて弾かれる（そちらが最終判定）。
        ok, why, info = ai_player.check_composition(
            total + 1, get_default_role_counts(total + 1),
            n_ai=len(self.ai_players) + 1)
        if not ok and why == "too_complex":
            await interaction.response.send_message(
                t(u_lang, "msg", "err_ai_too_complex",
                  load=f"{info['load']:,}", limit=f"{info['limit']:,}"), ephemeral=True)
            return

        await interaction.response.defer()
        # 既存の参加者（人間・AI両方）を渡して、名前とIDの重複を避けてもらう
        self.ai_players.extend(ai_player.make_ai_players(1, existing_players=self.all_players()))
        await self.update_message(interaction)

    @discord.ui.button(style=discord.ButtonStyle.secondary, row=1, custom_id="btn_remove_ai")
    async def remove_ai_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        update_last_active(interaction.channel_id)
        u_lang = get_user_lang(interaction)
        if interaction.user != self.host:
            await interaction.response.send_message(t(u_lang, "msg", "err_host_only"), ephemeral=True)
            return

        await interaction.response.defer()
        if self.ai_players:
            # 最後に足した1体を外す
            self.ai_players.pop()
        # AIが0体のときは何も起きない（エラーを出すほどのことではない）
        await self.update_message(interaction)

    @discord.ui.button(style=discord.ButtonStyle.primary, row=1, custom_id="btn_start_rec")
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        update_last_active(interaction.channel_id)
        u_lang = get_user_lang(interaction)
        if interaction.user != self.host:
            await interaction.response.send_message(t(u_lang, "msg", "err_host_only"), ephemeral=True)
            return

        # ★AIを足した後から人間が参加してくると、上限を超えることがある。
        #   （AI追加時のチェックだけでは防げない）
        #   ここで最終確認する。
        if self.ai_players:
            limit = ai_player.max_players_with_ai()
            total = len(self.players) + len(self.ai_players)
            if total > limit:
                await interaction.response.send_message(
                    t(u_lang, "msg", "err_ai_limit", max=limit, total=total), ephemeral=True)
                return

        games[interaction.channel_id] = {
            "host": self.host, "players": self.all_players(), "spectators": list(self.spectators), "inputs": {}, "dead": [],
            "blocked_yesterday": [], "history": {}, "day": 1, "used_library": [], "last_active": time.time(),
            "lang": self.lang,
            "prefs": {p: r for p, r in self.preferences.items() if p in self.players},
            "anti_prefs": {p: r for p, r in self.anti_preferences.items() if p in self.players}
        }
        await interaction.message.delete()
        # ★AIもここに含める。人間だけを渡すと、役職設定の画面が
        #   人間の人数で役職を割り振ろうとしてしまう。
        setup_view = RoleSetupView(host=self.host, players=self.all_players(), lang=self.lang)
        await interaction.channel.send(embed=setup_view.build_embed(), view=setup_view)

    def build_embed(self):
        # ★AIも「参加者」として同じ一覧に並べる。
        #   名前の末尾に（AI）が付いているので、専用の欄を作らなくても
        #   人間と見分けがつく。欄を分けると翻訳ファイルにも手を入れる
        #   ことになるので、ここは既存の表示をそのまま使う。
        all_p = self.all_players()
        player_names = "\n".join([f"- {p.display_name}" for p in all_p]) or "-"
        spectator_names = "\n".join([f"- {s.display_name}" for s in self.spectators]) or "-"
        embed = discord.Embed(title=t(self.lang, "msg", "recruit_title"), color=0x808080)
        embed.description = t(self.lang, "msg", "recruit_desc", host=self.host.display_name, p_count=len(all_p), p_list=player_names, s_count=len(self.spectators), s_list=spectator_names)
        if os.path.exists("banner.jpg"):
            embed.set_image(url="attachment://banner.jpg")
        return embed

    async def update_message(self, interaction: discord.Interaction):
        embed = self.build_embed()
        if os.path.exists("banner.jpg"):
            file = discord.File("banner.jpg", filename="banner.jpg")
            await interaction.edit_original_response(embed=embed, view=self, attachments=[file])
        else:
            await interaction.edit_original_response(embed=embed, view=self)

    async def refresh_main(self, interaction: discord.Interaction):
        embed = self.build_embed()
        if os.path.exists("banner.jpg"):
            file = discord.File("banner.jpg", filename="banner.jpg")
            await interaction.message.edit(embed=embed, view=self, attachments=[file])
        else:
            await interaction.message.edit(embed=embed, view=self)

def assign_roles_with_prefs(players, counts, prefs, anti=None):
    """役職を割り当てる。
    prefs: {player: role}  なりたい（抽選の優先権）
    anti:  {player: role}  なりたくない（可能な限り尊重・ハード制約）
    方針: 「拒否を最優先」「希望は抽選優先権」「全拒否を守れない時は最大限充足」。
    小規模なのでランダム探索を多数回行い、(拒否違反数→希望充足数) で最良解を選ぶ。"""
    import collections
    anti = anti or {}
    template = []
    for role, c in counts.items():
        template.extend([role] * int(c))
    players = list(players)
    if len(template) != len(players):
        # 念のための整合（理論上は一致するはず）
        template = (template + ["passenger"] * len(players))[:len(players)]

    def one_attempt():
        rem = collections.Counter(template)
        assignment = {}
        # フェーズA: なりたい役職を抽選（ランダム順＝抽選。枠が空いていて拒否と矛盾しない人から）
        order = list(players); random.shuffle(order)
        for p in order:
            w = prefs.get(p)
            if w and w != "any" and rem.get(w, 0) > 0 and anti.get(p) != w:
                assignment[p] = w; rem[w] -= 1
        # フェーズB: 残りを、なりたくない役職を避けつつランダム割り当て
        rest = [p for p in players if p not in assignment]; random.shuffle(rest)
        for p in rest:
            avail = [r for r in rem if rem[r] > 0]
            ok_choices = [r for r in avail if r != anti.get(p)]
            pick_from = ok_choices if ok_choices else avail  # 避けられない時は違反を許容
            if not pick_from:
                break
            r = random.choice(pick_from)
            assignment[p] = r; rem[r] -= 1
        return assignment

    best, best_score = None, None
    for _ in range(300):
        a = one_attempt()
        if len(a) != len(players):
            continue
        violations = sum(1 for p in players if anti.get(p) and a.get(p) == anti.get(p))
        wants_ok = sum(1 for p in players if prefs.get(p) and prefs.get(p) != "any" and a.get(p) == prefs.get(p))
        score = (violations, -wants_ok)  # 違反最小 → 希望充足最大
        if best_score is None or score < best_score:
            best, best_score = dict(a), score
            if violations == 0 and best_score[1] <= -min(
                len([p for p in players if prefs.get(p) and prefs.get(p) != "any"]),
                len(players)):
                break  # 違反0かつ希望も十分通った
    if best is None:  # 最終フォールバック（純ランダム）
        pool = list(template); random.shuffle(pool)
        best = {players[i]: pool[i] for i in range(len(players))}
    return best


async def distribute_roles(channel, players, counts, rules, settings, apply_prefs=False):
    game = games[channel.id]
    if apply_prefs:
        prefs = game.get("prefs", {})
        anti = game.get("anti_prefs", {})
        game["roles"] = assign_roles_with_prefs(players, counts, prefs, anti)
    else:
        roles = []
        for role_name, count in counts.items(): roles.extend([role_name] * count)
        random.shuffle(roles)
        game["roles"] = {players[i]: roles[i] for i in range(len(players))}
    game["settings"] = settings
    game["rules"] = rules
    game["pt"] = {"c": 0, "x": 0}

    g_lang = get_game_lang(channel.id)
    failed_dm = []

    if not rules.get("ghost", True):
        for p in game.get("dead", []):
            game["inputs"][p] = {"type": "ghost", "target": "none", "rank": "Dead", "rank_num": 999, "time": "-"}

    for player, role_key in game["roles"].items():
        # 個人の言語を取得
        p_lang = USER_LANGS.get(player.id, g_lang)
        
        r_name = t(p_lang, "roles", role_key)
        r_desc = t(p_lang, "role_desc", role_key)
        role_msg = t(p_lang, "msg", "dm_role_title", role=r_name, desc=r_desc)
        
        if role_key == "navigator":
            if rules["navigator"]:
                others = [p.display_name for p, r in game["roles"].items() if r == "navigator" and p != player]
                role_msg += t(p_lang, "msg", "dm_navigator_ally", others=', '.join(others)) if others else t(p_lang, "msg", "dm_navigator_alone")
            else: role_msg += t(p_lang, "msg", "dm_navigator_unknown")
        elif role_key == "charon":
            if rules["charon"]:
                others = [p.display_name for p, r in game["roles"].items() if r == "charon" and p != player]
                role_msg += t(p_lang, "msg", "dm_charon_ally", others=', '.join(others)) if others else t(p_lang, "msg", "dm_charon_alone")
            else: role_msg += t(p_lang, "msg", "dm_charon_unknown")
            if rules["c_knows_h"]:
                hades_list = [p.display_name for p, r in game["roles"].items() if r == "hades"]
                if hades_list: role_msg += t(p_lang, "msg", "dm_charon_hades", others=', '.join(hades_list))
            if rules.get("c_knows_s"):
                siren_list = [p.display_name for p, r in game["roles"].items() if r == "siren"]
                if siren_list: role_msg += t(p_lang, "msg", "dm_charon_siren", others=', '.join(siren_list))
        elif role_key == "hades":
            if rules["hades"]:
                others = [p.display_name for p, r in game["roles"].items() if r == "hades" and p != player]
                if others: role_msg += t(p_lang, "msg", "dm_hades_ally", others=', '.join(others))
            if rules["h_knows_c"]:
                charons = [p.display_name for p, r in game["roles"].items() if r == "charon"]
                if charons: role_msg += t(p_lang, "msg", "dm_hades_charon", others=', '.join(charons))
            if rules.get("h_knows_s"):
                siren_list = [p.display_name for p, r in game["roles"].items() if r == "siren"]
                if siren_list: role_msg += t(p_lang, "msg", "dm_hades_siren", others=', '.join(siren_list))
        elif role_key == "siren":
            if rules.get("s_knows_c", True):
                charons = [p.display_name for p, r in game["roles"].items() if r == "charon"]
                if charons: role_msg += t(p_lang, "msg", "dm_siren_charon", others=', '.join(charons))
            if rules.get("siren_knows", True):
                others = [p.display_name for p, r in game["roles"].items() if r == "siren" and p != player]
                role_msg += t(p_lang, "msg", "dm_siren_ally", others=', '.join(others)) if others else t(p_lang, "msg", "dm_siren_alone")
            if rules.get("s_knows_h"):
                hades_list = [p.display_name for p, r in game["roles"].items() if r == "hades"]
                if hades_list: role_msg += t(p_lang, "msg", "dm_siren_hades", others=', '.join(hades_list))

        raw_image_name = f"{role_key}_{p_lang}.jpg"
        valid_image = get_image_file(raw_image_name, p_lang)

        try:
            if valid_image:
                file = discord.File(valid_image, filename=valid_image)
                await player.send(content=role_msg, file=file)
            else:
                await player.send(content=role_msg)
        except discord.Forbidden:
            failed_dm.append(player)

    spectators = game.get("spectators", [])
    if spectators:
        if rules.get("allow_spectate", True):
            s_list = ""
            for p, r in game["roles"].items():
                s_list += f"- {p.display_name} : {t(g_lang, 'roles', r)}\n"
            spec_msg = t(g_lang, "msg", "dm_spectate_on", list=s_list)
        else:
            spec_msg = t(g_lang, "msg", "dm_spectate_off")
            
        for spec in spectators:
            try: await spec.send(content=spec_msg)
            except discord.Forbidden: pass

    for failed in failed_dm:
        try: await channel.send(t(g_lang, "msg", "dm_failed", name=failed.display_name))
        except: pass

    rule_texts = []
    
    def get_rule_label(key):
        label = t(g_lang, "settings", key)
        if label == "TEXT_NOT_FOUND": return get_setting_label(g_lang, key) 
        return label
        
    if counts.get("navigator", 0) >= 2:
        rule_texts.append(f"{get_rule_label('rule_nav_knows')}: {'ON' if rules['navigator'] else 'OFF'}")
    if counts.get("charon", 0) >= 2:
        rule_texts.append(f"{get_rule_label('rule_chr_knows')}: {'ON' if rules['charon'] else 'OFF'}")
    if counts["hades"] >= 2: rule_texts.append(f"{get_rule_label('rule_hds_knows')}: {'ON' if rules['hades'] else 'OFF'}")
    if counts["hades"] >= 1 and counts["charon"] >= 1:
        rule_texts.append(f"{get_rule_label('rule_hds_chr')}: {'ON' if rules['h_knows_c'] else 'OFF'}")
        rule_texts.append(f"{get_rule_label('rule_chr_hds')}: {'ON' if rules['c_knows_h'] else 'OFF'}")
    # セイレーン関連の認知ルール（0人の役職に関わるものは非表示）
    if counts.get("siren", 0) >= 2:
        rule_texts.append(f"{get_rule_label('rule_siren_knows')}: {'ON' if rules.get('siren_knows') else 'OFF'}")
    if counts.get("siren", 0) >= 1 and counts.get("charon", 0) >= 1:
        rule_texts.append(f"{get_rule_label('rule_s_knows_c')}: {'ON' if rules.get('s_knows_c') else 'OFF'}")
        rule_texts.append(f"{get_rule_label('rule_c_knows_s')}: {'ON' if rules.get('c_knows_s') else 'OFF'}")
    if counts.get("siren", 0) >= 1 and counts.get("hades", 0) >= 1:
        rule_texts.append(f"{get_rule_label('rule_h_knows_s')}: {'ON' if rules.get('h_knows_s') else 'OFF'}")
        rule_texts.append(f"{get_rule_label('rule_s_knows_h')}: {'ON' if rules.get('s_knows_h') else 'OFF'}")
    
    lib_text = t(g_lang, "dests", "library")
    if lib_text == "TEXT_NOT_FOUND": lib_text = "Library"
    rule_texts.append(f"{lib_text}: {'ON' if rules['library'] else 'OFF'}")
    
    ghost_text = t(g_lang, "roles", "ghost")
    if ghost_text == "TEXT_NOT_FOUND": ghost_text = "Ghost"
    rule_texts.append(f"{ghost_text}: {'ON' if rules['ghost'] else 'OFF'}")
    
    prefs_text = get_rule_label('rule_prefs')
    rule_texts.append(f"{prefs_text}: {'ON' if apply_prefs else 'OFF'}")
    
    rule_texts.append(t(g_lang, "msg", "rule_need", n=settings.get("need", 2)))
    
    rule_str = "\n".join(rule_texts)
    
    order = ["navigator", "passenger", "charon", "hades", "siren"]
    breakdown_str = " / ".join([f"{t(g_lang, 'roles', r)}: {counts.get(r, 0)}" for r in order])
    
    win_c = settings['win_c']
    win_x = settings['win_x']
    
    vic_o_text = get_rule_label('vic_o')
    vic_x_text = get_rule_label('vic_x')
    victory_str = f"{vic_o_text}: {win_c}pt\n{vic_x_text}: {win_x}pt"
    
    embed = discord.Embed(color=0x808080)
    embed.description = t(g_lang, "msg", "game_start_embed_desc", total=len(players), breakdown=breakdown_str, rules=rule_str, victory=victory_str)
    
    image_path = "banner.jpg"
    start_msg = t(g_lang, "msg", "game_start_msg")
    
    if os.path.exists(image_path):
        file = discord.File(image_path, filename="banner.jpg")
        await channel.send(content=start_msg, file=file)
    else:
        await channel.send(content=start_msg)

    await channel.send(embed=embed)
    game["day_start"] = time.time()
    
    day_embed = discord.Embed(title=t(g_lang, "msg", "day_start_title", day=1), color=0x4682B4)
    desc = t(g_lang, "msg", "day_start_desc", start_time=int(game['day_start']), current=0, total=len(players), lines="(None)")
    day_embed.description = desc
    
    view = TriggerInputView(channel.id, game["host"])
    game["main_msg"] = await channel.send(content=get_mentions(game), embed=day_embed, view=view)

    # ---- AIプレイヤーの頭脳を用意して、1日目の行動を始めさせる ----
    # init_game は、AIが1人もいなければ何もせず None を返す。
    # つまり人間だけのゲームには一切影響しない。
    if ai_brain.init_game(game):
        game["ai_task"] = asyncio.create_task(run_ai_actions(channel, game))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🌟 スラッシュコマンド
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@bot.tree.command(name="play", description="Start Ghost Liner game / ゴーストライナーの募集を開始します")
async def play(interaction: discord.Interaction):
    lang = get_user_lang(interaction)
    
    if interaction.channel_id in games:
        await interaction.response.send_message(t(lang, "msg", "err_already_playing"), ephemeral=True)
        return
        
    games[interaction.channel_id] = {"host": interaction.user, "last_active": time.time(), "lang": lang} 
    
    view = RecruitView(host=interaction.user, lang=lang)
    embed = discord.Embed(title=t(lang, "msg", "recruit_title"), color=0x808080)
    embed.description = t(lang, "msg", "recruit_desc", host=interaction.user.display_name, p_count=0, p_list="-", s_count=0, s_list="-")
    
    image_path = "banner.jpg"
    if os.path.exists(image_path):
        file = discord.File(image_path, filename="banner.jpg")
        embed.set_image(url="attachment://banner.jpg")
        await interaction.response.send_message(file=file, embed=embed, view=view)
    else:
        await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="reset", description="Reset current game / 現在のゲームを強制終了します")
async def reset(interaction: discord.Interaction):
    lang = get_user_lang(interaction)
    if interaction.channel_id in games:
        game = games[interaction.channel_id]
        is_host = (interaction.user == game.get("host"))
        is_admin = getattr(interaction.user.guild_permissions, "manage_channels", False)
        
        if is_host or is_admin:
            del games[interaction.channel_id]
            await interaction.response.send_message(t(lang, "msg", "reset_success"))
        else:
            await interaction.response.send_message(t(lang, "msg", "err_reset_permission"), ephemeral=True)
    else:
        await interaction.response.send_message(t(lang, "msg", "err_no_game"), ephemeral=True)

@bot.tree.command(name="rules", description="Show game rules / ルールを表示します")
async def rules(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    lang = get_user_lang(interaction)
    data = t(lang, "rules", "summary")
    
    embed = discord.Embed(title=data["title"], description=data["desc"], color=0x808080)
    view = RulesView(lang)
    
    raw_image_name = data.get("image")
    valid_image = get_image_file(raw_image_name, lang)
    
    if valid_image:
        file = discord.File(valid_image, filename=valid_image)
        embed.set_image(url=f"attachment://{valid_image}")
        await interaction.followup.send(embed=embed, file=file, view=view)
    else:
        await interaction.followup.send(embed=embed, view=view)

@bot.tree.command(name="language", description="Change your language setting / あなたの言語設定を変更します")
@app_commands.choices(lang=[
    app_commands.Choice(name="日本語 (Japanese)", value="ja"),
    app_commands.Choice(name="English (US)", value="en-US"),
    app_commands.Choice(name="繁體中文 (Taiwan)", value="zh-TW"),
    app_commands.Choice(name="简体中文 (China)", value="zh-CN"),
    app_commands.Choice(name="한국어 (Korean)", value="ko")
])
async def language(interaction: discord.Interaction, lang: app_commands.Choice[str]):
    USER_LANGS[interaction.user.id] = lang.value
    save_user_lang_to_db(interaction.user.id, lang.value)
    
    success_msg = {
        "ja": "言語を「日本語」に設定しました！",
        "en-US": "Language has been set to English!",
        "zh-TW": "語言已設定為「繁體中文」！",
        "zh-CN": "语言已设定为“简体中文”！",
        "ko": "언어가 '한국어'로 설정되었습니다!"
    }
    msg = success_msg.get(lang.value, f"Language set to {lang.value}")
    await interaction.response.send_message(msg, ephemeral=True)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🚨 予期せぬエラーが起きたときの処理（ログに記録する）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    # エラーの詳細をログに記録
    logger.error(f"❌ コマンド実行エラー ({interaction.command.name} by {interaction.user}): {error}")
    traceback.print_exception(type(error), error, error.__traceback__)
    
    # ユーザーには「システムエラー」とだけ伝える
    error_msg = "システムエラーが発生しました。開発者に報告されました。"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(error_msg, ephemeral=True)
        else:
            await interaction.response.send_message(error_msg, ephemeral=True)
    except:
        pass

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 起動処理
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 放置された卓を自動で終了させるまでの時間（秒）
RECRUIT_TIMEOUT = 3600      # 募集段階（まだ開始していない）
GAME_TIMEOUT = 86400        # ゲーム進行中
# ★AI入りの卓だけ短くする理由
#   AIは「ありうる配役の全パターン（世界）」を丸ごとメモリに持つ。
#   9人＋AI3体で約7MB、11人＋AI3体で約35MB。本番は512MBの
#   サーバなので、放棄された卓が24時間居座ると、遊んでいないのに
#   メモリを掴んだままになる。AI無しの卓は世界を持たない（ほぼ0MB）ので
#   従来どおりでよい。
AI_GAME_TIMEOUT = 21600     # ゲーム進行中＋AIが1人でもいる（6時間）


@tasks.loop(hours=1.0)
async def cleanup_inactive_games():
    current_time = time.time()
    to_delete = []
    for channel_id, game in games.items():
        if "players" not in game:
            threshold = RECRUIT_TIMEOUT
        elif any(ai_player.is_ai(p) for p in game["players"]):
            threshold = AI_GAME_TIMEOUT
        else:
            threshold = GAME_TIMEOUT
        if current_time - game.get("last_active", current_time) > threshold:
            to_delete.append(channel_id)
            
    for channel_id in to_delete:
        lang = games[channel_id].get("lang", "en-US")
        del games[channel_id]
        channel = bot.get_channel(channel_id)
        if channel:
            try: await channel.send(t(lang, "msg", "cleanup_msg"))
            except: pass

@bot.event
async def setup_hook():
    await bot.tree.sync()

@bot.event
async def on_ready(): 
    cleanup_inactive_games.start()
    # 起動したときにログに記録する
    logger.info(f"🚀 Bot logged in as: {bot.user.name} (ID: {bot.user.id})")
    logger.info(f"💎 Shards count: {bot.shard_count}")

if __name__ == "__main__":
    load_dotenv()
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    
    if not TOKEN:
        logger.error("Error: DISCORD_BOT_TOKEN not found.")
        exit()
        
    bot.run(TOKEN)