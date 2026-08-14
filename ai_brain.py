# ============================================================
# ai_brain.py
#   Discordのゲーム（main.py）と、AIの頭脳（engine/rules/decision/talk）
#   をつなぐ橋渡し役。
#
# ★なぜ橋渡しが必要なのか
#   両者はプレイヤーの表し方が違います。
#     main.py : discord.Member というオブジェクト
#     AI側     : "A" "B" のような、ただの文字列
#
#   オブジェクトをそのままAI側に渡す案も考えましたが、decision.py と
#   talk.py が sorted(players)（名前順に並べる）を何箇所も使っており、
#   Discordのオブジェクトは大小比較ができないのでエラーになります。
#   なので、ここで「オブジェクト ↔ 文字列」の対応表を持ちます。
#
# ★AI側の名前には、Discordの表示名をそのまま使う
#   "A" "B" のような記号にすると、talk.py が作る発言が
#   「Aが怪しい」になってしまい、あとで名前に戻す処理が必要になります。
#   戻し忘れると意味不明な発言が出るので、最初から表示名で通します。
#
#   ただし対応表はゲーム開始時に一度だけ作って固定します。
#   途中でニックネームを変えられても、AIの頭の中の名前は変わりません
#   （変わると、それまでに積み上げた推理が全部迷子になるため）。
# ============================================================

import random

import engine
import rules as rules_mod
import decision
import talk
import ai_player


# ============================================================
# 役職名・行先の変換表
# ============================================================
# main.py は英語のキー、AI側は日本語名を使う。
# 変換は必ずこの表を通す（あちこちで直接書かない）。
ROLE_KEY_TO_JA = {key: ja for ja, key in ai_player.JA_TO_ROLE_KEY.items()}

# 行先。main.py の ActionInputView が使う値に合わせる。
DEST_JA_TO_BOT = {
    "操舵室": "bridge",
    "図書室": "library",
    "談話室": "lounge",
}
DEST_BOT_TO_JA = {bot: ja for ja, bot in DEST_JA_TO_BOT.items()}


# ============================================================
# 調整つまみ
# ============================================================

# 1日目、カロンが「操舵室は混んでいるか」を判断できないときの既定値。
#
# ★経緯：simulate.py では、航海士・乗客が先に行先を決めてから
#   カロンが「今日は何人操舵室に行きそうか」を見て×を出すか決めていた。
#   しかしDiscordでは、その日の他人の行先は結果発表まで伏せられている。
#   なので2日目以降は「前日の操舵室の人数」（これは全員が見られる公開情報）
#   で代用する。1日目だけは履歴が無いので、この値を使う。
#
#   Trueにすると1日目から×を出しやすくなる（確率0.8）。
#   Falseなら出しにくい（0.2）。1日目に×を出すのは実プレイでも
#   risky なので、控えめな False にしてある。
DAY1_ASSUME_BRIDGE_CROWDED = False

# AIが入力を提出するまでの待ち時間（秒）。この範囲でばらつかせる。
#
# ★なぜ待つのか
#   このゲームは「誰が何番目に、何分何秒で提出したか」が全員に見える。
#   人間プレイヤーはそこも読んで遊ぶ（早すぎる＝迷いがない＝役職が
#   決まっている、など）。AIが即答すると毎回1〜3着を独占してしまい、
#   明らかに浮くうえ、順位という情報そのものが壊れる。
#
# ★なぜフェーズごとに違うのか
#   フェーズによって人間がかける時間がまるで違う。
#     朝の行先   : 議論してから決めるので長い
#     攻撃・妨害 : 選ぶだけなので短い（しかも大半の人は「なし」を出すだけ）
#     追放投票   : その中間
#   全部同じ長さにすると、短いフェーズでAI待ちの空き時間が生まれて
#   テンポが悪くなる。実際に遊んで「待たされる」と分かったので分けた。
#
#   最大値は「全員が必ずこの秒数までには出し終わる」上限。
#   最小値は「これより早くは出さない」下限（人間らしさのため）。
#   （main.run_ai_actions は乱数を昇順に並べて絶対時刻として待つので、
#     最大値がそのまま「最後の1人が提出する時刻」になる）
#   ★動作確認で待たされるのが邪魔なら、一時的に小さくしてよい。
#   ★朝の行先は 60→45 に短縮（2026-08-13、実プレイで「決めるのが遅い」と
#     指摘されたため）。
AI_ACTION_DELAY_MIN, AI_ACTION_DELAY_MAX = 8.0, 45.0    # 朝：行先とカード
AI_ATTACK_DELAY_MIN, AI_ATTACK_DELAY_MAX = 3.0, 15.0    # 攻撃・妨害
AI_VOTE_DELAY_MIN,   AI_VOTE_DELAY_MAX   = 6.0, 45.0    # 夜：追放投票

# AIの発言と発言のあいだの間隔（秒）。
# ★まとめて投稿すると9人ぶんが一瞬で流れて「議論」に見えないので、
#   人が読める速さで少しずつ出す。ただしAI8体だと
#   この間隔×8だけ時間がかかるので、上の入力の待ち時間より短くする。
TALK_GAP_MIN, TALK_GAP_MAX = 2.0, 5.0


# ============================================================
# 名前の対応表を作る
# ============================================================

def _build_names(players):
    """
    プレイヤーのオブジェクトから、AI側で使う文字列の名前を作る。

    ★表示名が重複していたら番号を付けて必ず一意にします。
      Discordでは別人が同じニックネームを付けられるので、
      そのまま使うと辞書のキーがぶつかって、2人が1人に潰れます。
    """
    name_of = {}
    player_of = {}
    used = set()

    for p in players:
        base = getattr(p, "display_name", None) or str(p)
        name = base
        n = 2
        while name in used:
            name = f"{base}({n})"
            n += 1
        used.add(name)
        name_of[p] = name
        player_of[name] = p

    # 順序を固定する。engine.build_worlds に渡す並びが毎回変わると
    # 世界の並びも変わって、デバッグの再現が取れなくなる。
    names = [name_of[p] for p in players]
    return names, name_of, player_of


