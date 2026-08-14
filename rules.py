# -*- coding: utf-8 -*-
"""
GHOST LINER 推理エンジン
================================
【ステップ2】証拠を使って、ありえない世界を消す（weightを0にする）

このファイルがやること：
  実際のゲームでわかった事実（observations）を受け取り、
  ルールに矛盾する世界の weight を 0.0 にする。

【証拠データ（observations）の形式】
  複数の種類の証拠をまとめて持つため、こういう入れ子の辞書にしています。

    observations = {
        "destinations": {
            1: {"A": "操舵室", "B": "操舵室", ...},   # 1日目の行先（上書き後の公開情報）
            2: {"A": "操舵室", "B": "談話室", ...},   # 2日目の行先
        },
        "bridge_results": {
            1: {"c": 2, "x": 1},   # 1日目、操舵室メンバーの合計：○2枚、×1枚
            2: {"c": 3, "x": 0},   # 2日目、操舵室メンバーの合計：○3枚、×0枚
        },
    }

  行先（destinations）は、亡霊やセイレーンに上書きされたあとの
  「実際に公開された行先」を入れてください（人間が見られる情報と揃えるため）。

  ○×の集計（bridge_results）は、main.py の game["history"][day] に
  すでに保存されている c / x の数字と同じものです
  （「誰が×を出したか」は非公開で、"合計何枚か"だけが公開情報）。

【実行方法】
    python rules.py
"""

import engine


# ============================================================
# 調整つまみ（あとで自己対戦の結果を見ながら数値を変える場所）
# ============================================================
# ここから下のルールは「矛盾したら消す」ハードルールではなく、
# 「ありそうな世界の重みを少し引き上げる」ソフトルールです。
# 何倍にするかに正解はないので、いったん仮の数値を置いています。

# 行先の宣言と実際の行動が食い違っていた（＝嘘をついた）とき、
# カロン・セイレーンである世界の重みを何倍にするか
LIE_WEIGHT_BOOST = 1.4

# 図書室の結果"宣言"（嘘の可能性あり）を、宣言者への信頼度に応じて
# どれだけ重みに反映するか。
#   REPORT_TRUST_BOOST : 信頼度100%のとき、宣言と一致する世界を何倍まで引き上げるか
#   REPORT_MIN_WEIGHT   : 信頼度0%でも、宣言と食い違う世界の重みをどこまでは残すか
#     （「明らかに嘘つきの発言でも、可能性をゼロと決めつけない」ための下限）
REPORT_TRUST_BOOST = 5.0
REPORT_MIN_WEIGHT = 0.05

# ★「その世界で、その嘘に動機があるか」を見るための倍率（2026-08-14追加）
#   宣言と食い違う世界でも、その世界で宣言者がカロン陣営なら、嘘をつくのは
#   ごく自然なこと（仲間を庇う／無実の人に濡れ衣を着せる）。
#   逆にその世界で宣言者が人間陣営なら、見たままを言わない理由が無い。
#   同じ「食い違い」でも意味がまったく違うので、分けて扱う。
#   ★これが「ライン」（カロン陣営同士の庇い合い）を推理できるようになる部分。
REPORT_LIE_WEIGHT_CHARON = 0.75   # カロン陣営が嘘をついた世界（ほとんど下げない）

# 「AがBに投票した」＝この2人は仲間ではない、という証拠の強さ
# （apply_vote_line_rule 参照）。仲間である世界の重みを何倍にするか。
# ★ハードルールにしない理由：人間のカロンは、疑いを逸らすために
#   わざと仲間に票を入れることがある（AIはやらないが、人間はやる）。
VOTE_LINE_WEIGHT = 0.35

# 図書室の"奪い合い"で生まれる駆け引き（カロンは物怖じせず主張し、人間陣営は
# 航海士をかばって遠慮しがち）は、AIの推理エンジン側では再現が難しいので、
# 代わりに「宣言そのものの説得力」に下駄を履かせる形で近似する。
# ★注意：この倍率は observations 側（宣言を作る側）で本当の役職を見て
#   あらかじめ決めておき、confidence_multiplier として渡す。ここから先の
#   計算（このファイル）は、なぜその倍率なのかを一切知らずに使うだけ。
#   AIの信念計算そのものには、本当の役職を絶対に触れさせないため。
REPORT_CHARON_CONFIDENCE_MULTIPLIER = 1.6  # カロン陣営の宣言は自信満々に響く
REPORT_HUMAN_HESITATION_MULTIPLIER = 0.6   # 人間陣営の宣言は言い淀みがちで割り引かれる


