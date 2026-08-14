# -*- coding: utf-8 -*-
"""
GHOST LINER ゲームシミュレーター
================================
これまで作った engine.py（推理の土台）・rules.py（世界を削る）・
decision.py（行動を決める）・talk.py（ひとり言）を全部つなげて、
AI同士で1ゲームを最初から最後まで自動進行させます。

【現在対応している人数】
  n_players引数で人数ごとの配役(COMPOSITIONS)・設定(GAME_SETTINGS)を
  切り替えられる作りですが、今後の調整・バランス確認は9人プレイ
  （航海士2・カロン2・セイレーン1・乗客4）を基準に行っていきます。
  __main__ のrun_batch(100, n_players=9)もそれに合わせてあります。

【実装済みの流れ】
  ・朝：図書室への立候補→絞り込み→航海士/乗客→カロン→セイレーンの
    順で行先とカードを決定（①の「行先宣言」は使わず、図書室の
    立候補フェーズだけがこの順番の駆け引きを担当）
  ・カロンの攻撃有無・操舵室での○×判断
  ・図書室で占った相手の役職を夜の議論で宣言（人間陣営は必ず正直、
    カロン・セイレーンは仲間を庇う／人間陣営に濡れ衣を着せる嘘をつける）
  ・亡霊の妨害（行先を談話室へ上書き、decision.decide_ghost_overwrite）
  ・夜の投票（一番怪しい相手へ、怪しさが閾値未満なら棄権）
  ・ひとり言（talk.py、ゲーム判断には無関係の演出）

【1人ずつ違う「信念状態」を持たせる仕組み】
  各AIは、自分の役職・知っている仲間（航海士同士/カロン同士、
  セイレーンはカロンを一方的に）を最初から知っている前提で、
  自分専用の world 一覧（private_worlds）を持ちます。そこに、
  毎日みんなが見られる公開情報（行先・○×集計・攻撃・図書室の宣言）で
  rules.py の絞り込みをかけ、さらに自分だけが知っている図書室の
  確認結果を私的に足していきます。他人に見える発言・投票には、
  私的情報を含まない public_worlds を別途使い分けています。

【実行方法】
    python simulate.py
"""

import random

import decision
import engine
import rules
import talk


N_PLAYERS = 7


# ============================================================
# 検証用の集計フック（ゲームの進行には一切影響しない）
# ============================================================
#
# ★これは「AIの調整が期待どおり効いたか」を数字で確かめるための仕掛けです。
#   勝率だけでは分からないこと（図書室カードを使い切れているか、AIの票が
#   1人に集中しすぎていないか、リーチの日にちゃんと操舵室へ集まれたか）を
#   数えます。
#
#   使い方：
#       simulate.STATS = simulate.new_stats()
#       simulate.run_batch(200, n_players=9)
#       print(simulate.format_stats(simulate.STATS))
#
#   STATS が None のままなら何も記録しません（＝通常の実行は今までどおり）。
STATS = None


def new_stats():
    """空の集計用の辞書を作る"""
    return {
        # リーチ（今日みんなが操舵室で○を出せば人間陣営が勝てる日）
        "reach_days": 0,          # リーチだった日の総数
        "reach_bridge_sum": 0,    # そのうち、実際に操舵室へ行ったカロン以外の人数の合計
        "reach_need_sum": 0,      # 全員行っていたら何人だったかの合計
        "reach_success": 0,       # 実際に○pt勝利に届いた日の数
        # 図書室カードの使い切り
        "end_alive": 0,           # 決着時に生きていた人の総数
        "end_library_unused": 0,  # そのうち図書室カードを1度も使わなかった人数
        # 投票の集中ぐあい
        "vote_days": 0,           # 投票が1票でも入った日の数
        "vote_cast": 0,           # 投じられた票の総数（棄権を除く）
        "vote_top": 0,            # そのうち最多得票の人に入った票の合計
        # 役職ごとに、本人がどこを選んだか（亡霊の妨害・呼び寄せで上書きされる前の
        # 「自分の意思」の集計）。ある役職だけ行先が偏っていると、人間から見て
        # 「あの人は毎日必ず操舵室にいる＝怪しい」と読まれる材料になる。
        "dest_by_role": {},       # 役職 -> {"操舵室": n, "図書室": n, "談話室": n}
        # 操舵室の人数不足（自動で×pt+1になる）
        "shortage_days": 0,       # 人数不足が起きた日の総数
        "games": 0,               # 集計したゲーム数
        "games_with_shortage": 0, # そのうち人数不足が1回でも起きたゲーム数
    }


def format_stats(st):
    """集計結果を人が読める形にする"""
    lines = []
    if st["reach_days"]:
        rate = st["reach_bridge_sum"] / st["reach_need_sum"] * 100
        lines.append(f"  リーチの日: {st['reach_days']}日")
        lines.append(f"    操舵室に集まれた割合: {rate:.1f}% "
                     f"（{st['reach_bridge_sum']}人 / 行けたはず{st['reach_need_sum']}人）")
        lines.append(f"    実際に○pt勝利まで届いた日: {st['reach_success']}日 "
                     f"({st['reach_success'] / st['reach_days'] * 100:.1f}%)")
    else:
        lines.append("  リーチの日: 0日")
    if st["end_alive"]:
        lines.append(f"  決着時に図書室カードが未使用だった生存者: "
                     f"{st['end_library_unused']}/{st['end_alive']}人 "
                     f"({st['end_library_unused'] / st['end_alive'] * 100:.1f}%)")
    if st["vote_cast"]:
        lines.append(f"  票の集中度（最多得票者が受けた票の割合）: "
                     f"{st['vote_top'] / st['vote_cast'] * 100:.1f}% "
                     f"（{st['vote_top']}票 / 全{st['vote_cast']}票・{st['vote_days']}日）")
    if st.get("games"):
        lines.append(f"  操舵室の人数不足: {st['shortage_days']}日 / "
                     f"{st['games_with_shortage']}ゲーム "
                     f"({st['games_with_shortage'] / st['games'] * 100:.1f}%のゲームで1回以上発生)")
    if st.get("dest_by_role"):
        lines.append("  役職ごとの行先（本人が選んだもの。妨害・呼び寄せの上書き前）:")
        order = ["航海士", "乗客", "カロン", "セイレーン"]
        for role in order + [r for r in st["dest_by_role"] if r not in order]:
            counts = st["dest_by_role"].get(role)
            if not counts:
                continue
            total = sum(counts.values())
            parts = "  ".join(
                f"{d} {counts.get(d, 0) / total * 100:5.1f}%"
                for d in ("操舵室", "図書室", "談話室"))
            lines.append(f"    {role:<6} {parts}   （のべ{total}人日）")
    return "\n".join(lines)


