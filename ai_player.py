# ============================================================
# ai_player.py
#   AIプレイヤーを「Discordのメンバーのふり」をさせるための偽物クラス。
#
# ★このファイルの目的
#   main.py は、プレイヤーを discord.Member というオブジェクトのまま
#   持ち回っています。
#     game["players"] = [Member, Member, ...]
#     game["roles"]   = {Member: "charon", ...}
#     game["inputs"]  = {Member: {...}, ...}
#   つまり「プレイヤー」はただのIDではなく、オブジェクトです。
#
#   ここで、AIプレイヤーのために main.py 側に
#     「もしAIだったら○○、人間だったら××」
#   という分岐をあちこち書くと、2200行あるViewの全部に条件分岐が
#   散らばって手に負えなくなります。
#
#   そこで逆の発想をします。
#     「main.py から見て Member と見分けがつかないニセモノ」
#   を用意すれば、main.py はAIの存在を知らないまま動きます。
#
#   これはプログラミングでは「ダックタイピング」と呼ばれる普通の手法です
#   （アヒルのように鳴くならアヒルとして扱ってよい、という意味）。
#
# ★注意点（これだけ守れば安全）
#   このニセモノを discord のAPIに本物のMemberとして渡すと落ちます。
#   具体的には mentions=[...] や guild.get_member() のような
#   「Discordサーバーに問い合わせる系」の引数に渡してはいけません。
#   main.py を調べた限り、プレイヤーオブジェクトに対して行われているのは
#     .display_name（名前表示）
#     .id          （識別子／言語設定の参照）
#     .mention     （メンション文字列の組み立て）
#     .send()      （役職を伝えるDM）
#   の4つだけなので、この4つさえ用意すれば足ります。
# ============================================================

import itertools
import math

import engine


# ============================================================
# 調整つまみ
# ============================================================

# AIのIDの開始番号。
# ★なぜ小さい数字なのか：
#   Discordの本物のIDは「スノーフレーク」という巨大な数（10^17〜10^19程度）
#   です。1000番台のIDが本物のユーザーと衝突することは絶対にありません。
#   逆に、それらしく見せようとして 900000000000000000 のような数を使うと、
#   実在するユーザーのIDとぶつかる可能性が出てきます。
AI_ID_BASE = 1000

# AIの名前の後ろに付ける印。
# ★誰がAIかは全員に分かっている方が健全、という方針は変えていません。
#   ただし名前そのものが「探偵AI」のように AI で終わる形になったので、
#   ここで さらに（AI）を足すと「探偵AI（AI）」になってしまいます。
#   そのため印は空にして、名前側で AI を名乗る形にしました。
#   ★AIかどうかの判定は is_ai()（クラスで判定）が行っていて、名前は
#     一切見ていないので、ここを空にしても動作には影響しません。
#
# ★絵文字（🤖 など）は使わない方が無難です。
#   Discordの表示では問題ありませんが、Windowsのコンソールは既定で
#   cp932という古い文字コードを使うので、python で実行して結果を
#   確認するときに絵文字だけで落ちます（実際に落ちました）。
#   ゲームの動作確認のたびに引っかかるので、ここは普通の文字にします。
AI_NAME_SUFFIX = ""