# ============================================================
# ルール1：航海士は毎日必ず操舵室へ行く
# ============================================================

def apply_navigator_rule(worlds, observations):
    """
    航海士ルールで世界を削る。

    world["roles"] で「航海士」になっている人が、
    どこかの日に「操舵室」以外へ行っていたら、
    その世界（＝その役職の組み合わせ）はありえないので weight を 0.0 にする。

    ★observations["forced_dest"]：日ごとの「自分の意思で行先を選べなかった人」
        例：{2: {"E"}}  → 2日目、Eは亡霊に談話室へ上書きされた

      この人たちは、このルールの判定から外します。
      亡霊の妨害で談話室へ引きずり出された航海士は、談話室にいますが
      航海士でないとは言えません。画面上も「⛔談話（上書き）」と
      表示されて上書きだと全員に分かるので、人間もそんな判断はしません。
      ここを見落とすと、AIは「真実の世界」を自分で消してしまいます。
      （この項目が無いときは、今まで通り全員を判定します）
    """
    destinations = observations["destinations"]
    forced = observations.get("forced_dest", {})

    for world in worlds:
        for player, role in world["roles"].items():
            if role != "航海士":
                continue
            # この世界では player は航海士 → 毎日操舵室に行っていないとおかしい
            for day, day_dest in destinations.items():
                if player in forced.get(day, ()):
                    continue  # 強制的に動かされた日は、本人の選択ではない
                dest = day_dest.get(player)
                if dest is not None and dest != "操舵室":
                    world["weight"] = 0.0
                    break  # 1日でも矛盾すれば、この世界はもう確定でアウト
    return worlds


# ============================================================
# ルール2：×を出せるのはカロンだけ（○×の集計ルール）
# ============================================================

def apply_bridge_count_rule(worlds, observations):
    """
    ○×の集計ルールで世界を削る。

    このゲームで×を出せる役職はカロンだけです
    （航海士・乗客・セイレーンは、操舵室に行ったら必ず○を出す）。

    なので、ある日の操舵室メンバーの中に×がN枚あったなら、
    その日操舵室に行った人の中に、少なくともN人はカロンがいないと
    数が合いません。カロンの人数がN人未満の世界はありえないので消します。

    ※「誰が×を出したか」までは公開情報からわからないので、
      「N人以上カロンがいる」ことまでしか言えません（＝それより人数が
      少ない世界だけを消す）。ここが航海士ルールとの違いです。
    """
    destinations = observations["destinations"]
    bridge_results = observations["bridge_results"]

    for day, day_dest in destinations.items():
        if day not in bridge_results:
            continue

        x_count = bridge_results[day]["x"]
        if x_count == 0:
            continue  # ×が1枚もなければ、このルールからは何も言えない

        # その日、操舵室に行った人の一覧
        bridge_members = [p for p, dest in day_dest.items() if dest == "操舵室"]

        for world in worlds:
            if world["weight"] == 0.0:
                continue  # すでにありえないと分かっている世界は見なくてよい

            charon_count = sum(
                1 for p in bridge_members if world["roles"].get(p) == "カロン"
            )
            if charon_count < x_count:
                world["weight"] = 0.0

    return worlds


# ============================================================
# ルール3：カロンが攻撃するには、その日談話室にいないといけない
# ============================================================