# ============================================================
# 準備：配役を決める・各AIの信念状態を作る
# ============================================================

def pick_ground_truth(players, composition, rng):
    """実際のゲームで使う「本当の役職配役」を1つランダムに選ぶ"""
    worlds = engine.build_worlds(players, composition)
    return rng.choice(worlds)["roles"]


def known_teammates(my_name, my_role, ground_truth):
    """
    自分の役職から、最初から知っている仲間を返す。

    ・航海士同士・カロン同士は「互いに」知る
    ・セイレーンは「カロンを一方的に」知る（逆に、カロンはセイレーンを知らない）
    """
    if my_role in ("航海士", "カロン"):
        return {p: r for p, r in ground_truth.items() if r == my_role and p != my_name}
    if my_role == "セイレーン":
        return {p: r for p, r in ground_truth.items() if r == "カロン"}
    return {}


def build_private_worlds(players, composition, my_name, my_role, ground_truth):
    """
    AI1人ぶんの「自分専用の信念状態」を作る。
    自分自身の役職と、最初から知っている仲間の役職を、図書室ルールと
    同じ仕組み（絶対に正しい私的な確定情報）を使って埋め込む。
    """
    worlds = engine.build_worlds(players, composition)
    reveals = [{"day": 0, "target": my_name, "role": my_role}]
    for name, role in known_teammates(my_name, my_role, ground_truth).items():
        reveals.append({"day": 0, "target": name, "role": role})
    rules.apply_library_rule(worlds, {"library_reveals": reveals})
    return worlds


def build_public_worlds(players, composition):
    """
    「誰でも見られる公開情報だけ」で組み立てる信念状態を作る。
    仲間が誰かという私的な情報は一切入れない。

    ★これが必要な理由（2つある）

    (1) 航海士が「知らないフリ」をするため
        航海士は相方の航海士を知っているので、私的な信念状態のままだと
        「操舵室にいた4人のうち、自分と相方は白だから、残る2人のどちらかが
        カロンだ」と、他の人には出せない精度で絞り込めてしまいます。
        その精度のまま投票すると、実際の卓なら「こいつ航海士だ」と一発で
        バレます。実際の航海士は、知っていても知らないフリをして
        振る舞わなければいけません。
        なので投票の判断だけは、この公開情報だけの信念状態を使います。

    (2) カロンが「自分が今どれくらい疑われているか」を測るため
        カロンは自分がカロンだと知っているので、自分の信念状態から
        自分の怪しさを計算すると必ず100%になってしまいます。
        他人から見た自分の姿を知るには、この公開情報だけの信念状態が必要です。
    """
    return engine.build_worlds(players, composition)


def talk_view(private_worlds, public_worlds, my_name, my_role):
    """
    発言に使ってよい視点を返す（朝・夜・図書室の宣言で共通）。

      乗客     → 自分の視点（私的情報を持たないので、そのまま喋ってよい）
      それ以外 → 公開情報＋「自分は乗客だ」という前提の視点
                 ＝乗客のふり。engine.view_as_passenger のコメントを参照。

    ★ai_brain._talk_worlds（Discord本番側）と同じ使い分けにしてあります。
      片方だけ直すと、自己対戦で確かめた挙動と本番がずれるので注意。
    """
    if my_role == "乗客":
        return private_worlds[my_name]
    return engine.view_as_passenger(public_worlds, my_name)


# ============================================================
# 1ゲームを最初から最後まで進行させる
# ============================================================

