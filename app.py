import os
import time
import requests
import google.generativeai as genai
import csv
import streamlit as st
from concurrent.futures import ThreadPoolExecutor
import random
from dotenv import load_dotenv
from datetime import datetime
import json
import webbrowser
import calendar

# .env ファイルから環境変数を読み込む
load_dotenv()

# 環境変数から設定を読み込む
GEMINI_API_KEY=st.secrets["GEMINI_API_KEY"]
RAKUTEN_APP_ID=st.secrets["RAKUTEN_APP_ID"]

genai.configure(api_key=GEMINI_API_KEY)

generation_config = {
    "temperature": 0.9,
    "top_p": 0.95,
    "top_k": 64,
    "max_output_tokens": 8192,
}

model = genai.GenerativeModel(
    model_name="gemini-2.5-flash-preview-04-17",
    generation_config=generation_config,
)

@st.cache_data
def load_category_data(file_path):
    categories = {}
    with open(file_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f, delimiter='\t')
        for row in reader:
            categories[row[1]] = row[0]
    return categories

def get_category_ids(user_request, categories, start_date, rice_ratio, bread_ratio, noodle_ratio):
    # 日本語の曜日名を取得
    weekdays = ["月", "火", "水", "木", "金", "土", "日"]
    start_weekday = weekdays[start_date.weekday()]
    
    prompt = f"""
    #以下は食材とそのカテゴリIDのリストです：
    {categories}

    ユーザーの要求: {user_request}
    開始日: {start_date.strftime('%Y-%m-%d')} ({start_weekday})
    主食の比重: ごはんもの {rice_ratio}%, パン {bread_ratio}%, 麺類 {noodle_ratio}%

    #条件
    この要求と日付、曜日に合う食材・料理を20個選び、必ずそのカテゴリIDをカンマ区切りで出力してください。
    日付と曜日から季節や特別なイベント（例：お正月、クリスマス、ハロウィンなど）を考慮し、適切な食材を選んでください。
    また、それ以外は絶対に出力しないでください。
    出力形式: カテゴリID1,カテゴリID2,カテゴリID3
    """
    
    response = model.generate_content(prompt)
    return response.text.strip()