def apply_charon_attack_rule(worlds, observations):
    """
    カロンの攻撃ルールで世界を削る。

    カロンが誰かを攻撃できるのは、自分自身がその日「談話室」を
    選んでいたときだけです（ルールブック：「自身が談話室を伏せた時
    のみ任意の一人を攻撃可」）。

    なので、ある日に攻撃（成功・自滅を問わず）がN件発生していたなら、
    その日談話室に行った人の中に、少なくともN人はカロンがいないと
    数が合いません。カロンの人数がN人未満の世界はありえないので消します。

    ○×の集計ルール（ルール2）と、考え方はまったく同じです。
    「操舵室」が「談話室」に変わり、「×の枚数」が「攻撃の件数」に
    変わっただけです。

    observations["attacks"]：日ごとに、その日発生した攻撃イベントの件数
        例：{1: 1, 3: 2}  → 1日目に1件、3日目に2件の攻撃(自滅含む)が発生
        攻撃が起きなかった日は書かなくてOK（0件として扱われます）
    """
    destinations = observations["destinations"]
    attacks = observations.get("attacks", {})

    for day, attack_count in attacks.items():
        if attack_count == 0:
            continue  # 攻撃が0件なら、このルールからは何も言えない

        day_dest = destinations.get(day, {})
        # その日、談話室に行った人の一覧
        lounge_members = [p for p, dest in day_dest.items() if dest == "談話室"]

        for world in worlds:
            if world["weight"] == 0.0:
                continue  # すでにありえないと分かっている世界は見なくてよい

            charon_count = sum(
                1 for p in lounge_members if world["roles"].get(p) == "カロン"
            )
            if charon_count < attack_count:
                world["weight"] = 0.0

    return worlds


# ============================================================
# ルール4：図書室で確認した役職は絶対に正しい
# ============================================================

def apply_library_rule(worlds, observations):
    """
    図書室ルールで世界を削る。

    図書室で確認した役職は「絶対に正しい」情報です。カロンが嘘をつく
    余地がない、いわば一番確実な証拠なので、それと食い違う世界は
    問答無用でありえません（これまでの人数ルールと違って、
    「N人以上」のような曖昧さがなく、ピンポイントで決まります）。

    observations["library_reveals"]：図書室で確認できた結果のリスト
        例：[{"day": 2, "target": "G", "role": "乗客"}]
        → 2日目、図書室でGの役職が「乗客」だと確認できた

    ※注意：この情報は「確認した本人」だけが知っている私的な情報です。
      他のプレイヤー（や他のAI）は、本人が話さない限り知りません。
      なので実際にbotへ組み込むときは、AIごとに「知っている
      library_reveals の中身」が違う状態でこの関数を呼ぶことになります。
      関数自体はどちらの場合でも同じように使えます。
    """
    library_reveals = observations.get("library_reveals", [])

    for reveal in library_reveals:
        target = reveal["target"]
        true_role = reveal["role"]

        for world in worlds:
            if world["weight"] == 0.0:
                continue  # すでにありえないと分かっている世界は見なくてよい
            if world["roles"].get(target) != true_role:
                world["weight"] = 0.0

    return worlds


# ============================================================
# ルール5：セイレーンの呼び寄せ
# ============================================================

def apply_siren_charm_rule(worlds, observations):
    """
    セイレーンの呼び寄せルールで世界を削る。

    呼び寄せ（セイレーンが誰かの行先を操舵室+○に上書きする能力）からは、
    2つのことがわかります。

    (1) 呼び寄せられた人は、セイレーンではありえない
        （ルールブック：「この呼び寄せは、セイレーン同士は不可能」
          自分自身を対象にすることもできない）

    (2) 呼び寄せが起きた日は、その本人（術者）が「操舵室」を選んでいた
        はず（自身が操舵室を伏せた時のみ発動する能力のため）。
        なので、呼び寄せられた人を除いた「本当に操舵室へ行った人」の中に、
        少なくとも呼び寄せの件数ぶんのセイレーンがいないと数が合わない。
        ○×集計ルール・カロンの攻撃ルールと同じ「最低人数」の考え方です。

    observations["siren_charms"]：日ごとに、その日呼び寄せられた人のリスト
        例：{1: ["G"]}  → 1日目、Gが呼び寄せられた

    ※呼び寄せられた人の行先は destinations の中では「操舵室」のまま
      記録されている前提です（見た目は操舵室と同じに公開されるため。
      本物の操舵室組と区別するために、このsiren_charmsが必要になります）。
    """
    destinations = observations["destinations"]
    siren_charms = observations.get("siren_charms", {})

    for day, charmed_players in siren_charms.items():
        if not charmed_players:
            continue

        # (1) 呼び寄せられた人はセイレーンではありえない
        for world in worlds:
            if world["weight"] == 0.0:
                continue
            for target in charmed_players:
                if world["roles"].get(target) == "セイレーン":
                    world["weight"] = 0.0
                    break

        # (2) 呼び寄せ組を除いた「本当の操舵室メンバー」の中に、
        #     呼び寄せ件数ぶんのセイレーンが必要
        day_dest = destinations.get(day, {})
        charmed_set = set(charmed_players)
        genuine_bridge_members = [
            p for p, dest in day_dest.items()
            if dest == "操舵室" and p not in charmed_set
        ]

        needed = len(charmed_players)
        for world in worlds:
            if world["weight"] == 0.0:
                continue
            siren_count = sum(
                1 for p in genuine_bridge_members if world["roles"].get(p) == "セイレーン"
            )
            if siren_count < needed:
                world["weight"] = 0.0

    return worlds