def play_one_game(rng=random, verbose=True, max_days=30, n_players=N_PLAYERS, debug_beliefs=False):
    players = engine.make_players(n_players)
    composition = engine.COMPOSITIONS[n_players]
    settings = engine.GAME_SETTINGS[n_players]

    ground_truth = pick_ground_truth(players, composition, rng)

    if verbose:
        print("=" * 60)
        print("■ 配役（本当は誰にも見えない情報）")
        for p in players:
            print(f"    {p}: {ground_truth[p]}")
        print()

    private_worlds = {
        p: build_private_worlds(players, composition, p, ground_truth[p], ground_truth)
        for p in players
    }
    # 全員が共通で見ている「公開情報だけの視点」。誰の私的情報も入っていないので、
    # 1つだけ作って使い回せる（航海士の投票と、カロンの自己被疑度の測定に使う）
    public_worlds = build_public_worlds(players, composition)

    # ひとり言の口調（人格＝AIの職業）。ゲーム開始時に1人1つ割り当て、
    # そのゲーム中は変えない（日によって口調がブレると不自然なため）。
    # どこへ行くか・誰を疑うかといった判断には一切影響しない（talk.py参照）。
    #
    # ★重複しないように配ること。以前は rng.choice で毎回独立に選んでいたので
    #   同じ職業が卓に2人出ることがあった。職業が「探偵AI」のような名前に
    #   なった今、これは同じ人が2人いるのと同じで明らかにおかしい。
    #   （本番の ai_brain 側は「人格＝AIの表示名」なので元から重複しない。
    #     ここは自己対戦用に A,B,C… という名前を使うので、別途配る必要がある）
    # ★プレイヤー名が職業名そのものの時（＝本番と同じ形で確認したい時）は、
    #   その名前を人格にする。それ以外（A,B,C… の自己対戦）は余りから配る。
    _known = set(talk.PERSONA_NAMES)
    personas = {p: p for p in players if p in _known}
    _rest = [n for n in talk.PERSONA_NAMES if n not in personas.values()]
    rng.shuffle(_rest)
    for i, p in enumerate([q for q in players if q not in personas]):
        personas[p] = _rest[i % len(_rest)] if _rest else None
    if verbose:
        print("■ 口調（人格。ゲーム中は固定。話す内容には影響しない）")
        for p in players:
            print(f"    {p}: {personas[p]}")
        print()

    # ★敵対心（decision.GRUDGE_* 参照）。図書室で名指しされた・投票された・
    #   発言で疑われた相手を根に持つ。推理ではなく感情の層で、誰を疑うか／
    #   誰に投票するかにだけ効く。
    grudges = decision.new_grudges()

    alive = list(players)
    dead = []
    c_pt = 0
    x_pt = 0
    used_library = set()
    exile_log = []  # (何日目か, 追放された人の本当の役職) の記録
    charon_last_card = {}  # カロンが前日に出したカード（"x"かどうかの判定に使う）
    ghost_blocked = set()  # 前日に亡霊から妨害された人（今日は誰からも妨害されない）
    suspected_history = {}  # 各プレイヤーが前日「一番怪しい」と見ていた相手（ひとり言の演出用）

    shared_observations = {
        "destinations": {},
        "bridge_results": {},
        "attacks": {},
        "attack_targets": {},
        "library_reports": {},
        # 以下はひとり言の話題づくり専用の記録（AIの推理計算には渡さない）。
        # どれもルール上、全員の目の前で起きる公開情報。
        "votes": {},    # 日付 -> {投票者: 投票先 or None}
        "exiled": {},   # 日付 -> 追放された人（いなければNone）
        "deaths": {},   # 日付 -> [(脱落者, "攻撃" or "返り討ち")]
    }

    day = 0
    result = None
    shortage_happened = False   # 検証用：このゲームで人数不足が1回でも起きたか

    while day < max_days:
        day += 1
        if verbose:
            print(f"--- {day}日目（生存: {','.join(alive)}） ---")

        # ★敵対心は1日ごとに薄れる（人間も、いつまでも同じ人を恨み続けはしない）
        decision.decay_grudges(grudges)

        charons_alive = [p for p in alive if ground_truth[p] == "カロン"]

        # ---- 朝：行先とカードを決める ----
        day_actions = {}        # player -> (dest, card)
        library_target_of = {}  # player -> 図書室で確認したい相手
        attacks_today = {}      # attacker -> target

        # ---- 朝(1) 図書室への立候補（「行きたい人？」と挙手を募る）----
        # 将来Discordに組み込むときは、ここが実際のチャットのやり取りになる。
        # 決着への近さ。図書室カードを使い切れずに終わるのを防ぐために使う
        # （ptはどちらも公開情報なので、判断に使っても情報漏洩にはならない）。
        urgency = decision.game_urgency(
            c_pt, settings["win_c"], x_pt, settings["win_x"])

        volunteers = []
        for p in alive:
            role = ground_truth[p]
            if p in used_library:
                continue  # 図書室カードは1人1回だけ。使い切った人は立候補できない
            # 「今日は図書室どころではない」の判定は decision 側に集約してある
            # （同じ判断を ai_brain.py も使うので、条件を2か所に書き写さない）。
            if role in engine.HUMAN_SIDE:
                # 人間陣営には制限がない（skip_library_today 参照）。
                # 見積もり計算(expected_charon_free_count)は世界を全部走査するので
                # 決して安くない。使われない値をわざわざ作らない。
                possible, ally_alive = 0, False
            else:
                # カロン陣営は仲間を知っているので正確に数えられる
                possible = len(alive) - len(charons_alive)
                ally_alive = any(q != p for q in charons_alive)
            if decision.skip_library_today(
                    role, c_pt, settings["win_c"], x_pt, settings["win_x"],
                    possible, ally_alive=ally_alive):
                continue
            if decision.volunteer_for_library(role, rng=rng, urgency=urgency):
                volunteers.append(p)

        # ---- 朝(2) 立候補が出そろってから、本当に行くか・譲って引くかを決める ----
        library_goers = []
        for p in volunteers:
            if decision.decide_library_final(ground_truth[p], len(volunteers),
                                             rng=rng, urgency=urgency):
                library_goers.append(p)

        # ---- 朝(3) 図書室に行かない人が、操舵室か談話室かを決める ----
        # 航海士・乗客 → カロン → セイレーン の順に決める。
        # こうすると、カロンは「実際に何人操舵室に集まりそうか」を見てから
        # ×を出すか判断でき、セイレーンは「実際に誰が図書室へ行くか」を
        # 見てから呼び寄せ対象を選べる（実プレイでの「朝の議論の空気を読む」
        # 動きを、決める順番で再現している）。

        # 前日、操舵室に何人集まったか（全員が結果発表で見た公開情報）。
        # 乗客が「リーチの日に人数を減らしすぎて人数不足にならないか」を
        # 判断するのに使う。★当日の行先ではなく前日の実績を使うこと
        # （当日の行先は伏せられているので、使うと情報漏洩になる）。
        yesterday_dests = shared_observations["destinations"].get(day - 1)
        bridge_crowd_estimate = (
            sum(1 for d in yesterday_dests.values() if d == "操舵室")
            if yesterday_dests else None)

        # 前日、人数不足で操舵が失敗していたか。○×の枚数の合計＝操舵室にいた
        # 生存者の数なので、これで正確に分かる（結果発表で全員が見た公開情報）。
        y_bridge = shared_observations["bridge_results"].get(day - 1)
        shortage_yesterday = (
            y_bridge is not None
            and y_bridge["c"] + y_bridge["x"] < settings["bridge_need"])

        # (3-a) 航海士・乗客が先に決める
        bridge_crowd_so_far = 0
        for p in alive:
            role = ground_truth[p]
            worlds = private_worlds[p]

            if p in library_goers:
                dest, card = "図書室", "c"
                library_target_of[p] = decision.choose_library_target(worlds, players, p, rng=rng)
            elif role == "航海士":
                dest, card = "操舵室", "c"
            elif role == "乗客":
                dest, card = decision.passenger_plan_destination(
                    worlds, players, p, rng=rng,
                    # リーチの日かどうかの判定に使う（自分の視点からの見積もり）
                    alive_players=alive, c_pt=c_pt, win_c=settings["win_c"],
                    # 「他人から見た自分の怪しさ」はカロンの時と同じく公開視点で測る。
                    # 自分視点だと自分が乗客だと分かっているので必ず0%になる。
                    my_public_suspicion=decision.suspicion_score(public_worlds, p),
                    # 人数不足の歯止め（前日の実績で見る）
                    bridge_crowd_estimate=bridge_crowd_estimate,
                    bridge_need=settings["bridge_need"],
                    # 「ちゃんと操舵室に来てくれよ」の反応
                    shortage_yesterday=shortage_yesterday,
                ), "c"
            else:
                continue  # カロン・セイレーンはこの後で決める

            day_actions[p] = (dest, card)
            if dest == "操舵室":
                bridge_crowd_so_far += 1

        # (3-b) カロンが、ここまでの集まり具合を見て決める
        for p in alive:
            if ground_truth[p] != "カロン" or p in library_goers:
                continue
            worlds = private_worlds[p]
            ally = next((q for q in charons_alive if q != p), None)
            played_x_yesterday = charon_last_card.get(p) == "x"
            ally_played_x_yesterday = ally is not None and charon_last_card.get(ally) == "x"
            dest, card, atk_target = decision.charon_plan_action(
                worlds, players, p, ally, alive, day, x_pt, settings["win_x"],
                played_x_yesterday=played_x_yesterday,
                ally_played_x_yesterday=ally_played_x_yesterday,
                c_pt=c_pt, win_c=settings["win_c"],
                # 「他人から見た自分の怪しさ」は公開情報の視点から測る
                my_public_suspicion=decision.suspicion_score(public_worlds, p),
                bridge_crowd_so_far=bridge_crowd_so_far,
                bridge_need=settings["bridge_need"],
                # 人数不足を狙うのは「人が減ってきてから」（脱落は公開情報）
                deaths_count=len(dead),
                rng=rng,
            )
            if atk_target is not None:
                attacks_today[p] = atk_target
            day_actions[p] = (dest, card)
            if dest == "操舵室":
                bridge_crowd_so_far += 1

        # (3-c) セイレーンが、実際に誰が図書室へ行くかを見て決める
        siren_charm_of = {}  # セイレーン -> 呼び寄せた相手（あれば）
        for p in alive:
            if ground_truth[p] != "セイレーン" or p in library_goers:
                continue
            worlds = private_worlds[p]
            real_library_dest = {q: "図書室" for q in library_goers}
            dest, card, charm_target = decision.siren_plan_action(
                worlds, players, p, declared_so_far_dest=real_library_dest, rng=rng,
                # 人数不足を狙う日の判定に使う
                alive_players=alive, bridge_need=settings["bridge_need"],
                deaths_count=len(dead),
            )
            day_actions[p] = (dest, card)
            if charm_target is not None:
                siren_charm_of[p] = charm_target

        # 未対応の役職への保険（現状の役職構成では発生しない）
        for p in alive:
            if p not in day_actions:
                day_actions[p] = ("操舵室", "c")

        # ---- 検証用の集計：今日はリーチだったか、実際に何人集まれたか ----
        # （ここは行先が全部決まった直後。亡霊の妨害や呼び寄せより前なので、
        #   「本人が自分の意思でどこを選んだか」を測っていることになる）
        reach_today = False
        if STATS is not None:
            # 役職ごとの行先（本人の意思。この後の妨害・呼び寄せで上書きされる前）
            for p in alive:
                counts = STATS["dest_by_role"].setdefault(ground_truth[p], {})
                d = day_actions[p][0]
                counts[d] = counts.get(d, 0) + 1

            humans_possible = len(alive) - len(charons_alive)
            reach_today = decision.humans_reach_point_win(
                c_pt, settings["win_c"], humans_possible)
            if reach_today:
                STATS["reach_days"] += 1
                STATS["reach_need_sum"] += humans_possible
                STATS["reach_bridge_sum"] += sum(
                    1 for p in alive
                    if ground_truth[p] != "カロン" and day_actions[p][0] == "操舵室")

        # ---- 朝：ひとり言（演出のみ。ゲームの判断には一切影響しない）----
        # 全員の行先が決まった直後（亡霊の妨害より前＝本人が実際に選んだ
        # 内容）に喋らせる。これで「図書室に行くつもり」「行きたかったが
        # 譲った」といった、その日の本当の予定を話せるようになる。
        if verbose:
            yesterday_dest = shared_observations["destinations"].get(day - 1)
            yesterday_bridge = shared_observations["bridge_results"].get(day - 1)
            y_x = yesterday_bridge["x"] if yesterday_bridge else None
            # ひとり言の話題に使う「盤面の公開情報」をまとめる。
            # ★ここに入れてよいのは、全員が見て分かる情報だけ。私的情報
            #   （仲間の正体など）を混ぜると、発言から役職が即バレする。
            board = {
                "day": day,            # 初日だけ専用のセリフに切り替わる
                "library_on": True,    # simulate.pyは図書室ありの設定で回す
                "destinations": shared_observations["destinations"],
                "yesterday_x_count": y_x,
                # ★日ごとの×の枚数（全部の履歴）。全員が見て分かる公開情報。
                #   「3日目の操舵室で×が出たあの面子」のように、前日以外の日に
                #   ついても話せるようにするために足した（talk.build_evidence_group_clause）。
                "bridge_x": {d: r["x"] for d, r
                             in shared_observations["bridge_results"].items()},
                # ★日ごとの攻撃の件数。カロンは談話室にいる日しか攻撃できないので、
                #   攻撃が起きた日の談話室の顔ぶれも「必ずカロンを含む集合」になる。
                "attacks_by_day": dict(shared_observations["attacks"]),
                "votes": shared_observations["votes"].get(day - 1),
                "exiled": shared_observations["exiled"].get(day - 1),
                "deaths": shared_observations["deaths"].get(day - 1),
                "c_pt": c_pt, "x_pt": x_pt,
                "win_c": settings["win_c"], "win_x": settings["win_x"],
                "alive": alive,
                # 前日、誰も図書室に行かなかったか（呼びかけの話題に使う）
                "library_idle": bool(yesterday_dest) and not any(
                    d == "図書室" for d in yesterday_dest.values()),
                "library_left": any(p not in used_library for p in alive),
                # 図書室で宣言された内容そのもの。★本当か嘘かの情報は
                # 入っていない（それを知っているのはsimulate.py側だけ）
                "library_reports": shared_observations["library_reports"],
            }
            # その日すでに誰かが話した話題。最初に話した人だけがその話題を
            # 扱えるようにして、同じ日に何人もの発言が同じ公開情報の復唱で
            # 埋まる不自然さを避ける（talk.py参照）。
            spoken_topics = set()
            # その日、直前までに誰かが名指しした疑いの相手（③：会話への反応）。
            prior_suspect = None
            for p in alive:  # 亡霊(deadの人)は喋らない。ルールブック通り
                role = ground_truth[p]
                talk_worlds = talk_view(private_worlds, public_worlds, p, role)
                allies = list(known_teammates(p, role, ground_truth).keys())
                line, used_topics, said_suspect = talk.generate_monologue(
                    role, talk_worlds, players, p, my_actual_dest=day_actions[p][0],
                    ally_names=allies, board=board,
                    suspected_yesterday=suspected_history.get(p),
                    wanted_library=(p in volunteers),
                    spoken_topics=spoken_topics,
                    prior_suspect=prior_suspect, persona=personas[p], rng=rng,
                    grudge=decision.grudge_of(grudges, p),
                )
                spoken_topics |= used_topics
                if said_suspect is not None:
                    prior_suspect = said_suspect
                    # ★名指しで疑われた人は、疑ってきた相手を少し根に持つ
                    decision.add_grudge(grudges, said_suspect, p,
                                        decision.GRUDGE_NAMED_IN_TALK)
                if line:
                    print(f"    💬 {p}: {line}")
                # ★生きている人の中から選ぶ（脱落者を翌日も疑い続けないため。
                #   ai_brain.py 側は元から alive を渡していた）
                suspected_history[p] = talk.find_suspected_target(talk_worlds, alive, p)

            print(f"    図書室に立候補: {volunteers or '(なし)'} → 実際に行く: {library_goers or '(なし)'}")

        # ---- セイレーンの呼び寄せ（図書室へ行くはずだった相手を操舵室へ上書き）----
        # 宣言やひとり言は「本人が実際に選んだ内容」のまま話させ、結果だけを
        # ここで覆す（下の亡霊の妨害と同じ考え方）。呼び寄せられた側は図書室
        # カードを消費していないので、used_library には入らない（後日また使える）。
        for siren, target in siren_charm_of.items():
            if target in library_goers:
                library_goers.remove(target)
                library_target_of.pop(target, None)
                day_actions[target] = ("操舵室", "c")
                if verbose:
                    print(f"    🎵 {siren}が{target}を操舵室へ呼び寄せた（図書室を阻止）")

        # ---- 亡霊の妨害（行先を談話室に上書き）----
        # 亡霊はそれぞれ独立に1人まで妨害できる。上書きは行先カードを
        # 伏せる前でも成立するルールなので、ここで day_actions を直接書き換える。
        ghost_overwritten_today = set()
        exclude_for_ghosts = set(ghost_blocked)
        for ghost in dead:
            target = decision.decide_ghost_overwrite(
                private_worlds[ghost], players, ground_truth[ghost], alive,
                blocked_today=exclude_for_ghosts, rng=rng,
            )
            if target is not None:
                day_actions[target] = ("談話室", "c")
                ghost_overwritten_today.add(target)
                exclude_for_ghosts.add(target)
        ghost_blocked = ghost_overwritten_today  # 次の日はこの人たちを妨害できない

        if verbose and ghost_overwritten_today:
            print(f"    亡霊の妨害（談話室へ上書き）: {sorted(ghost_overwritten_today)}")

        # 次の日のクールダウン判定用に、カロンが今日出したカードを記録しておく
        # （亡霊に上書きされていれば、その「見た目の」カードを記録する）
        for p in alive:
            if ground_truth[p] == "カロン":
                charon_last_card[p] = day_actions[p][1]

        # ---- 攻撃の解決 ----
        newly_dead = []
        attack_target_list = []
        death_causes = []  # ひとり言の話題用（誰がどう落ちたかは公開情報）
        for attacker, target in attacks_today.items():
            if attacker in newly_dead:
                continue
            attack_target_list.append(target)
            if day_actions[target][0] == "談話室":
                newly_dead.append(attacker)  # 返り討ち：攻撃者が自滅
                death_causes.append((attacker, "返り討ち"))
            elif target not in newly_dead:
                newly_dead.append(target)
                death_causes.append((target, "攻撃"))
        if death_causes:
            shared_observations["deaths"][day] = death_causes

        alive = [p for p in alive if p not in newly_dead]

        # ---- 操舵室の集計 ----
        bridge_members = [p for p in alive if day_actions[p][0] == "操舵室"]
        c_count = sum(1 for p in bridge_members if day_actions[p][1] == "c")
        x_count = sum(1 for p in bridge_members if day_actions[p][1] == "x")

        shortage = len(bridge_members) < settings["bridge_need"]
        if STATS is not None and shortage:
            STATS["shortage_days"] += 1
            shortage_happened = True
        if shortage:
            x_pt += 1
        elif x_count > 0:
            x_pt += x_count
        else:
            c_pt += c_count

        # 検証用：リーチだった日に、実際に○pt勝利まで届いたか
        if STATS is not None and reach_today and c_pt >= settings["win_c"]:
            STATS["reach_success"] += 1

        # ---- 図書室の集計 ----
        library_visitors = [p for p in alive if day_actions[p][0] == "図書室"]
        for p in library_visitors:
            used_library.add(p)

        library_reveal_for = None
        if len(library_visitors) == 1:
            visitor = library_visitors[0]
            target = library_target_of.get(visitor)
            if target is not None:
                library_reveal_for = (visitor, target, ground_truth[target])

        # ---- 夜の議論：図書室の結果を宣言する（嘘の可能性あり）----
        # 確認した本人は、夜の議論の冒頭で必ず結果を宣言する。内容が
        # 本当かどうかは decision.decide_library_report が役職ごとに決める。
        # 実際に読み上げるのは下の「夜の議論」ブロック（🌙の直前）。
        library_report_speech = None
        if library_reveal_for is not None:
            visitor, target, true_role = library_reveal_for
            claimed_role = decision.decide_library_report(ground_truth[visitor], true_role, rng=rng)
            # 図書室の"奪い合い"の駆け引き（カロンは物怖じせず主張し、人間陣営は
            # 航海士をかばって遠慮しがち）を、宣言の説得力の下駄として近似する。
            # 本当の役職はここ（simulate.py）でしか見ない。AIの信念計算には渡さない。
            confidence_multiplier = (
                rules.REPORT_CHARON_CONFIDENCE_MULTIPLIER
                if ground_truth[visitor] in engine.CHARON_SIDE
                else rules.REPORT_HUMAN_HESITATION_MULTIPLIER
            )
            shared_observations["library_reports"].setdefault(day, []).append({
                "reporter": visitor, "target": target, "claimed_role": claimed_role,
                "confidence_multiplier": confidence_multiplier,
            })
            # ★「お前はカロンだ」と名指しされた側は、言ってきた相手に強く反発する。
            #   本当にカロンでも、濡れ衣を着せられた無実の人でも同じ反応になる
            #   （どちらも「あいつは嘘つきだ」と主張する側に回るのが自然）。
            if claimed_role in engine.CHARON_SIDE:
                decision.add_grudge(grudges, target, visitor,
                                    decision.GRUDGE_LIBRARY_ACCUSED)
            if verbose:
                # 「なぜその人を選んだか」も含めて話させる。理由の判断には、
                # 他の発言と同じ視点の使い分け（乗客のみ私的視点）を使う。
                reporter_worlds = talk_view(
                    private_worlds, public_worlds, visitor, ground_truth[visitor])
                speech = talk.generate_library_report_speech(
                    reporter_worlds, target, claimed_role,
                    persona=personas[visitor], rng=rng,
                )
                # （嘘）は開発用の種明かし。実際のDiscord出力には出さない
                honesty = "" if claimed_role == true_role else "（嘘）"
                library_report_speech = f"    📖 {visitor}: {speech}{honesty}"

        dead.extend(newly_dead)

        # ---- 公開情報（みんなが見られる証拠）を更新 ----
        shared_observations["destinations"][day] = {p: day_actions[p][0] for p in day_actions}
        # ★亡霊に談話室へ引きずり出された人は、自分で行先を選んでいない。
        #   これを記録しておかないと、航海士ルールが「談話室にいた＝航海士では
        #   ありえない」と判断して、真実の世界を消してしまう。
        #   （実際の卓でも「⛔上書き」と表示されて全員に分かるので、
        #     人間はそんな判断はしない）
        if ghost_overwritten_today:
            shared_observations.setdefault("forced_dest", {})[day] = set(ghost_overwritten_today)
        shared_observations["bridge_results"][day] = {"c": c_count, "x": x_count}
        shared_observations["attacks"][day] = len(attacks_today)
        if attack_target_list:
            shared_observations["attack_targets"][day] = attack_target_list

        # ---- 各AIの信念状態を更新（公開情報＋自分だけの図書室結果） ----
        for worlds in list(private_worlds.values()) + [public_worlds]:
            rules.apply_navigator_rule(worlds, shared_observations)
            rules.apply_bridge_count_rule(worlds, shared_observations)
            rules.apply_charon_attack_rule(worlds, shared_observations)
            rules.apply_attack_victim_rule(worlds, shared_observations)
            rules.apply_library_report_rule(worlds, shared_observations)
            # ★「AがBに投票した＝この2人は仲間ではない」を読む（2026-08-14追加）
            #   前日ぶんだけを1回反映する（毎日全履歴をかけると掛け算が重なって
            #   効きすぎる。only_day のコメント参照）
            rules.apply_vote_line_rule(worlds, shared_observations, only_day=day - 1)

        if library_reveal_for is not None:
            visitor, target, true_role = library_reveal_for
            if visitor in private_worlds:
                rules.apply_library_rule(
                    private_worlds[visitor],
                    {"library_reveals": [{"day": day, "target": target, "role": true_role}]},
                )

        if debug_beliefs:
            print(f"    ---- {day}日目終了時点、各プレイヤーが投票で実際に使う視点 ----")
            for p in alive:
                role = ground_truth[p]
                belief = public_worlds if role == "航海士" else private_worlds[p]
                others = [q for q in alive if q != p]
                cells = "  ".join(
                    f"{q}:{decision.suspicion_score(belief, q) * 100:5.1f}%" for q in others
                )
                view_label = "(公開情報視点)" if role == "航海士" else "(私的視点)"
                print(f"      {p}[{role}]{view_label}: {cells}")

        if verbose:
            for p in players:
                mark = ""
                if p in newly_dead:
                    mark = "(本日死亡)"
                elif p in dead:
                    mark = "(退場済)"
                if p in day_actions:
                    dest, card = day_actions[p]
                    print(f"    {p}: {dest} {card} {mark}")
                else:
                    print(f"    {p}: - {mark}")
            print(f"    操舵室 {len(bridge_members)}人（○{c_count}:×{x_count}）人数不足={shortage}")
            print(f"    ○pt={c_pt}  ×pt={x_pt}")

        # ---- 夜の議論（演出のみ。ゲームの判断には一切影響しない）----
        # その日に同じ部屋だった人への言及・図書室の報告への反応・投票の予告
        # などを話す。朝のひとり言と同じく、投票そのものにはこの発言は
        # 使われない（decide_voteはtalk_worldsを直接見て判断するため）。
        if verbose:
            # 図書室に行った人の宣言が、夜の議論の口火を切る（ルールブック通り）
            if library_report_speech:
                print(library_report_speech)
            night_board = {
                "library_on": True,
                # 亡霊に上書きされた後の、実際に公開される行先
                "today_destinations": {p: day_actions[p][0] for p in day_actions},
                "today_x_count": x_count,
                "attack_happened": bool(attacks_today),
                "alive": alive,
                "library_reports": shared_observations["library_reports"],
                # ★どちらかの陣営が勝利ptに届いた日は専用のセリフに切り替える
                #   （talk.build_endgame_clause 参照）。勝敗判定はこの夜の
                #   追放が終わってから行われるので、まだ決着していない。
                "c_pt": c_pt, "x_pt": x_pt,
                "win_c": settings["win_c"], "win_x": settings["win_x"],
            }
            # その日すでに誰かが使った文。同じ文が2人から出ないように
            # 持ち回る（朝の spoken_topics と同じ考え方。talk._pick_fresh 参照）
            spoken_lines = set()
            for p in alive:  # 亡霊(deadの人)は喋らない
                role = ground_truth[p]
                talk_worlds = talk_view(private_worlds, public_worlds, p, role)
                allies = list(known_teammates(p, role, ground_truth).keys())
                comment, used_lines = talk.generate_night_comment(
                    talk_worlds, players, p, role=role, ally_names=allies,
                    board=night_board, persona=personas[p],
                    spoken_lines=spoken_lines, rng=rng,
                    grudge=decision.grudge_of(grudges, p),
                )
                spoken_lines |= used_lines
                if comment:
                    print(f"    🌙 {p}: {comment}")

        # ---- 夜の投票 ----
        charons_alive_now = [p for p in alive if ground_truth[p] == "カロン"]
        votes = {}
        for p in alive:
            role = ground_truth[p]
            exclude = set()
            # ★投票の視点は、発言とまったく同じ「乗客のふり視点」に統一する。
            #   （talk_view と同じ考え方。理由は下の3つ）
            #
            #   (1) 航海士：相方を知っている精度のまま投票すると「こいつ航海士だ」
            #       とバレる。乗客のふりをして投票する必要がある。
            #
            #   (2) カロン陣営：★以前ここは private_worlds を使っていて、
            #       9人卓のカロンは「自分と相方以外にカロンはいない」と
            #       知っているため、仲間を除外した残り全員の怪しさが
            #       VOTE_SUSPICION_THRESHOLD を割り、実測でカロンの93.7%・
            #       セイレーンの100%が毎晩棄権していた。
            #       毎晩棄権する人＝カロン陣営、と機械的に読まれてしまう。
            #       乗客のふり視点で投票させれば、票の出方が乗客と
            #       見分けられなくなる（＝仲間を避けること以外は同じ動き）。
            #
            #   (3) 乗客：もともと private_worlds ＝ 公開情報＋自分は乗客 なので、
            #       この統一で何も変わらない（図書室で見た結果は今まで通り使える）。
            vote_worlds = talk_view(private_worlds, public_worlds, p, role)

            threshold = decision.VOTE_SUSPICION_THRESHOLD
            if role == "カロン":
                # カロンは仲間の正体を100%知っているので、投票候補から仲間を除外する
                exclude = {q for q in charons_alive_now if q != p}
                threshold = decision.VOTE_SUSPICION_THRESHOLD_CHARON_SIDE
            elif role == "セイレーン":
                # ★セイレーンもカロンを知っているので同じ理由で除外する。
                #   ここが抜けていたため、セイレーンが自分の知っているカロンに
                #   投票していた（ai_brain.py 側は元から正しかった）。
                exclude = set(charons_alive_now)
                threshold = decision.VOTE_SUSPICION_THRESHOLD_CHARON_SIDE

            votes[p] = decision.decide_vote(vote_worlds, players, p, alive,
                                            threshold=threshold,
                                            exclude=exclude, rng=rng,
                                            grudge=decision.grudge_of(grudges, p))
        # ★自分に票を入れてきた相手を根に持つ（投票は全員に公開される）。
        #   ここが「人間陣営同士が誤解から敵対し合って自滅する」の源になる。
        for voter, target in votes.items():
            if target is not None:
                decision.add_grudge(grudges, target, voter,
                                    decision.GRUDGE_VOTED_AGAINST)
        vote_counts = {}
        for target in votes.values():
            if target is not None:
                vote_counts[target] = vote_counts.get(target, 0) + 1

        # 検証用：その日の票が1人にどれだけ集中したか
        if STATS is not None and vote_counts:
            STATS["vote_days"] += 1
            STATS["vote_cast"] += sum(vote_counts.values())
            STATS["vote_top"] += max(vote_counts.values())

        exiled = None
        if vote_counts:
            max_votes = max(vote_counts.values())
            top = [t for t, v in vote_counts.items() if v == max_votes]
            required = (len(alive) + 1) // 2
            if len(top) == 1 and max_votes >= required:
                exiled = top[0]

        # ひとり言の話題用に、投票の内訳と追放結果を残しておく
        # （投票は一斉に指名するので、どちらも公開情報）
        shared_observations["votes"][day] = dict(votes)
        shared_observations["exiled"][day] = exiled

        if exiled is not None:
            dead.append(exiled)
            alive = [p for p in alive if p != exiled]
            exile_log.append((day, ground_truth[exiled]))

        if verbose:
            print(f"    投票: {votes}")
            print(f"    追放: {exiled}")

        # ---- 勝敗判定 ----
        alive_charons = sum(1 for p in alive if ground_truth[p] == "カロン")
        if alive_charons == 0:
            result = ("human", "charon_dead", day, exile_log)
        elif alive_charons * 2 >= len(alive):
            result = ("charon", "charon_half", day, exile_log)
        elif c_pt >= settings["win_c"]:
            result = ("human", "c_pt", day, exile_log)
        elif x_pt >= settings["win_x"]:
            result = ("charon", "x_pt", day, exile_log)

        if verbose:
            print()

        if result is not None:
            break

    # ---- 検証用：決着時、図書室カードを使わずに終わった生存者の数 ----
    if STATS is not None:
        STATS["end_alive"] += len(alive)
        STATS["end_library_unused"] += sum(1 for p in alive if p not in used_library)
        STATS["games"] += 1
        if shortage_happened:
            STATS["games_with_shortage"] += 1

    if verbose:
        print("=" * 60)
        if result:
            winner, reason, final_day, _exile_log = result
            print(f"■ 決着：{winner}陣営の勝利（理由: {reason}, {final_day}日目）")
        else:
            print(f"■ 決着つかず（{max_days}日で打ち切り）")
        print("  最終役職:")
        for p in players:
            print(f"    {p}: {ground_truth[p]}  {'(死亡)' if p in dead else '(生存)'}")

    return result