def _known_teammates(player, players, roles, rules):
    """
    その人が最初から知っている仲間を {相手のオブジェクト: 日本語役職名} で返す。

    ★simulate.py は「航海士同士は必ず互いを知る」と決め打ちしていたが、
      ここでは bot の実際のルール設定（ホストがON/OFFできる）を読む。
      こうすると「6人プレイでは航海士は互いを知らない」も自動で正しくなる
      （main.py の RuleSetupView が n>=7 のときだけONにしているため）。
    """
    my_role = roles[player]
    found = {}

    def add(role_key, enabled):
        if not enabled:
            return
        for q in players:
            if q != player and roles.get(q) == role_key:
                found[q] = ROLE_KEY_TO_JA[role_key]

    if my_role == "navigator":
        add("navigator", rules.get("navigator", True))
    elif my_role == "charon":
        add("charon", rules.get("charon", True))
        add("siren", rules.get("c_knows_s", False))
    elif my_role == "siren":
        add("charon", rules.get("s_knows_c", True))
        add("siren", rules.get("siren_knows", True))
    # 乗客は何も知らない。
    # ハデスは engine.py に存在しない役職なので扱わない
    # （ハデス入りの構成は、そもそもAI参加時に開始できないよう止めてある）。

    return found


# ============================================================
# ゲーム開始時の準備
# ============================================================

def init_game(game):
    """
    配役が終わった直後に一度だけ呼ぶ。
    game["ai_state"] に、AIが考えるために必要なもの一式を作る。

    AIが1人もいなければ何もしない（人間だけのゲームに影響を与えない）。
    """
    players = game["players"]
    if not any(ai_player.is_ai(p) for p in players):
        return None

    roles = game["roles"]
    rules = game.get("rules", {})

    names, name_of, player_of = _build_names(players)

    # 実際に配られた役職から構成表を作る（日本語名で数える）
    composition = {}
    for p in players:
        ja = ROLE_KEY_TO_JA.get(roles[p])
        if ja is None:
            # ハデスなど engine.py が知らない役職が混ざっている。
            # 本来ここには来ない（開始前に止めている）が、
            # 黙って変な推理をするより、AIを動かさない方が安全。
            return None
        composition[ja] = composition.get(ja, 0) + 1

    # ---- 各AIの「自分だけの視点」を作る ----
    private_worlds = {}
    for p in players:
        if not ai_player.is_ai(p):
            continue   # 人間の頭の中は作らない
        my_name = name_of[p]

        # 自分の役職と、最初から知っている仲間を「確定情報」として埋め込む。
        # 図書室で役職を見たのと同じ仕組み（絶対に正しい私的な情報）を使う。
        reveals = [{"day": 0, "target": my_name, "role": ROLE_KEY_TO_JA[roles[p]]}]
        for mate, mate_role_ja in _known_teammates(p, players, roles, rules).items():
            reveals.append({"day": 0, "target": name_of[mate], "role": mate_role_ja})

        worlds = engine.build_worlds(names, composition)
        rules_mod.apply_library_rule(worlds, {"library_reveals": reveals})
        private_worlds[my_name] = worlds

    # ---- 「誰でも見られる情報だけ」の視点（1つを全員で共有）----
    # ★これが情報漏洩を防ぐ要。航海士が相方を知ったまま投票すると
    #   精度が高すぎて一発でバレるので、他人に見える行動にはこちらを使う。
    public_worlds = engine.build_worlds(names, composition)

    state = {
        "names": names,
        "name_of": name_of,
        "player_of": player_of,
        "composition": composition,
        "private_worlds": private_worlds,
        "public_worlds": public_worlds,
        # 公開情報の積み上げ（simulate.py の shared_observations と同じ形）
        "observations": {
            "destinations": {},
            "bridge_results": {},
            "attacks": {},
            "attack_targets": {},
            "library_reports": {},
            "votes": {},
            "exiled": {},
            "deaths": {},
        },
        # カロンが前日に出したカード（クールダウンの判断に使う）
        "charon_last_card": {},
        # 人格（口調と、言い切りの強さの基準線）。1人1つ固定で割り当てる。
        "personas": {},
        # 前日「一番怪しい」と見ていた相手（ひとり言の話題に使う）
        "suspected_history": {},
        # その日に決めた補助的な行動（図書室で誰を見るか、誰を襲うか等）。
        # 実際に使うのは後のフェーズなので、ここに取っておく。
        "plans": {},
        # ★敵対心（decision.GRUDGE_* 参照）。図書室で名指しされた・投票された・
        #   発言で疑われた相手を根に持つ。推理ではなく感情の層で、
        #   誰を疑うか／誰に投票するかにだけ効く。
        "grudges": decision.new_grudges(),
    }
    # ---- 人格を割り当てる ----
    # ★人格は「そのAIの表示名そのもの」です。名前が「探偵AI」なら人格も
    #   「探偵AI」。画面に出ている名前と喋り方がずれる事故が構造的に
    #   起きないようにするため、対応表を作らずこの形にしています。
    #   （talk.PERSONA_NAMES と ai_player.AI_NAME_POOL は同じ11個です）
    #   名前は make_ai_players が重複しないように配るので、同じ喋り方の人が
    #   卓に2人いることもありません。
    #
    # ★名前プールを使い切った時だけ「探偵AI2」のような名前になります。
    #   その場合は末尾の数字を落として人格を引きます（数字が付いただけで
    #   喋り方が共通プールに戻ってしまうと不自然なため）。
    known = set(talk.PERSONA_NAMES)
    for name in sorted(private_worlds):
        if name in known:
            state["personas"][name] = name
        else:
            base = name.rstrip("0123456789")
            state["personas"][name] = base if base in known else None

    game["ai_state"] = state
    return state


# ============================================================
# 朝：行先とカードを決める
# ============================================================

def _alive_names(game):
    """生きている人のAI側の名前一覧（元の並び順を保つ）"""
    st = game["ai_state"]
    dead = game.get("dead", [])
    return [st["name_of"][p] for p in game["players"] if p not in dead]


def _estimate_bridge_crowd(game):
    """
    「今日は操舵室が混みそうか」の見積もり。

    ★Discordでは、その日の他人の行先は結果発表まで伏せられている。
      なので前日の操舵室の人数を使う。これは結果発表で全員が見た
      公開情報なので、使ってもズルにならない。
      1日目は履歴が無いので、調整つまみの既定値を使う。

    ★★ここで「今日、他のAIが操舵室へ行くと決めた数」を足してはいけない。
      simulate.py は足していたが、あれは全員AIで「朝の議論で行先を
      宣言し合う」場面の再現だったから成立していた。
      Discordでは人間の議論をAIが読めない以上、同じ理屈は使えず、
      ただの情報漏洩（カロン役のAIが、航海士役のAIの当日の行先を
      知っている状態）になる。
    """
    st = game["ai_state"]
    day = game.get("day", 1)
    yesterday = st["observations"]["destinations"].get(day - 1)
    if not yesterday:
        need = game.get("settings", {}).get("need", 2)
        return need if DAY1_ASSUME_BRIDGE_CROWDED else 0
    return sum(1 for d in yesterday.values() if d == "操舵室")