# AIに割り当てる名前の候補。
#
# ★「職業名＋AI」で統一しています。単なる雰囲気作りではなく、実利があります：
#   ①人間プレイヤーが名前を見ただけで「こういう喋り方をするキャラだ」と
#     見当をつけられるので、同じ発言でも個性が伝わりやすくなります。
#   ②AIの喋り方（talk.pyの人格）を職業ごとに書き分ける時、職業には
#     固有の語彙があるので中身から違う文が書けます。「クール」「熱血」の
#     ような抽象的な性格だと語尾しか変えられませんでした。
#     （例：「話に矛盾が無い」を、将棋棋士なら「悪手が見当たらない」、
#       鍛冶屋なら「焼きが甘くない」、園芸家なら「根がまっすぐだ」と言える）
#
# ★職業選びで避けたもの（この方針は今後も守ってください）：
#   ・船乗り／航海士など、このゲームの役職そのものを連想させるもの
#   ・召喚士（セイレーンの呼び寄せ）、吟遊詩人（歌＝セイレーン）
#   ・忍者（潜伏＝カロン）、僧侶・騎士（守る役）、占い師・賢者（正体を見抜く）
#   ・狩人（人狼の狩人）
#   名前から役職の印象が付くと、人間の投票がそれに引っ張られます。
#   AIは人間の発言を読めず反論できないので、その不公平を増やしたくありません。
# ★11個に絞ってあります。同時に出るのは最大9体、実際の運用では2〜4体なので
#   数は足りています。数を増やすより、1つ1つの喋り方を作り込む方を優先しました
#   （talk.py の PERSONA_* に、この11個ぶんの語彙が書いてあります）。
#   ★11個は、語彙の領域が1つも被らないように選んであります：
#     推理／人気／手入れ／商い／身体／追跡／料理／物語／学生生活／ゲーム／脱力
#   足す時は、既存のどれかと同じ引き出しになっていないか確認してください。
#   （検討したが外したもの：バリスタAI＝コックAIと同領域・語彙が狭い、
#     冒険家AI＝猟師AIと同領域・輪郭が曖昧、写真家AI＝探偵AIと「観察」で重複、
#     ダンサーAI＝武道家AIと「身体」で重複）
AI_NAME_POOL = [
    "探偵AI", "芸能人AI", "美容師AI", "商人AI", "武道家AI",
    "猟師AI", "コックAI", "小説家AI", "大学生AI", "ゲーマーAI",
    "遊び人AI",
]


# ============================================================
# 偽メンバークラス
# ============================================================

class AIPlayer:
    """
    discord.Member のふりをするAIプレイヤー。

    main.py は、このオブジェクトを本物のプレイヤーと同じように
    game["players"] に入れたり、辞書のキーにしたりできます。
    """

    def __init__(self, display_name, ai_id):
        # --- main.py から実際に使われる4つ ---
        self.display_name = display_name   # 画面に出る名前
        self.id = ai_id                    # 識別子（整数）

        # .name は本物のMemberにもある属性です。
        # main.py のプレイヤー処理では使われていませんが、
        # 将来どこかで参照されても落ちないように用意しておきます。
        self.name = display_name

        # --- ここから下はAI側の都合で持つ情報（Discordには無い） ---

        # このAIが受け取ったDMの記録。
        # 実際には送信しませんが、捨ててしまうと
        # 「AIはちゃんと役職を受け取れたのか？」を確認できなくなるので
        # 中身を残しておきます。デバッグ用です。
        self.received_dms = []

        # 人格（talk.py の PERSONA_NAMES のどれか）。
        # 配役のタイミングで外から入れます。ここでは空にしておきます。
        self.persona = None

    # ------------------------------------------------------------
    # .mention : メンション文字列
    # ------------------------------------------------------------
    @property
    def mention(self):
        """
        本物のMemberなら "<@123456789>" という文字列を返し、
        Discordがそれを見て相手に通知を飛ばします。

        AIに通知を飛ばす意味はありませんし、存在しないIDで
        "<@1001>" などと書くと壊れた表示になるので、
        ただの名前を返します。

        main.py の get_mentions() は全員の .mention を空白で
        つなげているだけなので、ここが普通の文字列でも問題ありません。
        """
        return self.display_name

    # ------------------------------------------------------------
    # .send() : DM送信（何もしない）
    # ------------------------------------------------------------
    async def send(self, content=None, *, file=None, embed=None, view=None, **kwargs):
        """
        本物のMemberなら相手にDMを送ります。AIには不要なので、
        送ったことにして記録だけ残します。

        ★async（非同期）関数にしてあるのが重要です。
          main.py は `await player.send(...)` と書いているので、
          普通の関数にすると「awaitできない」というエラーで落ちます。
        """
        self.received_dms.append({
            "content": content,
            # discord.File オブジェクトそのものを持ち続けると
            # ファイルが開きっぱなしになるので、名前だけ控えます。
            "file": getattr(file, "filename", None) if file is not None else None,
        })

        # ★渡された画像ファイルは、ここで閉じます。
        #   main.py は送信する直前に discord.File(...) を作っており、
        #   これは実際に役職画像のファイルを開きます。本物のDiscordなら
        #   送信処理が閉じてくれますが、AIは送信しないので、
        #   閉じないとAIの人数ぶんファイルが開きっぱなしになります。
        if file is not None and hasattr(file, "close"):
            try:
                file.close()
            except Exception:
                pass

        return None   # 本物は送信したメッセージを返しますが、使われていません

    # ------------------------------------------------------------
    # 辞書のキーとして使うための取り決め
    # ------------------------------------------------------------
    # game["roles"] などは {プレイヤー: 値} という辞書なので、
    # プレイヤーは「辞書のキーにできる」必要があります。
    # IDが同じなら同一人物、という素直な決め方にします。

    def __hash__(self):
        return hash(("AIPlayer", self.id))

    def __eq__(self, other):
        return isinstance(other, AIPlayer) and other.id == self.id

    def __repr__(self):
        return f"<AIPlayer {self.display_name} id={self.id}>"