# ============================================================
# ルール6：攻撃対象になった人はカロンではありえない
# ============================================================

def apply_attack_victim_rule(worlds, observations):
    """
    カロン被害者ルールで世界を削る。

    カロンは、仲間のカロンを攻撃対象に選ぶことができません
    （ルールブック：「カロン同士の攻撃はできない」）。
    なので、その日誰かに攻撃された人は、生き残ったか死んだかに関係なく
    カロンではありえません（図書室ルールと同じく、曖昧さのないハードな
    確定情報です）。

    observations["attack_targets"]：日ごとに、その日攻撃対象になった人のリスト
        例：{1: ["F"]}  → 1日目、Fが攻撃対象になった（生死は問わない）
    """
    attack_targets = observations.get("attack_targets", {})

    for day, targets in attack_targets.items():
        for world in worlds:
            if world["weight"] == 0.0:
                continue
            for target in targets:
                if world["roles"].get(target) == "カロン":
                    world["weight"] = 0.0
                    break

    return worlds


# ============================================================
# ルール7：行先宣言（嘘をついてもいい情報）※ここからソフトルール
# ============================================================

def apply_destination_declaration_rule(worlds, observations, lie_weight=LIE_WEIGHT_BOOST):
    """
    行先宣言ルールで世界の重みを調整する。

    プレイヤーは朝の議論中に「今日は談話室に行こうと思う」のように
    行先を宣言できますが、これは嘘をついてもいい情報です。実際に
    伏せた行先（destinations）と食い違っていたら、その人は嘘を
    ついたことになります。

    ★これまでのルールとの違い（重要）
      今までは「矛盾したら世界を消す（weight=0）」でしたが、
      宣言は嘘の可能性がある情報なので、食い違ったからといって
      その世界が「ありえない」とまでは言い切れません。
      その代わり、食い違った人が「カロン」または「セイレーン」で
      ある世界の重みを、掛け算で少し引き上げます
      （lie_weight 倍。1.4なら4割増しの"ありえそう度"になる）。

      宣言通りだった場合は、何もしません。宣言通りに動くのは
      当たり前のことが多く、それだけでは「潔白の証拠」とまでは
      言えないからです（弱い情報なので、今回は無視する設計です）。

    observations["destination_declarations"]：宣言の記録
        例：{1: {"C": "談話室"}}
        → 1日目、Cが「談話室に行く」と宣言した

    lie_weight：食い違いが1回見つかるたびに重みを何倍にするか。
      正しい値の理論的な根拠はなく、自己対戦（工程4）で調整していく
      前提の仮の数値です（ファイル冒頭の LIE_WEIGHT_BOOST を参照）。
      同じ人が何日も嘘をついていたら、その分だけ掛け算が重なって
      どんどん重みが増えていきます（証拠が積み重なるイメージ）。
    """
    declarations = observations.get("destination_declarations", {})
    destinations = observations["destinations"]

    for day, day_declarations in declarations.items():
        day_dest = destinations.get(day, {})

        for player, declared_dest in day_declarations.items():
            actual_dest = day_dest.get(player)
            if actual_dest is None:
                continue  # その日の実際の行先がまだ分かっていない場合はスキップ
            if declared_dest == actual_dest:
                continue  # 宣言通りだった → 今回は何もしない

            # 宣言と食い違った（嘘をついた）→ カロン・セイレーンの重みを引き上げる
            for world in worlds:
                if world["weight"] == 0.0:
                    continue
                role = world["roles"].get(player)
                if role in ("カロン", "セイレーン"):
                    world["weight"] *= lie_weight

    return worlds