def decide_day_actions(game, rng=random):
    """
    その日、各AIが「どこへ行き、どのカードを出すか」を決める。

    戻り値: {プレイヤーのオブジェクト: {"dest": "bridge"/"library"/"lounge",
                                       "card": "c"/"x"}}

    ★決める順番は simulate.py と同じ（図書室 → 航海士・乗客 → カロン →
      セイレーン）。カロンは操舵室の混み具合を見てから、セイレーンは
      誰が図書室へ行くかを見てから決めたいので、この順番に意味がある。

    ★ここは1日に1回だけ呼ばれるので、敵対心の減衰もここで行う
      （人間も、いつまでも同じ人を恨み続けはしない）。
    """
    st = game.get("ai_state")
    if not st:
        return {}

    decision.decay_grudges(st["grudges"])   # ★敵対心は1日ごとに薄れる

    roles = game["roles"]
    rules = game.get("rules", {})
    settings = game.get("settings", {})
    day = game.get("day", 1)
    dead = game.get("dead", [])
    used_library = game.get("used_library", [])

    alive = _alive_names(game)
    # 今日決める必要があるAI（生きていて、まだ入力していない人）
    ai_todo = [p for p in game["players"]
               if ai_player.is_ai(p) and p not in dead and p not in game.get("inputs", {})]
    if not ai_todo:
        return {}

    name_of = st["name_of"]
    private = st["private_worlds"]
    public = st["public_worlds"]

    x_pt = game.get("pt", {}).get("x", 0)
    c_pt = game.get("pt", {}).get("c", 0)
    win_x = settings.get("win_x", 5)
    win_c = settings.get("win_c", 12)
    bridge_need = settings.get("need", 2)

    library_on = rules.get("library", True)
    charons_alive = [p for p in game["players"]
                     if roles.get(p) == "charon" and p not in dead]

    # volunteers / dest は、朝のひとり言（「図書室に行きたかった」など）で使う
    plan = {"library_target": {}, "attack": {}, "charm": {},
            "volunteers": [], "dest": {}}
    actions = {}          # プレイヤーのオブジェクト -> (行先の日本語, カード)

    # ---- (1) 図書室に立候補するか ----
    # ★制約：本来は「朝の議論で何人が手を挙げたか」を見て、譲るかどうかを
    #   決める仕組み。しかしDiscordでは人間の議論を読めないので、
    #   AIから見えるのは「AI同士の立候補数」だけになる。
    #   結果として、AIは本来より譲りにくくなる。ここは割り切り。
    # 決着への近さ。図書室カードを使い切れずに終わるのを防ぐために使う
    # （ptはどちらも全員に公開されているので、判断に使っても漏洩にはならない）。
    urgency = decision.game_urgency(c_pt, win_c, x_pt, win_x)

    volunteers = []
    if library_on:
        for p in ai_todo:
            if p.id in used_library:
                continue            # 図書室カードは1人1回だけ
            role_ja = ROLE_KEY_TO_JA[roles[p]]
            # 「今日は図書室どころではない」の判定は decision 側に集約してある
            # （simulate.py と同じ関数を呼ぶ＝条件が食い違わない）。
            if role_ja in engine.HUMAN_SIDE:
                # 人間陣営には制限がない（skip_library_today 参照）。
                # 見積もり計算(expected_charon_free_count)は世界を全部走査するので
                # 決して安くない。使われない値をわざわざ作らない。
                possible, ally_alive = 0, False
            else:
                # カロン陣営は仲間を知っているので正確に数えられる
                possible = len(alive) - len(charons_alive)
                ally_alive = any(q != p for q in charons_alive)
            if decision.skip_library_today(
                    role_ja, c_pt, win_c, x_pt, win_x, possible,
                    ally_alive=ally_alive):
                continue
            if decision.volunteer_for_library(role_ja, rng=rng, urgency=urgency):
                volunteers.append(p)

    library_goers = []
    for p in volunteers:
        if decision.decide_library_final(ROLE_KEY_TO_JA[roles[p]], len(volunteers),
                                         rng=rng, urgency=urgency):
            library_goers.append(p)

    # ---- (2) 図書室組・航海士・乗客が決める ----
    bridge_crowd = _estimate_bridge_crowd(game)

    # 前日、人数不足で操舵が失敗していたか。○×の枚数の合計＝操舵室にいた
    # 生存者の数なので、これで正確に分かる（結果発表で全員が見た公開情報）。
    y_bridge = st["observations"]["bridge_results"].get(day - 1)
    shortage_yesterday = (
        y_bridge is not None and y_bridge["c"] + y_bridge["x"] < bridge_need)

    for p in ai_todo:
        my = name_of[p]
        role_ja = ROLE_KEY_TO_JA[roles[p]]
        worlds = private[my]

        if p in library_goers:
            actions[p] = ("図書室", "c")
            plan["library_target"][my] = decision.choose_library_target(
                worlds, alive, my, rng=rng)
        elif role_ja == "航海士":
            actions[p] = ("操舵室", "c")      # 航海士は必ず操舵室・必ず○
        elif role_ja == "乗客":
            actions[p] = (decision.passenger_plan_destination(
                worlds, alive, my, rng=rng,
                # リーチの日かどうかの判定に使う（自分の視点からの見積もり）
                alive_players=alive, c_pt=c_pt, win_c=win_c,
                # 「他人から見た自分の怪しさ」はカロンの時と同じく公開視点で測る。
                # 自分視点だと自分が乗客だと分かっているので必ず0%になる。
                my_public_suspicion=decision.suspicion_score(public, my),
                # 人数不足の歯止め。カロンが使っているのと同じ前日ベースの
                # 見積もり（当日の行先は伏せられているので使えない）。
                bridge_crowd_estimate=bridge_crowd,
                bridge_need=bridge_need,
                # 「ちゃんと操舵室に来てくれよ」の反応
                shortage_yesterday=shortage_yesterday,
            ), "c")

    # ---- (3) カロンが、混み具合を見てから決める ----
    for p in ai_todo:
        if roles.get(p) != "charon" or p in library_goers:
            continue
        my = name_of[p]
        ally = next((q for q in charons_alive if q != p), None)
        ally_name = name_of[ally] if ally is not None else None

        dest, card, atk = decision.charon_plan_action(
            private[my], alive, my, ally_name, alive, day, x_pt, win_x,
            played_x_yesterday=st["charon_last_card"].get(my) == "x",
            ally_played_x_yesterday=(ally_name is not None
                                     and st["charon_last_card"].get(ally_name) == "x"),
            c_pt=c_pt, win_c=win_c,
            # 「他人から見た自分の怪しさ」は必ず公開情報の視点で測る。
            # 自分の視点だと自分がカロンだと知っているので100%になってしまう。
            my_public_suspicion=decision.suspicion_score(public, my),
            bridge_crowd_so_far=bridge_crowd,
            bridge_need=bridge_need,
            # 人数不足を狙うのは「人が減ってきてから」（脱落は公開情報）
            deaths_count=len(dead),
            rng=rng,
        )
        actions[p] = (dest, card)
        if atk is not None:
            plan["attack"][my] = atk

    # ---- (4) セイレーンが、誰が図書室へ行くかを見てから決める ----
    for p in ai_todo:
        if roles.get(p) != "siren" or p in library_goers:
            continue
        my = name_of[p]
        # AIの中で図書室へ行くと分かっている人だけ渡す
        # （人間が図書室へ行くかどうかは、この時点では知りようがない）
        known_library = {name_of[q]: "図書室" for q in library_goers}
        dest, card, charm = decision.siren_plan_action(
            private[my], alive, my, declared_so_far_dest=known_library, rng=rng,
            # 人数不足を狙う日の判定に使う
            alive_players=alive, bridge_need=bridge_need,
            deaths_count=len(dead))
        actions[p] = (dest, card)
        if charm is not None:
            plan["charm"][my] = charm

    # ---- (5) 取りこぼしへの保険 ----
    for p in ai_todo:
        if p not in actions:
            actions[p] = ("操舵室", "c")

    plan["volunteers"] = [name_of[p] for p in volunteers]
    plan["dest"] = {name_of[p]: d for p, (d, _c) in actions.items()}
    st["plans"][day] = plan

    # ---- Discord側の言葉に変換して返す ----
    result = {}
    for p, (dest_ja, card) in actions.items():
        result[p] = {"dest": DEST_JA_TO_BOT[dest_ja], "card": card}
    return result