# ============================================================
# 便利関数
# ============================================================

def is_ai(player):
    """
    そのプレイヤーがAIかどうか。

    main.py 側で「AIの分だけ自動で入力する」といった処理を書くときに使います。
    分岐を全部に散らすのは避けますが、
    「AIの手番を代わりに埋める」ところでは当然この判定が要ります。
    """
    return isinstance(player, AIPlayer)


def make_ai_players(count, existing_players=None, rng=None):
    """
    AIプレイヤーを count 人ぶん作って返す。

    count            : 作る人数
    existing_players : すでにゲームにいるプレイヤー（人間もAIも）。
                       名前とIDの重複を避けるために見ます。
    rng              : 乱数（再現性のあるテストをしたい時に random.Random を渡す）

    ★名前が候補プールより多く必要になった場合は
      「つばき2」のように番号を足して必ず一意にします。
      名前がかぶるとゲーム中に誰を指しているのか分からなくなるので、
      ここは妥協せず必ず一意にします。
    """
    import random as _random
    if rng is None:
        rng = _random

    existing_players = list(existing_players or [])

    # すでに使われている名前（人間のニックネームも含む）
    used_names = {getattr(p, "display_name", str(p)) for p in existing_players}

    # すでに使われているAIのID
    used_ids = {p.id for p in existing_players if is_ai(p)}

    made = []
    # itertools.count は 1, 2, 3, ... と無限に数える道具です。
    # 空いているIDを探すのに使います。
    id_counter = itertools.count(AI_ID_BASE + 1)

    for _ in range(count):
        name = _pick_free_name(used_names, rng)
        used_names.add(name)

        # --- IDを決める（空いている番号を探す） ---
        new_id = next(id_counter)
        while new_id in used_ids:
            new_id = next(id_counter)
        used_ids.add(new_id)

        made.append(AIPlayer(display_name=name, ai_id=new_id))

    return made


def _pick_free_name(used_names, rng):
    """
    まだ誰も使っていない名前を1つ選ぶ。

    ★なぜこう書くのか
      募集画面では「AIを追加」を1回押すごとに make_ai_players(1) を
      呼びます。つまり1体ずつ作られます。
      以前は呼ばれるたびに候補を独立に抽選していたので、
      12個も名前があるのに 8体目までに同じ名前を引いてしまい、
      「つばき（AI）」と「つばき2（AI）」が同時に卓にいる、という
      みっともない状態になりました（実際に起きました）。

      なので「すでに使われている名前を候補から外してから選ぶ」形にします。
      これなら12体までは必ず違う名前になります。
    """
    # まだ使われていない素の名前を集める
    free = [n for n in AI_NAME_POOL if (n + AI_NAME_SUFFIX) not in used_names]
    if free:
        return rng.choice(free) + AI_NAME_SUFFIX

    # プールを使い切った場合だけ、末尾に番号を足して探す。
    # 「つばき2」で全部埋まっていたら「つばき3」を試す、という具合。
    suffix_num = 2
    while True:
        free = [f"{n}{suffix_num}" for n in AI_NAME_POOL
                if (f"{n}{suffix_num}" + AI_NAME_SUFFIX) not in used_names]
        if free:
            return rng.choice(free) + AI_NAME_SUFFIX
        suffix_num += 1