# ============================================================
# ルール8：図書室の結果"宣言"（嘘の可能性あり、信頼度で重みづけ）
# ============================================================

def apply_library_report_rule(worlds, observations,
                               trust_boost=REPORT_TRUST_BOOST, min_weight=REPORT_MIN_WEIGHT):
    """
    図書室で確認した結果を、夜の議論で宣言した内容から世界の重みを動かす。

    行先宣言ルールと違って、これは「証拠が正しいかどうかを検証できない」
    タイプの情報です（実際に操舵室へ行ったかどうかは後で公開されて
    答え合わせができますが、図書室の中身は本人にしか分かりません）。
    なので、単純な「食い違ったら○倍」ではなく、
    ★宣言した人がどれだけ信頼できるか（＝今のところカロン・セイレーンらしく
      ないか）に応じて、信じる度合い自体を変えます。

    信頼度が高い人の宣言ほど、内容と一致する世界を強く引き上げ、
    食い違う世界を強く引き下げます。信頼度が低い人（すでに疑わしい人）の
    宣言は、内容が一致しても軽くしか信じません（＝重みをあまり動かさない）。

    observations["library_reports"]：日ごとの宣言のリスト
        例：{2: [{"reporter": "C", "target": "G", "claimed_role": "航海士",
                  "confidence_multiplier": 1.6}]}
        → 2日目、Cが「Gの役職は航海士だった」と宣言した。
        confidence_multiplier は「宣言そのものの説得力」の下駄（省略時1.0）。
        本当の役職を見て決める必要があるので、必ず宣言を作る側
        （simulate.py。本当の役職を知っている）で計算して埋め込むこと。
        このファイルは、その数字が何に基づくかを一切知らずに使うだけ。

    trust_boost：信頼度100%のとき、一致する世界を何倍まで引き上げるか
    min_weight ：信頼度0%でも、食い違う世界の重みをどこまでは残すか
      （「明らかに疑わしい人の発言でも、可能性をゼロと決めつけない」ための下限）
    """
    reports = observations.get("library_reports", {})

    for day, day_reports in reports.items():
        for report in day_reports:
            reporter = report["reporter"]
            target = report["target"]
            claimed = report["claimed_role"]
            confidence_multiplier = report.get("confidence_multiplier", 1.0)

            # 宣言者への信頼度を、今の推理結果（宣言前の状態）から計算する
            reporter_suspicion = (
                engine.role_ratio(worlds, reporter, "カロン")
                + engine.role_ratio(worlds, reporter, "セイレーン")
            )
            trust = min(1.0, (1.0 - reporter_suspicion) * confidence_multiplier)

            boost = 1.0 + trust_boost * trust
            penalty = max(min_weight, 1.0 - trust)

            for world in worlds:
                if world["weight"] == 0.0:
                    continue
                if world["roles"].get(target) == claimed:
                    world["weight"] *= boost
                    continue

                # ---- 宣言と食い違う世界 ----
                # ★ここが「ライン」を読めるかどうかの分かれ目（2026-08-14に修正）。
                #   以前はどの世界にも同じ penalty をかけていたため、
                #   「宣言者も対象も両方カロン陣営で、これは庇い合いの嘘」という
                #   世界まで一緒に沈めていた。＝AIはラインを疑うどころか、
                #   ラインの可能性を自分で消していた。
                #   その世界で宣言者がカロン陣営なら、嘘には動機があるので
                #   ほとんど下げない。人間陣営なら、見たままを言わない理由が
                #   無いので今まで通り強く下げる。
                if world["roles"].get(reporter) in engine.CHARON_SIDE:
                    world["weight"] *= REPORT_LIE_WEIGHT_CHARON
                else:
                    world["weight"] *= penalty

    return worlds