# ============================================================
# 夜：その日の結果を信念状態に反映する
#
# ★これがAIの「学習」にあたる、一番大事な処理。
#   ここが無いと、AIは初日の何も知らない状態のまま何日でも過ごす。
#   証拠が無いと他人の「航海士らしさ」は全員横並びの2/7≒28.6%にしか
#   ならず、カロンの攻撃の閾値50%を永久に超えないので誰も襲わないし、
#   投票も figuratively コイン投げになる。
# ============================================================

# main.py の行先の値 → AI側の日本語。
# "lounge_overwrite"（亡霊に談話室へ上書きされた）は、
# 「その人は実際に談話室にいた」ので談話室として扱う。
# 本人の意思でないことは forced_dest の方に別途記録する。
_RESULT_DEST_TO_JA = {
    "bridge": "操舵室",
    "library": "図書室",
    "lounge": "談話室",
    "lounge_overwrite": "談話室",
}


def record_day_results(game, results, c_count, x_count,
                       charmed=(), newly_dead=(), attack_targets=()):
    """
    1日の結果が確定した直後に呼ぶ。公開情報を記録して、
    全AIの信念状態を更新する。

    results        : main.py の day_results（{プレイヤー: {"dest":…, "card":…}}）
    c_count/x_count: その日の操舵室の○と✕の枚数
    charmed        : セイレーンに操舵室へ呼び寄せられた人（プレイヤーのオブジェクト）
    newly_dead     : その日の攻撃で死んだ人
    attack_targets : その日、攻撃の標的にされた人
    """
    st = game.get("ai_state")
    if not st:
        return

    day = game.get("day", 1)
    name_of = st["name_of"]
    obs = st["observations"]

    # ---- 誰がどこにいたか ----
    dests = {}
    forced = set()
    for p, data in results.items():
        d = data.get("dest")
        ja = _RESULT_DEST_TO_JA.get(d)
        if ja is None:
            continue          # 亡霊（type=ghost）はここに含めない
        dests[name_of[p]] = ja
        if d == "lounge_overwrite":
            forced.add(name_of[p])
    obs["destinations"][day] = dests
    if forced:
        obs.setdefault("forced_dest", {})[day] = forced

    # ---- 操舵室の○✕ ----
    obs["bridge_results"][day] = {"c": c_count, "x": x_count}

    # ---- 攻撃 ----
    # 攻撃は必ず誰か1人の死につながる（標的が死ぬか、返り討ちで本人が死ぬか）。
    # なので「その日の攻撃件数」＝「攻撃で死んだ人数」。
    obs["attacks"][day] = len(newly_dead)
    if attack_targets:
        obs["attack_targets"][day] = [name_of[p] for p in attack_targets]

    # 誰がどう脱落したか（翌朝の話題に使う。これも全員の目の前で起きた公開情報）
    if newly_dead:
        attackers = {q for q, tid in game.get("attacks", {}).items() if tid != "none"}
        obs["deaths"][day] = [
            (name_of[p], "返り討ち" if p in attackers else "攻撃") for p in newly_dead]

    # ---- セイレーンの呼び寄せ ----
    # ★simulate.py はこのルールを一度も使っていなかったが、Discordでは
    #   呼び寄せが🧜として全員に見えるので、証拠として使える。
    if charmed:
        obs.setdefault("siren_charms", {})[day] = [name_of[p] for p in charmed]

    # ---- カロンのクールダウン判定用（前日どのカードを出したか）----
    for p, data in results.items():
        if game["roles"].get(p) == "charon" and data.get("card"):
            st["charon_last_card"][name_of[p]] = data["card"]

    # ---- 全員の信念状態を、公開情報で絞り込む ----
    _apply_public_rules(st)


def _apply_public_rules(st):
    """公開情報から導けるルールを、全AIの視点と公開視点にかける。"""
    obs = st["observations"]
    for worlds in list(st["private_worlds"].values()) + [st["public_worlds"]]:
        rules_mod.apply_navigator_rule(worlds, obs)
        rules_mod.apply_bridge_count_rule(worlds, obs)
        rules_mod.apply_charon_attack_rule(worlds, obs)
        rules_mod.apply_attack_victim_rule(worlds, obs)
        rules_mod.apply_library_report_rule(worlds, obs)
        if obs.get("siren_charms"):
            rules_mod.apply_siren_charm_rule(worlds, obs)