# ============================================================
# 役職構成のチェック
#
# ★なぜこれが必要なのか
#   bot は、ホストが＋／－ボタンで役職の数を自由に決められます。
#   ところがAIの推理エンジン（engine.py）は、あらかじめ表に書いてある
#   構成しか知りません（今は7人・8人・9人の3通りだけ）。
#
#   表に無い構成でAIを混ぜると、AIは「ありうる配役の一覧」を
#   作れないので、何も考えられない状態になります。
#   なので、ゲームを始める前に止める必要があります。
# ============================================================

# 役職名の対応表。
# ★Discord側（main.py）は英語のキー、AI側（engine.py）は日本語名を
#   使っているので、どこかで必ず変換が要ります。その1箇所がここです。
#   ここ以外で変換しないことで、対応漏れを探しやすくしています。
#
#   「ハデス」は engine.py に存在しません（廃止してセイレーンに
#   置き換えたため）。なので、ここにも意図的に入れていません。
#   結果として、ハデスが1人でもいる構成は自動的に「AI非対応」と
#   判定されます。これは意図した動作です。
JA_TO_ROLE_KEY = {
    "航海士": "navigator",
    "乗客": "passenger",
    "カロン": "charon",
    "セイレーン": "siren",
}
ROLE_KEY_TO_JA = {key: ja for ja, key in JA_TO_ROLE_KEY.items()}


# ============================================================
# AIが参加できる構成かどうかの判定
# ============================================================
# ★以前は engine.COMPOSITIONS（7/8/9人の3構成）と完全一致しないと
#   AI参加を断っていた。しかし ai_brain.init_game は、実際に配られた
#   役職を数えて構成表をその場で作っているので、頭脳側は元から
#   人数にも内訳にも依存していない。断る理由は本当は3つだけ：
#     ① AIが知らない役職（ハデス）が入っている
#     ② 人数が多すぎる
#     ③ ありうる配役の組み合わせが多すぎて、思考が重くなりすぎる
#   ③は人数より「内訳」で決まる。同じ12人でも
#     カロン2人 → 11,880通り / カロン3人 → 55,440通り
#   と5倍違う。なので人数の表ではなく、組み合わせ数で判定する。

# 「世界数 × (AIの人数 + 1)」の上限。
# ★なぜAIの人数を掛けるのか
#   AIは1人ずつ「自分だけが知っていること」を含んだ世界一覧を持ち、
#   さらに全員共通の公開視点を1つ持つ。つまりメモリはこの積に比例する。
# ★1通りあたり約0.63KB（実測：12人55,440世界×4視点＝221,760通りで139MB）。
#
# ★この値の決め方＝サーバのメモリ。本番は DigitalOcean の
#   512MB / Ubuntu 24.04（$4/月）。内訳の見積もりは
#     OS         約100MB
#     bot本体    約 70MB（Python＋discord.py）
#     残り       約340MB ここから同時進行するゲームぶんを引く
#   6万通り＝約38MB/ゲーム。2卓同時でも約76MBで収まる。
#   ★サーバを1GBに上げたら15万まで引き上げてよい（12人＋AI3体が通る）。
#   ★逆に落ちるようなら下げる。下げるだけで安全側に倒せる。
MAX_WORLD_LOAD = 60_000

# 募集画面での人数の上限。内訳までは分からない段階の、大まかな歯止め。
# 本当の可否は、役職を決めた後に check_composition が判定する。
MAX_PLAYERS_WITH_AI = 13


def max_players_with_ai():
    """AIが参加できる最大人数。募集画面でAIを足しすぎないようにするために使う。"""
    return MAX_PLAYERS_WITH_AI