# ============================================================
# 複数回まとめて実行し、勝率などを集計する
# ============================================================

def run_batch(n_games, base_seed=0, n_players=N_PLAYERS):
    """
    n_games回、1ゲームずつ最初から最後まで自動で回して結果を集計する。

    1ゲームごとに違う乱数（base_seed + 通し番号）を使うので、毎回同じ
    ゲームを繰り返すのではなく、いろんな配役・展開を試すことになります。
    base_seedを変えれば、別の100回セットを試すこともできます
    （同じbase_seedなら、何度実行しても同じ100回になります＝再現性あり）。
    """
    results = []
    for i in range(n_games):
        rng = random.Random(base_seed + i)
        result = play_one_game(rng=rng, verbose=False, n_players=n_players)
        results.append(result)

    human_wins = [r for r in results if r and r[0] == "human"]
    charon_wins = [r for r in results if r and r[0] == "charon"]
    unfinished = [r for r in results if r is None]

    print("=" * 60)
    print(f"■ {n_games}ゲームの集計結果（{n_players}人プレイ）")
    print(f"  人間陣営の勝利: {len(human_wins)}回 ({len(human_wins) / n_games * 100:.1f}%)")
    print(f"  カロン陣営の勝利: {len(charon_wins)}回 ({len(charon_wins) / n_games * 100:.1f}%)")
    if unfinished:
        print(f"  決着つかず(max_days到達): {len(unfinished)}回")
    print()

    print("  ---- 勝因の内訳 ----")
    reason_labels = {
        "charon_dead": "人間陣営：カロン全滅",
        "charon_half": "カロン陣営：生存者の半数以上を占める",
        "c_pt": "人間陣営：○pt到達",
        "x_pt": "カロン陣営：×pt到達",
    }
    reason_counts = {}
    for r in results:
        if r is None:
            continue
        reason_counts[r[1]] = reason_counts.get(r[1], 0) + 1
    for reason, label in reason_labels.items():
        count = reason_counts.get(reason, 0)
        print(f"    {label}: {count}回 ({count / n_games * 100:.1f}%)")

    print()
    days = [r[2] for r in results if r is not None]
    if days:
        print(f"  ---- ゲームの長さ ----")
        print(f"    平均{sum(days) / len(days):.1f}日  最短{min(days)}日  最長{max(days)}日")

    return results