def record_library_result(game, visitor, target, true_role_key):
    """
    図書室で役職を確認した本人だけに、その結果を教える。

    ★これは私的情報。確認した本人の視点にだけ入れる。
      公開視点や他のAIに入れると、全員が知っているはずのない事実を
      知っていることになる。
    """
    st = game.get("ai_state")
    if not st or not ai_player.is_ai(visitor):
        return
    my = st["name_of"][visitor]
    ja = ROLE_KEY_TO_JA.get(true_role_key)
    if ja is None:
        return
    rules_mod.apply_library_rule(
        st["private_worlds"][my],
        {"library_reveals": [{"day": game.get("day", 1),
                              "target": st["name_of"][target], "role": ja}]},
    )


# ============================================================
# 図書室：誰の役職を確認するか
# ============================================================

def pick_library_target(game, visitor):
    """
    図書室に入れた本人が、誰の役職を見るかを決める。
    戻り値はプレイヤーのオブジェクト（決められなければ None）。

    ★朝の時点で decide_day_actions が決めた相手を優先して使う。
      「朝に○○を見てくると言ったのに、実際は違う人を見た」という
      ズレを防ぐため（人間から見ると不自然に映る）。
      その相手がもう死んでいる等で使えない場合だけ、選び直す。
    """
    st = game.get("ai_state")
    if not st or not ai_player.is_ai(visitor):
        return None

    my = st["name_of"][visitor]
    day = game.get("day", 1)
    dead = game.get("dead", [])
    alive = _alive_names(game)

    planned = st["plans"].get(day, {}).get("library_target", {}).get(my)
    if planned and planned in alive and planned != my:
        return st["player_of"][planned]

    target = decision.choose_library_target(st["private_worlds"][my], alive, my)
    if target is None or target not in st["player_of"]:
        return None
    p = st["player_of"][target]
    return None if p in dead else p


# ============================================================
# 夜：投票
# ============================================================

def decide_votes(game, rng=random):
    """
    生きているAI全員の投票先を決める。
    戻り値: {投票する人: 投票先のオブジェクト or None（棄権）}
    """
    st = game.get("ai_state")
    if not st:
        return {}

    roles = game["roles"]
    dead = game.get("dead", [])
    alive = _alive_names(game)
    charons_alive = [p for p in game["players"]
                     if roles.get(p) == "charon" and p not in dead]

    votes = {}
    for p in game["players"]:
        if not ai_player.is_ai(p) or p in dead:
            continue
        if p in game.get("votes", {}):
            continue

        my = st["name_of"][p]
        role = roles.get(p)
        # ★投票の視点は、発言とまったく同じ「乗客のふり視点」に統一する
        #   （_talk_worlds と同じ考え方）。
        #   ・航海士：相方を知っている精度で投票すると航海士だとバレる
        #   ・カロン陣営：★以前は private_worlds を使っていて、自分と仲間以外に
        #     カロンがいないと知っているせいで残り全員が閾値を割り、実測で
        #     カロンの93.7%・セイレーンの100%が毎晩棄権していた。
        #     毎晩棄権する人＝カロン陣営、と機械的に読まれてしまう。
        #   ・乗客：private_worlds ＝ 公開情報＋自分は乗客 なので変化なし
        worlds = _talk_worlds(st, game, p)
        exclude = set()
        threshold = decision.VOTE_SUSPICION_THRESHOLD

        if role == "charon":
            # ★カロンは仲間の正体を100%知っているので、投票候補から仲間を除外する
            exclude = {st["name_of"][q] for q in charons_alive if q != p}
            threshold = decision.VOTE_SUSPICION_THRESHOLD_CHARON_SIDE
        elif role == "siren":
            # セイレーンはカロンを知っているので、同じ理由で除外する
            exclude = {st["name_of"][q] for q in charons_alive}
            threshold = decision.VOTE_SUSPICION_THRESHOLD_CHARON_SIDE

        target_name = decision.decide_vote(worlds, alive, my, alive,
                                           threshold=threshold,
                                           exclude=exclude, rng=rng,
                                           grudge=decision.grudge_of(st["grudges"], my))
        votes[p] = st["player_of"].get(target_name) if target_name else None
    return votes


# ============================================================
# 夜：カロンの襲撃／セイレーンの呼び寄せ
# ============================================================

def decide_attacks(game):
    """
    生きているAIの襲撃・呼び寄せの入力を決める。

    戻り値: {プレイヤー: {"attack": 相手のオブジェクト or None,
                          "charm":  相手のオブジェクト or None}}
    朝に決めた plans をそのまま使う（そこで決まっているのが本来の設計）。
    """
    st = game.get("ai_state")
    if not st:
        return {}

    roles = game["roles"]
    dead = game.get("dead", [])
    day = game.get("day", 1)
    plan = st["plans"].get(day, {})
    already_charmed = game.get("sirened", [])

    out = {}
    for p in game["players"]:
        if not ai_player.is_ai(p) or p in dead:
            continue
        my = st["name_of"][p]
        entry = {"attack": None, "charm": None}

        if roles.get(p) == "charon":
            tgt = plan.get("attack", {}).get(my)
            q = st["player_of"].get(tgt) if tgt else None
            if q is not None and q not in dead and q != p:
                entry["attack"] = q

        elif roles.get(p) == "siren":
            tgt = plan.get("charm", {}).get(my)
            q = st["player_of"].get(tgt) if tgt else None
            # main.py 側の制限に合わせて念のため確認する
            # （セイレーン同士は不可・自分は不可・1人につき1回だけ）
            if (q is not None and q not in dead and q != p
                    and roles.get(q) != "siren" and q.id not in already_charmed):
                entry["charm"] = q

        out[p] = entry
    return out


def charon_talk_plans(game):
    """
    カロンの密談で、人間のカロンに知らせるためのAIカロンの予定を返す。

    戻り値: {AIカロンのオブジェクト: {"dest": "bridge"/"library"/"lounge",
                                     "card": "c"/"x",
                                     "attack": 相手のオブジェクト or None}}

    ★なぜ必要か
      AIは人間の言葉を聞き取れないので、密談で相談ができない。
      だからせめて「AI側の予定」を一方的に伝えて、人間のカロンが
      それに合わせられるようにする。

    ★予定は必ず「すでに確定しているもの」を返す。
      行先とカードは朝に提出済みの入力（game["inputs"]）から、
      攻撃先は decide_attacks() から取る。あとで実際に提出される値と
      同じものを見せないと、密談が嘘になってしまう。
    """
    st = game.get("ai_state")
    if not st:
        return {}

    dead = game.get("dead", [])
    attack_plans = decide_attacks(game)

    out = {}
    for p in game["players"]:
        if not ai_player.is_ai(p) or p in dead:
            continue
        if game["roles"].get(p) != "charon":
            continue
        inp = game.get("inputs", {}).get(p)
        if not inp or inp.get("type") != "alive":
            continue      # まだ行先を出していない（通常ここには来ない）
        out[p] = {
            "dest": inp.get("dest", "lounge"),
            "card": inp.get("card", "c"),
            "attack": attack_plans.get(p, {}).get("attack"),
        }
    return out