def count_worlds(counts):
    """
    その役職構成で、ありうる配役が何通りあるかを返す（多項係数）。
    engine.build_worlds が実際に作る世界の数と一致する。
    ★実際に作らずに数だけ求めるのが要点。作ってから「多すぎた」では
      その時点でメモリを食ってしまう。
    """
    used = [c for c in counts.values() if c > 0]
    total = math.factorial(sum(used))
    for c in used:
        total //= math.factorial(c)
    return total


def check_composition(n, counts, n_ai=1):
    """
    その人数・その役職構成で、AIが参加できるかを判定する。

    n      : プレイヤーの合計人数
    counts : main.py の役職設定 {"navigator": 2, "passenger": 4, ...}
             （0人の役職も入っている）
    n_ai   : そのゲームに参加するAIの数（思考の重さの見積もりに使う）

    戻り値 : (参加できるか, 断る理由, 表示用の情報)
        理由 : None（OK） /
               "max_players"  … 人数が多すぎる
               "role"         … AIが知らない役職が入っている
               "too_complex"  … 組み合わせが多すぎる
        情報 : エラー文に埋める値の辞書
    """
    if n > MAX_PLAYERS_WITH_AI:
        return False, "max_players", {"max": MAX_PLAYERS_WITH_AI, "n": n}

    # 0人の役職は「いない」のと同じなので、判定から外す
    active = {key: c for key, c in counts.items() if c > 0}

    unknown = [key for key in active if key not in ROLE_KEY_TO_JA]
    if unknown:
        return False, "role", {"roles": unknown}

    load = count_worlds(active) * (max(n_ai, 1) + 1)
    if load > MAX_WORLD_LOAD:
        return False, "too_complex", {"load": load, "limit": MAX_WORLD_LOAD,
                                      "worlds": count_worlds(active)}

    return True, None, {"load": load, "worlds": count_worlds(active)}


# ============================================================
# 単体確認用
#   python ai_player.py で実行すると、ここが動きます。
# ============================================================