# ============================================================
# ルール9：夜の投票から「ライン」を読む（ソフトルール）
# ============================================================

def apply_vote_line_rule(worlds, observations, line_weight=VOTE_LINE_WEIGHT,
                         charons_know_each_other=True, only_day=None):
    """
    「AがBに投票した」という公開情報から、2人が仲間である世界の重みを下げる。

    ★考え方
      カロン陣営は、正体を知っている仲間には票を入れません。追放されたら
      そのまま負けに直結するからです。逆に言うと「AがBに入れた」という
      事実は、★2人が仲間ではないことの証拠になります。
      人間の卓で言うところの「ライン切り」を、そのまま素直に数式にしたもの。

      ★向きに注意：ユーザーが最初に挙げた「AがBに入れなかったから怪しい」
        の方は、9人卓では票が散るので情報がほとんどありません
        （実測：投票ペアが両方カロン陣営だったのは2.4%、ランダムな
          ペアの基準値は7.5%。＝"入れた"方向にだけ強い信号がある）。
        なので、疑う材料ではなく「白を確保する材料」として働きます。

    ★誰が誰を知っているかで扱いが変わる（ここを間違えると逆効果）
      ・カロン同士は互いを知っている        → 票を入れ合わない
      ・セイレーンはカロンを知っている      → セイレーン→カロンには入れない
      ・カロンはセイレーンを知らない        → ★カロン→セイレーンは普通に起こる
        （実測でも、カロン陣営が入れた票の行き先はほぼ全部セイレーンだった）
      この非対称を無視して「カロン陣営同士なら一律で下げる」とすると、
      カロン→セイレーンの票を誤って"ラインではない証拠"として扱ってしまう。

    observations["votes"]：日ごとの投票結果 {日: {投票した人: 投票先 or None}}

    charons_know_each_other: カロン同士が互いの正体を知るルールか
      （ホストが切っている卓では、カロン同士も票を入れ合いうるので
        このルールを適用してはいけない）。

    only_day：この日の投票だけを反映する。
      ★これを必ず指定すること。ソフトルールは毎日呼ばれるので、
        全履歴を毎回かけると同じ1票が何度も掛け算されて（5日で0.35の5乗＝
        ほぼ0）、ハードルールと変わらない強さになってしまう。
        1日ぶんずつ、1回だけ反映するのが正しい。
    """
    votes = observations.get("votes", {})
    if only_day is not None:
        votes = {only_day: votes.get(only_day)} if only_day in votes else {}

    for _day, day_votes in votes.items():
        if not day_votes:
            continue
        for voter, target in day_votes.items():
            if target is None or target == voter:
                continue
            for world in worlds:
                if world["weight"] == 0.0:
                    continue
                roles = world["roles"]
                voter_role = roles.get(voter)
                target_role = roles.get(target)
                # この世界で、投票した人は相手の正体を知っていたか？
                knows = (
                    (voter_role == "カロン" and target_role == "カロン"
                     and charons_know_each_other)
                    or (voter_role == "セイレーン" and target_role == "カロン")
                )
                if knows:
                    world["weight"] *= line_weight

    return worlds


# ============================================================
# 確認用：全役職ぶんの割合をまとめて表示する
# ============================================================

def summarize(worlds, players):
    """現在の重みをもとに、各プレイヤーの役職ごとの可能性(割合)を返す"""
    role_names = set()
    for w in worlds:
        role_names.update(w["roles"].values())

    result = {}
    for p in players:
        result[p] = {r: engine.role_ratio(worlds, p, r) for r in role_names}
    return result


def print_summary(worlds, players):
    summary = summarize(worlds, players)
    role_names = sorted({r for p in summary for r in summary[p]})

    header = "      " + "  ".join(f"{r:>6}" for r in role_names)
    print(header)
    for p in players:
        cells = "  ".join(f"{summary[p][r]*100:5.1f}%" for r in role_names)
        print(f"   {p}: {cells}")