# ============================================================
# 1日目に操舵室へ何人集まるかを集計する
# ============================================================

def sample_day1_bridge_sizes(n_games, base_seed=0, n_players=N_PLAYERS):
    """
    1日目に何人が操舵室に集まるかだけを、n_games回サンプリングして集計する。
    1日目はまだ証拠が何も無い状態なので、全ゲームを最後まで回さなくても、
    行先の決定処理だけを繰り返せば十分（その分、高速に大量サンプルできる）。
    """
    players = engine.make_players(n_players)
    composition = engine.COMPOSITIONS[n_players]
    settings = engine.GAME_SETTINGS[n_players]
    sizes = []

    for i in range(n_games):
        rng = random.Random(base_seed + i)
        ground_truth = pick_ground_truth(players, composition, rng)
        private_worlds = {
            p: build_private_worlds(players, composition, p, ground_truth[p], ground_truth)
            for p in players
        }
        charons_alive = [p for p in players if ground_truth[p] == "カロン"]

        # 図書室の立候補フェーズ（本編と同じ2段階）
        volunteers = [p for p in players if decision.volunteer_for_library(ground_truth[p], rng=rng)]
        library_goers = [
            p for p in volunteers
            if decision.decide_library_final(ground_truth[p], len(volunteers), rng=rng)
        ]

        # 本編と同じ順番：航海士・乗客 → カロン → セイレーン
        bridge_count = 0
        for p in players:
            if p in library_goers:
                continue
            role = ground_truth[p]
            worlds = private_worlds[p]
            if role == "航海士":
                dest = "操舵室"
            elif role == "乗客":
                dest = decision.passenger_plan_destination(worlds, players, p, rng=rng)
            else:
                continue
            if dest == "操舵室":
                bridge_count += 1

        for p in players:
            if ground_truth[p] != "カロン" or p in library_goers:
                continue
            worlds = private_worlds[p]
            ally = next((q for q in charons_alive if q != p), None)
            dest, _card, _atk = decision.charon_plan_action(
                worlds, players, p, ally, players, day=1, x_pt=0,
                win_x=settings["win_x"],
                bridge_crowd_so_far=bridge_count, bridge_need=settings["bridge_need"],
                rng=rng,
            )
            if dest == "操舵室":
                bridge_count += 1

        for p in players:
            if ground_truth[p] != "セイレーン" or p in library_goers:
                continue
            worlds = private_worlds[p]
            real_library_dest = {q: "図書室" for q in library_goers}
            dest, _card, charm_target = decision.siren_plan_action(
                worlds, players, p, declared_so_far_dest=real_library_dest, rng=rng,
            )
            if dest == "操舵室":
                bridge_count += 1
            if charm_target is not None and charm_target in library_goers:
                library_goers.remove(charm_target)
                bridge_count += 1

        sizes.append((bridge_count, len(library_goers)))

    def show(label, values, unit="人"):
        print(f"  ---- {label} ----")
        counts = {}
        for v in values:
            counts[v] = counts.get(v, 0) + 1
        for v in sorted(counts):
            c = counts[v]
            pct = c / len(values) * 100
            bar = "■" * round(pct / 2)
            print(f"    {v:>2}{unit}: {c:>4}回 ({pct:5.1f}%) {bar}")
        avg = sum(values) / len(values)
        print(f"    平均{avg:.1f}{unit}  最少{min(values)}{unit}  最多{max(values)}{unit}")

    bridge_sizes = [b for b, _lib in sizes]
    library_sizes = [lib for _b, lib in sizes]

    print("=" * 60)
    print(f"■ 1日目の行先の分布（{n_games}ゲーム, {n_players}人プレイ）")
    show("実際に図書室へ行った人数", library_sizes)
    print()
    show("操舵室に集まった人数", bridge_sizes)
    return sizes