if __name__ == "__main__":
    import asyncio
    import random

    print("=" * 60)
    print("ステップ1の確認：AIプレイヤーが Member のふりをできているか")
    print("=" * 60)

    # --- 人間プレイヤーの代わり（本物のMemberの最小限のマネ） ---
    class FakeHuman:
        def __init__(self, name, uid):
            self.display_name = name
            self.id = uid
        @property
        def mention(self):
            return f"<@{self.id}>"
        async def send(self, content=None, *, file=None, **kwargs):
            print(f"    [人間 {self.display_name} へのDM] {str(content)[:30]}...")
        def __repr__(self):
            return f"<FakeHuman {self.display_name}>"

    humans = [
        FakeHuman("のぞみ", 111111111111111111),
        FakeHuman("たくみ", 222222222222222222),
    ]

    # --- AIを3人作る ---
    rng = random.Random(42)          # 毎回同じ結果になるよう種を固定
    ais = make_ai_players(3, existing_players=humans, rng=rng)

    print("\n【1】作られたAI")
    for a in ais:
        print(f"    {a!r}")

    # --- main.py がやっていることを、そのまま真似してみる ---
    players = humans + ais

    print("\n【2】辞書のキーとして使えるか（game['roles'] の真似）")
    roles = {}
    role_list = ["navigator", "passenger", "charon", "passenger", "siren"]
    for p, r in zip(players, role_list):
        roles[p] = r
    for p, r in roles.items():
        print(f"    {p.display_name:<10} : {r}")

    print("\n【3】get_mentions() の真似（AIには通知が飛ばない文字列になるか）")
    print("   ", " ".join([p.mention for p in players]))

    print("\n【4】役職DMの配布（main.py の distribute_roles と同じ書き方）")
    async def distribute():
        for player, role_key in roles.items():
            msg = f"あなたの役職は【{role_key}】です。"
            await player.send(content=msg)
    asyncio.run(distribute())
    for a in ais:
        print(f"    AI {a.display_name} が受け取ったDM: {a.received_dms}")

    print("\n【5】IDで探せるか（discord.utils.get と同じ仕組み）")
    target_id = ais[1].id
    found = next((p for p in players if p.id == target_id), None)
    print(f"    id={target_id} を探す → {found!r}")

    print("\n【6】AIかどうかの判定")
    for p in players:
        print(f"    {p.display_name:<10} : {'AI' if is_ai(p) else '人間'}")

    print("\n【7】名前が足りない場合（14人ぶん作って重複が無いか）")
    many = make_ai_players(14, rng=random.Random(1))
    names = [m.display_name for m in many]
    ids = [m.id for m in many]
    print(f"    名前: {names}")
    print(f"    名前の重複: {'なし' if len(set(names)) == len(names) else '★あり（不具合）'}")
    print(f"    IDの重複  : {'なし' if len(set(ids)) == len(ids) else '★あり（不具合）'}")

    print("\n【8】人間と名前がかぶった場合の回避")
    # わざとAIと同じ名前を名乗る人間を用意する
    trickster = FakeHuman(AI_NAME_POOL[0] + AI_NAME_SUFFIX, 333333333333333333)
    avoided = make_ai_players(2, existing_players=[trickster], rng=random.Random(0))
    print(f"    人間の名前: {trickster.display_name}")
    print(f"    AIの名前  : {[a.display_name for a in avoided]}")

    print("\n【9】AIが参加できる人数と構成")
    print(f"    人数の上限          : {max_players_with_ai()}人")
    print(f"    組み合わせ数の上限  : {MAX_WORLD_LOAD:,}通り（世界数×(AI人数+1)）")

    # main.py の初期値をそのまま持ってきて判定する
    def default_counts(n):
        """main.py の get_default_role_counts と同じもの（import を避けるため写し）"""
        if n <= 4: return {"navigator": 0, "passenger": max(0, n-1), "charon": 1, "hades": 0, "siren": 0}
        if n == 5: return {"navigator": 1, "passenger": 2, "charon": 1, "hades": 0, "siren": 1}
        if n == 6: return {"navigator": 1, "passenger": 3, "charon": 1, "hades": 0, "siren": 1}
        if n == 7: return {"navigator": 2, "passenger": 3, "charon": 2, "hades": 0, "siren": 0}
        if n == 8: return {"navigator": 2, "passenger": 3, "charon": 2, "hades": 0, "siren": 1}
        return {"navigator": 2, "passenger": max(4, n - 5), "charon": 2, "hades": 0, "siren": 1}

    print("\n    ・bot の初期値のまま、AI3体で始めた場合")
    for n in range(5, 15):
        ok, why, info = check_composition(n, default_counts(n), n_ai=3)
        detail = (f"{info.get('worlds', 0):>7,}通り" if "worlds" in info else "")
        print(f"      {n:>2}人: {'OK  ' if ok else 'NG  '}{detail}"
              f"{'' if ok else '   理由: ' + why}")

    print("\n    ・9人でホストが役職をいじった場合（AI3体）")
    cases = [
        ("元の構成",                {"navigator": 2, "passenger": 4, "charon": 2, "hades": 0, "siren": 1}),
        ("ハデスを1人入れた",       {"navigator": 2, "passenger": 3, "charon": 2, "hades": 1, "siren": 1}),
        ("セイレーンを外した",      {"navigator": 2, "passenger": 5, "charon": 2, "hades": 0, "siren": 0}),
        ("カロンを3人にした",       {"navigator": 2, "passenger": 3, "charon": 3, "hades": 0, "siren": 1}),
        ("航海士を1人にした",       {"navigator": 1, "passenger": 5, "charon": 2, "hades": 0, "siren": 1}),
        ("航海士4・カロン3",        {"navigator": 4, "passenger": 1, "charon": 3, "hades": 0, "siren": 1}),
    ]
    for label, c in cases:
        ok, why, info = check_composition(9, c, n_ai=3)
        print(f"      {label:<20} → {'OK' if ok else 'NG(' + str(why) + ')':<16}"
              f" {info.get('worlds', 0):>7,}通り")

    print("\n" + "=" * 60)
    print("確認終了")
    print("=" * 60)