# ============================================================
# 実行して確認する
# ============================================================

def main():
    n = 7
    composition = engine.COMPOSITIONS[n]
    players = engine.make_players(n)
    worlds = engine.build_worlds(players, composition)

    print("=" * 60)
    print(f"■ ルールのテスト（{n}人プレイ）")
    print(f"  世界の総数: {len(worlds)}")
    print()
    print("  ---- 何もわかっていない状態 ----")
    print_summary(worlds, players)

    # ---- ダミーの観測データ（テスト用の架空の2日間） ----
    # 1日目：操舵室 = A, B, E （合計 ○2枚：×1枚 → 誰か1人はカロン）
    # 2日目：操舵室 = A, D, E （合計 ○3枚：×0枚 → このルールからは何もわからない）
    observations = {
        "destinations": {
            1: {"A": "操舵室", "B": "操舵室", "C": "談話室", "D": "図書室",
                "E": "操舵室", "F": "談話室", "G": "談話室"},
            2: {"A": "操舵室", "B": "談話室", "C": "図書室", "D": "操舵室",
                "E": "操舵室", "F": "談話室", "G": "談話室"},
        },
        "bridge_results": {
            1: {"c": 2, "x": 1},
            2: {"c": 3, "x": 0},
        },
        # 1日目の談話室(C, F, G)で、攻撃(成功 or 自滅)が1件発生した
        "attacks": {
            1: 1,
        },
        # 2日目、図書室に1人だけ行ったC(day2データ参照)が、Gの役職を確認した
        "library_reveals": [
            {"day": 2, "target": "G", "role": "乗客"},
        ],
        # 1日目、Fが攻撃対象になった（生死は問わない）
        "attack_targets": {
            1: ["F"],
        },
    }

    # ---- まず航海士ルールだけ適用 ----
    apply_navigator_rule(worlds, observations)
    print()
    print(f"  ---- 航海士ルール適用後（残り {sum(1 for w in worlds if w['weight'] > 0)} / {len(worlds)} 世界） ----")
    print_summary(worlds, players)
    print("  ※ A・E が航海士に確定。B・C・D・F・G はまだ横並び。")

    # ---- 続けて○×の集計ルールも適用 ----
    apply_bridge_count_rule(worlds, observations)
    print()
    print(f"  ---- ○×集計ルールも適用後（残り {sum(1 for w in worlds if w['weight'] > 0)} / {len(worlds)} 世界） ----")
    print_summary(worlds, players)
    print("  ※ 1日目の操舵室(A,B,E)で×が1枚出ているのに、A・Eは航海士(×を出せない)。")
    print("    つまり×を出せるのはBしかいない → Bがカロンに確定するはずです。")
    print("    残るカロン枠は1人だけなので、C・D・F・Gのカロン率は25%ずつに絞られます。")

    # ---- さらにカロンの攻撃ルールも適用 ----
    apply_charon_attack_rule(worlds, observations)
    print()
    print(f"  ---- カロンの攻撃ルールも適用後（残り {sum(1 for w in worlds if w['weight'] > 0)} / {len(worlds)} 世界） ----")
    print_summary(worlds, players)
    print("  ※ 1日目に談話室へ行ったのはC・F・Gだけ（Dは図書室にいた）。")
    print("    でも1日目に攻撃が1件起きている＝談話室にカロンが最低1人いたはず。")
    print("    Dは談話室にいないのでカロンではありえない → D のカロン率が0%になり、")
    print("    残ったカロン枠1人はC・F・Gの中の誰かに絞られ、それぞれ33.3%になります。")

    # ---- さらに図書室ルールも適用 ----
    apply_library_rule(worlds, observations)
    print()
    print(f"  ---- 図書室ルールも適用後（残り {sum(1 for w in worlds if w['weight'] > 0)} / {len(worlds)} 世界） ----")
    print_summary(worlds, players)
    print("  ※ 2日目、図書室にいたCがGを確認し「乗客」だと判明。")
    print("    Gがカロンの世界は問答無用で消えるので、Gのカロン率は0%（乗客確定）。")
    print("    残ったカロン枠1人はC・Fのどちらかに絞られ、それぞれ50%になります。")

    # ---- 最後にカロン被害者ルールも適用 ----
    apply_attack_victim_rule(worlds, observations)
    print()
    print(f"  ---- カロン被害者ルールも適用後（残り {sum(1 for w in worlds if w['weight'] > 0)} / {len(worlds)} 世界） ----")
    print_summary(worlds, players)
    print("  ※ 1日目、Fが攻撃対象になった → Fはカロンではありえない。")
    print("    残っていたカロン候補はC・Fの2人だけだったので、これでCがカロンに確定です。")
    print("    7人全員の役職が特定できました（世界は1つに絞られたはずです）。")