# ============================================================
# 亡霊：誰かの行先を談話室へ上書きするか
# ============================================================

def decide_ghost_block(game, ghost, rng=random):
    """
    死んだAI（亡霊）が、誰かの行先を談話室に引きずり出すかを決める。
    戻り値: 相手のオブジェクト、または妨害しないなら None
    """
    st = game.get("ai_state")
    if not st or not ai_player.is_ai(ghost):
        return None

    my = st["name_of"][ghost]
    worlds = st["private_worlds"].get(my)
    if worlds is None:
        return None

    alive = _alive_names(game)
    # 前日に妨害された人は、今日は誰からも妨害されない（ルール）
    blocked = set()
    for uid in game.get("blocked_yesterday", []):
        q = next((x for x in game["players"] if x.id == uid), None)
        if q is not None:
            blocked.add(st["name_of"][q])

    target = decision.decide_ghost_overwrite(
        worlds, alive, ROLE_KEY_TO_JA.get(game["roles"].get(ghost)),
        alive, blocked_today=blocked, rng=rng)
    if target is None:
        return None
    return st["player_of"].get(target)


# ============================================================
# 発言（ひとり言）
#
# ★ここはゲームの判断に一切影響しない演出。
#   ただし「何を喋らせるか」は情報漏洩の最前線でもある。
#   発言に使う視点は、次のように使い分ける：
#     乗客        → 自分の視点（私的情報を持たないので問題ない）
#     それ以外    → 公開情報＋「自分は乗客だ」という前提の視点
#                   （＝乗客のふり。engine.view_as_passenger のコメント参照）
# ============================================================

def _talk_worlds(st, game, p):
    """発言に使ってよい視点を返す。"""
    my = st["name_of"][p]
    if game["roles"].get(p) == "passenger":
        return st["private_worlds"][my]
    # ★航海士・カロン・セイレーンは「自分は乗客だ」という前提で喋る。
    #   公開情報だけの視点(public_worlds)には自分が誰かの情報すら
    #   入っていないので、そのままでは「自分ではない」と言い返せない。
    return engine.view_as_passenger(st["public_worlds"], my)


def _ally_names(st, game, p):
    """その人が知っている仲間の名前（発言から除外するために使う）。"""
    return [st["name_of"][q] for q in
            _known_teammates(p, game["players"], game["roles"], game.get("rules", {}))]


def _speaking_ais(game):
    """今しゃべるAI。亡霊（死んだ人）は喋らない（ルールブック通り）。"""
    dead = game.get("dead", [])
    return [p for p in game["players"] if ai_player.is_ai(p) and p not in dead]


def morning_speeches(game, rng=random):
    """
    朝の議論のひとり言をまとめて作る。
    戻り値: [(プレイヤー, 発言), ...] 話す順
    """
    st = game.get("ai_state")
    if not st:
        return []

    day = game.get("day", 1)
    obs = st["observations"]
    alive = _alive_names(game)
    plan = st["plans"].get(day, {})
    settings = game.get("settings", {})

    yesterday_dest = obs["destinations"].get(day - 1)
    yesterday_bridge = obs["bridge_results"].get(day - 1)
    used_library = game.get("used_library", [])
    library_left = any(p.id not in used_library
                       for p in game["players"] if p not in game.get("dead", []))

    # ★board に入れてよいのは「全員が見て分かる情報」だけ。
    #   ここに私的情報を混ぜると発言から役職が即バレする。
    board = {
        "day": day,                    # 初日だけ専用のセリフに切り替わる
        # 図書室を使わないルール（7人プレイなど）では、発言から図書室を消す
        "library_on": game.get("rules", {}).get("library", True),
        "destinations": obs["destinations"],
        "yesterday_x_count": yesterday_bridge["x"] if yesterday_bridge else None,
        # ★日ごとの×の枚数（全部の履歴）。全員が見て分かる公開情報。
        #   「3日目に×が出たあの面子」のように、前日以外の日についても
        #   話せるようにするために足した（talk.build_evidence_group_clause）。
        "bridge_x": {d: r["x"] for d, r in obs["bridge_results"].items()},
        # ★日ごとの攻撃の件数。カロンは談話室にいる日しか攻撃できないので、
        #   攻撃が起きた日の談話室の顔ぶれも「必ずカロンを含む集合」になる。
        "attacks_by_day": dict(obs.get("attacks") or {}),
        "votes": obs["votes"].get(day - 1),
        "exiled": obs["exiled"].get(day - 1),
        "deaths": obs["deaths"].get(day - 1),
        "c_pt": game.get("pt", {}).get("c", 0),
        "x_pt": game.get("pt", {}).get("x", 0),
        "win_c": settings.get("win_c", 12),
        "win_x": settings.get("win_x", 5),
        "alive": alive,
        "library_idle": bool(yesterday_dest) and not any(
            d == "図書室" for d in yesterday_dest.values()),
        "library_left": library_left,
        "library_reports": obs["library_reports"],
    }

    # 同じ日に何人もが同じ公開情報を復唱しないよう、話した話題を持ち回る
    spoken_topics = set()
    prior_suspect = None
    out = []

    speakers = _speaking_ais(game)
    rng.shuffle(speakers)          # 毎日同じ順で喋ると不自然なので混ぜる
    for p in speakers:
        my = st["name_of"][p]
        worlds = _talk_worlds(st, game, p)
        try:
            line, used, said = talk.generate_monologue(
                ROLE_KEY_TO_JA[game["roles"][p]], worlds, alive, my,
                my_actual_dest=plan.get("dest", {}).get(my),
                ally_names=_ally_names(st, game, p), board=board,
                suspected_yesterday=st["suspected_history"].get(my),
                wanted_library=(my in plan.get("volunteers", [])),
                spoken_topics=spoken_topics, prior_suspect=prior_suspect,
                persona=st["personas"].get(my), rng=rng,
                grudge=decision.grudge_of(st["grudges"], my),
            )
        except Exception:
            continue               # 1人の発言が失敗してもゲームは止めない
        spoken_topics |= used
        if said is not None:
            prior_suspect = said
            # ★名指しで疑われた人は、疑ってきた相手を少し根に持つ
            decision.add_grudge(st["grudges"], said, my,
                                decision.GRUDGE_NAMED_IN_TALK)
        # 次の日の話題用に「今日一番怪しいと思っていた相手」を控える
        st["suspected_history"][my] = talk.find_suspected_target(worlds, alive, my)
        if line:
            out.append((p, line))
    return out