def get_recipe(category_id):
    url = "https://app.rakuten.co.jp/services/api/Recipe/CategoryRanking/20170426"
    params = {
        "applicationId": RAKUTEN_APP_ID,
        "categoryId": category_id.strip(),
        "format": "json",
        "elements": "recipeTitle,recipeUrl,recipeMaterial",
        "hits": 10  # 各カテゴリから最大10件のレシピを取得
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get('result', [])
    except requests.exceptions.RequestException as e:
        st.warning(f"カテゴリID {category_id} のAPIリクエストに失敗しました。")
        return []

@st.cache_data(ttl=3600)
def get_recipes(category_ids):
    all_recipes = []
    for category_id in category_ids.split(',')[:20]:  # 最大20カテゴリまで処理
        recipes = get_recipe(category_id)
        all_recipes.extend(recipes)
        time.sleep(1)  # 1秒間隔を空ける
    return all_recipes

def select_recipes(recipes, user_request, start_date, meal_types, rice_ratio, bread_ratio, noodle_ratio):
    recipes_info = "\n".join([
        f"{i+1}. {recipe['recipeTitle']} - 材料: {', '.join(recipe['recipeMaterial'])} - URL: {recipe.get('recipeUrl', 'URL不明')}"
        for i, recipe in enumerate(recipes)
    ])
    
    meal_types_str = ", ".join(meal_types)
    
    # 日本語の曜日名を取得
    weekdays = ["月", "火", "水", "木", "金", "土", "日"]
    
    prompt = f"""
    ユーザーの要求: {user_request}
    開始日: {start_date.strftime('%Y-%m-%d')} ({weekdays[start_date.weekday()]})
    食事タイプ: {meal_types_str}
    主食の比重: ごはんもの {rice_ratio}%, パン {bread_ratio}%, 麺類 {noodle_ratio}%
    
    以下は、その要求に基づいて選ばれた食材から作れるレシピのリストです：
    {recipes_info}
    
    これらのレシピから、ユーザーの要求に最も適した1週間分の献立（{meal_types_str}）を作成してください。
    各食事について、条件をもとに1日ごとにステップバイステップでレシピを選定し、その理由、材料、URLを記載してください。
    主食の比重に従ってレシピを選択してください。
    開始日から季節や特別なイベント（例：お正月、クリスマス、ハロウィンなど）、さらに曜日も考慮し、適切なレシピを選んでください。
    最後に、1週間分の献立で必要な材料の総まとめを作成してください。

    #必ず以下の出力形式に則って出力してください
    **[日付] ([曜日]):**
    """

    for meal_type in meal_types:
        prompt += f"""
    {meal_type}: [レシピNO].[レシピ名]
    理由: [選んだ理由]
    材料: [材料リスト]
    URL: [レシピのURL]
    """

    prompt += """
    ...
    1週間分の材料まとめ:
    
    **肉・魚:**
    [材料名]: [必要な量]

    **野菜:**
    [材料名]: [必要な量]

    **調味料など:**
    [材料名]: [必要な量]
    ...

    #条件
    - 必ず開始日から7日分の献立を出力してください。
    - 似た料理は絶対出さないでください。
    - 前日の残りなどは考慮しないでください。
    - 材料まとめでは、同じ材料を使用する場合はまとめて記載してください。ステップバイステップで、表記の重複がないことを確認してください。
    - 材料の量は、レシピに記載がない場合は適切な量を推定してください。「適量」という表現は禁止です。
    - 朝食は簡単に準備できるものを選んでください。
    - 夕食は朝昼に比べ手が込んでいるものを選んでください。
    - 主食そのもの、または主食にあうおかずなどを選択してください。
    - 絶対にサラダやスイーツ、味噌汁などを選ばないでください。
    - 開始日から季節や特別なイベント、曜日を考慮し、適切なレシピを選んでください。

    #全レシピリスト
    {recipes_info}
    """
    
    response = model.generate_content(prompt)
    return response.text.strip()

def parse_meal_plan(meal_plan, meal_types):
    lines = meal_plan.split('\n')
    parsed_plan = {}
    current_date = None
    current_meal = None
    materials_summary = []
    is_materials_summary = False

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if line.startswith("## 1週間分の材料まとめ:"):
            is_materials_summary = True
            continue

        if is_materials_summary:
            materials_summary.append(line)
            continue

        if line.startswith("**") and "(" in line and ")" in line:
            current_date = line.strip("*").split("(")[0].strip()
            parsed_plan[current_date] = {}
        elif any(line.startswith(f"{meal_type}:") for meal_type in meal_types):
            current_meal = line.split(":")[0]
            recipe = line.split(":", 1)[1].strip()
            parsed_plan[current_date][current_meal] = {"recipe": recipe, "reason": "", "materials": "", "url": ""}
        elif line.startswith("理由:"):
            if current_date and current_meal:
                parsed_plan[current_date][current_meal]["reason"] = line.split(":", 1)[1].strip()
        elif line.startswith("材料:"):
            if current_date and current_meal:
                parsed_plan[current_date][current_meal]["materials"] = line.split(":", 1)[1].strip()
        elif line.startswith("URL:"):
            if current_date and current_meal:
                parsed_plan[current_date][current_meal]["url"] = line.split(":", 1)[1].strip()

    return parsed_plan, materials_summary

def get_food_icon(meal_type):
    meal_icons = {
        "朝食": "fa-sun",
        "昼食": "fa-cloud-sun",
        "夕食": "fa-moon"
    }
    return meal_icons.get(meal_type, "fa-utensils")

def display_calendar(meal_plan):
    col1, col2, col3 = st.columns(3)
    
    for i, (date, meals) in enumerate(meal_plan.items()):
        if i % 3 == 0:
            col = col1
        elif i % 3 == 1:
            col = col2
        else:
            col = col3
        
        with col:
            st.subheader(date)
            for meal_type, meal_info in meals.items():
                icon = get_food_icon(meal_type)
                st.markdown(f"<i class='fas {icon}'></i> **{meal_type}**", unsafe_allow_html=True)
                st.write(meal_info['recipe'])
                if st.button(f"{date} {meal_type}の詳細を見る", key=f"{date}_{meal_type}"):
                    st.session_state.current_page = f"{date}_{meal_type}"

def display_meal_details(date, meal_type, meal_info):
    st.subheader(f"{date} - {meal_type}")
    st.write(f"**レシピ:** {meal_info['recipe']}")
    st.write(f"**理由:** {meal_info['reason']}")
    st.write(f"**材料:** {meal_info['materials']}")
    st.write(f"**URL:** [{meal_info['url']}]({meal_info['url']})")
    if st.button("カレンダーに戻る"):
        st.session_state.current_page = "calendar"

def save_meal_plan(meal_plan, materials_summary, save_path):
    data = {
        "meal_plan": meal_plan,
        "materials_summary": materials_summary
    }
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_meal_plan(file):
    content = file.getvalue().decode("utf-8")
    data = json.loads(content)
    return data["meal_plan"], data["materials_summary"]

def generate_html(meal_plan, materials_summary, html_path):
    html_content = """
    <!DOCTYPE html>
    <html lang="ja">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>1週間分の献立</title>
        <style>
            body { font-family: Arial, sans-serif; line-height: 1.6; padding: 20px; }
            h1, h2, h3 { color: #333; }
            .meal { margin-bottom: 20px; }
            .materials { background-color: #f4f4f4; padding: 10px; margin-top: 20px; }
        </style>
    </head>
    <body>
        <h1>1週間分の献立</h1>
    """

    for date, meals in meal_plan.items():
        html_content += f"<h2>{date}</h2>"
        for meal_type, details in meals.items():
            html_content += f"""
            <div class="meal">
                <h3>{meal_type}: {details['recipe']}</h3>
                <p><strong>理由:</strong> {details['reason']}</p>
                <p><strong>材料:</strong> {details['materials']}</p>
                <p><a href="{details['url']}" target="_blank">レシピを見る</a></p>
            </div>
            """

    html_content += "<h2>1週間分の材料まとめ</h2><div class='materials'>"
    for line in materials_summary:
        html_content += f"<p>{line}</p>"
    html_content += "</div></body></html>"

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

def main():
    st.set_page_config(page_title="AI主夫", layout="wide")
    
    # Font Awesome の CSS を追加
    st.markdown("""
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.1/css/all.min.css">
    <style>
    .fas { font-size: 24px; margin-right: 10px; }
    </style>
    """, unsafe_allow_html=True)
    
    st.title("AI主夫")
    st.write("あなたの要望に基づいて、1週間分のバランスの取れた献立を提案します。")

    categories = """
        category_full_id	category_name	category_url
        30	人気メニュー	https://recipe.rakuten.co.jp/category/30/
        31	定番の肉料理	https://recipe.rakuten.co.jp/category/31/
        32	定番の魚料理	https://recipe.rakuten.co.jp/category/32/
        33	卵料理	https://recipe.rakuten.co.jp/category/33/
        14	ご飯もの	https://recipe.rakuten.co.jp/category/14/
        15	パスタ	https://recipe.rakuten.co.jp/category/15/
        16	麺・粉物料理	https://recipe.rakuten.co.jp/category/16/
        17	汁物・スープ	https://recipe.rakuten.co.jp/category/17/
        23	鍋料理	https://recipe.rakuten.co.jp/category/23/
        18	サラダ	https://recipe.rakuten.co.jp/category/18/
        22	パン	https://recipe.rakuten.co.jp/category/22/
        21	お菓子	https://recipe.rakuten.co.jp/category/21/
        10	肉	https://recipe.rakuten.co.jp/category/10/
        11	魚	https://recipe.rakuten.co.jp/category/11/
        12	野菜	https://recipe.rakuten.co.jp/category/12/
        34	果物	https://recipe.rakuten.co.jp/category/34/
        19	ソース・調味料・ドレッシング	https://recipe.rakuten.co.jp/category/19/
        27	飲みもの	https://recipe.rakuten.co.jp/category/27/
        35	大豆・豆腐	https://recipe.rakuten.co.jp/category/35/
        13	その他の食材	https://recipe.rakuten.co.jp/category/13/
        20	お弁当	https://recipe.rakuten.co.jp/category/20/
        36	簡単料理	https://recipe.rakuten.co.jp/category/36/
        37	節約料理	https://recipe.rakuten.co.jp/category/37/
        38	今日の献立	https://recipe.rakuten.co.jp/category/38/
        39	健康料理	https://recipe.rakuten.co.jp/category/39/
        40	調理器具	https://recipe.rakuten.co.jp/category/40/
        26	その他の目的・シーン	https://recipe.rakuten.co.jp/category/26/
        41	中華料理	https://recipe.rakuten.co.jp/category/41/
        42	韓国料理	https://recipe.rakuten.co.jp/category/42/
        43	イタリア料理	https://recipe.rakuten.co.jp/category/43/
        44	フランス料理	https://recipe.rakuten.co.jp/category/44/
        25	西洋料理	https://recipe.rakuten.co.jp/category/25/
        46	エスニック料理・中南米	https://recipe.rakuten.co.jp/category/46/
        47	沖縄料理	https://recipe.rakuten.co.jp/category/47/
        48	日本各地の郷土料理	https://recipe.rakuten.co.jp/category/48/
        24	行事・イベント	https://recipe.rakuten.co.jp/category/24/
        49	おせち料理	https://recipe.rakuten.co.jp/category/49/
        50	クリスマス	https://recipe.rakuten.co.jp/category/50/
        51	ひな祭り	https://recipe.rakuten.co.jp/category/51/
        52	春（3月～5月）	https://recipe.rakuten.co.jp/category/52/
        53	夏（6月～8月）	https://recipe.rakuten.co.jp/category/53/
        54	秋（9月～11月）	https://recipe.rakuten.co.jp/category/54/
        55	冬（12月～2月）	https://recipe.rakuten.co.jp/category/55/
        10-275	牛肉	https://recipe.rakuten.co.jp/category/10-275/
        10-276	豚肉	https://recipe.rakuten.co.jp/category/10-276/
        10-277	鶏肉	https://recipe.rakuten.co.jp/category/10-277/
        10-278	ひき肉	https://recipe.rakuten.co.jp/category/10-278/
        10-68	ベーコン	https://recipe.rakuten.co.jp/category/10-68/
        10-66	ソーセージ・ウインナー	https://recipe.rakuten.co.jp/category/10-66/
        10-67	ハム	https://recipe.rakuten.co.jp/category/10-67/
        10-69	その他のお肉	https://recipe.rakuten.co.jp/category/10-69/
        11-70	サーモン・鮭	https://recipe.rakuten.co.jp/category/11-70/
        11-71	いわし	https://recipe.rakuten.co.jp/category/11-71/
        11-72	さば	https://recipe.rakuten.co.jp/category/11-72/
        11-73	あじ	https://recipe.rakuten.co.jp/category/11-73/
        11-74	ぶり	https://recipe.rakuten.co.jp/category/11-74/
        11-75	さんま	https://recipe.rakuten.co.jp/category/11-75/
        11-76	鯛	https://recipe.rakuten.co.jp/category/11-76/
        11-77	マグロ	https://recipe.rakuten.co.jp/category/11-77/
        11-443	たら	https://recipe.rakuten.co.jp/category/11-443/
        11-78	その他のさかな	https://recipe.rakuten.co.jp/category/11-78/
        11-80	いか	https://recipe.rakuten.co.jp/category/11-80/
        11-81	たこ	https://recipe.rakuten.co.jp/category/11-81/
        11-79	エビ	https://recipe.rakuten.co.jp/category/11-79/
        11-83	かに	https://recipe.rakuten.co.jp/category/11-83/
        11-444	牡蠣	https://recipe.rakuten.co.jp/category/11-444/
        11-82	貝類	https://recipe.rakuten.co.jp/category/11-82/
        11-445	明太子・魚卵	https://recipe.rakuten.co.jp/category/11-445/
        11-446	その他の魚介	https://recipe.rakuten.co.jp/category/11-446/
        12-447	なす	https://recipe.rakuten.co.jp/category/12-447/
        12-448	かぼちゃ	https://recipe.rakuten.co.jp/category/12-448/
        12-449	大根	https://recipe.rakuten.co.jp/category/12-449/
        12-450	きゅうり	https://recipe.rakuten.co.jp/category/12-450/
        12-97	じゃがいも	https://recipe.rakuten.co.jp/category/12-97/
        12-452	さつまいも	https://recipe.rakuten.co.jp/category/12-452/
        12-98	キャベツ	https://recipe.rakuten.co.jp/category/12-98/
        12-453	白菜	https://recipe.rakuten.co.jp/category/12-453/
        12-454	トマト	https://recipe.rakuten.co.jp/category/12-454/
        12-99	もやし	https://recipe.rakuten.co.jp/category/12-99/
        12-456	小松菜	https://recipe.rakuten.co.jp/category/12-456/
        12-457	ほうれん草	https://recipe.rakuten.co.jp/category/12-457/
        12-455	ごぼう	https://recipe.rakuten.co.jp/category/12-455/
        12-451	アボカド	https://recipe.rakuten.co.jp/category/12-451/
        12-96	玉ねぎ	https://recipe.rakuten.co.jp/category/12-96/
        12-458	ブロッコリー	https://recipe.rakuten.co.jp/category/12-458/
        12-95	にんじん	https://recipe.rakuten.co.jp/category/12-95/
        12-100	春野菜	https://recipe.rakuten.co.jp/category/12-100/
        12-101	夏野菜	https://recipe.rakuten.co.jp/category/12-101/
        12-102	秋野菜	https://recipe.rakuten.co.jp/category/12-102/
        12-103	冬野菜	https://recipe.rakuten.co.jp/category/12-103/
        12-105	きのこ	https://recipe.rakuten.co.jp/category/12-105/
        12-107	香味野菜・ハーブ	https://recipe.rakuten.co.jp/category/12-107/
        12-104	その他の野菜	https://recipe.rakuten.co.jp/category/12-104/
        13-478	もち米	https://recipe.rakuten.co.jp/category/13-478/
        13-706	もち麦	https://recipe.rakuten.co.jp/category/13-706/
        13-479	マカロニ・ペンネ	https://recipe.rakuten.co.jp/category/13-479/
        13-480	ホットケーキミックス	https://recipe.rakuten.co.jp/category/13-480/
        13-481	粉類	https://recipe.rakuten.co.jp/category/13-481/
        13-108	練物	https://recipe.rakuten.co.jp/category/13-108/
        13-109	加工食品	https://recipe.rakuten.co.jp/category/13-109/
        13-482	チーズ	https://recipe.rakuten.co.jp/category/13-482/
        13-483	ヨーグルト	https://recipe.rakuten.co.jp/category/13-483/
        13-111	こんにゃく	https://recipe.rakuten.co.jp/category/13-111/
        13-112	しらたき	https://recipe.rakuten.co.jp/category/13-112/
        13-113	海藻	https://recipe.rakuten.co.jp/category/13-113/
        13-114	乾物	https://recipe.rakuten.co.jp/category/13-114/
        13-484	漬物	https://recipe.rakuten.co.jp/category/13-484/
        13-115	その他の食材	https://recipe.rakuten.co.jp/category/13-115/
        14-121	オムライス	https://recipe.rakuten.co.jp/category/14-121/
        14-131	チャーハン	https://recipe.rakuten.co.jp/category/14-131/
        14-126	パエリア	https://recipe.rakuten.co.jp/category/14-126/
        14-124	タコライス	https://recipe.rakuten.co.jp/category/14-124/
        14-122	チキンライス	https://recipe.rakuten.co.jp/category/14-122/
        14-123	ハヤシライス	https://recipe.rakuten.co.jp/category/14-123/
        14-125	ロコモコ	https://recipe.rakuten.co.jp/category/14-125/
        14-127	ピラフ	https://recipe.rakuten.co.jp/category/14-127/
        14-368	ハッシュドビーフ	https://recipe.rakuten.co.jp/category/14-368/
        14-128	その他○○ライス	https://recipe.rakuten.co.jp/category/14-128/
        14-129	寿司	https://recipe.rakuten.co.jp/category/14-129/
        14-130	丼物	https://recipe.rakuten.co.jp/category/14-130/
        14-132	炊き込みご飯	https://recipe.rakuten.co.jp/category/14-132/
        14-133	おかゆ・雑炊類	https://recipe.rakuten.co.jp/category/14-133/
        14-134	おにぎり	https://recipe.rakuten.co.jp/category/14-134/
        14-135	アレンジごはん	https://recipe.rakuten.co.jp/category/14-135/
        14-271	その他のごはん料理	https://recipe.rakuten.co.jp/category/14-271/
        15-687	カルボナーラ	https://recipe.rakuten.co.jp/category/15-687/
        15-137	ミートソース	https://recipe.rakuten.co.jp/category/15-137/
        15-676	ナポリタン	https://recipe.rakuten.co.jp/category/15-676/
        15-681	ペペロンチーノ	https://recipe.rakuten.co.jp/category/15-681/
        15-369	ジェノベーゼ	https://recipe.rakuten.co.jp/category/15-369/
        15-677	ペスカトーレ	https://recipe.rakuten.co.jp/category/15-677/
        15-683	たらこパスタ・明太子パスタ	https://recipe.rakuten.co.jp/category/15-683/
        15-682	ボンゴレ	https://recipe.rakuten.co.jp/category/15-682/
        15-678	アラビアータ	https://recipe.rakuten.co.jp/category/15-678/
        15-679	トマトクリームパスタ	https://recipe.rakuten.co.jp/category/15-679/
        15-684	納豆パスタ	https://recipe.rakuten.co.jp/category/15-684/
        15-680	トマト系パスタ	https://recipe.rakuten.co.jp/category/15-680/
        15-138	クリーム系パスタ	https://recipe.rakuten.co.jp/category/15-138/
        15-139	オイル・塩系パスタ	https://recipe.rakuten.co.jp/category/15-139/
        15-140	チーズ系パスタ	https://recipe.rakuten.co.jp/category/15-140/
        15-141	バジルソース系パスタ	https://recipe.rakuten.co.jp/category/15-141/
        15-142	和風パスタ	https://recipe.rakuten.co.jp/category/15-142/
        15-685	きのこパスタ	https://recipe.rakuten.co.jp/category/15-685/
        15-686	ツナパスタ	https://recipe.rakuten.co.jp/category/15-686/
        15-143	冷製パスタ	https://recipe.rakuten.co.jp/category/15-143/
        15-145	スープスパ・スープパスタ	https://recipe.rakuten.co.jp/category/15-145/
        15-146	その他のパスタ	https://recipe.rakuten.co.jp/category/15-146/
        15-144	パスタソース	https://recipe.rakuten.co.jp/category/15-144/
        15-147	ニョッキ	https://recipe.rakuten.co.jp/category/15-147/
        15-151	ラザニア	https://recipe.rakuten.co.jp/category/15-151/
        15-382	ラビオリ	https://recipe.rakuten.co.jp/category/15-382/
        16-152	うどん	https://recipe.rakuten.co.jp/category/16-152/
        16-153	蕎麦	https://recipe.rakuten.co.jp/category/16-153/
        16-154	そうめん	https://recipe.rakuten.co.jp/category/16-154/
        16-155	焼きそば	https://recipe.rakuten.co.jp/category/16-155/
        16-156	ラーメン	https://recipe.rakuten.co.jp/category/16-156/
        16-383	冷やし中華	https://recipe.rakuten.co.jp/category/16-383/
        16-384	つけ麺	https://recipe.rakuten.co.jp/category/16-384/
        16-272	その他の麺	https://recipe.rakuten.co.jp/category/16-272/
        16-385	お好み焼き	https://recipe.rakuten.co.jp/category/16-385/
        16-386	たこ焼き	https://recipe.rakuten.co.jp/category/16-386/
        16-158	粉物料理	https://recipe.rakuten.co.jp/category/16-158/
        17-159	味噌汁	https://recipe.rakuten.co.jp/category/17-159/
        17-161	豚汁	https://recipe.rakuten.co.jp/category/17-161/
        17-387	けんちん汁	https://recipe.rakuten.co.jp/category/17-387/
        17-160	お吸い物	https://recipe.rakuten.co.jp/category/17-160/
        17-388	かぼちゃスープ	https://recipe.rakuten.co.jp/category/17-388/
        17-169	野菜スープ	https://recipe.rakuten.co.jp/category/17-169/
        17-389	チャウダー・クラムチャウダー	https://recipe.rakuten.co.jp/category/17-389/
        17-171	コーンスープ・ポタージュ	https://recipe.rakuten.co.jp/category/17-171/
        17-168	トマトスープ	https://recipe.rakuten.co.jp/category/17-168/
        17-167	コンソメスープ	https://recipe.rakuten.co.jp/category/17-167/
        17-170	クリームスープ	https://recipe.rakuten.co.jp/category/17-170/
        17-164	中華スープ	https://recipe.rakuten.co.jp/category/17-164/
        17-165	和風スープ	https://recipe.rakuten.co.jp/category/17-165/
        17-166	韓国風スープ	https://recipe.rakuten.co.jp/category/17-166/
        17-173	その他のスープ	https://recipe.rakuten.co.jp/category/17-173/
        17-390	ポトフ	https://recipe.rakuten.co.jp/category/17-390/
        17-162	その他の汁物	https://recipe.rakuten.co.jp/category/17-162/
        18-415	ポテトサラダ	https://recipe.rakuten.co.jp/category/18-415/
        18-416	春雨サラダ	https://recipe.rakuten.co.jp/category/18-416/
        18-417	大根サラダ	https://recipe.rakuten.co.jp/category/18-417/
        18-418	コールスロー	https://recipe.rakuten.co.jp/category/18-418/
        18-419	かぼちゃサラダ	https://recipe.rakuten.co.jp/category/18-419/
        18-420	ごぼうサラダ	https://recipe.rakuten.co.jp/category/18-420/
        18-421	マカロニサラダ	https://recipe.rakuten.co.jp/category/18-421/
        18-187	シーザーサラダ	https://recipe.rakuten.co.jp/category/18-187/
        18-423	コブサラダ	https://recipe.rakuten.co.jp/category/18-423/
        18-424	タラモサラダ	https://recipe.rakuten.co.jp/category/18-424/
        18-189	スパゲティサラダ	https://recipe.rakuten.co.jp/category/18-189/
        18-190	ホットサラダ・温野菜	https://recipe.rakuten.co.jp/category/18-190/
        18-703	ジャーサラダ	https://recipe.rakuten.co.jp/category/18-703/
        18-184	素材で選ぶサラダ	https://recipe.rakuten.co.jp/category/18-184/
        18-188	味付けで選ぶサラダ	https://recipe.rakuten.co.jp/category/18-188/
        18-185	マヨネーズを使ったサラダ	https://recipe.rakuten.co.jp/category/18-185/
        18-186	ナンプラーを使ったサラダ	https://recipe.rakuten.co.jp/category/18-186/
        18-191	その他のサラダ	https://recipe.rakuten.co.jp/category/18-191/
        19-192	ソース	https://recipe.rakuten.co.jp/category/19-192/
        19-193	タレ	https://recipe.rakuten.co.jp/category/19-193/
        19-194	つゆ	https://recipe.rakuten.co.jp/category/19-194/
        19-195	だし	https://recipe.rakuten.co.jp/category/19-195/
        19-196	ドレッシング	https://recipe.rakuten.co.jp/category/19-196/
        19-675	発酵食品・発酵調味料	https://recipe.rakuten.co.jp/category/19-675/
        19-273	その他調味料	https://recipe.rakuten.co.jp/category/19-273/
        19-274	スパイス＆ハーブ	https://recipe.rakuten.co.jp/category/19-274/
        19-463	柚子胡椒	https://recipe.rakuten.co.jp/category/19-463/
        19-464	オリーブオイル	https://recipe.rakuten.co.jp/category/19-464/
        19-700	ココナッツオイル	https://recipe.rakuten.co.jp/category/19-700/
        20-485	キャラ弁	https://recipe.rakuten.co.jp/category/20-485/
        20-197	お弁当のおかず	https://recipe.rakuten.co.jp/category/20-197/
        20-486	運動会のお弁当	https://recipe.rakuten.co.jp/category/20-486/
        20-487	お花見のお弁当	https://recipe.rakuten.co.jp/category/20-487/
        20-488	遠足・ピクニックのお弁当	https://recipe.rakuten.co.jp/category/20-488/
        20-198	色別おかず	https://recipe.rakuten.co.jp/category/20-198/
        20-199	作り置き・冷凍できるおかず	https://recipe.rakuten.co.jp/category/20-199/
        20-200	すきまおかず	https://recipe.rakuten.co.jp/category/20-200/
        20-201	使い回しおかず	https://recipe.rakuten.co.jp/category/20-201/
        20-202	子供のお弁当	https://recipe.rakuten.co.jp/category/20-202/
        20-203	大人のお弁当	https://recipe.rakuten.co.jp/category/20-203/
        20-258	部活のお弁当	https://recipe.rakuten.co.jp/category/20-258/
        21-204	クッキー	https://recipe.rakuten.co.jp/category/21-204/
        21-440	スイートポテト	https://recipe.rakuten.co.jp/category/21-440/
        21-205	チーズケーキ	https://recipe.rakuten.co.jp/category/21-205/
        21-438	シフォンケーキ	https://recipe.rakuten.co.jp/category/21-438/
        21-439	パウンドケーキ	https://recipe.rakuten.co.jp/category/21-439/
        21-206	ケーキ	https://recipe.rakuten.co.jp/category/21-206/
        21-215	ホットケーキ・パンケーキ	https://recipe.rakuten.co.jp/category/21-215/
        21-207	タルト・パイ	https://recipe.rakuten.co.jp/category/21-207/
        21-208	チョコレート	https://recipe.rakuten.co.jp/category/21-208/
        21-209	スコーン・マフィン	https://recipe.rakuten.co.jp/category/21-209/
        21-210	焼き菓子	https://recipe.rakuten.co.jp/category/21-210/
        21-211	プリン	https://recipe.rakuten.co.jp/category/21-211/
        21-216	ドーナツ	https://recipe.rakuten.co.jp/category/21-216/
        21-212	シュークリーム・エクレア	https://recipe.rakuten.co.jp/category/21-212/
        21-441	ゼリー・寒天・ムース	https://recipe.rakuten.co.jp/category/21-441/
        21-442	アイス・シャーベット	https://recipe.rakuten.co.jp/category/21-442/
        21-214	和菓子	https://recipe.rakuten.co.jp/category/21-214/
        21-217	その他のお菓子	https://recipe.rakuten.co.jp/category/21-217/
        21-218	クリーム・ジャム	https://recipe.rakuten.co.jp/category/21-218/
        22-432	サンドイッチ	https://recipe.rakuten.co.jp/category/22-432/
        22-433	フレンチトースト	https://recipe.rakuten.co.jp/category/22-433/
        22-434	食パン	https://recipe.rakuten.co.jp/category/22-434/
        22-435	蒸しパン	https://recipe.rakuten.co.jp/category/22-435/
        22-436	ホットサンド	https://recipe.rakuten.co.jp/category/22-436/
        22-229	惣菜パン	https://recipe.rakuten.co.jp/category/22-229/
        22-221	菓子パン	https://recipe.rakuten.co.jp/category/22-221/
        22-220	プレーンなパン	https://recipe.rakuten.co.jp/category/22-220/
        22-222	クロワッサン・デニッシュ	https://recipe.rakuten.co.jp/category/22-222/
        22-219	ハードブレッド	https://recipe.rakuten.co.jp/category/22-219/
        22-223	天然酵母パン	https://recipe.rakuten.co.jp/category/22-223/
        22-227	世界各国のパン	https://recipe.rakuten.co.jp/category/22-227/
        22-231	ヘルシーなパン	https://recipe.rakuten.co.jp/category/22-231/
        22-437	キャラパン	https://recipe.rakuten.co.jp/category/22-437/
        22-230	その他のパン	https://recipe.rakuten.co.jp/category/22-230/
        23-391	おでん	https://recipe.rakuten.co.jp/category/23-391/
        23-392	すき焼き	https://recipe.rakuten.co.jp/category/23-392/
        23-393	もつ鍋	https://recipe.rakuten.co.jp/category/23-393/
        23-394	しゃぶしゃぶ	https://recipe.rakuten.co.jp/category/23-394/
        23-395	キムチ鍋	https://recipe.rakuten.co.jp/category/23-395/
        23-396	湯豆腐	https://recipe.rakuten.co.jp/category/23-396/
        23-397	豆乳鍋	https://recipe.rakuten.co.jp/category/23-397/
        23-398	ちゃんこ鍋	https://recipe.rakuten.co.jp/category/23-398/
        23-399	寄せ鍋	https://recipe.rakuten.co.jp/category/23-399/
        23-400	水炊き	https://recipe.rakuten.co.jp/category/23-400/
        23-401	トマト鍋	https://recipe.rakuten.co.jp/category/23-401/
        23-402	あんこう鍋	https://recipe.rakuten.co.jp/category/23-402/
        23-403	石狩鍋	https://recipe.rakuten.co.jp/category/23-403/
        23-404	カレー鍋	https://recipe.rakuten.co.jp/category/23-404/
        23-405	きりたんぽ鍋	https://recipe.rakuten.co.jp/category/23-405/
        23-406	韓国鍋・チゲ鍋	https://recipe.rakuten.co.jp/category/23-406/
        23-407	雪見鍋（みぞれ鍋）	https://recipe.rakuten.co.jp/category/23-407/
        23-408	蒸し鍋	https://recipe.rakuten.co.jp/category/23-408/
        23-409	ねぎま鍋	https://recipe.rakuten.co.jp/category/23-409/
        23-410	鴨鍋	https://recipe.rakuten.co.jp/category/23-410/
        23-411	カニ鍋	https://recipe.rakuten.co.jp/category/23-411/
        23-412	火鍋	https://recipe.rakuten.co.jp/category/23-412/
        23-413	牡蠣鍋	https://recipe.rakuten.co.jp/category/23-413/
        23-698	白味噌鍋	https://recipe.rakuten.co.jp/category/23-698/
        23-234	その他の鍋	https://recipe.rakuten.co.jp/category/23-234/
        24-631	お食い初め料理	https://recipe.rakuten.co.jp/category/24-631/
        24-632	誕生日の料理	https://recipe.rakuten.co.jp/category/24-632/
        24-633	結婚記念日	https://recipe.rakuten.co.jp/category/24-633/
        24-634	パーティー料理・ホームパーティ	https://recipe.rakuten.co.jp/category/24-634/
        24-635	子どものパーティ	https://recipe.rakuten.co.jp/category/24-635/
        24-238	バーベキュー	https://recipe.rakuten.co.jp/category/24-238/
        24-244	その他イベント	https://recipe.rakuten.co.jp/category/24-244/
        25-256	スペイン料理	https://recipe.rakuten.co.jp/category/25-256/
        25-701	イギリス料理	https://recipe.rakuten.co.jp/category/25-701/
        25-248	ロシア料理	https://recipe.rakuten.co.jp/category/25-248/
        25-255	ドイツ料理	https://recipe.rakuten.co.jp/category/25-255/
        25-257	トルコ料理	https://recipe.rakuten.co.jp/category/25-257/
        26-262	おもてなし料理	https://recipe.rakuten.co.jp/category/26-262/
        26-260	おつまみ	https://recipe.rakuten.co.jp/category/26-260/
        26-261	限られた食材・調理器具で工夫	https://recipe.rakuten.co.jp/category/26-261/
        26-265	料理のちょいテク・裏技	https://recipe.rakuten.co.jp/category/26-265/
        27-266	コーヒー	https://recipe.rakuten.co.jp/category/27-266/
        27-267	お茶	https://recipe.rakuten.co.jp/category/27-267/
        27-268	ソフトドリンク	https://recipe.rakuten.co.jp/category/27-268/
        27-465	ジュース・スムージー	https://recipe.rakuten.co.jp/category/27-465/
        27-269	お酒	https://recipe.rakuten.co.jp/category/27-269/
        30-300	ハンバーグ	https://recipe.rakuten.co.jp/category/30-300/
        30-301	餃子	https://recipe.rakuten.co.jp/category/30-301/
        30-302	肉じゃが	https://recipe.rakuten.co.jp/category/30-302/
        30-307	カレー	https://recipe.rakuten.co.jp/category/30-307/
        30-303	牛丼	https://recipe.rakuten.co.jp/category/30-303/
        30-304	親子丼	https://recipe.rakuten.co.jp/category/30-304/
        30-305	豚の生姜焼き	https://recipe.rakuten.co.jp/category/30-305/
        30-306	グラタン	https://recipe.rakuten.co.jp/category/30-306/
        30-309	唐揚げ	https://recipe.rakuten.co.jp/category/30-309/
        30-310	コロッケ	https://recipe.rakuten.co.jp/category/30-310/
        30-308	シチュー	https://recipe.rakuten.co.jp/category/30-308/
        30-311	煮物	https://recipe.rakuten.co.jp/category/30-311/
        30-312	野菜炒め	https://recipe.rakuten.co.jp/category/30-312/
        30-313	天ぷら	https://recipe.rakuten.co.jp/category/30-313/
        30-314	揚げ物	https://recipe.rakuten.co.jp/category/30-314/
        30-315	豆腐料理	https://recipe.rakuten.co.jp/category/30-315/
        30-316	和え物	https://recipe.rakuten.co.jp/category/30-316/
        30-317	酢の物	https://recipe.rakuten.co.jp/category/30-317/
        31-318	ローストビーフ	https://recipe.rakuten.co.jp/category/31-318/
        31-319	豚の角煮	https://recipe.rakuten.co.jp/category/31-319/
        31-320	チキン南蛮	https://recipe.rakuten.co.jp/category/31-320/
        31-321	ピーマンの肉詰め	https://recipe.rakuten.co.jp/category/31-321/
        31-323	ロールキャベツ	https://recipe.rakuten.co.jp/category/31-323/
        31-324	スペアリブ	https://recipe.rakuten.co.jp/category/31-324/
        31-325	ローストチキン	https://recipe.rakuten.co.jp/category/31-325/
        31-326	もつ煮込み	https://recipe.rakuten.co.jp/category/31-326/
        31-327	ミートボール・肉団子	https://recipe.rakuten.co.jp/category/31-327/
        31-328	ミートローフ	https://recipe.rakuten.co.jp/category/31-328/
        31-329	牛すじ煮込み	https://recipe.rakuten.co.jp/category/31-329/
        31-330	とんかつ	https://recipe.rakuten.co.jp/category/31-330/
        31-331	ポークソテー	https://recipe.rakuten.co.jp/category/31-331/
        31-332	つくね	https://recipe.rakuten.co.jp/category/31-332/
        31-333	チャーシュー（焼き豚）	https://recipe.rakuten.co.jp/category/31-333/
        31-334	煮豚	https://recipe.rakuten.co.jp/category/31-334/
        31-322	ステーキ	https://recipe.rakuten.co.jp/category/31-322/
        31-335	鶏肉料理	https://recipe.rakuten.co.jp/category/31-335/
        32-336	ぶり大根	https://recipe.rakuten.co.jp/category/32-336/
        32-337	ぶりの照り焼き	https://recipe.rakuten.co.jp/category/32-337/
        32-338	さばの味噌煮	https://recipe.rakuten.co.jp/category/32-338/
        32-339	煮魚	https://recipe.rakuten.co.jp/category/32-339/
        32-340	あさりの酒蒸し	https://recipe.rakuten.co.jp/category/32-340/
        32-341	鮭のムニエル	https://recipe.rakuten.co.jp/category/32-341/
        32-342	南蛮漬け	https://recipe.rakuten.co.jp/category/32-342/
        32-343	焼き魚	https://recipe.rakuten.co.jp/category/32-343/
        32-344	鮭のホイル焼き	https://recipe.rakuten.co.jp/category/32-344/
        32-345	いわしのつみれ	https://recipe.rakuten.co.jp/category/32-345/
        32-346	かつおのたたき	https://recipe.rakuten.co.jp/category/32-346/
        32-347	いわしの梅煮	https://recipe.rakuten.co.jp/category/32-347/
        32-348	かぶら蒸し	https://recipe.rakuten.co.jp/category/32-348/
        32-349	その他の魚料理	https://recipe.rakuten.co.jp/category/32-349/
        33-350	ゆで卵	https://recipe.rakuten.co.jp/category/33-350/
        33-351	温泉卵	https://recipe.rakuten.co.jp/category/33-351/
        33-352	半熟卵	https://recipe.rakuten.co.jp/category/33-352/
        33-353	だし巻き卵・卵焼き	https://recipe.rakuten.co.jp/category/33-353/
        33-354	茶碗蒸し	https://recipe.rakuten.co.jp/category/33-354/
        33-355	キッシュ	https://recipe.rakuten.co.jp/category/33-355/
        33-356	オムレツ	https://recipe.rakuten.co.jp/category/33-356/
        33-357	かに玉	https://recipe.rakuten.co.jp/category/33-357/
        33-358	スクランブルエッグ	https://recipe.rakuten.co.jp/category/33-358/
        33-359	煮卵	https://recipe.rakuten.co.jp/category/33-359/
        33-360	目玉焼き	https://recipe.rakuten.co.jp/category/33-360/
        33-361	ニラ玉	https://recipe.rakuten.co.jp/category/33-361/
        33-362	ポーチドエッグ	https://recipe.rakuten.co.jp/category/33-362/
        33-363	スコッチエッグ	https://recipe.rakuten.co.jp/category/33-363/
        33-364	卵とじ	https://recipe.rakuten.co.jp/category/33-364/
        33-365	薄焼き卵	https://recipe.rakuten.co.jp/category/33-365/
        33-366	炒り卵	https://recipe.rakuten.co.jp/category/33-366/
        33-367	その他の卵料理	https://recipe.rakuten.co.jp/category/33-367/
        34-688	りんご	https://recipe.rakuten.co.jp/category/34-688/
        34-459	ゆず	https://recipe.rakuten.co.jp/category/34-459/
        34-460	柿	https://recipe.rakuten.co.jp/category/34-460/
        34-461	レモン	https://recipe.rakuten.co.jp/category/34-461/
        34-697	バナナ	https://recipe.rakuten.co.jp/category/34-697/
        34-462	ブルーベリー	https://recipe.rakuten.co.jp/category/34-462/
        34-690	グレープフルーツ	https://recipe.rakuten.co.jp/category/34-690/
        34-691	キウイ	https://recipe.rakuten.co.jp/category/34-691/
        34-702	オレンジ	https://recipe.rakuten.co.jp/category/34-702/
        34-692	春の果物	https://recipe.rakuten.co.jp/category/34-692/
        34-693	夏の果物	https://recipe.rakuten.co.jp/category/34-693/
        34-689	秋の果物	https://recipe.rakuten.co.jp/category/34-689/
        34-695	冬の果物	https://recipe.rakuten.co.jp/category/34-695/
        34-696	その他の果物	https://recipe.rakuten.co.jp/category/34-696/
        35-466	おから	https://recipe.rakuten.co.jp/category/35-466/
        35-467	厚揚げ	https://recipe.rakuten.co.jp/category/35-467/
        35-468	納豆	https://recipe.rakuten.co.jp/category/35-468/
        35-469	高野豆腐	https://recipe.rakuten.co.jp/category/35-469/
        35-470	豆乳	https://recipe.rakuten.co.jp/category/35-470/
        35-471	木綿豆腐	https://recipe.rakuten.co.jp/category/35-471/
        35-472	絹ごし豆腐	https://recipe.rakuten.co.jp/category/35-472/
        35-473	油揚げ	https://recipe.rakuten.co.jp/category/35-473/
        35-474	大豆ミート	https://recipe.rakuten.co.jp/category/35-474/
        35-475	塩豆腐	https://recipe.rakuten.co.jp/category/35-475/
        35-476	その他の大豆・豆腐	https://recipe.rakuten.co.jp/category/35-476/
        35-477	豆類	https://recipe.rakuten.co.jp/category/35-477/
        36-489	簡単お菓子	https://recipe.rakuten.co.jp/category/36-489/
        36-490	簡単夕食	https://recipe.rakuten.co.jp/category/36-490/
        36-491	簡単おつまみ	https://recipe.rakuten.co.jp/category/36-491/
        36-492	簡単おもてなし料理	https://recipe.rakuten.co.jp/category/36-492/
        36-493	簡単鶏肉料理	https://recipe.rakuten.co.jp/category/36-493/
        36-494	簡単豚肉料理	https://recipe.rakuten.co.jp/category/36-494/
        36-495	簡単魚料理	https://recipe.rakuten.co.jp/category/36-495/
        36-496	5分以内の簡単料理	https://recipe.rakuten.co.jp/category/36-496/
        36-497	男の簡単料理	https://recipe.rakuten.co.jp/category/36-497/
        37-498	100円以下の節約料理	https://recipe.rakuten.co.jp/category/37-498/
        37-499	300円前後の節約料理	https://recipe.rakuten.co.jp/category/37-499/
        37-500	500円前後の節約料理	https://recipe.rakuten.co.jp/category/37-500/
        38-501	朝食の献立（朝ごはん）	https://recipe.rakuten.co.jp/category/38-501/
        38-502	昼食の献立（昼ごはん）	https://recipe.rakuten.co.jp/category/38-502/
        38-503	夕食の献立（晩御飯）	https://recipe.rakuten.co.jp/category/38-503/
        39-504	低カロリー・ダイエット	https://recipe.rakuten.co.jp/category/39-504/
        39-505	ヘルシー料理	https://recipe.rakuten.co.jp/category/39-505/
        39-705	高血圧向け	https://recipe.rakuten.co.jp/category/39-705/
        39-699	糖質制限	https://recipe.rakuten.co.jp/category/39-699/
        39-506	マクロビオティック	https://recipe.rakuten.co.jp/category/39-506/
        39-507	ベジタリアン	https://recipe.rakuten.co.jp/category/39-507/
        39-508	疲労回復	https://recipe.rakuten.co.jp/category/39-508/
        39-509	妊娠中の食事	https://recipe.rakuten.co.jp/category/39-509/
        39-510	離乳食	https://recipe.rakuten.co.jp/category/39-510/
        39-511	幼児食	https://recipe.rakuten.co.jp/category/39-511/
        40-512	圧力鍋	https://recipe.rakuten.co.jp/category/40-512/
        40-513	ホームベーカリー	https://recipe.rakuten.co.jp/category/40-513/
        40-514	シリコンスチーマー	https://recipe.rakuten.co.jp/category/40-514/
        40-707	キッチンバサミ	https://recipe.rakuten.co.jp/category/40-707/
        40-515	タジン鍋	https://recipe.rakuten.co.jp/category/40-515/
        40-516	炊飯器	https://recipe.rakuten.co.jp/category/40-516/
        40-704	メイソンジャー	https://recipe.rakuten.co.jp/category/40-704/
        40-517	スープジャー	https://recipe.rakuten.co.jp/category/40-517/
        40-518	ホットプレート	https://recipe.rakuten.co.jp/category/40-518/
        40-519	電子レンジ	https://recipe.rakuten.co.jp/category/40-519/
        40-520	無水鍋	https://recipe.rakuten.co.jp/category/40-520/
        40-521	ホーロー鍋	https://recipe.rakuten.co.jp/category/40-521/
        40-522	ミキサー	https://recipe.rakuten.co.jp/category/40-522/
        40-523	中華鍋	https://recipe.rakuten.co.jp/category/40-523/
        40-524	フライパン一つでできる	https://recipe.rakuten.co.jp/category/40-524/
        40-525	メーカー・ブランド	https://recipe.rakuten.co.jp/category/40-525/
        40-526	その他の調理器具	https://recipe.rakuten.co.jp/category/40-526/
        41-531	酢豚	https://recipe.rakuten.co.jp/category/41-531/
        41-532	チンジャオロース	https://recipe.rakuten.co.jp/category/41-532/
        41-533	八宝菜	https://recipe.rakuten.co.jp/category/41-533/
        41-534	マーボー豆腐（麻婆豆腐）	https://recipe.rakuten.co.jp/category/41-534/
        41-535	エビチリ	https://recipe.rakuten.co.jp/category/41-535/
        41-536	エビマヨ	https://recipe.rakuten.co.jp/category/41-536/
        41-537	ホイコーロー（回鍋肉）	https://recipe.rakuten.co.jp/category/41-537/
        41-539	バンバンジー	https://recipe.rakuten.co.jp/category/41-539/
        41-542	油淋鶏	https://recipe.rakuten.co.jp/category/41-542/
        41-543	ビーフン	https://recipe.rakuten.co.jp/category/41-543/
        41-538	ジャージャー麺	https://recipe.rakuten.co.jp/category/41-538/
        41-541	坦々麺	https://recipe.rakuten.co.jp/category/41-541/
        41-546	春巻き	https://recipe.rakuten.co.jp/category/41-546/
        41-547	肉まん	https://recipe.rakuten.co.jp/category/41-547/
        41-548	焼売（シュウマイ）	https://recipe.rakuten.co.jp/category/41-548/
        41-540	杏仁豆腐	https://recipe.rakuten.co.jp/category/41-540/
        41-544	ちまき（中華ちまき）	https://recipe.rakuten.co.jp/category/41-544/
        41-545	サンラータン（酸辣湯）	https://recipe.rakuten.co.jp/category/41-545/
        41-549	その他の中華料理	https://recipe.rakuten.co.jp/category/41-549/
        42-550	チャプチェ	https://recipe.rakuten.co.jp/category/42-550/
        42-551	チヂミ	https://recipe.rakuten.co.jp/category/42-551/
        42-552	ビビンバ	https://recipe.rakuten.co.jp/category/42-552/
        42-553	ナムル	https://recipe.rakuten.co.jp/category/42-553/
        42-554	キムチ	https://recipe.rakuten.co.jp/category/42-554/
        42-555	プルコギ	https://recipe.rakuten.co.jp/category/42-555/
        42-565	スンドゥブ	https://recipe.rakuten.co.jp/category/42-565/
        42-556	チョレギサラダ	https://recipe.rakuten.co.jp/category/42-556/
        42-557	冷麺	https://recipe.rakuten.co.jp/category/42-557/
        42-558	サムゲタン	https://recipe.rakuten.co.jp/category/42-558/
        42-559	サムギョプサル	https://recipe.rakuten.co.jp/category/42-559/
        42-560	クッパ	https://recipe.rakuten.co.jp/category/42-560/
        42-561	タッカルビ	https://recipe.rakuten.co.jp/category/42-561/
        42-562	カムジャタン	https://recipe.rakuten.co.jp/category/42-562/
        42-563	トッポギ	https://recipe.rakuten.co.jp/category/42-563/
        42-564	ケジャン	https://recipe.rakuten.co.jp/category/42-564/
        42-566	テンジャンチゲ	https://recipe.rakuten.co.jp/category/42-566/
        42-567	その他のチゲ	https://recipe.rakuten.co.jp/category/42-567/
        42-568	その他の韓国料理	https://recipe.rakuten.co.jp/category/42-568/
        43-569	ピザ	https://recipe.rakuten.co.jp/category/43-569/
        43-570	ミネストローネ	https://recipe.rakuten.co.jp/category/43-570/
        43-578	リゾット	https://recipe.rakuten.co.jp/category/43-578/
        43-571	バーニャカウダ	https://recipe.rakuten.co.jp/category/43-571/
        43-577	カルパッチョ	https://recipe.rakuten.co.jp/category/43-577/
        43-572	アクアパッツァ	https://recipe.rakuten.co.jp/category/43-572/
        43-573	ピカタ	https://recipe.rakuten.co.jp/category/43-573/
        43-574	ブルスケッタ	https://recipe.rakuten.co.jp/category/43-574/
        43-575	パニーノ・パニーニ	https://recipe.rakuten.co.jp/category/43-575/
        43-576	カルツォーネ	https://recipe.rakuten.co.jp/category/43-576/
        43-579	カプレーゼ	https://recipe.rakuten.co.jp/category/43-579/
        43-580	パンナコッタ	https://recipe.rakuten.co.jp/category/43-580/
        43-581	ティラミス	https://recipe.rakuten.co.jp/category/43-581/
        43-582	その他のイタリア料理	https://recipe.rakuten.co.jp/category/43-582/
        44-583	ラタトゥイユ	https://recipe.rakuten.co.jp/category/44-583/
        44-584	チーズフォンデュ	https://recipe.rakuten.co.jp/category/44-584/
        44-585	テリーヌ	https://recipe.rakuten.co.jp/category/44-585/
        44-586	ブイヤベース	https://recipe.rakuten.co.jp/category/44-586/
        44-587	ムニエル	https://recipe.rakuten.co.jp/category/44-587/
        44-588	ビスク	https://recipe.rakuten.co.jp/category/44-588/
        44-589	マリネ	https://recipe.rakuten.co.jp/category/44-589/
        44-590	ガレット	https://recipe.rakuten.co.jp/category/44-590/
        44-591	その他のフランス料理	https://recipe.rakuten.co.jp/category/44-591/
        46-596	タイ料理	https://recipe.rakuten.co.jp/category/46-596/
        46-597	インド料理	https://recipe.rakuten.co.jp/category/46-597/
        46-598	ベトナム料理	https://recipe.rakuten.co.jp/category/46-598/
        46-599	メキシコ料理	https://recipe.rakuten.co.jp/category/46-599/
        47-602	ゴーヤチャンプル	https://recipe.rakuten.co.jp/category/47-602/
        47-600	ソーキそば・沖縄そば	https://recipe.rakuten.co.jp/category/47-600/
        47-601	海ぶどう	https://recipe.rakuten.co.jp/category/47-601/
        47-603	そうめんチャンプルー	https://recipe.rakuten.co.jp/category/47-603/
        47-604	ラフテー	https://recipe.rakuten.co.jp/category/47-604/
        47-605	ミミガー	https://recipe.rakuten.co.jp/category/47-605/
        47-606	ジューシー	https://recipe.rakuten.co.jp/category/47-606/
        47-607	サーターアンダーギー	https://recipe.rakuten.co.jp/category/47-607/
        47-608	ヒラヤーチー	https://recipe.rakuten.co.jp/category/47-608/
        47-609	コーレーグス・島唐辛子	https://recipe.rakuten.co.jp/category/47-609/
        47-610	その他の沖縄料理	https://recipe.rakuten.co.jp/category/47-610/
        48-612	ちゃんちゃん焼き	https://recipe.rakuten.co.jp/category/48-612/
        48-613	筑前煮	https://recipe.rakuten.co.jp/category/48-613/
        48-611	ジンギスカン	https://recipe.rakuten.co.jp/category/48-611/
        48-614	すいとん	https://recipe.rakuten.co.jp/category/48-614/
        48-615	ほうとう	https://recipe.rakuten.co.jp/category/48-615/
        48-616	ひつまぶし	https://recipe.rakuten.co.jp/category/48-616/
        48-617	ちゃんぽん	https://recipe.rakuten.co.jp/category/48-617/
        48-618	明石焼き	https://recipe.rakuten.co.jp/category/48-618/
        48-619	いかめし	https://recipe.rakuten.co.jp/category/48-619/
        48-620	せんべい汁	https://recipe.rakuten.co.jp/category/48-620/
        48-621	皿うどん	https://recipe.rakuten.co.jp/category/48-621/
        48-622	きりたんぽ	https://recipe.rakuten.co.jp/category/48-622/
        48-623	のっぺい汁	https://recipe.rakuten.co.jp/category/48-623/
        48-624	治部煮	https://recipe.rakuten.co.jp/category/48-624/
        48-625	いちご煮	https://recipe.rakuten.co.jp/category/48-625/
        48-626	三升漬け	https://recipe.rakuten.co.jp/category/48-626/
        48-627	三平汁	https://recipe.rakuten.co.jp/category/48-627/
        48-628	じゃっぱ汁	https://recipe.rakuten.co.jp/category/48-628/
        48-629	辛子蓮根	https://recipe.rakuten.co.jp/category/48-629/
        48-630	その他の郷土料理	https://recipe.rakuten.co.jp/category/48-630/
        49-636	きんとん（栗きんとん）	https://recipe.rakuten.co.jp/category/49-636/
        49-637	お雑煮	https://recipe.rakuten.co.jp/category/49-637/
        49-638	錦玉子・伊達巻	https://recipe.rakuten.co.jp/category/49-638/
        49-639	なます	https://recipe.rakuten.co.jp/category/49-639/
        49-640	黒豆	https://recipe.rakuten.co.jp/category/49-640/
        49-641	数の子	https://recipe.rakuten.co.jp/category/49-641/
        49-642	田作り	https://recipe.rakuten.co.jp/category/49-642/
        49-643	煮しめ	https://recipe.rakuten.co.jp/category/49-643/
        49-644	たたきごぼう	https://recipe.rakuten.co.jp/category/49-644/
        49-645	昆布巻き	https://recipe.rakuten.co.jp/category/49-645/
        49-646	酢れんこん	https://recipe.rakuten.co.jp/category/49-646/
        49-648	おせちの海老料理	https://recipe.rakuten.co.jp/category/49-648/
        49-649	八幡巻き	https://recipe.rakuten.co.jp/category/49-649/
        49-650	簡単おせち料理	https://recipe.rakuten.co.jp/category/49-650/
        49-651	その他のおせち料理	https://recipe.rakuten.co.jp/category/49-651/
        50-652	クリスマスケーキ	https://recipe.rakuten.co.jp/category/50-652/
        50-653	クリスマスオードブル	https://recipe.rakuten.co.jp/category/50-653/
        50-654	クリスマスチキン	https://recipe.rakuten.co.jp/category/50-654/
        50-655	クリスマスサラダ	https://recipe.rakuten.co.jp/category/50-655/
        50-656	クリスマス向けアレンジ	https://recipe.rakuten.co.jp/category/50-656/
        51-657	ひな祭りケーキ	https://recipe.rakuten.co.jp/category/51-657/
        51-658	ひな祭りちらしずし	https://recipe.rakuten.co.jp/category/51-658/
        51-659	ひな祭り向けアレンジ	https://recipe.rakuten.co.jp/category/51-659/
        52-660	ホワイトデー	https://recipe.rakuten.co.jp/category/52-660/
        52-661	お花見・春の行楽	https://recipe.rakuten.co.jp/category/52-661/
        52-662	子供の日	https://recipe.rakuten.co.jp/category/52-662/
        52-663	母の日	https://recipe.rakuten.co.jp/category/52-663/
        53-664	父の日	https://recipe.rakuten.co.jp/category/53-664/
        53-665	夏バテ対策	https://recipe.rakuten.co.jp/category/53-665/
        53-666	お祭り	https://recipe.rakuten.co.jp/category/53-666/
        53-667	十五夜・お月見	https://recipe.rakuten.co.jp/category/53-667/
        54-668	ハロウィン	https://recipe.rakuten.co.jp/category/54-668/
        54-669	秋の行楽・紅葉	https://recipe.rakuten.co.jp/category/54-669/
        54-670	七五三の料理	https://recipe.rakuten.co.jp/category/54-670/
        55-671	節分	https://recipe.rakuten.co.jp/category/55-671/
        55-672	恵方巻き	https://recipe.rakuten.co.jp/category/55-672/
        55-673	ななくさ粥（七草粥）	https://recipe.rakuten.co.jp/category/55-673/
        55-674	バレンタイン	https://recipe.rakuten.co.jp/category/55-674/
        10-66-50	ソーセージ・ウインナー	https://recipe.rakuten.co.jp/category/10-66-50/
        10-67-1491	生ハム	https://recipe.rakuten.co.jp/category/10-67-1491/
        10-67-1492	鶏ハム	https://recipe.rakuten.co.jp/category/10-67-1492/
        10-67-321	その他のハム	https://recipe.rakuten.co.jp/category/10-67-321/
        10-68-49	ベーコン	https://recipe.rakuten.co.jp/category/10-68-49/
        10-69-45	ラムチョップ・ラム肉	https://recipe.rakuten.co.jp/category/10-69-45/
        10-69-46	ホルモン・レバー	https://recipe.rakuten.co.jp/category/10-69-46/
        10-69-51	ランチョンミート・スパム	https://recipe.rakuten.co.jp/category/10-69-51/
        10-69-457	ジビエ	https://recipe.rakuten.co.jp/category/10-69-457/
        10-69-461	馬肉	https://recipe.rakuten.co.jp/category/10-69-461/
        10-69-458	鴨肉	https://recipe.rakuten.co.jp/category/10-69-458/
        10-69-460	猪肉	https://recipe.rakuten.co.jp/category/10-69-460/
        10-69-462	フォアグラ	https://recipe.rakuten.co.jp/category/10-69-462/
        10-69-1493	七面鳥	https://recipe.rakuten.co.jp/category/10-69-1493/
        10-69-52	その他のお肉加工品	https://recipe.rakuten.co.jp/category/10-69-52/
        10-69-47	その他のお肉	https://recipe.rakuten.co.jp/category/10-69-47/
        11-70-55	鮭	https://recipe.rakuten.co.jp/category/11-70-55/
        11-70-839	サーモン	https://recipe.rakuten.co.jp/category/11-70-839/
        11-70-1494	スモークサーモン	https://recipe.rakuten.co.jp/category/11-70-1494/
        11-70-1495	鮭フレーク	https://recipe.rakuten.co.jp/category/11-70-1495/
        11-71-54	いわし	https://recipe.rakuten.co.jp/category/11-71-54/
        11-72-322	さば	https://recipe.rakuten.co.jp/category/11-72-322/
        11-73-58	あじ	https://recipe.rakuten.co.jp/category/11-73-58/
        11-74-57	ぶり	https://recipe.rakuten.co.jp/category/11-74-57/
        11-75-56	さんま	https://recipe.rakuten.co.jp/category/11-75-56/
        11-76-325	鯛	https://recipe.rakuten.co.jp/category/11-76-325/
        11-77-53	マグロ	https://recipe.rakuten.co.jp/category/11-77-53/
        11-78-522	カジキマグロ（めかじき）	https://recipe.rakuten.co.jp/category/11-78-522/
        11-78-465	さわら	https://recipe.rakuten.co.jp/category/11-78-465/
        11-78-469	しらす	https://recipe.rakuten.co.jp/category/11-78-469/
        11-78-324	かつお	https://recipe.rakuten.co.jp/category/11-78-324/
        11-78-471	ししゃも	https://recipe.rakuten.co.jp/category/11-78-471/
        11-78-334	うなぎ	https://recipe.rakuten.co.jp/category/11-78-334/
        11-78-1497	にしん	https://recipe.rakuten.co.jp/category/11-78-1497/
        11-78-323	カレイ（カラスカレイ）	https://recipe.rakuten.co.jp/category/11-78-323/
        11-78-523	赤魚	https://recipe.rakuten.co.jp/category/11-78-523/
        11-78-328	金目鯛	https://recipe.rakuten.co.jp/category/11-78-328/
        11-78-1498	甘鯛	https://recipe.rakuten.co.jp/category/11-78-1498/
        11-78-472	穴子	https://recipe.rakuten.co.jp/category/11-78-472/
        11-78-1499	ヒラメ	https://recipe.rakuten.co.jp/category/11-78-1499/
        11-78-841	メバル	https://recipe.rakuten.co.jp/category/11-78-841/
        11-78-327	ワカサギ	https://recipe.rakuten.co.jp/category/11-78-327/
        11-78-468	ほっけ	https://recipe.rakuten.co.jp/category/11-78-468/
        11-78-840	きす	https://recipe.rakuten.co.jp/category/11-78-840/
        11-78-466	あんこう	https://recipe.rakuten.co.jp/category/11-78-466/
        11-78-1500	カサゴ	https://recipe.rakuten.co.jp/category/11-78-1500/
        11-78-1501	鱧	https://recipe.rakuten.co.jp/category/11-78-1501/
        11-78-1502	その他のさかな	https://recipe.rakuten.co.jp/category/11-78-1502/
        11-79-1503	むきえび	https://recipe.rakuten.co.jp/category/11-79-1503/
        11-79-1504	桜えび	https://recipe.rakuten.co.jp/category/11-79-1504/
        11-79-1505	甘エビ	https://recipe.rakuten.co.jp/category/11-79-1505/
        11-79-1506	小エビ	https://recipe.rakuten.co.jp/category/11-79-1506/
        11-79-1507	干しエビ	https://recipe.rakuten.co.jp/category/11-79-1507/
        11-79-65	その他のエビ	https://recipe.rakuten.co.jp/category/11-79-65/
        11-80-68	いか	https://recipe.rakuten.co.jp/category/11-80-68/
        11-81-67	たこ	https://recipe.rakuten.co.jp/category/11-81-67/
        11-82-60	あさり	https://recipe.rakuten.co.jp/category/11-82-60/
        11-82-61	ホタテ	https://recipe.rakuten.co.jp/category/11-82-61/
        11-82-63	はまぐり	https://recipe.rakuten.co.jp/category/11-82-63/
        11-82-477	ムール貝	https://recipe.rakuten.co.jp/category/11-82-477/
        11-82-478	しじみ	https://recipe.rakuten.co.jp/category/11-82-478/
        11-82-330	サザエ	https://recipe.rakuten.co.jp/category/11-82-330/
        11-82-329	あわび	https://recipe.rakuten.co.jp/category/11-82-329/
        11-82-475	つぶ貝	https://recipe.rakuten.co.jp/category/11-82-475/
        11-82-476	ホッキ貝	https://recipe.rakuten.co.jp/category/11-82-476/
        11-82-64	その他の貝	https://recipe.rakuten.co.jp/category/11-82-64/
        11-83-66	かに	https://recipe.rakuten.co.jp/category/11-83-66/
        12-95-13	にんじん	https://recipe.rakuten.co.jp/category/12-95-13/
        12-96-7	玉ねぎ	https://recipe.rakuten.co.jp/category/12-96-7/
        12-97-17	じゃがいも	https://recipe.rakuten.co.jp/category/12-97-17/
        12-98-1	キャベツ	https://recipe.rakuten.co.jp/category/12-98-1/
        12-99-318	もやし	https://recipe.rakuten.co.jp/category/12-99-318/
        12-100-10	たけのこ	https://recipe.rakuten.co.jp/category/12-100-10/
        12-100-2	レタス	https://recipe.rakuten.co.jp/category/12-100-2/
        12-100-11	アスパラ	https://recipe.rakuten.co.jp/category/12-100-11/
        12-100-83	ふき	https://recipe.rakuten.co.jp/category/12-100-83/
        12-100-858	新玉ねぎ	https://recipe.rakuten.co.jp/category/12-100-858/
        12-100-445	菜の花	https://recipe.rakuten.co.jp/category/12-100-445/
        12-100-859	新じゃが	https://recipe.rakuten.co.jp/category/12-100-859/
        12-100-444	とうみょう（豆苗）	https://recipe.rakuten.co.jp/category/12-100-444/
        12-100-23	そら豆	https://recipe.rakuten.co.jp/category/12-100-23/
        12-100-82	うど	https://recipe.rakuten.co.jp/category/12-100-82/
        12-100-845	さやえんどう	https://recipe.rakuten.co.jp/category/12-100-845/
        12-100-21	えんどう豆	https://recipe.rakuten.co.jp/category/12-100-21/
        12-100-81	ぜんまい	https://recipe.rakuten.co.jp/category/12-100-81/
        12-100-1530	たらの芽	https://recipe.rakuten.co.jp/category/12-100-1530/
        12-100-1993	わらび	https://recipe.rakuten.co.jp/category/12-100-1993/
        12-100-317	クレソン	https://recipe.rakuten.co.jp/category/12-100-317/
        12-100-844	グリーンピース	https://recipe.rakuten.co.jp/category/12-100-844/
        12-100-84	よもぎ	https://recipe.rakuten.co.jp/category/12-100-84/
        12-100-846	スナップえんどう	https://recipe.rakuten.co.jp/category/12-100-846/
        12-100-454	せり	https://recipe.rakuten.co.jp/category/12-100-454/
        12-101-31	ゴーヤ	https://recipe.rakuten.co.jp/category/12-101-31/
        12-101-315	ズッキーニ	https://recipe.rakuten.co.jp/category/12-101-315/
        12-101-821	とうがん（冬瓜）	https://recipe.rakuten.co.jp/category/12-101-821/
        12-101-30	ピーマン	https://recipe.rakuten.co.jp/category/12-101-30/
        12-101-32	オクラ	https://recipe.rakuten.co.jp/category/12-101-32/
        12-101-1532	ししとう	https://recipe.rakuten.co.jp/category/12-101-1532/
        12-101-509	モロヘイヤ	https://recipe.rakuten.co.jp/category/12-101-509/
        12-101-22	いんげん	https://recipe.rakuten.co.jp/category/12-101-22/
        12-101-456	パプリカ	https://recipe.rakuten.co.jp/category/12-101-456/
        12-101-511	空芯菜	https://recipe.rakuten.co.jp/category/12-101-511/
        12-101-24	枝豆	https://recipe.rakuten.co.jp/category/12-101-24/
        12-101-422	とうもろこし	https://recipe.rakuten.co.jp/category/12-101-422/
        12-101-28	うり（瓜）	https://recipe.rakuten.co.jp/category/12-101-28/
        12-101-515	ささげ	https://recipe.rakuten.co.jp/category/12-101-515/
        12-101-1533	そうめんかぼちゃ	https://recipe.rakuten.co.jp/category/12-101-1533/
        12-102-15	れんこん	https://recipe.rakuten.co.jp/category/12-102-15/
        12-102-16	かぶ	https://recipe.rakuten.co.jp/category/12-102-16/
        12-102-18	山芋	https://recipe.rakuten.co.jp/category/12-102-18/
        12-102-847	長芋	https://recipe.rakuten.co.jp/category/12-102-847/
        12-102-1534	ぎんなん(銀杏)	https://recipe.rakuten.co.jp/category/12-102-1534/
        12-102-449	春菊	https://recipe.rakuten.co.jp/category/12-102-449/
        12-102-319	チンゲン菜	https://recipe.rakuten.co.jp/category/12-102-319/
        12-102-452	大和芋	https://recipe.rakuten.co.jp/category/12-102-452/
        12-103-308	里芋	https://recipe.rakuten.co.jp/category/12-103-308/
        12-103-3	水菜	https://recipe.rakuten.co.jp/category/12-103-3/
        12-103-4	にら	https://recipe.rakuten.co.jp/category/12-103-4/
        12-103-314	セロリ	https://recipe.rakuten.co.jp/category/12-103-314/
        12-103-34	カリフラワー	https://recipe.rakuten.co.jp/category/12-103-34/
        12-103-8	長ネギ（ねぎ）	https://recipe.rakuten.co.jp/category/12-103-8/
        12-103-442	くわい	https://recipe.rakuten.co.jp/category/12-103-442/
        12-103-514	わさび菜	https://recipe.rakuten.co.jp/category/12-103-514/
        12-103-451	ユリ根	https://recipe.rakuten.co.jp/category/12-103-451/
        12-104-1539	ヤーコン	https://recipe.rakuten.co.jp/category/12-104-1539/
        12-104-1540	にんにくの芽	https://recipe.rakuten.co.jp/category/12-104-1540/
        12-104-1541	芽キャベツ	https://recipe.rakuten.co.jp/category/12-104-1541/
        12-104-1542	高菜	https://recipe.rakuten.co.jp/category/12-104-1542/
        12-104-1543	らっきょう	https://recipe.rakuten.co.jp/category/12-104-1543/
        12-104-1544	ラディッシュ	https://recipe.rakuten.co.jp/category/12-104-1544/
        12-104-1545	むかご	https://recipe.rakuten.co.jp/category/12-104-1545/
        12-104-1546	かいわれ大根	https://recipe.rakuten.co.jp/category/12-104-1546/
        12-104-1547	スプラウト	https://recipe.rakuten.co.jp/category/12-104-1547/
        12-104-1548	エシャロット	https://recipe.rakuten.co.jp/category/12-104-1548/
        12-104-1960	その他の野菜	https://recipe.rakuten.co.jp/category/12-104-1960/
        12-105-75	しいたけ	https://recipe.rakuten.co.jp/category/12-105-75/
        12-105-339	エリンギ	https://recipe.rakuten.co.jp/category/12-105-339/
        12-105-78	えのき	https://recipe.rakuten.co.jp/category/12-105-78/
        12-105-76	しめじ	https://recipe.rakuten.co.jp/category/12-105-76/
        12-105-77	まいたけ	https://recipe.rakuten.co.jp/category/12-105-77/
        12-105-337	松茸	https://recipe.rakuten.co.jp/category/12-105-337/
        12-105-79	なめこ	https://recipe.rakuten.co.jp/category/12-105-79/
        12-105-338	マッシュルーム	https://recipe.rakuten.co.jp/category/12-105-338/
        12-105-80	その他のきのこ	https://recipe.rakuten.co.jp/category/12-105-80/
        12-107-36	みょうが	https://recipe.rakuten.co.jp/category/12-107-36/
        12-107-316	生姜（新生姜）	https://recipe.rakuten.co.jp/category/12-107-316/
        12-107-448	しそ・大葉	https://recipe.rakuten.co.jp/category/12-107-448/
        12-107-9	ガーリック・にんにく	https://recipe.rakuten.co.jp/category/12-107-9/
        12-107-513	とうがらし・葉唐辛子	https://recipe.rakuten.co.jp/category/12-107-513/
        12-107-856	万能ねぎ	https://recipe.rakuten.co.jp/category/12-107-856/
        12-107-450	パセリ	https://recipe.rakuten.co.jp/category/12-107-450/
        12-107-1535	パクチー	https://recipe.rakuten.co.jp/category/12-107-1535/
        12-107-1536	ローズマリー	https://recipe.rakuten.co.jp/category/12-107-1536/
        12-107-1537	バジル	https://recipe.rakuten.co.jp/category/12-107-1537/
        12-107-1538	フェンネル	https://recipe.rakuten.co.jp/category/12-107-1538/
        13-108-490	ちくわ	https://recipe.rakuten.co.jp/category/13-108-490/
        13-108-107	はんぺん	https://recipe.rakuten.co.jp/category/13-108-107/
        13-108-1635	さつま揚げ	https://recipe.rakuten.co.jp/category/13-108-1635/
        13-108-529	がんもどき	https://recipe.rakuten.co.jp/category/13-108-529/
        13-108-528	かまぼこ	https://recipe.rakuten.co.jp/category/13-108-528/
        13-108-530	カニカマ	https://recipe.rakuten.co.jp/category/13-108-530/
        13-108-108	その他の練物	https://recipe.rakuten.co.jp/category/13-108-108/
        13-109-531	缶詰	https://recipe.rakuten.co.jp/category/13-109-531/
        13-109-1636	カニ缶	https://recipe.rakuten.co.jp/category/13-109-1636/
        13-109-1637	さば缶	https://recipe.rakuten.co.jp/category/13-109-1637/
        13-109-1638	トマト缶	https://recipe.rakuten.co.jp/category/13-109-1638/
        13-109-843	ツナ缶	https://recipe.rakuten.co.jp/category/13-109-843/
        13-109-1639	鮭缶	https://recipe.rakuten.co.jp/category/13-109-1639/
        13-109-1640	缶詰アレンジ	https://recipe.rakuten.co.jp/category/13-109-1640/
        13-109-110	インスタントラーメン	https://recipe.rakuten.co.jp/category/13-109-110/
        13-109-111	冷凍食品	https://recipe.rakuten.co.jp/category/13-109-111/
        13-109-109	シリアル	https://recipe.rakuten.co.jp/category/13-109-109/
        13-109-112	レトルト食品	https://recipe.rakuten.co.jp/category/13-109-112/
        13-109-113	その他の加工食品	https://recipe.rakuten.co.jp/category/13-109-113/
        13-111-1648	糸こんにゃく	https://recipe.rakuten.co.jp/category/13-111-1648/
        13-111-1649	玉こんにゃく	https://recipe.rakuten.co.jp/category/13-111-1649/
        13-111-1650	板こんにゃく	https://recipe.rakuten.co.jp/category/13-111-1650/
        13-111-124	その他のこんにゃく	https://recipe.rakuten.co.jp/category/13-111-124/
        13-112-125	しらたき	https://recipe.rakuten.co.jp/category/13-112-125/
        13-113-120	ひじき	https://recipe.rakuten.co.jp/category/13-113-120/
        13-113-73	わかめ	https://recipe.rakuten.co.jp/category/13-113-73/
        13-113-335	もずく	https://recipe.rakuten.co.jp/category/13-113-335/
        13-113-72	昆布	https://recipe.rakuten.co.jp/category/13-113-72/
        13-113-336	海苔	https://recipe.rakuten.co.jp/category/13-113-336/
        13-113-1651	切り昆布	https://recipe.rakuten.co.jp/category/13-113-1651/
        13-113-1652	塩昆布	https://recipe.rakuten.co.jp/category/13-113-1652/
        13-113-1653	めかぶ	https://recipe.rakuten.co.jp/category/13-113-1653/
        13-113-74	その他の海藻	https://recipe.rakuten.co.jp/category/13-113-74/
        13-114-491	切り干し大根	https://recipe.rakuten.co.jp/category/13-114-491/
        13-114-350	春雨	https://recipe.rakuten.co.jp/category/13-114-350/
        13-114-119	きくらげ	https://recipe.rakuten.co.jp/category/13-114-119/
        13-114-533	麩	https://recipe.rakuten.co.jp/category/13-114-533/
        13-114-117	かんぴょう	https://recipe.rakuten.co.jp/category/13-114-117/
        13-114-492	干し椎茸	https://recipe.rakuten.co.jp/category/13-114-492/
        13-114-534	佃煮	https://recipe.rakuten.co.jp/category/13-114-534/
        13-114-121	かつお節（鰹節）	https://recipe.rakuten.co.jp/category/13-114-121/
        13-114-1654	ちりめん山椒	https://recipe.rakuten.co.jp/category/13-114-1654/
        13-114-123	その他の乾物	https://recipe.rakuten.co.jp/category/13-114-123/
        13-115-126	その他の食材	https://recipe.rakuten.co.jp/category/13-115-126/
        14-121-553	オムライス	https://recipe.rakuten.co.jp/category/14-121-553/
        14-122-552	チキンライス	https://recipe.rakuten.co.jp/category/14-122-552/
        14-123-567	ハヤシライス	https://recipe.rakuten.co.jp/category/14-123-567/
        14-124-568	タコライス	https://recipe.rakuten.co.jp/category/14-124-568/
        14-125-569	ロコモコ	https://recipe.rakuten.co.jp/category/14-125-569/
        14-126-303	パエリア	https://recipe.rakuten.co.jp/category/14-126-303/
        14-127-1310	エビピラフ	https://recipe.rakuten.co.jp/category/14-127-1310/
        14-127-1311	カレーピラフ	https://recipe.rakuten.co.jp/category/14-127-1311/
        14-127-142	その他のピラフ	https://recipe.rakuten.co.jp/category/14-127-142/
        14-128-145	その他○○ライス	https://recipe.rakuten.co.jp/category/14-128-145/
        14-129-560	ちらし寿司	https://recipe.rakuten.co.jp/category/14-129-560/
        14-129-559	いなり寿司	https://recipe.rakuten.co.jp/category/14-129-559/
        14-129-561	手巻き寿司	https://recipe.rakuten.co.jp/category/14-129-561/
        14-129-890	巻き寿司	https://recipe.rakuten.co.jp/category/14-129-890/
        14-129-889	押し寿司	https://recipe.rakuten.co.jp/category/14-129-889/
        14-129-888	にぎり寿司・手まり寿司	https://recipe.rakuten.co.jp/category/14-129-888/
        14-129-891	お祝い・パーティ寿司	https://recipe.rakuten.co.jp/category/14-129-891/
        14-129-1313	すし飯	https://recipe.rakuten.co.jp/category/14-129-1313/
        14-129-140	その他の寿司	https://recipe.rakuten.co.jp/category/14-129-140/
        14-130-544	豚丼	https://recipe.rakuten.co.jp/category/14-130-544/
        14-130-1314	天津丼・天津飯	https://recipe.rakuten.co.jp/category/14-130-1314/
        14-130-546	中華丼	https://recipe.rakuten.co.jp/category/14-130-546/
        14-130-542	カツ丼	https://recipe.rakuten.co.jp/category/14-130-542/
        14-130-548	天丼	https://recipe.rakuten.co.jp/category/14-130-548/
        14-130-547	海鮮丼	https://recipe.rakuten.co.jp/category/14-130-547/
        14-130-1315	しらす丼	https://recipe.rakuten.co.jp/category/14-130-1315/
        14-130-1316	三色丼・そぼろ丼	https://recipe.rakuten.co.jp/category/14-130-1316/
        14-130-540	玉子丼	https://recipe.rakuten.co.jp/category/14-130-540/
        14-130-545	鶏丼	https://recipe.rakuten.co.jp/category/14-130-545/
        14-130-135	その他のどんぶり	https://recipe.rakuten.co.jp/category/14-130-135/
        14-131-1317	キムチチャーハン	https://recipe.rakuten.co.jp/category/14-131-1317/
        14-131-892	あんかけチャーハン	https://recipe.rakuten.co.jp/category/14-131-892/
        14-131-1318	レタスチャーハン	https://recipe.rakuten.co.jp/category/14-131-1318/
        14-131-1319	納豆チャーハン	https://recipe.rakuten.co.jp/category/14-131-1319/
        14-131-1320	高菜チャーハン	https://recipe.rakuten.co.jp/category/14-131-1320/
        14-131-893	アレンジチャーハン	https://recipe.rakuten.co.jp/category/14-131-893/
        14-131-136	その他のチャーハン	https://recipe.rakuten.co.jp/category/14-131-136/
        14-131-894	ナシゴレン	https://recipe.rakuten.co.jp/category/14-131-894/
        14-131-554	そばめし	https://recipe.rakuten.co.jp/category/14-131-554/
        14-132-1321	栗ご飯	https://recipe.rakuten.co.jp/category/14-132-1321/
        14-132-555	おこわ・赤飯	https://recipe.rakuten.co.jp/category/14-132-555/
        14-132-1322	たけのこご飯	https://recipe.rakuten.co.jp/category/14-132-1322/
        14-132-1323	鯛めし	https://recipe.rakuten.co.jp/category/14-132-1323/
        14-132-1324	豆ごはん	https://recipe.rakuten.co.jp/category/14-132-1324/
        14-132-1325	松茸ご飯	https://recipe.rakuten.co.jp/category/14-132-1325/
        14-132-1326	鶏飯	https://recipe.rakuten.co.jp/category/14-132-1326/
        14-132-1327	深川飯	https://recipe.rakuten.co.jp/category/14-132-1327/
        14-132-1328	かやくご飯	https://recipe.rakuten.co.jp/category/14-132-1328/
        14-132-137	その他の炊き込みご飯	https://recipe.rakuten.co.jp/category/14-132-137/
        14-132-138	混ぜご飯	https://recipe.rakuten.co.jp/category/14-132-138/
        14-133-139	おかゆ	https://recipe.rakuten.co.jp/category/14-133-139/
        14-133-557	雑炊	https://recipe.rakuten.co.jp/category/14-133-557/
        14-133-558	おじや	https://recipe.rakuten.co.jp/category/14-133-558/
        14-134-550	肉巻きおにぎり	https://recipe.rakuten.co.jp/category/14-134-550/
        14-134-549	焼きおにぎり	https://recipe.rakuten.co.jp/category/14-134-549/
        14-134-1329	スパムおにぎり	https://recipe.rakuten.co.jp/category/14-134-1329/
        14-134-141	その他のおにぎり	https://recipe.rakuten.co.jp/category/14-134-141/
        14-135-570	残りごはん・冷ごはん	https://recipe.rakuten.co.jp/category/14-135-570/
        14-135-571	お茶漬け	https://recipe.rakuten.co.jp/category/14-135-571/
        14-135-143	ドリア	https://recipe.rakuten.co.jp/category/14-135-143/
        15-137-590	ミートソース	https://recipe.rakuten.co.jp/category/15-137-590/
        15-138-155	クリーム系パスタ	https://recipe.rakuten.co.jp/category/15-138-155/
        15-139-157	オイル・塩系パスタ	https://recipe.rakuten.co.jp/category/15-139-157/
        15-140-900	チーズ系パスタ	https://recipe.rakuten.co.jp/category/15-140-900/
        15-141-592	バジルソース系パスタ	https://recipe.rakuten.co.jp/category/15-141-592/
        15-142-156	和風パスタ	https://recipe.rakuten.co.jp/category/15-142-156/
        15-143-357	冷製パスタ	https://recipe.rakuten.co.jp/category/15-143-357/
        15-144-242	パスタソース	https://recipe.rakuten.co.jp/category/15-144-242/
        15-145-591	スープスパ・スープパスタ	https://recipe.rakuten.co.jp/category/15-145-591/
        15-146-158	その他のパスタ	https://recipe.rakuten.co.jp/category/15-146-158/
        15-147-905	ニョッキ	https://recipe.rakuten.co.jp/category/15-147-905/
        15-151-810	ラザニア	https://recipe.rakuten.co.jp/category/15-151-810/
        16-152-913	カレーうどん	https://recipe.rakuten.co.jp/category/16-152-913/
        16-152-1332	鍋焼きうどん	https://recipe.rakuten.co.jp/category/16-152-1332/
        16-152-911	サラダうどん	https://recipe.rakuten.co.jp/category/16-152-911/
        16-152-1333	冷やしうどん	https://recipe.rakuten.co.jp/category/16-152-1333/
        16-152-1334	きつねうどん	https://recipe.rakuten.co.jp/category/16-152-1334/
        16-152-1335	肉うどん	https://recipe.rakuten.co.jp/category/16-152-1335/
        16-152-912	煮込みうどん	https://recipe.rakuten.co.jp/category/16-152-912/
        16-152-1336	釜揚げうどん	https://recipe.rakuten.co.jp/category/16-152-1336/
        16-152-572	焼うどん	https://recipe.rakuten.co.jp/category/16-152-572/
        16-152-1337	ぶっかけうどん	https://recipe.rakuten.co.jp/category/16-152-1337/
        16-152-150	アレンジうどん	https://recipe.rakuten.co.jp/category/16-152-150/
        16-153-915	あったかい蕎麦	https://recipe.rakuten.co.jp/category/16-153-915/
        16-153-916	冷たい蕎麦	https://recipe.rakuten.co.jp/category/16-153-916/
        16-153-149	アレンジそば	https://recipe.rakuten.co.jp/category/16-153-149/
        16-153-917	そば寿司	https://recipe.rakuten.co.jp/category/16-153-917/
        16-154-151	素麺・冷麦	https://recipe.rakuten.co.jp/category/16-154-151/
        16-154-573	にゅうめん	https://recipe.rakuten.co.jp/category/16-154-573/
        16-154-918	アレンジそうめん	https://recipe.rakuten.co.jp/category/16-154-918/
        16-155-1338	あんかけ焼きそば	https://recipe.rakuten.co.jp/category/16-155-1338/
        16-155-575	塩焼きそば	https://recipe.rakuten.co.jp/category/16-155-575/
        16-155-574	ソース焼きそば	https://recipe.rakuten.co.jp/category/16-155-574/
        16-155-152	アレンジ焼きそば	https://recipe.rakuten.co.jp/category/16-155-152/
        16-156-1339	味噌ラーメン	https://recipe.rakuten.co.jp/category/16-156-1339/
        16-156-1340	塩ラーメン	https://recipe.rakuten.co.jp/category/16-156-1340/
        16-156-1341	冷やしラーメン	https://recipe.rakuten.co.jp/category/16-156-1341/
        16-156-1342	醤油ラーメン	https://recipe.rakuten.co.jp/category/16-156-1342/
        16-156-1343	トマトラーメン	https://recipe.rakuten.co.jp/category/16-156-1343/
        16-156-1344	豚骨ラーメン	https://recipe.rakuten.co.jp/category/16-156-1344/
        16-156-1345	ラーメンサラダ	https://recipe.rakuten.co.jp/category/16-156-1345/
        16-156-1346	その他のラーメン	https://recipe.rakuten.co.jp/category/16-156-1346/
        16-156-919	ラーメンスープ・つけだれ	https://recipe.rakuten.co.jp/category/16-156-919/
        16-158-920	おやき	https://recipe.rakuten.co.jp/category/16-158-920/
        16-158-921	おかず系のクレープ	https://recipe.rakuten.co.jp/category/16-158-921/
        17-159-1355	あさり味噌汁	https://recipe.rakuten.co.jp/category/17-159-1355/
        17-159-1356	しじみ味噌汁	https://recipe.rakuten.co.jp/category/17-159-1356/
        17-159-1357	なすの味噌汁	https://recipe.rakuten.co.jp/category/17-159-1357/
        17-159-1358	なめこの味噌汁	https://recipe.rakuten.co.jp/category/17-159-1358/
        17-159-814	その他の味噌汁	https://recipe.rakuten.co.jp/category/17-159-814/
        17-160-813	お吸い物	https://recipe.rakuten.co.jp/category/17-160-813/
        17-161-790	豚汁	https://recipe.rakuten.co.jp/category/17-161-790/
        17-162-1360	冷汁	https://recipe.rakuten.co.jp/category/17-162-1360/
        17-162-1361	粕汁	https://recipe.rakuten.co.jp/category/17-162-1361/
        17-162-1362	すまし汁	https://recipe.rakuten.co.jp/category/17-162-1362/
        17-162-1363	あら汁	https://recipe.rakuten.co.jp/category/17-162-1363/
        17-162-1364	つみれ汁	https://recipe.rakuten.co.jp/category/17-162-1364/
        17-162-815	その他の汁物	https://recipe.rakuten.co.jp/category/17-162-815/
        17-164-1367	ワンタンスープ	https://recipe.rakuten.co.jp/category/17-164-1367/
        17-164-1368	わかめスープ	https://recipe.rakuten.co.jp/category/17-164-1368/
        17-164-1369	春雨スープ	https://recipe.rakuten.co.jp/category/17-164-1369/
        17-164-89	その他の中華スープ	https://recipe.rakuten.co.jp/category/17-164-89/
        17-165-479	和風スープ	https://recipe.rakuten.co.jp/category/17-165-479/
        17-166-480	韓国風スープ	https://recipe.rakuten.co.jp/category/17-166-480/
        17-167-86	コンソメスープ	https://recipe.rakuten.co.jp/category/17-167-86/
        17-168-87	トマトスープ	https://recipe.rakuten.co.jp/category/17-168-87/
        17-169-481	野菜スープ	https://recipe.rakuten.co.jp/category/17-169-481/
        17-170-90	クリームスープ	https://recipe.rakuten.co.jp/category/17-170-90/
        17-171-88	コーンスープ・ポタージュ	https://recipe.rakuten.co.jp/category/17-171-88/
        17-173-341	オニオンスープ	https://recipe.rakuten.co.jp/category/17-173-341/
        17-173-926	オニオングラタンスープ	https://recipe.rakuten.co.jp/category/17-173-926/
        17-173-524	ビシソワーズ	https://recipe.rakuten.co.jp/category/17-173-524/
        17-173-1370	冷製スープ	https://recipe.rakuten.co.jp/category/17-173-1370/
        17-173-340	豆乳スープ	https://recipe.rakuten.co.jp/category/17-173-340/
        17-173-345	にんじんスープ	https://recipe.rakuten.co.jp/category/17-173-345/
        17-173-346	モロヘイヤスープ	https://recipe.rakuten.co.jp/category/17-173-346/
        17-173-343	豆スープ	https://recipe.rakuten.co.jp/category/17-173-343/
        17-173-91	その他のスープ	https://recipe.rakuten.co.jp/category/17-173-91/
        18-184-946	豆腐サラダ	https://recipe.rakuten.co.jp/category/18-184-946/
        18-184-1408	キャベツサラダ	https://recipe.rakuten.co.jp/category/18-184-1408/
        18-184-1409	人参サラダ	https://recipe.rakuten.co.jp/category/18-184-1409/
        18-184-1412	豚しゃぶ・冷しゃぶサラダ	https://recipe.rakuten.co.jp/category/18-184-1412/
        18-184-1410	アボカドサラダ	https://recipe.rakuten.co.jp/category/18-184-1410/
        18-184-1411	切り干し大根サラダ	https://recipe.rakuten.co.jp/category/18-184-1411/
        18-184-947	海藻サラダ	https://recipe.rakuten.co.jp/category/18-184-947/
        18-184-943	トマトサラダ	https://recipe.rakuten.co.jp/category/18-184-943/
        18-184-945	ツナサラダ	https://recipe.rakuten.co.jp/category/18-184-945/
        18-184-948	ゴーヤサラダ	https://recipe.rakuten.co.jp/category/18-184-948/
        18-184-949	魚介のサラダ	https://recipe.rakuten.co.jp/category/18-184-949/
        18-184-950	お肉を使ったサラダ	https://recipe.rakuten.co.jp/category/18-184-950/
        18-185-951	マヨネーズを使ったサラダ	https://recipe.rakuten.co.jp/category/18-185-951/
        18-186-952	ナンプラーを使ったサラダ	https://recipe.rakuten.co.jp/category/18-186-952/
        18-187-796	シーザーサラダ	https://recipe.rakuten.co.jp/category/18-187-796/
        18-188-953	和風のサラダ	https://recipe.rakuten.co.jp/category/18-188-953/
        18-188-954	中華サラダ	https://recipe.rakuten.co.jp/category/18-188-954/
        18-188-955	イタリアンサラダ	https://recipe.rakuten.co.jp/category/18-188-955/
        18-188-956	韓国風のサラダ	https://recipe.rakuten.co.jp/category/18-188-956/
        18-188-957	洋風・デリ風のサラダ	https://recipe.rakuten.co.jp/category/18-188-957/
        18-188-958	アジアンサラダ	https://recipe.rakuten.co.jp/category/18-188-958/
        18-189-797	スパゲティサラダ	https://recipe.rakuten.co.jp/category/18-189-797/
        18-190-959	ホットサラダ・温野菜	https://recipe.rakuten.co.jp/category/18-190-959/
        18-191-794	その他のサラダ	https://recipe.rakuten.co.jp/category/18-191-794/
        19-192-239	トマトソース	https://recipe.rakuten.co.jp/category/19-192-239/
        19-192-241	タルタルソース	https://recipe.rakuten.co.jp/category/19-192-241/
        19-192-1553	バジルソース	https://recipe.rakuten.co.jp/category/19-192-1553/
        19-192-1554	ホワイトソース	https://recipe.rakuten.co.jp/category/19-192-1554/
        19-192-1555	ステーキソース	https://recipe.rakuten.co.jp/category/19-192-1555/
        19-192-1556	サルサソース	https://recipe.rakuten.co.jp/category/19-192-1556/
        19-192-1557	ハンバーグソース	https://recipe.rakuten.co.jp/category/19-192-1557/
        19-192-960	デミグラスソース	https://recipe.rakuten.co.jp/category/19-192-960/
        19-192-1558	バーニャカウダソース	https://recipe.rakuten.co.jp/category/19-192-1558/
        19-192-627	マヨネーズ	https://recipe.rakuten.co.jp/category/19-192-627/
        19-192-1559	ピザソース	https://recipe.rakuten.co.jp/category/19-192-1559/
        19-192-1560	チリソース	https://recipe.rakuten.co.jp/category/19-192-1560/
        19-192-963	ジェノベーゼソース	https://recipe.rakuten.co.jp/category/19-192-963/
        19-192-961	照り焼きソース	https://recipe.rakuten.co.jp/category/19-192-961/
        19-192-962	オーロラソース	https://recipe.rakuten.co.jp/category/19-192-962/
        19-192-240	クリームソース	https://recipe.rakuten.co.jp/category/19-192-240/
        19-192-287	フルーツソース	https://recipe.rakuten.co.jp/category/19-192-287/
        19-192-243	お肉に合うソース	https://recipe.rakuten.co.jp/category/19-192-243/
        19-192-244	シーフードに合うソース	https://recipe.rakuten.co.jp/category/19-192-244/
        19-192-245	その他のソース	https://recipe.rakuten.co.jp/category/19-192-245/
        19-193-966	焼肉のたれ	https://recipe.rakuten.co.jp/category/19-193-966/
        19-193-1561	冷やし中華のたれ	https://recipe.rakuten.co.jp/category/19-193-1561/
        19-193-965	ごまだれ	https://recipe.rakuten.co.jp/category/19-193-965/
        19-193-1562	焼き鳥のたれ	https://recipe.rakuten.co.jp/category/19-193-1562/
        19-193-363	ラー油・食べるラー油	https://recipe.rakuten.co.jp/category/19-193-363/
        19-193-967	マリネ液	https://recipe.rakuten.co.jp/category/19-193-967/
        19-193-964	餃子のタレ	https://recipe.rakuten.co.jp/category/19-193-964/
        19-193-246	お肉に合うタレ	https://recipe.rakuten.co.jp/category/19-193-246/
        19-193-247	シーフードに合うタレ	https://recipe.rakuten.co.jp/category/19-193-247/
        19-193-248	野菜に合うタレ	https://recipe.rakuten.co.jp/category/19-193-248/
        19-193-249	その他のタレ	https://recipe.rakuten.co.jp/category/19-193-249/
        19-194-250	めんつゆ	https://recipe.rakuten.co.jp/category/19-194-250/
        19-194-1563	そばつゆ・そうめんつゆ	https://recipe.rakuten.co.jp/category/19-194-1563/
        19-194-251	天つゆ	https://recipe.rakuten.co.jp/category/19-194-251/
        19-194-252	その他のつゆ	https://recipe.rakuten.co.jp/category/19-194-252/
        19-195-1564	ほんだし	https://recipe.rakuten.co.jp/category/19-195-1564/
        19-195-1565	白だし	https://recipe.rakuten.co.jp/category/19-195-1565/
        19-195-300	その他のだし	https://recipe.rakuten.co.jp/category/19-195-300/
        19-196-253	フレンチドレッシング	https://recipe.rakuten.co.jp/category/19-196-253/
        19-196-256	和風ドレッシング	https://recipe.rakuten.co.jp/category/19-196-256/
        19-196-254	イタリアンドレッシング	https://recipe.rakuten.co.jp/category/19-196-254/
        19-196-255	中華ドレッシング	https://recipe.rakuten.co.jp/category/19-196-255/
        19-196-968	シーザードレッシング	https://recipe.rakuten.co.jp/category/19-196-968/
        19-196-969	ゴマドレッシング	https://recipe.rakuten.co.jp/category/19-196-969/
        19-196-257	マヨネーズ系ドレッシング	https://recipe.rakuten.co.jp/category/19-196-257/
        19-196-258	その他のドレッシング	https://recipe.rakuten.co.jp/category/19-196-258/
        20-197-970	お弁当のおかず	https://recipe.rakuten.co.jp/category/20-197-970/
        20-198-971	赤色系のおかず	https://recipe.rakuten.co.jp/category/20-198-971/
        20-198-972	黄色系のおかず	https://recipe.rakuten.co.jp/category/20-198-972/
        20-198-973	緑系のおかず	https://recipe.rakuten.co.jp/category/20-198-973/
        20-198-974	白系のおかず	https://recipe.rakuten.co.jp/category/20-198-974/
        20-198-975	黒・茶系のおかず	https://recipe.rakuten.co.jp/category/20-198-975/
        20-199-976	作り置き・冷凍できるおかず	https://recipe.rakuten.co.jp/category/20-199-976/
        20-200-977	すきまおかず	https://recipe.rakuten.co.jp/category/20-200-977/
        20-201-978	使い回しおかず	https://recipe.rakuten.co.jp/category/20-201-978/
        20-202-214	ごはんのお弁当（子供用）	https://recipe.rakuten.co.jp/category/20-202-214/
        20-202-215	パンのお弁当（子供用）	https://recipe.rakuten.co.jp/category/20-202-215/
        20-202-216	おにぎりのお弁当（子供用）	https://recipe.rakuten.co.jp/category/20-202-216/
        20-202-218	かわいいおかず	https://recipe.rakuten.co.jp/category/20-202-218/
        20-202-219	その他のお弁当（子供用）	https://recipe.rakuten.co.jp/category/20-202-219/
        20-202-979	パスタのお弁当（子供）	https://recipe.rakuten.co.jp/category/20-202-979/
        20-203-220	ごはんのお弁当（大人用）	https://recipe.rakuten.co.jp/category/20-203-220/
        20-203-221	パンのお弁当（大人用）	https://recipe.rakuten.co.jp/category/20-203-221/
        20-203-222	おにぎりのお弁当（大人用）	https://recipe.rakuten.co.jp/category/20-203-222/
        20-203-224	お弁当のおかず（大人用）	https://recipe.rakuten.co.jp/category/20-203-224/
        20-203-980	パスタのお弁当（大人用）	https://recipe.rakuten.co.jp/category/20-203-980/
        20-203-225	その他のお弁当（大人用）	https://recipe.rakuten.co.jp/category/20-203-225/
        21-204-985	アイシングクッキー	https://recipe.rakuten.co.jp/category/21-204-985/
        21-204-1442	おからクッキー	https://recipe.rakuten.co.jp/category/21-204-1442/
        21-204-1443	ホットケーキミックスでクッキー	https://recipe.rakuten.co.jp/category/21-204-1443/
        21-204-1444	チョコチップクッキー	https://recipe.rakuten.co.jp/category/21-204-1444/
        21-204-1445	豆乳クッキー	https://recipe.rakuten.co.jp/category/21-204-1445/
        21-204-498	その他のクッキー	https://recipe.rakuten.co.jp/category/21-204-498/
        21-204-610	ビスケット	https://recipe.rakuten.co.jp/category/21-204-610/
        21-204-611	サブレ	https://recipe.rakuten.co.jp/category/21-204-611/
        21-205-625	レアチーズケーキ	https://recipe.rakuten.co.jp/category/21-205-625/
        21-205-986	ベイクドチーズケーキ	https://recipe.rakuten.co.jp/category/21-205-986/
        21-205-1446	スフレチーズケーキ	https://recipe.rakuten.co.jp/category/21-205-1446/
        21-205-189	その他のチーズケーキ	https://recipe.rakuten.co.jp/category/21-205-189/
        21-206-497	ロールケーキ	https://recipe.rakuten.co.jp/category/21-206-497/
        21-206-190	スポンジケーキ	https://recipe.rakuten.co.jp/category/21-206-190/
        21-206-1449	バナナケーキ	https://recipe.rakuten.co.jp/category/21-206-1449/
        21-206-623	モンブラン	https://recipe.rakuten.co.jp/category/21-206-623/
        21-206-1450	レモンケーキ	https://recipe.rakuten.co.jp/category/21-206-1450/
        21-206-188	ショートケーキ	https://recipe.rakuten.co.jp/category/21-206-188/
        21-206-1451	ミルクレープ	https://recipe.rakuten.co.jp/category/21-206-1451/
        21-206-1452	フルーツケーキ	https://recipe.rakuten.co.jp/category/21-206-1452/
        21-206-194	その他のケーキ	https://recipe.rakuten.co.jp/category/21-206-194/
        21-207-817	アップルパイ	https://recipe.rakuten.co.jp/category/21-207-817/
        21-207-1454	ミートパイ	https://recipe.rakuten.co.jp/category/21-207-1454/
        21-207-193	タルト	https://recipe.rakuten.co.jp/category/21-207-193/
        21-207-306	パイ	https://recipe.rakuten.co.jp/category/21-207-306/
        21-207-622	ミルフィーユ	https://recipe.rakuten.co.jp/category/21-207-622/
        21-207-987	タルト台	https://recipe.rakuten.co.jp/category/21-207-987/
        21-208-989	ガトーショコラ	https://recipe.rakuten.co.jp/category/21-208-989/
        21-208-607	生チョコ	https://recipe.rakuten.co.jp/category/21-208-607/
        21-208-608	ブラウニー	https://recipe.rakuten.co.jp/category/21-208-608/
        21-208-988	チョコレートケーキ	https://recipe.rakuten.co.jp/category/21-208-988/
        21-208-1120	トリュフ	https://recipe.rakuten.co.jp/category/21-208-1120/
        21-208-1455	ザッハトルテ	https://recipe.rakuten.co.jp/category/21-208-1455/
        21-208-201	その他のチョコレート	https://recipe.rakuten.co.jp/category/21-208-201/
        21-209-507	マフィン	https://recipe.rakuten.co.jp/category/21-209-507/
        21-209-499	スコーン	https://recipe.rakuten.co.jp/category/21-209-499/
        21-210-990	カップケーキ	https://recipe.rakuten.co.jp/category/21-210-990/
        21-210-609	マカロン	https://recipe.rakuten.co.jp/category/21-210-609/
        21-210-619	マドレーヌ	https://recipe.rakuten.co.jp/category/21-210-619/
        21-210-1457	ワッフル	https://recipe.rakuten.co.jp/category/21-210-1457/
        21-210-991	フィナンシェ	https://recipe.rakuten.co.jp/category/21-210-991/
        21-210-202	その他の焼き菓子	https://recipe.rakuten.co.jp/category/21-210-202/
        21-211-1458	かぼちゃプリン	https://recipe.rakuten.co.jp/category/21-211-1458/
        21-211-1459	マンゴープリン	https://recipe.rakuten.co.jp/category/21-211-1459/
        21-211-1460	豆乳プリン	https://recipe.rakuten.co.jp/category/21-211-1460/
        21-211-1461	カスタードプリン	https://recipe.rakuten.co.jp/category/21-211-1461/
        21-211-1462	焼きプリン	https://recipe.rakuten.co.jp/category/21-211-1462/
        21-211-197	その他のプリン・プディング	https://recipe.rakuten.co.jp/category/21-211-197/
        21-212-195	シュークリーム	https://recipe.rakuten.co.jp/category/21-212-195/
        21-212-992	エクレア	https://recipe.rakuten.co.jp/category/21-212-992/
        21-214-200	カステラ	https://recipe.rakuten.co.jp/category/21-214-200/
        21-214-184	おはぎ	https://recipe.rakuten.co.jp/category/21-214-184/
        21-214-598	ぜんざい	https://recipe.rakuten.co.jp/category/21-214-598/
        21-214-95	おしるこ	https://recipe.rakuten.co.jp/category/21-214-95/
        21-214-1471	白玉団子	https://recipe.rakuten.co.jp/category/21-214-1471/
        21-214-180	だんご	https://recipe.rakuten.co.jp/category/21-214-180/
        21-214-1472	みたらし団子	https://recipe.rakuten.co.jp/category/21-214-1472/
        21-214-183	どら焼き	https://recipe.rakuten.co.jp/category/21-214-183/
        21-214-182	羊羹	https://recipe.rakuten.co.jp/category/21-214-182/
        21-214-1473	水ようかん	https://recipe.rakuten.co.jp/category/21-214-1473/
        21-214-1474	芋ようかん	https://recipe.rakuten.co.jp/category/21-214-1474/
        21-214-599	ういろう	https://recipe.rakuten.co.jp/category/21-214-599/
        21-214-1475	かりんとう	https://recipe.rakuten.co.jp/category/21-214-1475/
        21-214-597	大福	https://recipe.rakuten.co.jp/category/21-214-597/
        21-214-181	まんじゅう	https://recipe.rakuten.co.jp/category/21-214-181/
        21-214-600	くずもち	https://recipe.rakuten.co.jp/category/21-214-600/
        21-214-596	わらび餅	https://recipe.rakuten.co.jp/category/21-214-596/
        21-214-185	お餅	https://recipe.rakuten.co.jp/category/21-214-185/
        21-214-186	せんべい	https://recipe.rakuten.co.jp/category/21-214-186/
        21-214-187	その他の和菓子	https://recipe.rakuten.co.jp/category/21-214-187/
        21-215-1453	ホットケーキ・パンケーキ	https://recipe.rakuten.co.jp/category/21-215-1453/
        21-216-602	焼きドーナツ	https://recipe.rakuten.co.jp/category/21-216-602/
        21-216-603	生ドーナツ	https://recipe.rakuten.co.jp/category/21-216-603/
        21-216-196	その他のドーナツ	https://recipe.rakuten.co.jp/category/21-216-196/
        21-217-1476	大学芋	https://recipe.rakuten.co.jp/category/21-217-1476/
        21-217-614	マシュマロ	https://recipe.rakuten.co.jp/category/21-217-614/
        21-217-616	クレープ	https://recipe.rakuten.co.jp/category/21-217-616/
        21-217-1477	パフェ	https://recipe.rakuten.co.jp/category/21-217-1477/
        21-217-1478	コンポート	https://recipe.rakuten.co.jp/category/21-217-1478/
        21-217-620	水切りヨーグルト	https://recipe.rakuten.co.jp/category/21-217-620/
        21-217-1479	生キャラメル	https://recipe.rakuten.co.jp/category/21-217-1479/
        21-217-122	ドライフルーツ	https://recipe.rakuten.co.jp/category/21-217-122/
        21-217-199	ヨーグルトを使ったお菓子	https://recipe.rakuten.co.jp/category/21-217-199/
        21-217-212	世界のお菓子	https://recipe.rakuten.co.jp/category/21-217-212/
        21-217-213	創作・オリジナルお菓子	https://recipe.rakuten.co.jp/category/21-217-213/
        21-217-626	飴・キャンディー	https://recipe.rakuten.co.jp/category/21-217-626/
        21-217-205	その他のお菓子	https://recipe.rakuten.co.jp/category/21-217-205/
        21-218-1480	生クリーム	https://recipe.rakuten.co.jp/category/21-218-1480/
        21-218-1481	カスタードクリーム	https://recipe.rakuten.co.jp/category/21-218-1481/
        21-218-294	チョコレートクリーム	https://recipe.rakuten.co.jp/category/21-218-294/
        21-218-295	ピーナツクリーム	https://recipe.rakuten.co.jp/category/21-218-295/
        21-218-296	キャラメルクリーム	https://recipe.rakuten.co.jp/category/21-218-296/
        21-218-297	バタークリーム	https://recipe.rakuten.co.jp/category/21-218-297/
        21-218-298	ゴマクリーム	https://recipe.rakuten.co.jp/category/21-218-298/
        21-218-299	その他のクリーム	https://recipe.rakuten.co.jp/category/21-218-299/
        21-218-1482	梅ジャム	https://recipe.rakuten.co.jp/category/21-218-1482/
        21-218-291	ブルーベリージャム	https://recipe.rakuten.co.jp/category/21-218-291/
        21-218-288	オレンジジャム・マーマレード	https://recipe.rakuten.co.jp/category/21-218-288/
        21-218-289	イチゴジャム	https://recipe.rakuten.co.jp/category/21-218-289/
        21-218-290	リンゴジャム	https://recipe.rakuten.co.jp/category/21-218-290/
        21-218-293	ミルクジャム	https://recipe.rakuten.co.jp/category/21-218-293/
        21-218-292	その他のジャム	https://recipe.rakuten.co.jp/category/21-218-292/
        21-218-993	コンフィチュール	https://recipe.rakuten.co.jp/category/21-218-993/
        22-219-165	バゲット・フランスパン	https://recipe.rakuten.co.jp/category/22-219-165/
        22-219-166	カンパーニュ	https://recipe.rakuten.co.jp/category/22-219-166/
        22-219-994	エピ	https://recipe.rakuten.co.jp/category/22-219-994/
        22-219-995	全粒粉・ライ麦・雑穀パン	https://recipe.rakuten.co.jp/category/22-219-995/
        22-220-173	ベーグル	https://recipe.rakuten.co.jp/category/22-220-173/
        22-220-996	イングリッシュマフィン	https://recipe.rakuten.co.jp/category/22-220-996/
        22-220-1002	米粉パン	https://recipe.rakuten.co.jp/category/22-220-1002/
        22-220-167	ロールパン	https://recipe.rakuten.co.jp/category/22-220-167/
        22-220-997	レーズンパン	https://recipe.rakuten.co.jp/category/22-220-997/
        22-220-999	ミルクパン	https://recipe.rakuten.co.jp/category/22-220-999/
        22-220-1001	白パン	https://recipe.rakuten.co.jp/category/22-220-1001/
        22-220-1000	胡桃パン	https://recipe.rakuten.co.jp/category/22-220-1000/
        22-220-998	丸パン	https://recipe.rakuten.co.jp/category/22-220-998/
        22-221-1438	ラスク	https://recipe.rakuten.co.jp/category/22-221-1438/
        22-221-1439	塩ケーキ（ケークサレ）	https://recipe.rakuten.co.jp/category/22-221-1439/
        22-221-1003	シナモンロール	https://recipe.rakuten.co.jp/category/22-221-1003/
        22-221-209	メロンパン	https://recipe.rakuten.co.jp/category/22-221-209/
        22-221-207	クリームパン	https://recipe.rakuten.co.jp/category/22-221-207/
        22-221-206	あんぱん	https://recipe.rakuten.co.jp/category/22-221-206/
        22-221-1440	揚げパン	https://recipe.rakuten.co.jp/category/22-221-1440/
        22-221-211	その他の菓子パン	https://recipe.rakuten.co.jp/category/22-221-211/
        22-222-169	クロワッサン	https://recipe.rakuten.co.jp/category/22-222-169/
        22-222-168	デニッシュ	https://recipe.rakuten.co.jp/category/22-222-168/
        22-223-1009	白神こだま酵母	https://recipe.rakuten.co.jp/category/22-223-1009/
        22-223-1006	ホシノ天然酵母	https://recipe.rakuten.co.jp/category/22-223-1006/
        22-223-1007	パネトーネマザー	https://recipe.rakuten.co.jp/category/22-223-1007/
        22-223-1008	あこ天然酵母	https://recipe.rakuten.co.jp/category/22-223-1008/
        22-223-1005	自家製酵母を使ったパン	https://recipe.rakuten.co.jp/category/22-223-1005/
        22-223-1004	自家製酵母の作り方	https://recipe.rakuten.co.jp/category/22-223-1004/
        22-223-365	その他の酵母	https://recipe.rakuten.co.jp/category/22-223-365/
        22-227-358	フォカッチャ	https://recipe.rakuten.co.jp/category/22-227-358/
        22-227-1014	ブリオッシュ	https://recipe.rakuten.co.jp/category/22-227-1014/
        22-227-585	ピタサンド・ピタパン	https://recipe.rakuten.co.jp/category/22-227-585/
        22-227-1015	プレッツェル	https://recipe.rakuten.co.jp/category/22-227-1015/
        22-227-1012	グリッシーニ	https://recipe.rakuten.co.jp/category/22-227-1012/
        22-229-1433	ハンバーガー	https://recipe.rakuten.co.jp/category/22-229-1433/
        22-229-1434	ホットドッグ	https://recipe.rakuten.co.jp/category/22-229-1434/
        22-229-1435	ガーリックトースト	https://recipe.rakuten.co.jp/category/22-229-1435/
        22-229-587	カレーパン	https://recipe.rakuten.co.jp/category/22-229-587/
        22-229-1436	ピザトースト	https://recipe.rakuten.co.jp/category/22-229-1436/
        22-229-1016	コーンパン	https://recipe.rakuten.co.jp/category/22-229-1016/
        22-229-1017	焼きそばパン	https://recipe.rakuten.co.jp/category/22-229-1017/
        22-229-1018	チーズパン	https://recipe.rakuten.co.jp/category/22-229-1018/
        22-229-1019	マヨネーズを使ったパン	https://recipe.rakuten.co.jp/category/22-229-1019/
        22-229-1437	その他の惣菜パン	https://recipe.rakuten.co.jp/category/22-229-1437/
        22-230-175	その他	https://recipe.rakuten.co.jp/category/22-230-175/
        22-231-1020	牛乳・卵を使わないパン	https://recipe.rakuten.co.jp/category/22-231-1020/
        22-231-1021	オイルを使わないパン	https://recipe.rakuten.co.jp/category/22-231-1021/
        23-234-100	その他	https://recipe.rakuten.co.jp/category/23-234-100/
        24-238-1896	アウトドア料理・キャンプ料理	https://recipe.rakuten.co.jp/category/24-238-1896/
        24-238-1051	ダッチオーブン	https://recipe.rakuten.co.jp/category/24-238-1051/
        24-238-1050	燻製	https://recipe.rakuten.co.jp/category/24-238-1050/
        24-238-1045	バーベキューの野菜料理	https://recipe.rakuten.co.jp/category/24-238-1045/
        24-238-1046	バーベキューの肉料理	https://recipe.rakuten.co.jp/category/24-238-1046/
        24-238-1047	バーベキューのご飯もの	https://recipe.rakuten.co.jp/category/24-238-1047/
        24-238-1048	バーベキューの海の幸料理	https://recipe.rakuten.co.jp/category/24-238-1048/
        24-238-1049	バーベキューの山の幸・川の幸料理	https://recipe.rakuten.co.jp/category/24-238-1049/
        24-238-374	バーベキュー向けアレンジ	https://recipe.rakuten.co.jp/category/24-238-374/
        24-244-386	その他イベント	https://recipe.rakuten.co.jp/category/24-244-386/
        25-248-1059	ビーフストロガノフ	https://recipe.rakuten.co.jp/category/25-248-1059/
        25-248-1060	ボルシチ	https://recipe.rakuten.co.jp/category/25-248-1060/
        25-248-1061	ピロシキ	https://recipe.rakuten.co.jp/category/25-248-1061/
        25-248-1062	ロシアンティー	https://recipe.rakuten.co.jp/category/25-248-1062/
        25-248-1063	ペリメニ	https://recipe.rakuten.co.jp/category/25-248-1063/
        25-248-1064	その他のロシア料理	https://recipe.rakuten.co.jp/category/25-248-1064/
        25-255-740	シュトーレン	https://recipe.rakuten.co.jp/category/25-255-740/
        25-255-736	ザワークラウト	https://recipe.rakuten.co.jp/category/25-255-736/
        25-255-738	バームクーヘン	https://recipe.rakuten.co.jp/category/25-255-738/
        25-255-737	アイスバイン	https://recipe.rakuten.co.jp/category/25-255-737/
        25-255-739	トルテ	https://recipe.rakuten.co.jp/category/25-255-739/
        25-255-741	その他のドイツ料理	https://recipe.rakuten.co.jp/category/25-255-741/
        25-256-733	スパニッシュオムレツ	https://recipe.rakuten.co.jp/category/25-256-733/
        25-256-732	ガスパチョ	https://recipe.rakuten.co.jp/category/25-256-732/
        25-256-734	チュロス	https://recipe.rakuten.co.jp/category/25-256-734/
        25-256-1827	アヒージョ	https://recipe.rakuten.co.jp/category/25-256-1827/
        25-256-1828	ピンチョス	https://recipe.rakuten.co.jp/category/25-256-1828/
        25-256-735	その他のスペイン料理	https://recipe.rakuten.co.jp/category/25-256-735/
        25-257-716	ケバブ	https://recipe.rakuten.co.jp/category/25-257-716/
        25-257-717	トルコアイス	https://recipe.rakuten.co.jp/category/25-257-717/
        25-257-718	キョフテ	https://recipe.rakuten.co.jp/category/25-257-718/
        25-257-720	その他のトルコ料理	https://recipe.rakuten.co.jp/category/25-257-720/
        20-258-981	部活のお弁当	https://recipe.rakuten.co.jp/category/20-258-981/
        26-260-1068	ビールに合うおつまみ	https://recipe.rakuten.co.jp/category/26-260-1068/
        26-260-1069	ワインに合うおつまみ	https://recipe.rakuten.co.jp/category/26-260-1069/
        26-260-1070	日本酒に合うおつまみ	https://recipe.rakuten.co.jp/category/26-260-1070/
        26-260-1071	焼酎に合うおつまみ	https://recipe.rakuten.co.jp/category/26-260-1071/
        26-260-1072	混ぜるだけでおつまみ	https://recipe.rakuten.co.jp/category/26-260-1072/
        26-260-1073	火を使わないでおつまみ	https://recipe.rakuten.co.jp/category/26-260-1073/
        26-260-1074	フライパンだけでおつまみ	https://recipe.rakuten.co.jp/category/26-260-1074/
        26-261-402	小麦を使わない（小麦アレルギー）	https://recipe.rakuten.co.jp/category/26-261-402/
        26-261-403	卵を使わない（卵アレルギー）	https://recipe.rakuten.co.jp/category/26-261-403/
        26-261-404	蕎麦を使わない（そばアレルギー）	https://recipe.rakuten.co.jp/category/26-261-404/
        26-261-405	牛乳を使わない（牛乳アレルギー）	https://recipe.rakuten.co.jp/category/26-261-405/
        26-261-406	大豆を使わない（大豆アレルギー）	https://recipe.rakuten.co.jp/category/26-261-406/
        26-261-407	ピーナツを使わない（ピーナッツアレルギー）	https://recipe.rakuten.co.jp/category/26-261-407/
        26-261-408	チョコレートを使わない	https://recipe.rakuten.co.jp/category/26-261-408/
        26-261-413	お肉を使わない	https://recipe.rakuten.co.jp/category/26-261-413/
        26-261-414	魚介類を使わない	https://recipe.rakuten.co.jp/category/26-261-414/
        26-261-415	火を使わない料理	https://recipe.rakuten.co.jp/category/26-261-415/
        26-261-416	包丁を使わない料理	https://recipe.rakuten.co.jp/category/26-261-416/
        26-261-1121	ミキサーを使わない料理	https://recipe.rakuten.co.jp/category/26-261-1121/
        26-261-417	化学調味料を使わない	https://recipe.rakuten.co.jp/category/26-261-417/
        26-261-418	油を使わない	https://recipe.rakuten.co.jp/category/26-261-418/
        26-261-419	その他○○を使わない（材料）	https://recipe.rakuten.co.jp/category/26-261-419/
        26-261-501	その他○○で作れる（材料）	https://recipe.rakuten.co.jp/category/26-261-501/
        26-261-502	その他○○を使わない（調理器具）	https://recipe.rakuten.co.jp/category/26-261-502/
        26-262-1085	春のおもてなし料理	https://recipe.rakuten.co.jp/category/26-262-1085/
        26-262-1086	夏のおもてなし料理	https://recipe.rakuten.co.jp/category/26-262-1086/
        26-262-1087	秋のおもてなし料理	https://recipe.rakuten.co.jp/category/26-262-1087/
        26-262-1088	冬のおもてなし料理	https://recipe.rakuten.co.jp/category/26-262-1088/
        26-262-1076	メイン料理	https://recipe.rakuten.co.jp/category/26-262-1076/
        26-262-1077	前菜・サラダ	https://recipe.rakuten.co.jp/category/26-262-1077/
        26-262-1078	魚のおもてなし料理	https://recipe.rakuten.co.jp/category/26-262-1078/
        26-262-1079	お肉のおもてなし料理	https://recipe.rakuten.co.jp/category/26-262-1079/
        26-262-1080	ごはんのおもてなし料理	https://recipe.rakuten.co.jp/category/26-262-1080/
        26-262-1081	デザート	https://recipe.rakuten.co.jp/category/26-262-1081/
        26-262-1082	おもてなしもう一品	https://recipe.rakuten.co.jp/category/26-262-1082/
        26-262-1083	彩鮮やか	https://recipe.rakuten.co.jp/category/26-262-1083/
        26-262-1084	前日に作り置き	https://recipe.rakuten.co.jp/category/26-262-1084/
        26-265-1114	料理のちょいテク・裏技	https://recipe.rakuten.co.jp/category/26-265-1114/
        27-266-1585	カプチーノ	https://recipe.rakuten.co.jp/category/27-266-1585/
        27-266-275	エスプレッソ	https://recipe.rakuten.co.jp/category/27-266-275/
        27-266-1586	アイスコーヒー	https://recipe.rakuten.co.jp/category/27-266-1586/
        27-266-274	カフェオレ	https://recipe.rakuten.co.jp/category/27-266-274/
        27-266-1587	カフェラテ	https://recipe.rakuten.co.jp/category/27-266-1587/
        27-266-1588	フラペチーノ	https://recipe.rakuten.co.jp/category/27-266-1588/
        27-266-276	フレーバーコーヒー	https://recipe.rakuten.co.jp/category/27-266-276/
        27-266-277	アルコール入りコーヒー	https://recipe.rakuten.co.jp/category/27-266-277/
        27-266-764	ベトナムコーヒー	https://recipe.rakuten.co.jp/category/27-266-764/
        27-266-278	その他のコーヒー	https://recipe.rakuten.co.jp/category/27-266-278/
        27-267-269	緑茶	https://recipe.rakuten.co.jp/category/27-267-269/
        27-267-1959	抹茶	https://recipe.rakuten.co.jp/category/27-267-1959/
        27-267-1589	ほうじ茶	https://recipe.rakuten.co.jp/category/27-267-1589/
        27-267-1590	玄米茶	https://recipe.rakuten.co.jp/category/27-267-1590/
        27-267-270	紅茶	https://recipe.rakuten.co.jp/category/27-267-270/
        27-267-1591	ミルクティー	https://recipe.rakuten.co.jp/category/27-267-1591/
        27-267-1592	アールグレイ	https://recipe.rakuten.co.jp/category/27-267-1592/
        27-267-1593	ダージリン	https://recipe.rakuten.co.jp/category/27-267-1593/
        27-267-1594	アッサム	https://recipe.rakuten.co.jp/category/27-267-1594/
        27-267-271	烏龍茶（ウーロン茶）	https://recipe.rakuten.co.jp/category/27-267-271/
        27-267-1595	中国茶	https://recipe.rakuten.co.jp/category/27-267-1595/
        27-267-272	健康茶	https://recipe.rakuten.co.jp/category/27-267-272/
        27-267-1596	ジャスミン茶	https://recipe.rakuten.co.jp/category/27-267-1596/
        27-267-273	その他のお茶	https://recipe.rakuten.co.jp/category/27-267-273/
        27-268-266	牛乳・乳飲料	https://recipe.rakuten.co.jp/category/27-268-266/
        27-268-265	ココア	https://recipe.rakuten.co.jp/category/27-268-265/
        27-268-260	炭酸飲料	https://recipe.rakuten.co.jp/category/27-268-260/
        27-268-261	スポーツドリンク	https://recipe.rakuten.co.jp/category/27-268-261/
        27-268-262	健康飲料	https://recipe.rakuten.co.jp/category/27-268-262/
        27-268-264	チョコレートドリンク	https://recipe.rakuten.co.jp/category/27-268-264/
        27-268-267	ヨーグルトドリンク	https://recipe.rakuten.co.jp/category/27-268-267/
        27-268-268	その他のソフトドリンク	https://recipe.rakuten.co.jp/category/27-268-268/
        27-269-279	ビール	https://recipe.rakuten.co.jp/category/27-269-279/
        27-269-280	焼酎	https://recipe.rakuten.co.jp/category/27-269-280/
        27-269-281	梅酒	https://recipe.rakuten.co.jp/category/27-269-281/
        27-269-1607	甘酒	https://recipe.rakuten.co.jp/category/27-269-1607/
        27-269-283	カクテル	https://recipe.rakuten.co.jp/category/27-269-283/
        27-269-1605	モヒート	https://recipe.rakuten.co.jp/category/27-269-1605/
        27-269-1606	ジントニック	https://recipe.rakuten.co.jp/category/27-269-1606/
        27-269-1608	卵酒	https://recipe.rakuten.co.jp/category/27-269-1608/
        27-269-282	健康酒	https://recipe.rakuten.co.jp/category/27-269-282/
        27-269-284	その他のお酒	https://recipe.rakuten.co.jp/category/27-269-284/
        14-271-147	その他のごはん料理	https://recipe.rakuten.co.jp/category/14-271-147/
        16-272-153	その他の麺	https://recipe.rakuten.co.jp/category/16-272-153/
        19-273-302	その他調味料	https://recipe.rakuten.co.jp/category/19-273-302/
        19-274-364	スパイス＆ハーブ	https://recipe.rakuten.co.jp/category/19-274-364/
        10-275-516	牛肉薄切り	https://recipe.rakuten.co.jp/category/10-275-516/
        10-275-1483	牛タン	https://recipe.rakuten.co.jp/category/10-275-1483/
        10-275-822	牛かたまり肉・ステーキ用・焼肉用	https://recipe.rakuten.co.jp/category/10-275-822/
        10-275-823	その他の牛肉・ビーフ	https://recipe.rakuten.co.jp/category/10-275-823/
        10-276-830	豚バラ肉	https://recipe.rakuten.co.jp/category/10-276-830/
        10-276-1484	豚ヒレ肉	https://recipe.rakuten.co.jp/category/10-276-1484/
        10-276-1485	豚ロース	https://recipe.rakuten.co.jp/category/10-276-1485/
        10-276-1486	豚もも肉	https://recipe.rakuten.co.jp/category/10-276-1486/
        10-276-1487	豚レバー	https://recipe.rakuten.co.jp/category/10-276-1487/
        10-276-517	豚薄切り肉	https://recipe.rakuten.co.jp/category/10-276-517/
        10-276-828	豚かたまり肉	https://recipe.rakuten.co.jp/category/10-276-828/
        10-276-829	豚こま切れ肉	https://recipe.rakuten.co.jp/category/10-276-829/
        10-276-43	その他の豚肉	https://recipe.rakuten.co.jp/category/10-276-43/
        10-277-519	ささみ	https://recipe.rakuten.co.jp/category/10-277-519/
        10-277-1488	手羽元	https://recipe.rakuten.co.jp/category/10-277-1488/
        10-277-520	手羽先	https://recipe.rakuten.co.jp/category/10-277-520/
        10-277-518	鶏もも肉	https://recipe.rakuten.co.jp/category/10-277-518/
        10-277-1119	鶏むね肉	https://recipe.rakuten.co.jp/category/10-277-1119/
        10-277-1489	砂肝	https://recipe.rakuten.co.jp/category/10-277-1489/
        10-277-1490	鶏レバー	https://recipe.rakuten.co.jp/category/10-277-1490/
        10-277-834	その他の鶏肉	https://recipe.rakuten.co.jp/category/10-277-834/
        10-278-836	豚ひき肉	https://recipe.rakuten.co.jp/category/10-278-836/
        10-278-838	鶏ひき肉	https://recipe.rakuten.co.jp/category/10-278-838/
        10-278-837	合い挽き肉	https://recipe.rakuten.co.jp/category/10-278-837/
        10-278-835	牛ひき肉	https://recipe.rakuten.co.jp/category/10-278-835/
        10-278-48	その他のひき肉	https://recipe.rakuten.co.jp/category/10-278-48/
        30-300-1130	ハンバーグステーキ	https://recipe.rakuten.co.jp/category/30-300-1130/
        30-300-1131	煮込みハンバーグ	https://recipe.rakuten.co.jp/category/30-300-1131/
        30-300-1132	和風ハンバーグ	https://recipe.rakuten.co.jp/category/30-300-1132/
        30-300-1135	豆腐ハンバーグ	https://recipe.rakuten.co.jp/category/30-300-1135/
        30-300-1133	おからハンバーグ	https://recipe.rakuten.co.jp/category/30-300-1133/
        30-300-1134	照り焼きハンバーグ	https://recipe.rakuten.co.jp/category/30-300-1134/
        30-300-1136	その他のハンバーグ	https://recipe.rakuten.co.jp/category/30-300-1136/
        30-301-1138	焼き餃子	https://recipe.rakuten.co.jp/category/30-301-1138/
        30-301-1137	水餃子	https://recipe.rakuten.co.jp/category/30-301-1137/
        30-301-1139	蒸し餃子	https://recipe.rakuten.co.jp/category/30-301-1139/
        30-301-1140	揚げ餃子	https://recipe.rakuten.co.jp/category/30-301-1140/
        30-301-1141	スープ餃子	https://recipe.rakuten.co.jp/category/30-301-1141/
        30-301-1142	その他の餃子	https://recipe.rakuten.co.jp/category/30-301-1142/
        30-302-1143	肉じゃが	https://recipe.rakuten.co.jp/category/30-302-1143/
        30-303-1144	牛丼	https://recipe.rakuten.co.jp/category/30-303-1144/
        30-304-1145	親子丼	https://recipe.rakuten.co.jp/category/30-304-1145/
        30-305-1146	豚の生姜焼き	https://recipe.rakuten.co.jp/category/30-305-1146/
        30-306-1148	マカロニグラタン	https://recipe.rakuten.co.jp/category/30-306-1148/
        30-306-1152	チキングラタン	https://recipe.rakuten.co.jp/category/30-306-1152/
        30-306-1151	シーフードグラタン	https://recipe.rakuten.co.jp/category/30-306-1151/
        30-306-1150	ミートグラタン	https://recipe.rakuten.co.jp/category/30-306-1150/
        30-306-1147	ポテトグラタン	https://recipe.rakuten.co.jp/category/30-306-1147/
        30-306-1149	かぼちゃのグラタン	https://recipe.rakuten.co.jp/category/30-306-1149/
        30-306-1153	和風グラタン	https://recipe.rakuten.co.jp/category/30-306-1153/
        30-306-1155	豆腐グラタン	https://recipe.rakuten.co.jp/category/30-306-1155/
        30-306-1154	スパグラ（スパゲティーグラタン）	https://recipe.rakuten.co.jp/category/30-306-1154/
        30-306-1156	その他のグラタン	https://recipe.rakuten.co.jp/category/30-306-1156/
        30-307-1159	チキンカレー	https://recipe.rakuten.co.jp/category/30-307-1159/
        30-307-1166	ポークカレー	https://recipe.rakuten.co.jp/category/30-307-1166/
        30-307-1165	ビーフカレー	https://recipe.rakuten.co.jp/category/30-307-1165/
        30-307-1164	野菜カレー	https://recipe.rakuten.co.jp/category/30-307-1164/
        30-307-1157	ドライカレー	https://recipe.rakuten.co.jp/category/30-307-1157/
        30-307-1158	キーマカレー	https://recipe.rakuten.co.jp/category/30-307-1158/
        30-307-1160	スープカレー	https://recipe.rakuten.co.jp/category/30-307-1160/
        30-307-1161	シーフードカレー	https://recipe.rakuten.co.jp/category/30-307-1161/
        30-307-1162	インドカレー	https://recipe.rakuten.co.jp/category/30-307-1162/
        30-307-1163	グリーンカレー	https://recipe.rakuten.co.jp/category/30-307-1163/
        30-307-1167	ルウから作るカレー	https://recipe.rakuten.co.jp/category/30-307-1167/
        30-307-1168	その他のカレー	https://recipe.rakuten.co.jp/category/30-307-1168/
        30-308-1169	ビーフシチュー	https://recipe.rakuten.co.jp/category/30-308-1169/
        30-308-1170	クリームシチュー	https://recipe.rakuten.co.jp/category/30-308-1170/
        30-308-1171	タンシチュー	https://recipe.rakuten.co.jp/category/30-308-1171/
        30-308-1172	その他のシチュー	https://recipe.rakuten.co.jp/category/30-308-1172/
        30-309-1173	鶏のから揚げ	https://recipe.rakuten.co.jp/category/30-309-1173/
        30-309-1174	カレイの唐揚げ	https://recipe.rakuten.co.jp/category/30-309-1174/
        30-309-1175	たこの唐揚げ	https://recipe.rakuten.co.jp/category/30-309-1175/
        30-309-1176	手羽先の唐揚げ	https://recipe.rakuten.co.jp/category/30-309-1176/
        30-309-1177	その他のから揚げ	https://recipe.rakuten.co.jp/category/30-309-1177/
        30-310-1181	ポテトコロッケ	https://recipe.rakuten.co.jp/category/30-310-1181/
        30-310-1179	クリームコロッケ	https://recipe.rakuten.co.jp/category/30-310-1179/
        30-310-1178	かぼちゃコロッケ	https://recipe.rakuten.co.jp/category/30-310-1178/
        30-310-1184	さつまいもコロッケ	https://recipe.rakuten.co.jp/category/30-310-1184/
        30-310-1183	里芋コロッケ	https://recipe.rakuten.co.jp/category/30-310-1183/
        30-310-1182	おからコロッケ	https://recipe.rakuten.co.jp/category/30-310-1182/
        30-310-1185	カレーコロッケ	https://recipe.rakuten.co.jp/category/30-310-1185/
        30-310-1180	ライスコロッケ	https://recipe.rakuten.co.jp/category/30-310-1180/
        30-310-1186	その他のコロッケ	https://recipe.rakuten.co.jp/category/30-310-1186/
        30-311-1187	かぼちゃの煮物	https://recipe.rakuten.co.jp/category/30-311-1187/
        30-311-1188	大根の煮物	https://recipe.rakuten.co.jp/category/30-311-1188/
        30-311-1204	なすの煮びたし	https://recipe.rakuten.co.jp/category/30-311-1204/
        30-311-1189	ひじきの煮物	https://recipe.rakuten.co.jp/category/30-311-1189/
        30-311-1190	里芋の煮物	https://recipe.rakuten.co.jp/category/30-311-1190/
        30-311-1191	厚揚げの煮物	https://recipe.rakuten.co.jp/category/30-311-1191/
        30-311-1192	きんぴらごぼう	https://recipe.rakuten.co.jp/category/30-311-1192/
        30-311-1193	ふろふき大根	https://recipe.rakuten.co.jp/category/30-311-1193/
        30-311-1991	豚バラ大根	https://recipe.rakuten.co.jp/category/30-311-1991/
        30-311-1194	ふきの煮物	https://recipe.rakuten.co.jp/category/30-311-1194/
        30-311-1195	たけのこの煮物	https://recipe.rakuten.co.jp/category/30-311-1195/
        30-311-1196	レンコンのきんぴら	https://recipe.rakuten.co.jp/category/30-311-1196/
        30-311-1197	冬瓜の煮物	https://recipe.rakuten.co.jp/category/30-311-1197/
        30-311-1200	梅の甘露煮	https://recipe.rakuten.co.jp/category/30-311-1200/
        30-311-1201	栗の甘露煮	https://recipe.rakuten.co.jp/category/30-311-1201/
        30-311-1198	金柑の甘露煮	https://recipe.rakuten.co.jp/category/30-311-1198/
        30-311-1202	さんまの甘露煮	https://recipe.rakuten.co.jp/category/30-311-1202/
        30-311-1199	鮎の甘露煮	https://recipe.rakuten.co.jp/category/30-311-1199/
        30-311-1203	うま煮	https://recipe.rakuten.co.jp/category/30-311-1203/
        30-311-1205	小松菜の煮びたし	https://recipe.rakuten.co.jp/category/30-311-1205/
        30-311-1206	白菜のクリーム煮	https://recipe.rakuten.co.jp/category/30-311-1206/
        30-311-1207	イカと大根の煮物	https://recipe.rakuten.co.jp/category/30-311-1207/
        30-311-1208	牛肉のしぐれ煮	https://recipe.rakuten.co.jp/category/30-311-1208/
        30-311-1209	その他の煮物	https://recipe.rakuten.co.jp/category/30-311-1209/
        30-312-1214	豚キムチ	https://recipe.rakuten.co.jp/category/30-312-1214/
        30-312-1210	レバニラ炒め	https://recipe.rakuten.co.jp/category/30-312-1210/
        30-312-1211	肉野菜炒め	https://recipe.rakuten.co.jp/category/30-312-1211/
        30-312-1212	なすの味噌炒め	https://recipe.rakuten.co.jp/category/30-312-1212/
        30-312-1213	もやし炒め	https://recipe.rakuten.co.jp/category/30-312-1213/
        30-312-1215	その他の野菜炒め	https://recipe.rakuten.co.jp/category/30-312-1215/
        30-313-1216	天ぷら	https://recipe.rakuten.co.jp/category/30-313-1216/
        30-314-1217	かき揚げ	https://recipe.rakuten.co.jp/category/30-314-1217/
        30-314-1224	エビフライ	https://recipe.rakuten.co.jp/category/30-314-1224/
        30-314-1218	メンチカツ	https://recipe.rakuten.co.jp/category/30-314-1218/
        30-314-1219	チキンカツ	https://recipe.rakuten.co.jp/category/30-314-1219/
        30-314-1220	カツレツ	https://recipe.rakuten.co.jp/category/30-314-1220/
        30-314-1221	串カツ	https://recipe.rakuten.co.jp/category/30-314-1221/
        30-314-1222	竜田揚げ	https://recipe.rakuten.co.jp/category/30-314-1222/
        30-314-1223	フライ	https://recipe.rakuten.co.jp/category/30-314-1223/
        30-314-1225	アジフライ	https://recipe.rakuten.co.jp/category/30-314-1225/
        30-314-1226	フライドチキン	https://recipe.rakuten.co.jp/category/30-314-1226/
        30-314-1227	チキンナゲット	https://recipe.rakuten.co.jp/category/30-314-1227/
        30-314-1228	その他の揚げ物	https://recipe.rakuten.co.jp/category/30-314-1228/
        30-315-1229	豆腐ステーキ	https://recipe.rakuten.co.jp/category/30-315-1229/
        30-315-1230	揚げ出し豆腐	https://recipe.rakuten.co.jp/category/30-315-1230/
        30-315-1231	炒り豆腐	https://recipe.rakuten.co.jp/category/30-315-1231/
        30-315-1232	冷奴	https://recipe.rakuten.co.jp/category/30-315-1232/
        30-315-1233	肉豆腐	https://recipe.rakuten.co.jp/category/30-315-1233/
        30-316-1239	白和え	https://recipe.rakuten.co.jp/category/30-316-1239/
        30-316-1234	ほうれん草の胡麻和え	https://recipe.rakuten.co.jp/category/30-316-1234/
        30-316-1235	ほうれん草のおひたし	https://recipe.rakuten.co.jp/category/30-316-1235/
        30-316-1236	菜の花のおひたし	https://recipe.rakuten.co.jp/category/30-316-1236/
        30-316-1237	菜の花のからしあえ	https://recipe.rakuten.co.jp/category/30-316-1237/
        30-316-1238	小松菜のおひたし	https://recipe.rakuten.co.jp/category/30-316-1238/
        30-316-1240	その他の和え物	https://recipe.rakuten.co.jp/category/30-316-1240/
        30-317-1241	わかめの酢の物	https://recipe.rakuten.co.jp/category/30-317-1241/
        30-317-1242	きゅうりの酢の物	https://recipe.rakuten.co.jp/category/30-317-1242/
        30-317-1243	その他の酢の物	https://recipe.rakuten.co.jp/category/30-317-1243/
        31-318-1244	ローストビーフ	https://recipe.rakuten.co.jp/category/31-318-1244/
        31-319-1245	豚の角煮	https://recipe.rakuten.co.jp/category/31-319-1245/
        31-320-1246	チキン南蛮	https://recipe.rakuten.co.jp/category/31-320-1246/
        31-321-1247	ピーマンの肉詰め	https://recipe.rakuten.co.jp/category/31-321-1247/
        31-322-1248	ステーキ	https://recipe.rakuten.co.jp/category/31-322-1248/
        31-323-1249	ロールキャベツ	https://recipe.rakuten.co.jp/category/31-323-1249/
        31-324-1250	スペアリブ	https://recipe.rakuten.co.jp/category/31-324-1250/
        31-325-1251	ローストチキン	https://recipe.rakuten.co.jp/category/31-325-1251/
        31-326-1252	もつ煮込み	https://recipe.rakuten.co.jp/category/31-326-1252/
        31-327-1253	ミートボール・肉団子	https://recipe.rakuten.co.jp/category/31-327-1253/
        31-328-1254	ミートローフ	https://recipe.rakuten.co.jp/category/31-328-1254/
        31-329-1255	牛すじ煮込み	https://recipe.rakuten.co.jp/category/31-329-1255/
        31-330-1256	とんかつ	https://recipe.rakuten.co.jp/category/31-330-1256/
        31-331-1257	ポークソテー	https://recipe.rakuten.co.jp/category/31-331-1257/
        31-332-1258	つくね	https://recipe.rakuten.co.jp/category/31-332-1258/
        31-333-1259	チャーシュー（焼き豚）	https://recipe.rakuten.co.jp/category/31-333-1259/
        31-334-1260	煮豚	https://recipe.rakuten.co.jp/category/31-334-1260/
        31-335-1261	鶏肉のトマト煮	https://recipe.rakuten.co.jp/category/31-335-1261/
        31-335-1262	鶏肉のクリーム煮	https://recipe.rakuten.co.jp/category/31-335-1262/
        31-335-1263	鶏肉のさっぱり煮	https://recipe.rakuten.co.jp/category/31-335-1263/
        31-335-1264	照り焼きチキン	https://recipe.rakuten.co.jp/category/31-335-1264/
        31-335-1265	チキンソテー	https://recipe.rakuten.co.jp/category/31-335-1265/
        31-335-1266	鶏そぼろ	https://recipe.rakuten.co.jp/category/31-335-1266/
        31-335-1267	蒸し鶏	https://recipe.rakuten.co.jp/category/31-335-1267/
        31-335-1268	焼き鳥	https://recipe.rakuten.co.jp/category/31-335-1268/
        31-335-1269	その他の鶏肉料理	https://recipe.rakuten.co.jp/category/31-335-1269/
        32-336-1270	ぶり大根	https://recipe.rakuten.co.jp/category/32-336-1270/
        32-337-1271	ぶりの照り焼き	https://recipe.rakuten.co.jp/category/32-337-1271/
        32-338-1272	さばの味噌煮	https://recipe.rakuten.co.jp/category/32-338-1272/
        32-339-1273	金目鯛の煮付け	https://recipe.rakuten.co.jp/category/32-339-1273/
        32-339-1274	カレイの煮付け	https://recipe.rakuten.co.jp/category/32-339-1274/
        32-339-1275	さばの煮付け	https://recipe.rakuten.co.jp/category/32-339-1275/
        32-339-1276	メバルの煮付け	https://recipe.rakuten.co.jp/category/32-339-1276/
        32-339-1277	その他の煮魚	https://recipe.rakuten.co.jp/category/32-339-1277/
        32-340-1278	あさりの酒蒸し	https://recipe.rakuten.co.jp/category/32-340-1278/
        32-341-1279	鮭のムニエル	https://recipe.rakuten.co.jp/category/32-341-1279/
        32-342-1280	鯵の南蛮漬け	https://recipe.rakuten.co.jp/category/32-342-1280/
        32-342-1281	鮭の南蛮漬け	https://recipe.rakuten.co.jp/category/32-342-1281/
        32-342-1282	その他の南蛮漬け	https://recipe.rakuten.co.jp/category/32-342-1282/
        32-343-1285	焼き魚	https://recipe.rakuten.co.jp/category/32-343-1285/
        32-344-1286	鮭のホイル焼き	https://recipe.rakuten.co.jp/category/32-344-1286/
        32-345-1287	いわしのつみれ	https://recipe.rakuten.co.jp/category/32-345-1287/
        32-346-1288	かつおのたたき	https://recipe.rakuten.co.jp/category/32-346-1288/
        32-347-1289	いわしの梅煮	https://recipe.rakuten.co.jp/category/32-347-1289/
        32-348-1290	かぶら蒸し	https://recipe.rakuten.co.jp/category/32-348-1290/
        32-349-1291	その他の魚料理	https://recipe.rakuten.co.jp/category/32-349-1291/
        33-350-1292	ゆで卵	https://recipe.rakuten.co.jp/category/33-350-1292/
        33-351-1293	温泉卵	https://recipe.rakuten.co.jp/category/33-351-1293/
        33-352-1294	半熟卵	https://recipe.rakuten.co.jp/category/33-352-1294/
        33-353-1295	だし巻き卵・卵焼き	https://recipe.rakuten.co.jp/category/33-353-1295/
        33-354-1296	茶碗蒸し	https://recipe.rakuten.co.jp/category/33-354-1296/
        33-355-1297	キッシュ	https://recipe.rakuten.co.jp/category/33-355-1297/
        33-356-1298	オムレツ	https://recipe.rakuten.co.jp/category/33-356-1298/
        33-357-1299	かに玉	https://recipe.rakuten.co.jp/category/33-357-1299/
        33-358-1300	スクランブルエッグ	https://recipe.rakuten.co.jp/category/33-358-1300/
        33-359-1301	煮卵	https://recipe.rakuten.co.jp/category/33-359-1301/
        33-360-1302	目玉焼き	https://recipe.rakuten.co.jp/category/33-360-1302/
        33-361-1303	ニラ玉	https://recipe.rakuten.co.jp/category/33-361-1303/
        33-362-1304	ポーチドエッグ	https://recipe.rakuten.co.jp/category/33-362-1304/
        33-363-1305	スコッチエッグ	https://recipe.rakuten.co.jp/category/33-363-1305/
        33-364-1306	卵とじ	https://recipe.rakuten.co.jp/category/33-364-1306/
        33-365-1307	薄焼き卵	https://recipe.rakuten.co.jp/category/33-365-1307/
        33-366-1308	炒り卵	https://recipe.rakuten.co.jp/category/33-366-1308/
        33-367-1309	その他の卵料理	https://recipe.rakuten.co.jp/category/33-367-1309/
        14-368-1312	ハッシュドビーフ	https://recipe.rakuten.co.jp/category/14-368-1312/
        15-369-1949	ジェノベーゼ	https://recipe.rakuten.co.jp/category/15-369-1949/
        15-382-1331	ラビオリ	https://recipe.rakuten.co.jp/category/15-382-1331/
        16-383-1347	冷やし中華	https://recipe.rakuten.co.jp/category/16-383-1347/
        16-384-1348	つけ麺	https://recipe.rakuten.co.jp/category/16-384-1348/
        16-385-1349	広島風お好み焼き	https://recipe.rakuten.co.jp/category/16-385-1349/
        16-385-1350	モダン焼き	https://recipe.rakuten.co.jp/category/16-385-1350/
        16-385-1351	ねぎ焼き	https://recipe.rakuten.co.jp/category/16-385-1351/
        16-385-1352	その他のお好み焼	https://recipe.rakuten.co.jp/category/16-385-1352/
        16-385-1353	もんじゃ焼き	https://recipe.rakuten.co.jp/category/16-385-1353/
        16-386-1354	たこ焼き	https://recipe.rakuten.co.jp/category/16-386-1354/
        17-387-1359	けんちん汁	https://recipe.rakuten.co.jp/category/17-387-1359/
        17-388-1365	かぼちゃスープ	https://recipe.rakuten.co.jp/category/17-388-1365/
        17-389-1366	チャウダー・クラムチャウダー	https://recipe.rakuten.co.jp/category/17-389-1366/
        17-390-1371	ポトフ	https://recipe.rakuten.co.jp/category/17-390-1371/
        23-391-1372	おでん	https://recipe.rakuten.co.jp/category/23-391-1372/
        23-392-1373	すき焼き	https://recipe.rakuten.co.jp/category/23-392-1373/
        23-393-1374	もつ鍋	https://recipe.rakuten.co.jp/category/23-393-1374/
        23-394-1375	しゃぶしゃぶ	https://recipe.rakuten.co.jp/category/23-394-1375/
        23-395-1376	キムチ鍋	https://recipe.rakuten.co.jp/category/23-395-1376/
        23-396-1377	湯豆腐	https://recipe.rakuten.co.jp/category/23-396-1377/
        23-397-1378	豆乳鍋	https://recipe.rakuten.co.jp/category/23-397-1378/
        23-398-1379	ちゃんこ鍋	https://recipe.rakuten.co.jp/category/23-398-1379/
        23-399-1380	寄せ鍋	https://recipe.rakuten.co.jp/category/23-399-1380/
        23-400-1381	水炊き	https://recipe.rakuten.co.jp/category/23-400-1381/
        23-401-1382	トマト鍋	https://recipe.rakuten.co.jp/category/23-401-1382/
        23-402-1383	あんこう鍋	https://recipe.rakuten.co.jp/category/23-402-1383/
        23-403-1384	石狩鍋	https://recipe.rakuten.co.jp/category/23-403-1384/
        23-404-1385	カレー鍋	https://recipe.rakuten.co.jp/category/23-404-1385/
        23-405-1386	きりたんぽ鍋	https://recipe.rakuten.co.jp/category/23-405-1386/
        23-406-1387	韓国鍋・チゲ鍋	https://recipe.rakuten.co.jp/category/23-406-1387/
        23-407-1388	雪見鍋（みぞれ鍋）	https://recipe.rakuten.co.jp/category/23-407-1388/
        23-408-1389	蒸し鍋	https://recipe.rakuten.co.jp/category/23-408-1389/
        23-409-1390	ねぎま鍋	https://recipe.rakuten.co.jp/category/23-409-1390/
        23-410-1391	鴨鍋	https://recipe.rakuten.co.jp/category/23-410-1391/
        23-411-1392	カニ鍋	https://recipe.rakuten.co.jp/category/23-411-1392/
        23-412-1393	火鍋	https://recipe.rakuten.co.jp/category/23-412-1393/
        23-413-1394	牡蠣鍋	https://recipe.rakuten.co.jp/category/23-413-1394/
        18-415-1395	ポテトサラダ	https://recipe.rakuten.co.jp/category/18-415-1395/
        18-416-1396	春雨サラダ	https://recipe.rakuten.co.jp/category/18-416-1396/
        18-417-1397	大根サラダ	https://recipe.rakuten.co.jp/category/18-417-1397/
        18-418-1398	コールスロー	https://recipe.rakuten.co.jp/category/18-418-1398/
        18-419-1399	かぼちゃサラダ	https://recipe.rakuten.co.jp/category/18-419-1399/
        18-420-1400	ごぼうサラダ	https://recipe.rakuten.co.jp/category/18-420-1400/
        18-421-1401	マカロニサラダ	https://recipe.rakuten.co.jp/category/18-421-1401/
        18-423-1403	コブサラダ	https://recipe.rakuten.co.jp/category/18-423-1403/
        18-424-1404	タラモサラダ	https://recipe.rakuten.co.jp/category/18-424-1404/
        22-432-1428	サンドイッチ	https://recipe.rakuten.co.jp/category/22-432-1428/
        22-433-1429	フレンチトースト	https://recipe.rakuten.co.jp/category/22-433-1429/
        22-434-1430	食パン	https://recipe.rakuten.co.jp/category/22-434-1430/
        22-435-1431	蒸しパン	https://recipe.rakuten.co.jp/category/22-435-1431/
        22-436-1432	ホットサンド	https://recipe.rakuten.co.jp/category/22-436-1432/
        22-437-1441	キャラパン	https://recipe.rakuten.co.jp/category/22-437-1441/
        21-438-1447	シフォンケーキ	https://recipe.rakuten.co.jp/category/21-438-1447/
        21-439-1448	パウンドケーキ	https://recipe.rakuten.co.jp/category/21-439-1448/
        21-440-1456	スイートポテト	https://recipe.rakuten.co.jp/category/21-440-1456/
        21-441-1463	寒天	https://recipe.rakuten.co.jp/category/21-441-1463/
        21-441-1464	ゼリー	https://recipe.rakuten.co.jp/category/21-441-1464/
        21-441-1465	コーヒーゼリー	https://recipe.rakuten.co.jp/category/21-441-1465/
        21-441-1466	フルーツゼリー	https://recipe.rakuten.co.jp/category/21-441-1466/
        21-441-1467	ムース・ババロア	https://recipe.rakuten.co.jp/category/21-441-1467/
        21-442-1468	アイスクリーム	https://recipe.rakuten.co.jp/category/21-442-1468/
        21-442-1469	ジェラート	https://recipe.rakuten.co.jp/category/21-442-1469/
        21-442-1470	シャーベット	https://recipe.rakuten.co.jp/category/21-442-1470/
        11-443-1496	たら	https://recipe.rakuten.co.jp/category/11-443-1496/
        11-444-1508	牡蠣	https://recipe.rakuten.co.jp/category/11-444-1508/
        11-445-1510	明太子	https://recipe.rakuten.co.jp/category/11-445-1510/
        11-445-1511	たらこ	https://recipe.rakuten.co.jp/category/11-445-1511/
        11-445-1512	いくら・筋子	https://recipe.rakuten.co.jp/category/11-445-1512/
        11-446-1513	ちりめんじゃこ	https://recipe.rakuten.co.jp/category/11-446-1513/
        11-446-1514	なまこ	https://recipe.rakuten.co.jp/category/11-446-1514/
        11-446-1515	うに	https://recipe.rakuten.co.jp/category/11-446-1515/
        11-446-1516	白子	https://recipe.rakuten.co.jp/category/11-446-1516/
        11-446-1517	くらげ	https://recipe.rakuten.co.jp/category/11-446-1517/
        12-447-1518	なす	https://recipe.rakuten.co.jp/category/12-447-1518/
        12-448-1519	かぼちゃ	https://recipe.rakuten.co.jp/category/12-448-1519/
        12-449-1520	大根	https://recipe.rakuten.co.jp/category/12-449-1520/
        12-450-1521	きゅうり	https://recipe.rakuten.co.jp/category/12-450-1521/
        12-451-1522	アボカド	https://recipe.rakuten.co.jp/category/12-451-1522/
        12-452-1523	さつまいも	https://recipe.rakuten.co.jp/category/12-452-1523/
        12-453-1524	白菜	https://recipe.rakuten.co.jp/category/12-453-1524/
        12-454-1525	トマト	https://recipe.rakuten.co.jp/category/12-454-1525/
        12-455-1526	ごぼう	https://recipe.rakuten.co.jp/category/12-455-1526/
        12-456-1527	小松菜	https://recipe.rakuten.co.jp/category/12-456-1527/
        12-457-1528	ほうれん草	https://recipe.rakuten.co.jp/category/12-457-1528/
        12-458-1529	ブロッコリー	https://recipe.rakuten.co.jp/category/12-458-1529/
        34-459-1549	ゆず	https://recipe.rakuten.co.jp/category/34-459-1549/
        34-460-1550	柿	https://recipe.rakuten.co.jp/category/34-460-1550/
        34-461-1551	レモン	https://recipe.rakuten.co.jp/category/34-461-1551/
        34-462-1552	ブルーベリー	https://recipe.rakuten.co.jp/category/34-462-1552/
        19-463-1583	柚子胡椒	https://recipe.rakuten.co.jp/category/19-463-1583/
        19-464-1584	オリーブオイル	https://recipe.rakuten.co.jp/category/19-464-1584/
        27-465-1597	グリーンスムージー	https://recipe.rakuten.co.jp/category/27-465-1597/
        27-465-1598	野菜ジュース	https://recipe.rakuten.co.jp/category/27-465-1598/
        27-465-1601	梅ジュース	https://recipe.rakuten.co.jp/category/27-465-1601/
        27-465-1602	梅シロップ	https://recipe.rakuten.co.jp/category/27-465-1602/
        27-465-1603	ジンジャーシロップ	https://recipe.rakuten.co.jp/category/27-465-1603/
        27-465-1599	シェーク・ミックスジュース	https://recipe.rakuten.co.jp/category/27-465-1599/
        27-465-1600	フルーツジュース	https://recipe.rakuten.co.jp/category/27-465-1600/
        27-465-1604	酵素ジュース	https://recipe.rakuten.co.jp/category/27-465-1604/
        35-466-1609	おから	https://recipe.rakuten.co.jp/category/35-466-1609/
        35-467-1610	厚揚げ	https://recipe.rakuten.co.jp/category/35-467-1610/
        35-468-1611	納豆	https://recipe.rakuten.co.jp/category/35-468-1611/
        35-469-1612	高野豆腐	https://recipe.rakuten.co.jp/category/35-469-1612/
        35-470-1613	豆乳	https://recipe.rakuten.co.jp/category/35-470-1613/
        35-471-1614	木綿豆腐	https://recipe.rakuten.co.jp/category/35-471-1614/
        35-472-1615	絹ごし豆腐	https://recipe.rakuten.co.jp/category/35-472-1615/
        35-473-1616	油揚げ	https://recipe.rakuten.co.jp/category/35-473-1616/
        35-474-1617	大豆ミート	https://recipe.rakuten.co.jp/category/35-474-1617/
        35-475-1618	塩豆腐	https://recipe.rakuten.co.jp/category/35-475-1618/
        35-476-1619	その他の大豆・豆腐	https://recipe.rakuten.co.jp/category/35-476-1619/
        35-477-1620	大豆	https://recipe.rakuten.co.jp/category/35-477-1620/
        35-477-1621	ひよこ豆	https://recipe.rakuten.co.jp/category/35-477-1621/
        35-477-1622	金時豆	https://recipe.rakuten.co.jp/category/35-477-1622/
        35-477-1623	レンズ豆	https://recipe.rakuten.co.jp/category/35-477-1623/
        35-477-1624	ミックスビーンズ	https://recipe.rakuten.co.jp/category/35-477-1624/
        35-477-1625	その他の豆	https://recipe.rakuten.co.jp/category/35-477-1625/
        13-478-1626	もち米	https://recipe.rakuten.co.jp/category/13-478-1626/
        13-479-1627	マカロニ・ペンネ	https://recipe.rakuten.co.jp/category/13-479-1627/
        13-480-1628	ホットケーキミックス	https://recipe.rakuten.co.jp/category/13-480-1628/
        13-481-1629	米粉	https://recipe.rakuten.co.jp/category/13-481-1629/
        13-481-1630	きなこ	https://recipe.rakuten.co.jp/category/13-481-1630/
        13-481-1631	そば粉	https://recipe.rakuten.co.jp/category/13-481-1631/
        13-481-1632	小麦粉	https://recipe.rakuten.co.jp/category/13-481-1632/
        13-481-1633	葛粉	https://recipe.rakuten.co.jp/category/13-481-1633/
        13-481-1634	その他の粉物	https://recipe.rakuten.co.jp/category/13-481-1634/
        13-482-1641	クリームチーズ	https://recipe.rakuten.co.jp/category/13-482-1641/
        13-482-1642	モッツァレラチーズ	https://recipe.rakuten.co.jp/category/13-482-1642/
        13-482-1643	カマンベールチーズ	https://recipe.rakuten.co.jp/category/13-482-1643/
        13-482-1644	マスカルポーネ	https://recipe.rakuten.co.jp/category/13-482-1644/
        13-482-1645	カッテージチーズ	https://recipe.rakuten.co.jp/category/13-482-1645/
        13-482-1646	その他の乳製品	https://recipe.rakuten.co.jp/category/13-482-1646/
        13-483-1647	ヨーグルト	https://recipe.rakuten.co.jp/category/13-483-1647/
        13-484-1660	梅干し	https://recipe.rakuten.co.jp/category/13-484-1660/
        13-484-1655	きゅうりの漬物	https://recipe.rakuten.co.jp/category/13-484-1655/
        13-484-1656	浅漬け	https://recipe.rakuten.co.jp/category/13-484-1656/
        13-484-1657	塩漬け	https://recipe.rakuten.co.jp/category/13-484-1657/
        13-484-1658	ぬかづけ（糠漬け）	https://recipe.rakuten.co.jp/category/13-484-1658/
        13-484-1659	しば漬け（柴漬け）	https://recipe.rakuten.co.jp/category/13-484-1659/
        13-484-1661	福神漬け	https://recipe.rakuten.co.jp/category/13-484-1661/
        13-484-1662	たまり漬け	https://recipe.rakuten.co.jp/category/13-484-1662/
        13-484-2000	ピクルス	https://recipe.rakuten.co.jp/category/13-484-2000/
        13-484-1663	その他の漬物	https://recipe.rakuten.co.jp/category/13-484-1663/
        20-485-1664	キャラ弁	https://recipe.rakuten.co.jp/category/20-485-1664/
        20-486-1665	運動会のお弁当	https://recipe.rakuten.co.jp/category/20-486-1665/
        20-487-1666	お花見のお弁当	https://recipe.rakuten.co.jp/category/20-487-1666/
        20-488-1667	遠足・ピクニックのお弁当	https://recipe.rakuten.co.jp/category/20-488-1667/
        36-489-1668	簡単お菓子	https://recipe.rakuten.co.jp/category/36-489-1668/
        36-490-1669	簡単夕食	https://recipe.rakuten.co.jp/category/36-490-1669/
        36-491-1670	簡単おつまみ	https://recipe.rakuten.co.jp/category/36-491-1670/
        36-492-1671	簡単おもてなし料理	https://recipe.rakuten.co.jp/category/36-492-1671/
        36-493-1672	簡単鶏肉料理	https://recipe.rakuten.co.jp/category/36-493-1672/
        36-494-1673	簡単豚肉料理	https://recipe.rakuten.co.jp/category/36-494-1673/
        36-495-1674	簡単魚料理	https://recipe.rakuten.co.jp/category/36-495-1674/
        36-496-1675	5分以内の簡単料理	https://recipe.rakuten.co.jp/category/36-496-1675/
        36-497-1676	男の簡単料理	https://recipe.rakuten.co.jp/category/36-497-1676/
        37-498-1677	100円以下の節約料理	https://recipe.rakuten.co.jp/category/37-498-1677/
        37-499-1678	300円前後の節約料理	https://recipe.rakuten.co.jp/category/37-499-1678/
        37-500-1679	500円前後の節約料理	https://recipe.rakuten.co.jp/category/37-500-1679/
        38-501-1680	朝食の献立（朝ごはん）	https://recipe.rakuten.co.jp/category/38-501-1680/
        38-502-1681	昼食の献立（昼ごはん）	https://recipe.rakuten.co.jp/category/38-502-1681/
        38-503-1682	夕食の献立（晩御飯）	https://recipe.rakuten.co.jp/category/38-503-1682/
        39-504-1683	低カロリーおかず	https://recipe.rakuten.co.jp/category/39-504-1683/
        39-504-1684	低カロリー主食	https://recipe.rakuten.co.jp/category/39-504-1684/
        39-504-1685	低カロリーお菓子	https://recipe.rakuten.co.jp/category/39-504-1685/
        39-505-1686	食物繊維の多い食品の料理	https://recipe.rakuten.co.jp/category/39-505-1686/
        39-505-1687	カルシウムの多い食品の料理	https://recipe.rakuten.co.jp/category/39-505-1687/
        39-505-1688	鉄分の多い食べ物	https://recipe.rakuten.co.jp/category/39-505-1688/
        39-505-1689	ビタミンの多い食品の料理	https://recipe.rakuten.co.jp/category/39-505-1689/
        39-505-1690	その他のヘルシー食材	https://recipe.rakuten.co.jp/category/39-505-1690/
        39-505-2004	ヘルシーワンプレート	https://recipe.rakuten.co.jp/category/39-505-2004/
        39-506-1691	マクロビオティック	https://recipe.rakuten.co.jp/category/39-506-1691/
        39-507-1692	ベジタリアン	https://recipe.rakuten.co.jp/category/39-507-1692/
        39-508-1693	疲労回復	https://recipe.rakuten.co.jp/category/39-508-1693/
        39-509-1694	鉄分の多いレシピ	https://recipe.rakuten.co.jp/category/39-509-1694/
        39-509-1695	葉酸の多いレシピ	https://recipe.rakuten.co.jp/category/39-509-1695/
        39-509-1696	カルシウムの多いレシピ	https://recipe.rakuten.co.jp/category/39-509-1696/
        39-509-1697	食物繊維の多いレシピ	https://recipe.rakuten.co.jp/category/39-509-1697/
        39-509-1698	ビタミンCの多いレシピ	https://recipe.rakuten.co.jp/category/39-509-1698/
        39-510-1699	離乳食5～6ヶ月（ゴックン期）	https://recipe.rakuten.co.jp/category/39-510-1699/
        39-510-1700	離乳食7～8ヶ月（モグモグ期）	https://recipe.rakuten.co.jp/category/39-510-1700/
        39-510-1701	離乳食9～11ヶ月（カミカミ期）	https://recipe.rakuten.co.jp/category/39-510-1701/
        39-510-1702	離乳食12～18ヶ月（パクパク期）	https://recipe.rakuten.co.jp/category/39-510-1702/
        39-511-1703	幼児食(1歳半頃～2歳頃)	https://recipe.rakuten.co.jp/category/39-511-1703/
        39-511-1704	幼児食(3歳頃～6歳頃)	https://recipe.rakuten.co.jp/category/39-511-1704/
        40-512-1705	圧力鍋で作るごはん・パスタ	https://recipe.rakuten.co.jp/category/40-512-1705/
        40-512-1706	圧力鍋で作るカレー	https://recipe.rakuten.co.jp/category/40-512-1706/
        40-512-1707	圧力鍋で作る豚の角煮	https://recipe.rakuten.co.jp/category/40-512-1707/
        40-512-1708	圧力鍋で作るスペアリブ	https://recipe.rakuten.co.jp/category/40-512-1708/
        40-512-1709	圧力鍋で作るその他の肉のおかず	https://recipe.rakuten.co.jp/category/40-512-1709/
        40-512-1710	圧力鍋で作る野菜のおかず	https://recipe.rakuten.co.jp/category/40-512-1710/
        40-512-1711	圧力鍋で作る魚介のおかず	https://recipe.rakuten.co.jp/category/40-512-1711/
        40-512-1712	圧力鍋で作るスープ	https://recipe.rakuten.co.jp/category/40-512-1712/
        40-512-1713	圧力鍋で作るスイーツ	https://recipe.rakuten.co.jp/category/40-512-1713/
        40-512-1714	その他の圧力鍋で作る料理	https://recipe.rakuten.co.jp/category/40-512-1714/
        40-513-1715	ホームベーカリーにおまかせ	https://recipe.rakuten.co.jp/category/40-513-1715/
        40-513-1716	ホームベーカリー使いこなし	https://recipe.rakuten.co.jp/category/40-513-1716/
        40-514-1717	シリコンスチーマーで作るごはん・パスタ	https://recipe.rakuten.co.jp/category/40-514-1717/
        40-514-1718	シリコンスチーマーで作る肉のおかず	https://recipe.rakuten.co.jp/category/40-514-1718/
        40-514-1719	シリコンスチーマーで作る野菜のおかず	https://recipe.rakuten.co.jp/category/40-514-1719/
        40-514-1720	シリコンスチーマーで作る魚介のおかず	https://recipe.rakuten.co.jp/category/40-514-1720/
        40-514-1721	シリコンスチーマーで作るスープ	https://recipe.rakuten.co.jp/category/40-514-1721/
        40-514-1722	シリコンスチーマーで作るスイーツ	https://recipe.rakuten.co.jp/category/40-514-1722/
        40-514-1723	その他のシリコンスチーマーで作る料理	https://recipe.rakuten.co.jp/category/40-514-1723/
        40-515-1724	タジン鍋	https://recipe.rakuten.co.jp/category/40-515-1724/
        40-516-1725	炊飯器で作るケーキ	https://recipe.rakuten.co.jp/category/40-516-1725/
        40-516-1726	炊飯器で作るチーズケーキ	https://recipe.rakuten.co.jp/category/40-516-1726/
        40-516-1727	炊飯器で作るピラフ	https://recipe.rakuten.co.jp/category/40-516-1727/
        40-516-1728	炊飯器で作るホットケーキミックス	https://recipe.rakuten.co.jp/category/40-516-1728/
        40-516-1729	その他の炊飯器で作る料理	https://recipe.rakuten.co.jp/category/40-516-1729/
        40-517-1730	スープジャー	https://recipe.rakuten.co.jp/category/40-517-1730/
        40-518-1731	ホットプレートで作るパエリア	https://recipe.rakuten.co.jp/category/40-518-1731/
        40-518-1732	その他のホットプレートで作る料理	https://recipe.rakuten.co.jp/category/40-518-1732/
        40-519-1733	電子レンジで作るとうもろこし	https://recipe.rakuten.co.jp/category/40-519-1733/
        40-519-1734	電子レンジで作る温泉卵	https://recipe.rakuten.co.jp/category/40-519-1734/
        40-519-1735	電子レンジで作る茶碗蒸し	https://recipe.rakuten.co.jp/category/40-519-1735/
        40-519-1736	電子レンジで作る焼き芋・さつまいも	https://recipe.rakuten.co.jp/category/40-519-1736/
        40-519-1737	電子レンジで作る焼き魚	https://recipe.rakuten.co.jp/category/40-519-1737/
        40-519-1738	電子レンジで作るじゃがバター	https://recipe.rakuten.co.jp/category/40-519-1738/
        40-519-1739	その他の電子レンジで作る料理	https://recipe.rakuten.co.jp/category/40-519-1739/
        40-520-1740	無水鍋	https://recipe.rakuten.co.jp/category/40-520-1740/
        40-521-1741	ホーロー鍋で作るごはん・パスタ	https://recipe.rakuten.co.jp/category/40-521-1741/
        40-521-1742	ホーロー鍋で作る肉のおかず	https://recipe.rakuten.co.jp/category/40-521-1742/
        40-521-1743	ホーロー鍋で作る野菜のおかず	https://recipe.rakuten.co.jp/category/40-521-1743/
        40-521-1744	ホーロー鍋で作る魚介のおかず	https://recipe.rakuten.co.jp/category/40-521-1744/
        40-521-1745	ホーロー鍋で作るスープ	https://recipe.rakuten.co.jp/category/40-521-1745/
        40-521-1746	ホーロー鍋で作るスイーツ	https://recipe.rakuten.co.jp/category/40-521-1746/
        40-521-1747	その他のホーロー鍋で作る料理	https://recipe.rakuten.co.jp/category/40-521-1747/
        40-522-1748	ミキサー	https://recipe.rakuten.co.jp/category/40-522-1748/
        40-523-1749	中華鍋	https://recipe.rakuten.co.jp/category/40-523-1749/
        40-524-1750	フライパン一つでできる	https://recipe.rakuten.co.jp/category/40-524-1750/
        40-525-1751	vitamix(バイタミックス)	https://recipe.rakuten.co.jp/category/40-525-1751/
        40-525-1752	バーミックス	https://recipe.rakuten.co.jp/category/40-525-1752/
        40-525-1753	クイジナート	https://recipe.rakuten.co.jp/category/40-525-1753/
        40-525-1754	ルクエ	https://recipe.rakuten.co.jp/category/40-525-1754/
        40-525-1755	ル・クルーゼ	https://recipe.rakuten.co.jp/category/40-525-1755/
        40-525-1756	ストウブ	https://recipe.rakuten.co.jp/category/40-525-1756/
        40-525-1757	活力鍋	https://recipe.rakuten.co.jp/category/40-525-1757/
        40-525-1758	ビタントニオ	https://recipe.rakuten.co.jp/category/40-525-1758/
        40-526-1759	その他の調理器具	https://recipe.rakuten.co.jp/category/40-526-1759/
        41-531-1760	酢豚	https://recipe.rakuten.co.jp/category/41-531-1760/
        41-532-1761	チンジャオロース	https://recipe.rakuten.co.jp/category/41-532-1761/
        41-533-1762	八宝菜	https://recipe.rakuten.co.jp/category/41-533-1762/
        41-534-1763	マーボー豆腐（麻婆豆腐）	https://recipe.rakuten.co.jp/category/41-534-1763/
        41-535-1764	エビチリ	https://recipe.rakuten.co.jp/category/41-535-1764/
        41-536-1765	エビマヨ	https://recipe.rakuten.co.jp/category/41-536-1765/
        41-537-1766	ホイコーロー（回鍋肉）	https://recipe.rakuten.co.jp/category/41-537-1766/
        41-538-1767	ジャージャー麺	https://recipe.rakuten.co.jp/category/41-538-1767/
        41-539-1768	バンバンジー	https://recipe.rakuten.co.jp/category/41-539-1768/
        41-540-1769	杏仁豆腐	https://recipe.rakuten.co.jp/category/41-540-1769/
        41-541-1770	坦々麺	https://recipe.rakuten.co.jp/category/41-541-1770/
        41-542-1771	油淋鶏	https://recipe.rakuten.co.jp/category/41-542-1771/
        41-543-1772	ビーフン	https://recipe.rakuten.co.jp/category/41-543-1772/
        41-544-1773	ちまき（中華ちまき）	https://recipe.rakuten.co.jp/category/41-544-1773/
        41-545-1774	サンラータン（酸辣湯）	https://recipe.rakuten.co.jp/category/41-545-1774/
        41-546-1775	春巻き	https://recipe.rakuten.co.jp/category/41-546-1775/
        41-547-1776	肉まん	https://recipe.rakuten.co.jp/category/41-547-1776/
        41-548-1777	焼売（シュウマイ）	https://recipe.rakuten.co.jp/category/41-548-1777/
        41-549-1778	その他の中華料理	https://recipe.rakuten.co.jp/category/41-549-1778/
        42-550-1779	チャプチェ	https://recipe.rakuten.co.jp/category/42-550-1779/
        42-551-1780	チヂミ	https://recipe.rakuten.co.jp/category/42-551-1780/
        42-552-1781	ビビンバ	https://recipe.rakuten.co.jp/category/42-552-1781/
        42-553-1782	もやしナムル	https://recipe.rakuten.co.jp/category/42-553-1782/
        42-553-1783	その他のナムル	https://recipe.rakuten.co.jp/category/42-553-1783/
        42-554-1784	キムチ	https://recipe.rakuten.co.jp/category/42-554-1784/
        42-555-1785	プルコギ	https://recipe.rakuten.co.jp/category/42-555-1785/
        42-556-1786	チョレギサラダ	https://recipe.rakuten.co.jp/category/42-556-1786/
        42-557-1787	冷麺	https://recipe.rakuten.co.jp/category/42-557-1787/
        42-558-1788	サムゲタン	https://recipe.rakuten.co.jp/category/42-558-1788/
        42-559-1789	サムギョプサル	https://recipe.rakuten.co.jp/category/42-559-1789/
        42-560-1790	クッパ	https://recipe.rakuten.co.jp/category/42-560-1790/
        42-561-1791	タッカルビ	https://recipe.rakuten.co.jp/category/42-561-1791/
        42-562-1792	カムジャタン	https://recipe.rakuten.co.jp/category/42-562-1792/
        42-563-1793	トッポギ	https://recipe.rakuten.co.jp/category/42-563-1793/
        42-564-1794	ケジャン	https://recipe.rakuten.co.jp/category/42-564-1794/
        42-565-1795	スンドゥブ	https://recipe.rakuten.co.jp/category/42-565-1795/
        42-566-1796	テンジャンチゲ	https://recipe.rakuten.co.jp/category/42-566-1796/
        42-567-1797	その他のチゲ	https://recipe.rakuten.co.jp/category/42-567-1797/
        42-568-1798	その他の韓国料理	https://recipe.rakuten.co.jp/category/42-568-1798/
        43-569-1799	ピザ	https://recipe.rakuten.co.jp/category/43-569-1799/
        43-570-1800	ミネストローネ	https://recipe.rakuten.co.jp/category/43-570-1800/
        43-571-1801	バーニャカウダ	https://recipe.rakuten.co.jp/category/43-571-1801/
        43-572-1802	アクアパッツァ	https://recipe.rakuten.co.jp/category/43-572-1802/
        43-573-1803	ピカタ	https://recipe.rakuten.co.jp/category/43-573-1803/
        43-574-1804	ブルスケッタ	https://recipe.rakuten.co.jp/category/43-574-1804/
        43-575-1805	パニーノ・パニーニ	https://recipe.rakuten.co.jp/category/43-575-1805/
        43-576-1806	カルツォーネ	https://recipe.rakuten.co.jp/category/43-576-1806/
        43-577-1807	サーモンカルパッチョ	https://recipe.rakuten.co.jp/category/43-577-1807/
        43-577-1808	鯛のカルパッチョ	https://recipe.rakuten.co.jp/category/43-577-1808/
        43-577-1809	タコのカルパッチョ	https://recipe.rakuten.co.jp/category/43-577-1809/
        43-577-1810	その他のカルパッチョ	https://recipe.rakuten.co.jp/category/43-577-1810/
        43-578-1811	リゾット	https://recipe.rakuten.co.jp/category/43-578-1811/
        43-579-1812	カプレーゼ	https://recipe.rakuten.co.jp/category/43-579-1812/
        43-580-1813	パンナコッタ	https://recipe.rakuten.co.jp/category/43-580-1813/
        43-581-1814	ティラミス	https://recipe.rakuten.co.jp/category/43-581-1814/
        43-582-1815	その他のイタリア料理	https://recipe.rakuten.co.jp/category/43-582-1815/
        44-583-1816	ラタトゥイユ	https://recipe.rakuten.co.jp/category/44-583-1816/
        44-584-1817	チーズフォンデュ	https://recipe.rakuten.co.jp/category/44-584-1817/
        44-585-1818	テリーヌ	https://recipe.rakuten.co.jp/category/44-585-1818/
        44-586-1819	ブイヤベース	https://recipe.rakuten.co.jp/category/44-586-1819/
        44-587-1820	ムニエル	https://recipe.rakuten.co.jp/category/44-587-1820/
        44-588-1821	ビスク	https://recipe.rakuten.co.jp/category/44-588-1821/
        44-589-1822	サーモンのマリネ	https://recipe.rakuten.co.jp/category/44-589-1822/
        44-589-1823	タコのマリネ	https://recipe.rakuten.co.jp/category/44-589-1823/
        44-589-1824	その他のマリネ	https://recipe.rakuten.co.jp/category/44-589-1824/
        44-590-1825	ガレット	https://recipe.rakuten.co.jp/category/44-590-1825/
        44-591-1826	その他のフランス料理	https://recipe.rakuten.co.jp/category/44-591-1826/
        46-596-1829	トムヤムクン	https://recipe.rakuten.co.jp/category/46-596-1829/
        46-596-1830	タイカレー	https://recipe.rakuten.co.jp/category/46-596-1830/
        46-596-1831	パッタイ	https://recipe.rakuten.co.jp/category/46-596-1831/
        46-596-1832	タイスキ	https://recipe.rakuten.co.jp/category/46-596-1832/
        46-596-1833	サテ	https://recipe.rakuten.co.jp/category/46-596-1833/
        46-596-1834	ヤムウンセン	https://recipe.rakuten.co.jp/category/46-596-1834/
        46-596-1835	その他のタイ料理	https://recipe.rakuten.co.jp/category/46-596-1835/
        46-597-1836	タンドリーチキン	https://recipe.rakuten.co.jp/category/46-597-1836/
        46-597-1837	ナン	https://recipe.rakuten.co.jp/category/46-597-1837/
        46-597-1838	ラッシー	https://recipe.rakuten.co.jp/category/46-597-1838/
        46-597-1839	サモサ	https://recipe.rakuten.co.jp/category/46-597-1839/
        46-597-1840	チャイ	https://recipe.rakuten.co.jp/category/46-597-1840/
        46-597-1841	チャパティ	https://recipe.rakuten.co.jp/category/46-597-1841/
        46-597-1842	シークカバブ	https://recipe.rakuten.co.jp/category/46-597-1842/
        46-597-1843	ビリヤニ	https://recipe.rakuten.co.jp/category/46-597-1843/
        46-597-1844	ラッサム	https://recipe.rakuten.co.jp/category/46-597-1844/
        46-597-1845	ドーサ	https://recipe.rakuten.co.jp/category/46-597-1845/
        46-597-1846	その他のインド料理	https://recipe.rakuten.co.jp/category/46-597-1846/
        46-598-1847	生春巻き	https://recipe.rakuten.co.jp/category/46-598-1847/
        46-598-1848	フォー	https://recipe.rakuten.co.jp/category/46-598-1848/
        46-598-1849	その他のベトナム料理	https://recipe.rakuten.co.jp/category/46-598-1849/
        46-599-1850	タコス	https://recipe.rakuten.co.jp/category/46-599-1850/
        46-599-1851	チリコンカン	https://recipe.rakuten.co.jp/category/46-599-1851/
        46-599-1852	トルティーヤ	https://recipe.rakuten.co.jp/category/46-599-1852/
        46-599-1853	ブリート・ブリトー	https://recipe.rakuten.co.jp/category/46-599-1853/
        46-599-1854	ワカモレ	https://recipe.rakuten.co.jp/category/46-599-1854/
        46-599-1855	サルサ	https://recipe.rakuten.co.jp/category/46-599-1855/
        46-599-1856	ナチョス	https://recipe.rakuten.co.jp/category/46-599-1856/
        46-599-1857	エンチラーダ	https://recipe.rakuten.co.jp/category/46-599-1857/
        46-599-1858	ケサディーヤ・ケサディージャ	https://recipe.rakuten.co.jp/category/46-599-1858/
        46-599-1859	その他のメキシコ料理	https://recipe.rakuten.co.jp/category/46-599-1859/
        47-600-1860	ソーキそば・沖縄そば	https://recipe.rakuten.co.jp/category/47-600-1860/
        47-601-1861	海ぶどう	https://recipe.rakuten.co.jp/category/47-601-1861/
        47-602-1862	ゴーヤチャンプル	https://recipe.rakuten.co.jp/category/47-602-1862/
        47-603-1863	そうめんチャンプルー	https://recipe.rakuten.co.jp/category/47-603-1863/
        47-604-1864	ラフテー	https://recipe.rakuten.co.jp/category/47-604-1864/
        47-605-1865	ミミガー	https://recipe.rakuten.co.jp/category/47-605-1865/
        47-606-1866	ジューシー	https://recipe.rakuten.co.jp/category/47-606-1866/
        47-607-1867	サーターアンダーギー	https://recipe.rakuten.co.jp/category/47-607-1867/
        47-608-1868	ヒラヤーチー	https://recipe.rakuten.co.jp/category/47-608-1868/
        47-609-1869	コーレーグス・島唐辛子	https://recipe.rakuten.co.jp/category/47-609-1869/
        47-610-1870	その他の沖縄料理	https://recipe.rakuten.co.jp/category/47-610-1870/
        48-611-1871	ジンギスカン	https://recipe.rakuten.co.jp/category/48-611-1871/
        48-612-1872	ちゃんちゃん焼き	https://recipe.rakuten.co.jp/category/48-612-1872/
        48-613-1873	筑前煮	https://recipe.rakuten.co.jp/category/48-613-1873/
        48-614-1874	すいとん	https://recipe.rakuten.co.jp/category/48-614-1874/
        48-615-1875	ほうとう	https://recipe.rakuten.co.jp/category/48-615-1875/
        48-616-1876	ひつまぶし	https://recipe.rakuten.co.jp/category/48-616-1876/
        48-617-1877	ちゃんぽん	https://recipe.rakuten.co.jp/category/48-617-1877/
        48-618-1878	明石焼き	https://recipe.rakuten.co.jp/category/48-618-1878/
        48-619-1879	いかめし	https://recipe.rakuten.co.jp/category/48-619-1879/
        48-620-1880	せんべい汁	https://recipe.rakuten.co.jp/category/48-620-1880/
        48-621-1881	皿うどん	https://recipe.rakuten.co.jp/category/48-621-1881/
        48-622-1882	きりたんぽ	https://recipe.rakuten.co.jp/category/48-622-1882/
        48-623-1883	のっぺい汁	https://recipe.rakuten.co.jp/category/48-623-1883/
        48-624-1884	治部煮	https://recipe.rakuten.co.jp/category/48-624-1884/
        48-625-1885	いちご煮	https://recipe.rakuten.co.jp/category/48-625-1885/
        48-626-1886	三升漬け	https://recipe.rakuten.co.jp/category/48-626-1886/
        48-627-1887	三平汁	https://recipe.rakuten.co.jp/category/48-627-1887/
        48-628-1888	じゃっぱ汁	https://recipe.rakuten.co.jp/category/48-628-1888/
        48-629-1889	辛子蓮根	https://recipe.rakuten.co.jp/category/48-629-1889/
        48-630-1890	その他の郷土料理	https://recipe.rakuten.co.jp/category/48-630-1890/
        24-631-1891	お食い初め料理	https://recipe.rakuten.co.jp/category/24-631-1891/
        24-632-1892	誕生日の料理	https://recipe.rakuten.co.jp/category/24-632-1892/
        24-633-1893	結婚記念日	https://recipe.rakuten.co.jp/category/24-633-1893/
        24-634-1894	パーティー料理・ホームパーティ	https://recipe.rakuten.co.jp/category/24-634-1894/
        24-635-1895	子どものパーティ	https://recipe.rakuten.co.jp/category/24-635-1895/
        49-636-1897	きんとん（栗きんとん）	https://recipe.rakuten.co.jp/category/49-636-1897/
        49-637-1898	お雑煮	https://recipe.rakuten.co.jp/category/49-637-1898/
        49-638-1899	錦玉子・伊達巻	https://recipe.rakuten.co.jp/category/49-638-1899/
        49-639-1900	なます	https://recipe.rakuten.co.jp/category/49-639-1900/
        49-640-1901	黒豆	https://recipe.rakuten.co.jp/category/49-640-1901/
        49-641-1902	数の子	https://recipe.rakuten.co.jp/category/49-641-1902/
        49-642-1903	田作り	https://recipe.rakuten.co.jp/category/49-642-1903/
        49-643-1904	煮しめ	https://recipe.rakuten.co.jp/category/49-643-1904/
        49-644-1905	たたきごぼう	https://recipe.rakuten.co.jp/category/49-644-1905/
        49-645-1906	昆布巻き	https://recipe.rakuten.co.jp/category/49-645-1906/
        49-646-1907	酢れんこん	https://recipe.rakuten.co.jp/category/49-646-1907/
        49-648-1909	おせちの海老料理	https://recipe.rakuten.co.jp/category/49-648-1909/
        49-649-1910	八幡巻き	https://recipe.rakuten.co.jp/category/49-649-1910/
        49-650-1911	簡単おせち料理	https://recipe.rakuten.co.jp/category/49-650-1911/
        49-651-1912	その他のおせち料理	https://recipe.rakuten.co.jp/category/49-651-1912/
        50-652-1913	クリスマスケーキ	https://recipe.rakuten.co.jp/category/50-652-1913/
        50-653-1914	クリスマスオードブル	https://recipe.rakuten.co.jp/category/50-653-1914/
        50-654-1915	クリスマスチキン	https://recipe.rakuten.co.jp/category/50-654-1915/
        50-655-1916	クリスマスサラダ	https://recipe.rakuten.co.jp/category/50-655-1916/
        50-656-1917	クリスマス向けアレンジ	https://recipe.rakuten.co.jp/category/50-656-1917/
        51-657-1918	ひな祭りケーキ	https://recipe.rakuten.co.jp/category/51-657-1918/
        51-658-1919	ひな祭りちらしずし	https://recipe.rakuten.co.jp/category/51-658-1919/
        51-659-1920	ひな祭り向けアレンジ	https://recipe.rakuten.co.jp/category/51-659-1920/
        52-660-1921	ホワイトデーのお菓子	https://recipe.rakuten.co.jp/category/52-660-1921/
        52-660-1922	ホワイトデーのチョコ	https://recipe.rakuten.co.jp/category/52-660-1922/
        52-660-1923	ホワイトデーのクッキー	https://recipe.rakuten.co.jp/category/52-660-1923/
        52-660-1924	ホワイトデー向けアレンジ	https://recipe.rakuten.co.jp/category/52-660-1924/
        52-661-1929	お花見・春の行楽	https://recipe.rakuten.co.jp/category/52-661-1929/
        52-662-1930	こどもの日	https://recipe.rakuten.co.jp/category/52-662-1930/
        52-663-1925	母の日のケーキ	https://recipe.rakuten.co.jp/category/52-663-1925/
        52-663-1926	母の日のお菓子	https://recipe.rakuten.co.jp/category/52-663-1926/
        52-663-1927	母の日の料理	https://recipe.rakuten.co.jp/category/52-663-1927/
        52-663-1928	母の日向けアレンジ	https://recipe.rakuten.co.jp/category/52-663-1928/
        53-664-1931	父の日	https://recipe.rakuten.co.jp/category/53-664-1931/
        53-665-1932	夏バテ対策	https://recipe.rakuten.co.jp/category/53-665-1932/
        53-666-1933	お祭り	https://recipe.rakuten.co.jp/category/53-666-1933/
        53-667-1934	十五夜・お月見	https://recipe.rakuten.co.jp/category/53-667-1934/
        54-668-1935	ハロウィンのお菓子	https://recipe.rakuten.co.jp/category/54-668-1935/
        54-668-1936	ハロウィン向けアレンジ	https://recipe.rakuten.co.jp/category/54-668-1936/
        54-669-1937	秋の行楽・紅葉	https://recipe.rakuten.co.jp/category/54-669-1937/
        54-670-1938	七五三の料理	https://recipe.rakuten.co.jp/category/54-670-1938/
        55-671-1939	節分	https://recipe.rakuten.co.jp/category/55-671-1939/
        55-672-1940	恵方巻き	https://recipe.rakuten.co.jp/category/55-672-1940/
        55-673-1941	ななくさ（七草粥）	https://recipe.rakuten.co.jp/category/55-673-1941/
        55-674-1942	バレンタインのケーキ	https://recipe.rakuten.co.jp/category/55-674-1942/
        55-674-1943	バレンタインチョコ	https://recipe.rakuten.co.jp/category/55-674-1943/
        55-674-1944	バレンタインの焼き菓子	https://recipe.rakuten.co.jp/category/55-674-1944/
        55-674-1945	バレンタイン向けアレンジ	https://recipe.rakuten.co.jp/category/55-674-1945/
        19-675-1566	塩麹	https://recipe.rakuten.co.jp/category/19-675-1566/
        19-675-1567	醤油麹	https://recipe.rakuten.co.jp/category/19-675-1567/
        19-675-1999	塩レモン	https://recipe.rakuten.co.jp/category/19-675-1999/
        19-675-1580	にんにく醤油	https://recipe.rakuten.co.jp/category/19-675-1580/
        19-675-1581	酒粕	https://recipe.rakuten.co.jp/category/19-675-1581/
        19-675-1569	肉味噌	https://recipe.rakuten.co.jp/category/19-675-1569/
        19-675-1568	味噌	https://recipe.rakuten.co.jp/category/19-675-1568/
        19-675-1570	酢味噌	https://recipe.rakuten.co.jp/category/19-675-1570/
        19-675-1571	酢	https://recipe.rakuten.co.jp/category/19-675-1571/
        19-675-1572	バルサミコ酢	https://recipe.rakuten.co.jp/category/19-675-1572/
        19-675-1573	黒酢	https://recipe.rakuten.co.jp/category/19-675-1573/
        19-675-1574	すし酢	https://recipe.rakuten.co.jp/category/19-675-1574/
        19-675-1575	梅酢	https://recipe.rakuten.co.jp/category/19-675-1575/
        19-675-1576	ポン酢	https://recipe.rakuten.co.jp/category/19-675-1576/
        19-675-1577	三杯酢	https://recipe.rakuten.co.jp/category/19-675-1577/
        19-675-1578	カレー粉	https://recipe.rakuten.co.jp/category/19-675-1578/
        19-675-1579	しょうゆ	https://recipe.rakuten.co.jp/category/19-675-1579/
        19-675-1582	その他の発酵食品・発酵調味料	https://recipe.rakuten.co.jp/category/19-675-1582/
        15-676-1947	ナポリタン	https://recipe.rakuten.co.jp/category/15-676-1947/
        15-677-1950	ペスカトーレ	https://recipe.rakuten.co.jp/category/15-677-1950/
        15-678-1953	アラビアータ	https://recipe.rakuten.co.jp/category/15-678-1953/
        15-679-1954	トマトクリームパスタ	https://recipe.rakuten.co.jp/category/15-679-1954/
        15-680-1958	トマト系パスタ	https://recipe.rakuten.co.jp/category/15-680-1958/
        15-681-1948	ペペロンチーノ	https://recipe.rakuten.co.jp/category/15-681-1948/
        15-682-1952	ボンゴレ	https://recipe.rakuten.co.jp/category/15-682-1952/
        15-683-1951	たらこパスタ・明太子パスタ	https://recipe.rakuten.co.jp/category/15-683-1951/
        15-684-1955	納豆パスタ	https://recipe.rakuten.co.jp/category/15-684-1955/
        15-685-1956	きのこパスタ	https://recipe.rakuten.co.jp/category/15-685-1956/
        15-686-1957	ツナパスタ	https://recipe.rakuten.co.jp/category/15-686-1957/
        15-687-1946	カルボナーラ	https://recipe.rakuten.co.jp/category/15-687-1946/
        34-688-1961	りんご	https://recipe.rakuten.co.jp/category/34-688-1961/
        34-689-1981	栗	https://recipe.rakuten.co.jp/category/34-689-1981/
        34-689-1982	梨	https://recipe.rakuten.co.jp/category/34-689-1982/
        34-689-1983	ぶどう	https://recipe.rakuten.co.jp/category/34-689-1983/
        34-689-1984	洋梨・ラフランス	https://recipe.rakuten.co.jp/category/34-689-1984/
        34-689-1985	ザクロ	https://recipe.rakuten.co.jp/category/34-689-1985/
        34-690-1963	グレープフルーツ	https://recipe.rakuten.co.jp/category/34-690-1963/
        34-691-1964	キウイ	https://recipe.rakuten.co.jp/category/34-691-1964/
        34-692-1965	いちご	https://recipe.rakuten.co.jp/category/34-692-1965/
        34-692-1966	デコポン	https://recipe.rakuten.co.jp/category/34-692-1966/
        34-693-1967	梅	https://recipe.rakuten.co.jp/category/34-693-1967/
        34-693-1968	すだち	https://recipe.rakuten.co.jp/category/34-693-1968/
        34-693-1969	ピーナツ（落花生）	https://recipe.rakuten.co.jp/category/34-693-1969/
        34-693-1970	桃	https://recipe.rakuten.co.jp/category/34-693-1970/
        34-693-1971	プルーン	https://recipe.rakuten.co.jp/category/34-693-1971/
        34-693-1972	あんず	https://recipe.rakuten.co.jp/category/34-693-1972/
        34-693-1973	夏みかん	https://recipe.rakuten.co.jp/category/34-693-1973/
        34-693-1974	チェリー（さくらんぼ）	https://recipe.rakuten.co.jp/category/34-693-1974/
        34-693-1975	びわ	https://recipe.rakuten.co.jp/category/34-693-1975/
        34-693-1976	スイカ	https://recipe.rakuten.co.jp/category/34-693-1976/
        34-693-1977	メロン	https://recipe.rakuten.co.jp/category/34-693-1977/
        34-693-1978	イチジク	https://recipe.rakuten.co.jp/category/34-693-1978/
        34-693-1979	パイナップル	https://recipe.rakuten.co.jp/category/34-693-1979/
        34-693-1980	マンゴー	https://recipe.rakuten.co.jp/category/34-693-1980/
        34-695-1986	きんかん	https://recipe.rakuten.co.jp/category/34-695-1986/
        34-695-1987	みかん	https://recipe.rakuten.co.jp/category/34-695-1987/
        34-695-1988	はっさく	https://recipe.rakuten.co.jp/category/34-695-1988/
        34-695-1989	いよかん	https://recipe.rakuten.co.jp/category/34-695-1989/
        34-696-1990	その他の果物	https://recipe.rakuten.co.jp/category/34-696-1990/
        34-697-1962	バナナ	https://recipe.rakuten.co.jp/category/34-697-1962/
        23-698-1992	白味噌鍋	https://recipe.rakuten.co.jp/category/23-698-1992/
        39-699-1995	糖質制限	https://recipe.rakuten.co.jp/category/39-699-1995/
        19-700-1994	ココナッツオイル	https://recipe.rakuten.co.jp/category/19-700-1994/
        25-701-1996	シェパーズパイ	https://recipe.rakuten.co.jp/category/25-701-1996/
        25-701-1997	ショートブレッド	https://recipe.rakuten.co.jp/category/25-701-1997/
        25-701-1998	ジンジャークッキー	https://recipe.rakuten.co.jp/category/25-701-1998/
        25-701-2001	その他のイギリス料理	https://recipe.rakuten.co.jp/category/25-701-2001/
        34-702-2002	オレンジ	https://recipe.rakuten.co.jp/category/34-702-2002/
        18-703-2003	ジャーサラダ	https://recipe.rakuten.co.jp/category/18-703-2003/
        40-704-2005	メイソンジャー	https://recipe.rakuten.co.jp/category/40-704-2005/
        39-705-2006	高血圧向け	https://recipe.rakuten.co.jp/category/39-705-2006/
        13-706-2007	もち麦	https://recipe.rakuten.co.jp/category/13-706-2007/
        40-707-2008	キッチンバサミ	https://recipe.rakuten.co.jp/category/40-707-2008/
"""

    user_request = st.text_input("1週間分の献立について、どのような要望がありますか？（例：野菜中心、和食メイン、簡単な料理など）")
    
    start_date = st.date_input("開始日を選択してください", min_value=datetime.now().date())
    weekdays = ["月", "火", "水", "木", "金", "土", "日"]
    st.write(f"選択された開始日: {start_date.strftime('%Y-%m-%d')} ({weekdays[start_date.weekday()]})")
    
    meal_types = st.multiselect("食事の種類を選択してください", ["朝食", "昼食", "夕食"], default=["朝食", "昼食", "夕食"])

    # 主食の比重選択
    st.subheader("主食の比重")
    rice_ratio = st.slider("ごはんもの", 0, 100, 50, 10)
    bread_ratio = st.slider("パン", 0, 100, 25, 10)
    noodle_ratio = st.slider("麺類", 0, 100, 25, 10)

    if "meal_plan" not in st.session_state:
        st.session_state.meal_plan = None
        st.session_state.materials_summary = None
        st.session_state.current_page = "calendar"
        st.session_state.debug_info = {}

    if st.button("献立を作成", key="create_plan"):
        if user_request and meal_types:
            with st.spinner("献立を作成中...しばらくお待ちください（1-2分程度かかります）"):
                progress_bar = st.progress(0)
                
                progress_bar.progress(10)
                category_ids = get_category_ids(user_request, categories, start_date, rice_ratio, bread_ratio, noodle_ratio)
                st.session_state.debug_info['category_ids'] = category_ids
                
                progress_bar.progress(30)
                recipes = get_recipes(category_ids)
                st.session_state.debug_info['recipes'] = recipes
                
                if not recipes:
                    st.error("レシピを取得できませんでした。もう一度お試しください。")
                    return

                progress_bar.progress(50)
                meal_plan_text = select_recipes(recipes, user_request, start_date, meal_types, rice_ratio, bread_ratio, noodle_ratio)
                st.session_state.debug_info['meal_plan_text'] = meal_plan_text
                
                progress_bar.progress(80)
                st.session_state.meal_plan, st.session_state.materials_summary = parse_meal_plan(meal_plan_text, meal_types)
                st.session_state.debug_info['parsed_meal_plan'] = st.session_state.meal_plan
                st.session_state.debug_info['materials_summary'] = st.session_state.materials_summary
                
                progress_bar.progress(100)
                
                st.success("献立が完成しました！")
                st.session_state.current_page = "calendar"
        else:
            st.warning("要望を入力し、少なくとも1つの食事タイプを選択してください。")

    # 献立が生成された後に保存機能を表示
    if st.session_state.meal_plan:
        st.subheader("献立の保存")
        default_save_dir = r"C:\Users\b1019\recipe_service\保存"
        save_dir = st.text_input("保存先ディレクトリを入力してください:", value=default_save_dir)
        if st.button("献立を保存"):
            try:
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                
                # JSONファイルの保存
                json_path = os.path.join(save_dir, f"meal_plan_{timestamp}.json")
                save_meal_plan(st.session_state.meal_plan, st.session_state.materials_summary, json_path)
                st.success(f"献立をJSONファイルとして {json_path} に保存しました。")

                # HTMLファイルの生成
                html_path = os.path.join(save_dir, f"meal_plan_{timestamp}.html")
                generate_html(st.session_state.meal_plan, st.session_state.materials_summary, html_path)
                st.success(f"HTMLファイルを {html_path} に生成しました。")
                
                if st.button("HTMLを開く", key="open_html"):
                    webbrowser.open(f"file://{os.path.abspath(html_path)}")
            except Exception as e:
                st.error(f"献立の保存中にエラーが発生しました: {str(e)}")
                st.error("ディレクトリの権限や空き容量を確認してください。")

    # 読み込み機能の強化（Streamlitの標準ファイルアップローダーを使用）
    st.subheader("保存した献立を読み込む")
    uploaded_file = st.file_uploader("JSONファイルをアップロードしてください", type=["json"])
    
    if uploaded_file is not None:
        try:
            st.session_state.meal_plan, st.session_state.materials_summary = load_meal_plan(uploaded_file)
            st.success("献立を読み込みました。")
            st.session_state.current_page = "calendar"
        except Exception as e:
            st.error(f"献立の読み込みに失敗しました: {str(e)}")

    # 献立の表示（カレンダーまたは詳細）
    if st.session_state.meal_plan:
        if st.session_state.current_page == "calendar":
            display_calendar(st.session_state.meal_plan)
            st.subheader("1週間分の材料まとめ")
            for line in st.session_state.materials_summary:
                st.write(line)
        elif "_" in st.session_state.current_page:
            date, meal_type = st.session_state.current_page.split("_")
            if date in st.session_state.meal_plan and meal_type in st.session_state.meal_plan[date]:
                display_meal_details(date, meal_type, st.session_state.meal_plan[date][meal_type])
            else:
                st.error(f"{date}の{meal_type}の情報が見つかりません。")
                if st.button("カレンダーに戻る"):
                    st.session_state.current_page = "calendar"

    # デバッグ情報の表示
    st.subheader("デバッグ情報")
    if st.session_state.debug_info:
        st.write("選択されたカテゴリID:")
        st.code(st.session_state.debug_info['category_ids'])
        
        st.write("取得されたレシピ数:")
        st.write(len(st.session_state.debug_info['recipes']))
        
        st.write("生成された献立テキスト:")
        st.code(st.session_state.debug_info['meal_plan_text'])
        
        st.write("解析された献立データ:")
        st.json(st.session_state.debug_info['parsed_meal_plan'])
        
        st.write("材料まとめ:")
        for line in st.session_state.debug_info['materials_summary']:
            st.write(line)

    st.sidebar.title("使い方")
    st.sidebar.write("""
    1. 要望を入力欄に記入してください。
    2. 開始日を選択してください。
    3. 食事の種類を選択してください。
    4. 主食の比重を調整してください。
    5. '献立を作成'ボタンをクリックします。
    6. 生成された献立を保存する場合は、保存先を指定して'献立を保存'ボタンをクリックしてください。
    7. カレンダー形式で献立が表示されます。
    8. 各日付の詳細を見るには、対応するボタンをクリックしてください。
    9. 保存した献立を読み込むには、ファイルパスを指定して'献立を読み込む'ボタンをクリックしてください。
    """)

if __name__ == "__main__":
    main()