def main_siren():
    # セイレーンは7人プレイにはいないので、ここだけ8人プレイ(A〜H)で試す
    n = 8
    composition = engine.COMPOSITIONS[n]
    players = engine.make_players(n)
    worlds = engine.build_worlds(players, composition)

    print()
    print("=" * 60)
    print(f"■ セイレーンの呼び寄せルールのテスト（{n}人プレイ）")
    print(f"  世界の総数: {len(worlds)}")
    print()
    print("  ---- 何もわかっていない状態 ----")
    print_summary(worlds, players)

    # ---- ダミーの観測データ（1日だけの架空データ） ----
    # 本当に操舵室へ行ったのは A と C の2人だけ。
    # G は見た目だけ「操舵室」だが、これはセイレーンに呼び寄せられた結果。
    observations = {
        "destinations": {
            1: {"A": "操舵室", "B": "談話室", "C": "操舵室", "D": "談話室",
                "E": "図書室", "F": "談話室", "G": "操舵室", "H": "談話室"},
        },
        "siren_charms": {
            1: ["G"],
        },
    }

    apply_siren_charm_rule(worlds, observations)
    print()
    print(f"  ---- 呼び寄せルール適用後（残り {sum(1 for w in worlds if w['weight'] > 0)} / {len(worlds)} 世界） ----")
    print_summary(worlds, players)
    print("  ※ Gは呼び寄せられた本人なので、セイレーンである確率が0%になります。")
    print("    その日「本当に」操舵室にいたのはA・Cだけ（Gを除く）なので、")
    print("    セイレーンはAかCのどちらかに絞られ、それぞれの確率が上がります。")
    print("    それ以外（B・D・E・F・H）のセイレーン率も0%になります。")


def main_declaration():
    # 行先宣言ルール単体の効果を見るため、まっさらな7人プレイで試す
    n = 7
    composition = engine.COMPOSITIONS[n]
    players = engine.make_players(n)
    worlds = engine.build_worlds(players, composition)

    print()
    print("=" * 60)
    print(f"■ 行先宣言ルールのテスト（{n}人プレイ、他のルールは適用しない）")
    print(f"  世界の総数: {len(worlds)}")
    print()
    print("  ---- 何もわかっていない状態 ----")
    print_summary(worlds, players)

    # ---- ダミーの観測データ ----
    # Cは「談話室に行く」と宣言したが、実際には操舵室に行った（宣言と食い違い＝嘘）
    observations = {
        "destinations": {
            1: {"A": "操舵室", "B": "談話室", "C": "操舵室", "D": "図書室",
                "E": "談話室", "F": "談話室", "G": "談話室"},
        },
        "destination_declarations": {
            1: {"C": "談話室"},
        },
    }

    apply_destination_declaration_rule(worlds, observations)
    print()
    print(f"  ---- 行先宣言ルール適用後（LIE_WEIGHT_BOOST={LIE_WEIGHT_BOOST}） ----")
    print_summary(worlds, players)
    print("  ※ Cは「談話室に行く」と宣言したのに、実際は操舵室に行っていた＝嘘。")
    print(f"    Cがカロンである世界の重みが{LIE_WEIGHT_BOOST}倍された結果、")
    print("    Cのカロン率だけ28.6%より上がり、他の6人はわずかに下がっているはずです。")
    print("    （ハードルールと違って0%や100%にはならない、「傾き」だけの変化です）")


if __name__ == "__main__":
    main()
    main_siren()
    main_declaration()
    main_siren()