def night_speeches(game, rng=random):
    """
    夜の議論のコメントをまとめて作る。
    戻り値: [(プレイヤー, 発言), ...]
    """
    st = game.get("ai_state")
    if not st:
        return []

    day = game.get("day", 1)
    obs = st["observations"]
    alive = _alive_names(game)
    bridge = obs["bridge_results"].get(day, {})

    night_board = {
        "today_destinations": obs["destinations"].get(day, {}),
        "today_x_count": bridge.get("x", 0),
        "attack_happened": bool(obs["attacks"].get(day, 0)),
        "alive": alive,
        "library_reports": obs["library_reports"],
        "library_on": game.get("rules", {}).get("library", True),
        # ★どちらかの陣営が勝利ptに届いた日は専用のセリフに切り替える
        #   （talk.build_endgame_clause 参照）。勝敗判定はこの夜の追放が
        #   終わってから行われるので、この時点ではまだ決着していない。
        "c_pt": game.get("pt", {}).get("c", 0),
        "x_pt": game.get("pt", {}).get("x", 0),
        "win_c": game.get("settings", {}).get("win_c"),
        "win_x": game.get("settings", {}).get("win_x"),
    }

    out = []
    speakers = _speaking_ais(game)
    rng.shuffle(speakers)
    # その日すでにAIが使った文。同じ文が2人から出ないように持ち回る
    # （朝の spoken_topics と同じ考え方。talk._pick_fresh 参照）。
    # ★人間の発言は読めない仕様なので、避けるのはAI同士の重複だけ。
    spoken_lines = set()
    for p in speakers:
        my = st["name_of"][p]
        try:
            comment, used_lines = talk.generate_night_comment(
                _talk_worlds(st, game, p), alive, my,
                role=ROLE_KEY_TO_JA[game["roles"][p]],
                ally_names=_ally_names(st, game, p),
                board=night_board, persona=st["personas"].get(my),
                spoken_lines=spoken_lines, rng=rng,
                grudge=decision.grudge_of(st["grudges"], my),
            )
        except Exception:
            continue
        spoken_lines |= used_lines
        if comment:
            out.append((p, comment))
    return out


def library_report(game, visitor, target, true_role_key, rng=random):
    """
    図書室に入ったAIが、夜の議論の口火として何を報告するかを決める。

    戻り値: 報告の文章（喋らないなら None）

    ★この関数は「本当の役職」を talk.py に渡さない。
      嘘をつくかどうかは decision.decide_library_report が決め、
      talk.py には「何と申告するか」だけを渡す。
      talk.py が真偽を知らなければ、うっかり嘘だと分かる言い方を
      してしまう事故が構造的に起きない。
    """
    st = game.get("ai_state")
    if not st or not ai_player.is_ai(visitor):
        return None

    my = st["name_of"][visitor]
    true_ja = ROLE_KEY_TO_JA.get(true_role_key)
    if true_ja is None:
        return None

    claimed = decision.decide_library_report(
        ROLE_KEY_TO_JA[game["roles"][visitor]], true_ja, rng=rng)

    # ★先に発言を作る。作れなかった場合は「何も言わなかった」ことにする。
    #   順番が逆だと、チャットに何も出ていないのにAIだけが報告内容を
    #   知っている状態になり、人間から見て理由の分からない投票が起きる。
    try:
        speech = talk.generate_library_report_speech(
            _talk_worlds(st, game, visitor), st["name_of"][target], claimed,
            persona=st["personas"].get(my), rng=rng)
    except Exception:
        return None
    if not speech:
        return None

    # ★申告内容は「実際にチャットで言われたこと」なので、全員の推理材料に
    #   なる。本当か嘘かは記録しない（誰も知らないので当然）。
    day = game.get("day", 1)
    st["observations"]["library_reports"].setdefault(day, []).append({
        "reporter": my,
        "target": st["name_of"][target],
        "claimed_role": claimed,
        "confidence_multiplier": (
            rules_mod.REPORT_CHARON_CONFIDENCE_MULTIPLIER
            if game["roles"][visitor] in ("charon", "siren")
            else rules_mod.REPORT_HUMAN_HESITATION_MULTIPLIER
        ),
    })
    # ★「お前はカロンだ」と名指しされた側は、言ってきた相手に強く反発する。
    #   本当にカロンでも、濡れ衣を着せられた無実の人でも同じ反応になる
    #   （どちらも「あいつは嘘つきだ」と主張する側に回るのが自然）。
    if claimed in engine.CHARON_SIDE:
        decision.add_grudge(st["grudges"], st["name_of"][target], my,
                            decision.GRUDGE_LIBRARY_ACCUSED)
    return speech


def record_vote_results(game, exiled):
    """
    投票の結果を、翌朝の話題用に記録する。
    （投票先も追放結果も、全員の目の前で公開される情報）
    """
    st = game.get("ai_state")
    if not st:
        return
    day = game.get("day", 1)
    name_of = st["name_of"]

    votes = {}
    for voter, data in game.get("votes", {}).items():
        tid = data.get("target")
        tgt = None
        if tid and tid != "none":
            tgt = next((q for q in game["players"] if str(q.id) == str(tid)), None)
        votes[name_of[voter]] = name_of[tgt] if tgt else None
    st["observations"]["votes"][day] = votes
    st["observations"]["exiled"][day] = name_of[exiled] if exiled else None

    # ★自分に票を入れてきた相手を根に持つ（投票は全員に公開される）。
    #   人間陣営同士が誤解から敵対し合って自滅する、という人間くさい展開を
    #   生む部分（decision.GRUDGE_* 参照）。
    for voter, target in votes.items():
        if target is not None:
            decision.add_grudge(st["grudges"], target, voter,
                                decision.GRUDGE_VOTED_AGAINST)

    # ★「AがBに投票した＝この2人は仲間ではない」を推理に反映する。
    #   前日ぶんだけを1回反映する（毎日全履歴をかけると効きすぎる）。
    charons_know = bool(game.get("rules", {}).get("charon", True))
    for worlds in list(st["private_worlds"].values()) + [st["public_worlds"]]:
        rules_mod.apply_vote_line_rule(worlds, st["observations"],
                                       charons_know_each_other=charons_know,
                                       only_day=day)