# ============================================================
# カロンが何日目に最初に追放されるかを集計する
# ============================================================

def analyze_first_charon_exile(n_games, base_seed=0, n_players=N_PLAYERS):
    """
    n_games回シミュレートして、「投票でカロンが最初に追放されるのは
    何日目が多いか」を集計する。

    投票以外（攻撃による死亡など）でカロンが先にいなくなった場合は
    数えない。あくまで「投票で追放された」ケースだけを見る。
    """
    first_exile_days = []
    no_charon_exile = 0

    for i in range(n_games):
        rng = random.Random(base_seed + i)
        result = play_one_game(rng=rng, verbose=False, n_players=n_players)
        if result is None:
            continue
        _winner, _reason, _final_day, exile_log = result
        charon_exiles = [day for day, role in exile_log if role == "カロン"]
        if charon_exiles:
            first_exile_days.append(min(charon_exiles))
        else:
            no_charon_exile += 1

    print("=" * 60)
    print(f"■ カロンが最初に投票で追放される日（{n_games}ゲーム, {n_players}人プレイ）")
    print(f"  投票でカロンが追放されたゲーム: {len(first_exile_days)}回")
    print(f"  投票では一度もカロンが追放されなかったゲーム: {no_charon_exile}回")
    print("    （攻撃で先に死んだ／ゲームが終わるまで疑われなかった、など）")

    if first_exile_days:
        print()
        print("  ---- 日ごとの内訳 ----")
        day_counts = {}
        for d in first_exile_days:
            day_counts[d] = day_counts.get(d, 0) + 1
        for d in sorted(day_counts):
            count = day_counts[d]
            bar = "■" * count
            print(f"    {d}日目: {count:>3}回 {bar}")

        avg = sum(first_exile_days) / len(first_exile_days)
        print()
        print(f"    平均{avg:.1f}日目  最短{min(first_exile_days)}日目  最長{max(first_exile_days)}日目")

    return first_exile_days


if __name__ == "__main__":
    run_batch(100, n_players=9)