# ============================================================
# 単体確認用
#   python ai_brain.py で実行すると、ここが動きます。
# ============================================================

if __name__ == "__main__":
    print("=" * 66)
    print("ステップ3の確認：AIが行先とカードを決められるか")
    print("=" * 66)

    def make_game(seed):
        rng = random.Random(seed)
        players = ai_player.make_ai_players(9, rng=rng)
        # 9人の正しい構成で配役する
        role_list = (["navigator"] * 2 + ["charon"] * 2 + ["siren"] * 1
                     + ["passenger"] * 4)
        rng.shuffle(role_list)
        game = {
            "players": players,
            "roles": {p: r for p, r in zip(players, role_list)},
            "rules": {"navigator": True, "charon": True, "siren_knows": True,
                      "s_knows_c": True, "c_knows_s": False, "library": True,
                      "ghost": True},
            "settings": {"need": 3, "win_c": 12, "win_x": 5},
            "pt": {"c": 0, "x": 0},
            "dead": [], "inputs": {}, "used_library": [], "day": 1,
        }
        return game, rng

    game, rng = make_game(11)
    st = init_game(game)

    print("\n【1】信念状態が作られたか")
    print(f"    AI側の名前   : {st['names']}")
    print(f"    構成         : {st['composition']}")
    n_worlds = len(st["public_worlds"])
    alive_w = sum(1 for w in st["public_worlds"] if w["weight"] > 0)
    print(f"    世界の総数   : {n_worlds}（公開視点で生きている世界: {alive_w}）")
    for name, w in list(st["private_worlds"].items())[:3]:
        alive_p = sum(1 for x in w if x["weight"] > 0)
        print(f"      {name} の視点で生きている世界: {alive_p}")

    print("\n【2】私的情報が正しく入っているか（自分の役職を100%と思えているか）")
    import rules as _r
    for p in game["players"][:4]:
        my = st["name_of"][p]
        true_role = ROLE_KEY_TO_JA[game["roles"][p]]
        ratio = engine.role_ratio(st["private_worlds"][my], my, true_role)
        pub = engine.role_ratio(st["public_worlds"], my, true_role)
        print(f"    {my:<12} 本当は{true_role:<6} 自分視点:{ratio*100:5.1f}%  公開視点:{pub*100:5.1f}%")

    print("\n【3】1日目の行動")
    actions = decide_day_actions(game, rng=rng)
    counts = {"bridge": 0, "library": 0, "lounge": 0}
    for p in game["players"]:
        a = actions[p]
        counts[a["dest"]] += 1
        role = ROLE_KEY_TO_JA[game["roles"][p]]
        card = "○" if a["card"] == "c" else "✕"
        print(f"    {st['name_of'][p]:<12} {role:<6} → {DEST_BOT_TO_JA[a['dest']]}  {card}")
    print(f"\n    内訳: 操舵室{counts['bridge']}人 / 図書室{counts['library']}人 / 談話室{counts['lounge']}人")

    plan = st["plans"][1]
    print(f"\n    図書室で確認する相手: {plan['library_target']}")
    print(f"    カロンの襲撃対象    : {plan['attack']}")
    print(f"    セイレーンの呼び寄せ: {plan['charm']}")

    print("\n【4】ルール違反が無いか（100ゲームぶん）")
    bad = []
    stats = {"bridge": 0, "library": 0, "lounge": 0, "x": 0, "total": 0}
    for seed in range(100):
        g, r = make_game(seed)
        init_game(g)
        acts = decide_day_actions(g, rng=r)
        for p, a in acts.items():
            stats[a["dest"]] += 1
            stats["total"] += 1
            if a["card"] == "x":
                stats["x"] += 1
            role = ROLE_KEY_TO_JA[g["roles"][p]]
            # 航海士は必ず操舵室・必ず○
            if role == "航海士" and (a["dest"] != "bridge" or a["card"] != "c"):
                bad.append(f"seed{seed}: 航海士が {a}")
            # ✕を出せるのはカロンだけ
            if a["card"] == "x" and role != "カロン":
                bad.append(f"seed{seed}: {role}が✕を出した")
            # 図書室に行けるのは航海士以外
            if a["dest"] == "library" and role == "航海士":
                bad.append(f"seed{seed}: 航海士が図書室へ")
    print(f"    のべ{stats['total']}人ぶんの行動を確認")
    print(f"    違反: {len(bad)}件")
    for b in bad[:5]:
        print("      ", b)
    print(f"    行先の割合: 操舵室{stats['bridge']/stats['total']*100:.1f}% "
          f"図書室{stats['library']/stats['total']*100:.1f}% "
          f"談話室{stats['lounge']/stats['total']*100:.1f}%")
    print(f"    ✕が出た割合: {stats['x']/stats['total']*100:.1f}%")

    print("\n【5】前日の操舵室の人数で、カロンの✕が変わるか")
    # ★DAY1_ASSUME_BRIDGE_CROWDED と _estimate_bridge_crowd が
    #   本当に効いているかを直接確かめる。効いていなければ、
    #   この2つは飾りということになる。
    def charon_x_rate(yesterday_bridge_count, trials=300):
        """前日の操舵室が○人だったとき、カロンが✕を出した割合"""
        x, total = 0, 0
        for seed in range(trials):
            g, r = make_game(seed)
            init_game(g)
            g["day"] = 2
            if yesterday_bridge_count is not None:
                names = g["ai_state"]["names"]
                g["ai_state"]["observations"]["destinations"][1] = {
                    n: ("操舵室" if i < yesterday_bridge_count else "談話室")
                    for i, n in enumerate(names)
                }
            acts = decide_day_actions(g, rng=r)
            for p, a in acts.items():
                if ROLE_KEY_TO_JA[g["roles"][p]] != "カロン":
                    continue
                if a["dest"] != "bridge":
                    continue      # 談話室にいるカロンはカードを出さない
                total += 1
                if a["card"] == "x":
                    x += 1
        return x, total

    need = 3
    for cnt in [0, 2, 3, 6]:
        x, total = charon_x_rate(cnt)
        judge = "混んでいる" if cnt >= need else "空いている"
        print(f"    前日の操舵室 {cnt}人（必要{need}人 → {judge}）: "
              f"操舵室のカロン{total}人中 ✕が{x}人 = {x/total*100:.1f}%")
    print("    （調整つまみの想定値: 混んでいる日 80% / 空いている日 20%）")

    print("\n" + "=" * 66)
    print("確認終了")
    print("=" * 66)
